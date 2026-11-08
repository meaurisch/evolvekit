"""A study, compiled: an ordinary `evolvekit.yaml`, and the runner's `study-run.json`.

Both land in the run's directory, `<study>/runs/<run-id>/`, and every path in
them is relative to it -- the cases are `../../cases/...`, the runner
`../../harness/runner.py` -- except the application's own, which is the one
thing about a study that belongs to a machine. The config is readable by an
expert and runnable with `python -m evolvekit run`; nothing in it is special
to studies.

    study                                   evolvekit config
    tuned settings, data changes            problem.parameters (help = the harness's `explain`)
    expression constraints                  problem.parameter_constraints
    fixed settings, levers, KPIs, sums,     study-run.json, handed to the runner
      guardrails, SQL constraints
    training cases                          the command stages' instances
    test cases                              the final check only, never the search
    limits.time_per_case_s                  the runner's --time-limit; the stage timeout is
                                            1.5 x the limit + 30 s when the application
                                            stops itself, else the limit itself
    limits.retries / runs_per_case          retries / seeds
    one goal level                          score.objective and direction
    several levels                          score.levels (normalize: none, no racing)
    guardrails                              evaluate.gates: guardrail_violation <= 0
    budget.hours                            budget.max_hours (what the plan leaves the search)
    budget.ai                               models, `rewrite` among the operators, budget.max_usd
    the plan                                stages, workers, pin_cpus, children, generations
    vary.code (and the harness's code:)     problem.tree (no parameters), the brief in the
                                            description, a builtin-build stage with a build;
                                            write_run pins the base to a commit
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from evolvekit.harness import HarnessError
from evolvekit.harness.manifest import Harness
from evolvekit.harness.plan import Plan
from evolvekit.harness.study import Study, parse_within, resolve_kpi

__all__ = [
    "compile_study", "write_run", "runner_input", "runner_kpi", "base_settings", "runner_settings", "study_operators",
    "ai_cap_usd",
    "OPERATORS", "OPERATORS_WITH_AI", "OPERATORS_CODE", "OPERATORS_CODE_AGENT", "OPERATORS_NO_SEARCH",
    "ACT_PROVIDERS", "STUDY_RUN", "CONFIG", "UP",
]

STUDY_RUN = "study-run.json"
CONFIG = "evolvekit.yaml"

OPERATORS = {"param_local": 0.5, "param_tpe": 0.25, "param_lhs": 0.15, "param_cross": 0.1}
"""Without a model: the mix that worked on the PyVRP benchmark."""

OPERATORS_WITH_AI = {"param_local": 0.35, "rewrite": 0.3, "param_tpe": 0.2, "param_lhs": 0.15}

OPERATORS_CODE = {"diff": 1.0}
"""A code study's search: a diff against the base (decision 37: no rewrite,
crossover or big step on a tree)."""

ACT_PROVIDERS = ("claude-cli",)
"""The providers whose coding agent a code study is given (`agent_patch`).
copilot-cli has an `act` too (`providers.base.supports_act`), but it is not
verified against a real Copilot CLI yet: its study searches with `diff` alone,
and `config._tree_rules` refuses `agent_patch` with it."""

OPERATORS_CODE_AGENT = {"agent_patch": 0.5, "diff": 0.5}
"""A code study whose strong model's provider can act (decision 37): the
coding agent beside the diff prompt. The plan's `rewrite` share is not
compiled: `rewrite` does not work on a tree."""

OPERATORS_NO_SEARCH = {"param_lhs": 1.0}
"""A bake-off, or a code study without a model: nothing is generated, and the
config still loads without models (decision D13). The operator is never invoked."""

UP = "../.."


def ai_cap_usd(study: Study) -> float:
    """The dollars a study's AI may spend in one run (`budget.max_usd` of the
    compiled run): `budget.ai.max_usd`, or 1.0 when the study gives none."""
    cap = float(study.budget.ai_max_usd or 0.0)
    return cap if cap > 0 else 1.0


def study_operators(study: Study, models: dict[str, Any] | None = None, *, bakeoff: bool = False) -> dict[str, float]:
    """The search's operator shares for `study`: what `compile_study` writes as
    `search.operators`, and what the plan counts the AI's time by. With AI, a
    code study whose strong model's provider can act gets the coding agent
    beside the diff prompt; one without `models` is planned as the diff prompt
    alone. A bake-off searches nothing."""
    code = getattr(study, "code", None)
    ai = study.budget.ai_enabled and not (code is not None and bakeoff)
    if code is not None:
        acts = ai and (models or {}).get("strong", {}).get("provider") in ACT_PROVIDERS
        return dict((OPERATORS_CODE_AGENT if acts else OPERATORS_CODE) if ai else OPERATORS_NO_SEARCH)
    return dict(OPERATORS_WITH_AI if ai else OPERATORS)
"""From a run's directory to its study's."""


def _quoted(path: str) -> str:
    """A path as one token of a stage command: forward slashes (a backslash is
    an escape to the command parser), quoted when it has a space."""
    posix = path.replace("\\", "/")
    return f'"{posix}"' if " " in posix else posix


def base_settings(study: Study, harness: Harness, root: Path | None) -> dict[str, Any]:
    """Every setting's value when it is not tuned: fixed, else the input
    settings file's, else the harness's default."""
    given = _given_settings(study, harness, root)
    return {name: given[name] if name in given else setting.parameter.coerce(setting.parameter.default)
            for name, setting in harness.settings.items()}


def runner_settings(study: Study, harness: Harness, root: Path | None) -> dict[str, Any]:
    """The settings `study-run.json` hands the runner (`settings.base`). A
    settings study hands every one (`base_settings`). A code study hands only
    the settings the study gives -- fixed in `vary.settings`, or in an input
    settings file -- and leaves the rest to the program: each combination,
    and the starting point, runs with its own code's defaults. A harness
    default passed as a flag would override a default the changed code sets,
    so the change would never run."""
    if study.code is None:
        return base_settings(study, harness, root)
    given = _given_settings(study, harness, root)
    return {name: given[name] for name in harness.settings if name in given}


def _given_settings(study: Study, harness: Harness, root: Path | None) -> dict[str, Any]:
    """The setting values the study itself gives: fixed in `vary.settings`,
    else in an input settings file. No harness default is among them."""
    from_file: dict[str, Any] = {}
    for name, spec in harness.inputs.items():
        if spec.provides != "settings" or name not in study.inputs or root is None:
            continue
        path = root / study.inputs[name]
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise HarnessError(f"inputs.{name}: {study.inputs[name]} is not a readable JSON object: {exc}") from None
        if not isinstance(loaded, dict):
            raise HarnessError(f"inputs.{name}: {study.inputs[name]} must hold a JSON object of setting values")
        for key, value in loaded.items():
            key = str(key).lstrip("-").replace("-", "_")
            if key in harness.settings:
                problem = harness.settings[key].parameter.problem_with(value)
                if problem:
                    raise HarnessError(f"inputs.{name}: {key}: {problem}")
                from_file[key] = harness.settings[key].parameter.coerce(value)
    given = {}
    for name, setting in harness.settings.items():
        choice = study.settings.get(name)
        if choice is not None and choice.mode == "fixed":
            given[name] = setting.parameter.coerce(choice.value)
        elif name in from_file:
            given[name] = from_file[name]
    return given


def _parameters(study: Study, harness: Harness, base: dict[str, Any]) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for name in study.tuned_settings():
        setting = harness.settings[name]
        parameter, choice = setting.parameter, study.settings[name]
        start = choice.start if choice.start is not None else base[name]
        spec: dict[str, Any] = {"type": parameter.type, "default": start}
        if parameter.numeric:
            low = parameter.low if choice.low is None else choice.low
            high = parameter.high if choice.high is None else choice.high
            if parameter.type == "int":
                low, high = int(math.ceil(low)), int(math.floor(high))  # type: ignore[arg-type]
            if not low <= start <= high:  # type: ignore[operator]
                raise HarnessError(
                    f"vary.settings.{name}: the starting value {start!r} lies outside the range "
                    f"[{low:g}, {high:g}]; widen the range or change the start"
                )
            spec.update({"low": low, "high": high})
            if parameter.log:
                spec["log"] = True
        elif parameter.type == "choice":
            spec["choices"] = list(parameter.choices)
        spec["help"] = setting.explain or setting.help or setting.label
        parameters[name] = spec
    for name, change in study.data.items():
        lever = harness.levers[change.lever]
        what = lever.columns.get(change.column, change.column) if change.column else lever.label
        rows = f" of the rows where {change.where}" if change.where else ""
        verb = {"scale": "a factor on", "set": "the value of", "add": "an amount added to"}[change.mode]
        parameters[name] = {
            "type": "float", "low": change.low, "high": change.high, "default": change.start,
            "help": (change.says or f"{verb} {what} in {lever.table}{rows}") + (f". {lever.explain}" if lever.explain else ""),
        }
    return parameters


def _description(study: Study, harness: Harness) -> str:
    lines = [harness.summary or harness.title, f"Study: {study.name}."]
    goal = []
    for index, level in enumerate(study.goal):
        says = resolve_kpi(study, harness, level.kpi)["says"]
        word = "highest" if level.direction == "higher" else "lowest"
        within = parse_within(level.equal_within)
        if within is None:
            goal.append(f"{word} {says}")
        else:
            value, relative = within
            goal.append(f"{word} {says} (counting as equal within {value * 100:g} %)" if relative
                        else f"{word} {says} (counting as equal within {value:g})")
    if goal:
        lines.append("Goal, in order of importance: " + "; then ".join(goal) + ".")
    if study.constraints:
        lines.append("Constraints: " + "; ".join(c.says for c in study.constraints) + ".")
    if study.guardrails:
        lines.append("Must hold on every case: " + "; ".join(
            f"{r.kpi} at most {r.max:g}" if r.max is not None else f"{r.kpi} at least {r.min:g}" for r in study.guardrails
        ) + ".")
    return "\n".join(lines)


def _screen_cases(cases: list[str], count: int, root: Path | None) -> list[str]:
    """`count` training cases spread over their sizes: the smallest, the
    largest and evenly between, so a screen sees the range the search will."""
    if root is not None:
        sized = sorted(cases, key=lambda c: ((root / c).stat().st_size if (root / c).is_file() else 0, c))
    else:
        sized = sorted(cases)
    if count >= len(sized):
        return sized
    if count == 1:
        return [sized[0]]
    picks = sorted({round(i * (len(sized) - 1) / (count - 1)) for i in range(count)})
    return [sized[i] for i in picks]


def compile_study(
    study: Study,
    harness: Harness,
    plan: Plan,
    *,
    root: Path | None = None,
    models: dict[str, Any] | None = None,
    bakeoff: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """`(evolvekit.yaml as a dict, study-run.json as a dict)` for a run of
    `study` in `<root>/runs/<id>/`. `models` is the engine's `models` section
    when the study lets a model take part (`budget.ai`). A code study
    (`vary.code`) compiles to `problem.tree`, its base as the study says it
    (`write_run` pins it); `bakeoff` compiles one that generates nothing."""
    if plan.blocked:
        raise HarnessError(f"plan: {plan.blocked}")
    code = study.code
    if code is not None and harness.code is None:
        raise HarnessError("vary.code: the harness has no code section, so this study cannot change code")
    base = base_settings(study, harness, root)
    parameters = _parameters(study, harness, base)
    if code is not None and parameters:
        raise HarnessError("vary: a study changes code or tunes settings, not both yet; fix the settings, "
                           "or vary code alone")
    single = len(study.goal) == 1
    time_limit = float(study.limits.time_per_case_s)

    def command(limit: float) -> str:
        runner = f"{UP}/harness/runner.py"
        values = "" if code is not None else " --values {params_json}"  # a tree has no tuned values
        tail = (
            f"solve --case {{instance}} --seed {{seed}}{values} --study {STUDY_RUN}"
            + (f" --time-limit {limit:g}" if harness.time_limit.accepts else "")
        )
        if harness.application.kind == "python":
            return f"{_quoted(study.application_path)} {runner} {tail}"
        app = "{app}" if code is not None else _quoted(study.application_path)  # the program inside each tree
        return f"{{python}} {runner} --app {app} {tail}"

    def timeout(limit: float) -> float:
        return round(limit * 1.5 + 30, 1) if harness.time_limit.accepts else limit

    cases = [f"{UP}/{case}" for case in study.training]
    normalize = "none"
    if single and study.kpis[study.goal[0].kpi].kind == "harness" and harness.kpis[study.goal[0].kpi].positive:
        normalize = "baseline"  # a positive KPI: every case counts equally, as a percentage of the start
    shared = {
        "kind": "command",
        "kpis_from": "stdout",
        "seeds": study.limits.runs_per_case,
        "workers": plan.workers,
        "retries": study.limits.retries,
        "normalize": normalize,
    }
    if plan.pin_cpus:
        shared["pin_cpus"] = list(plan.pin_cpus)
    stages: list[dict[str, Any]] = [{"id": "static", "kind": "builtin-static"}]
    built = code is not None and harness.code is not None and bool(harness.code.build)
    if built:
        stages.append({"id": "build", "kind": "builtin-build"})  # second, after the static stage (D8)
    if plan.screening and not built:  # with a build, one candidate at a time: no screen (D9)
        screened = _screen_cases(study.training, plan.screen_cases, root)
        stages.append({
            "id": "screen", **shared,
            "command": command(plan.screen_time_limit_s),
            "instances": [f"{UP}/{case}" for case in screened],
            "timeout": timeout(plan.screen_time_limit_s),
            "promote": {"top_k_per_generation": 2},
        })
    stages.append({"id": "full", **shared, "command": command(time_limit), "instances": cases,
                   "timeout": timeout(time_limit)})

    if single:
        level = study.goal[0]
        score: dict[str, Any] = {"objective": level.kpi, "direction": "maximize" if level.direction == "higher" else "minimize"}
    else:
        score = {"levels": []}
        for level in study.goal:
            entry: dict[str, Any] = {"kpi": level.kpi, "direction": "maximize" if level.direction == "higher" else "minimize"}
            within = parse_within(level.equal_within)
            if within is not None:
                entry["tolerance"], entry["relative"] = within[0], within[1]
            score["levels"].append(entry)
    evaluate: dict[str, Any] = {"stages": stages, "score": score}
    if study.guardrails:
        evaluate["gates"] = [{"kpi": "guardrail_violation", "max": 0}]

    ai = study.budget.ai_enabled and not (code is not None and bakeoff)
    if ai and not models:
        raise HarnessError("budget.ai: AI search help is on, and no model is set up (Settings)")
    problem: dict[str, Any]
    if code is not None:
        problem = {"description": _description(study, harness) + "\nWhat to improve: " + code.brief,
                   "tree": _tree(study, harness)}
    else:
        problem = {"description": _description(study, harness), "parameters": parameters}
    operators = study_operators(study, models, bakeoff=bakeoff)
    expressions = [{"expr": c.expr, "says": c.says} for c in study.constraints if c.expr]
    if expressions:
        problem["parameter_constraints"] = expressions
    novelty: dict[str, Any] = {"behavioural": "off" if harness.time_limit.accepts else "auto"}
    if code is not None:
        novelty["near"] = {"method": "off"}  # it compares code structure, and a candidate is a diff
    config: dict[str, Any] = {
        "problem": problem,
        "evaluate": evaluate,
        "search": {
            "operators": dict(operators),
            "children_per_generation": plan.children,
            "generations": plan.generations,
            "seed": 1,
            "big_step_every": 0,
            "scratchpad_every": 0,
            "novelty": novelty,
        },
        "budget": {
            "max_hours": round(plan.max_hours, 4),
            "max_usd": ai_cap_usd(study) if ai else 1.0,
            # Every child that reaches the final stage counts: the default (20
            # a day) would stop a two-hour study in its second round.
            "max_full_evals_per_day": plan.generations * plan.children + 10,
        },
        "stop": {"patience": plan.generations},
    }
    if ai:
        config["models"] = models
    return config, runner_input(study, harness, runner_settings(study, harness, root))


def _tree(study: Study, harness: Harness) -> dict[str, Any]:
    """`problem.tree` of a code study: the study's source and base, the files it
    may change (its own subset, else the harness's), and the harness's build
    and program. A python harness builds into the study's application Python,
    which `{python}` stands for (`problem.tree.python`)."""
    code, spec = study.code, harness.code
    assert code is not None and spec is not None
    tree: dict[str, Any] = {"repo": code.repo, "base": code.from_ref, "open": list(code.open or spec.open)}
    if spec.build:
        tree.update({"build": spec.build, "build_timeout_s": spec.build_timeout_s})
        if harness.application.kind == "python" and study.application_path:
            tree["python"] = study.application_path
    if spec.app:
        tree["app"] = spec.app
    return tree


def runner_input(
    study: Study,
    harness: Harness,
    base: dict[str, Any],
    *,
    kpis: dict[str, dict[str, Any]] | None = None,
    guardrails: bool = True,
    up: str = UP,
) -> dict[str, Any]:
    """`study-run.json`: what the runner needs besides the tuned values.
    `kpis` replaces the study's own (the preview measures every KPI the
    harness offers, so the app can show each one's value before it is chosen).
    `up` is the way from the folder the runner runs in to the study's."""
    return {
        "study_run": 1,
        "harness": {"id": harness.id, "version": harness.version},
        "name": study.name,
        "time_limit_s": float(study.limits.time_per_case_s),
        "settings": {"base": base, "tuned": study.tuned_settings()},
        "levers": {
            name: {
                "lever": change.lever, "table": harness.levers[change.lever].table,
                "column": change.column or None, "where": change.where, "mode": change.mode,
                "code": harness.levers[change.lever].code,
                "integer": bool(change.column) and harness.tables[harness.levers[change.lever].table].columns[change.column].type == "int",
            }
            for name, change in study.data.items()
        },
        "constraints": [c.to_yaml() for c in study.constraints],
        "kpis": kpis if kpis is not None else {
            name: runner_kpi(resolve_kpi(study, harness, name)) for name in study.kpis
        },
        "guardrails": [
            {"kpi": r.kpi, **({"max": r.max} if r.max is not None else {}), **({"min": r.min} if r.min is not None else {})}
            for r in study.guardrails
        ] if guardrails else [],
        "inputs": {name: f"{up}/{path}" for name, path in study.inputs.items()},
        "tables": harness.declared(),
    }


def runner_kpi(resolved: dict[str, Any]) -> dict[str, Any]:
    """A resolved KPI (`study.resolve_kpi`) as the runner reads it."""
    kpi: dict[str, Any] = {"direction": resolved["direction"]}
    for key in ("sql", "params", "measure", "weighted"):
        if resolved.get(key):
            kpi[key] = resolved[key]
    return kpi


def write_run(
    study: Study,
    harness: Harness,
    plan: Plan,
    root: Path,
    run_id: str,
    *,
    models: dict[str, Any] | None = None,
    bakeoff: bool = False,
) -> Path:
    """Compile `study` into `<root>/runs/<run_id>/` and return that directory.
    The run keeps a copy of `study.yaml` as it was when it started, so what
    the run says about itself (its cases, goal, choices) stays true when the
    study is edited afterwards. A code study's base is pinned here, once, to a
    commit (a ref as it is now; `working` as a snapshot of the working tree),
    so the run means the same tree whenever its config is loaded again."""
    if study.code is not None:  # the bake-off writes runs without study_problems' search check
        repo = study.code.repo
        if not repo:
            raise HarnessError("vary.code.repo: give the full path to the source; the study names none")
        if not (PurePosixPath(repo).is_absolute() or PureWindowsPath(repo).is_absolute()):
            raise HarnessError(f"vary.code.repo: give the full path to the source; {repo!r} is relative")
    config, study_run = compile_study(study, harness, plan, root=root, models=models, bakeoff=bakeoff)
    spec = config["problem"].get("tree")
    if spec is not None:
        from evolvekit import tree  # only a code study needs git

        try:
            located = tree.Repo(spec["repo"])
        except tree.TreeError as exc:
            raise HarnessError(f"vary.code.repo: {exc}") from None
        try:
            spec["base"] = tree.resolve_base(located, spec["base"])
        except tree.TreeError as exc:
            raise HarnessError(f"vary.code.from: {exc}") from None
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if (root / "study.yaml").is_file():
        shutil.copyfile(root / "study.yaml", run_dir / "study.yaml")
    (run_dir / CONFIG).write_text(
        "# Compiled from ../../study.yaml by evolvekit. Runnable as it is:\n"
        f"#   python -m evolvekit run --config {CONFIG} --run-dir .\n"
        + yaml.safe_dump(config, sort_keys=False, allow_unicode=True, width=110),
        encoding="utf-8", newline="\n",
    )
    (run_dir / STUDY_RUN).write_text(json.dumps(study_run, indent=2) + "\n", encoding="utf-8", newline="\n")
    (run_dir / "plan.json").write_text(json.dumps(plan.to_json(), indent=2) + "\n", encoding="utf-8", newline="\n")
    return run_dir
