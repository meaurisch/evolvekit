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
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import yaml

from evolvekit.harness import HarnessError
from evolvekit.harness.manifest import Harness
from evolvekit.harness.plan import Plan
from evolvekit.harness.study import Study, parse_within, resolve_kpi

__all__ = ["compile_study", "write_run", "OPERATORS", "OPERATORS_WITH_AI", "STUDY_RUN", "CONFIG"]

STUDY_RUN = "study-run.json"
CONFIG = "evolvekit.yaml"

OPERATORS = {"param_local": 0.5, "param_tpe": 0.25, "param_lhs": 0.15, "param_cross": 0.1}
"""Without a model: the mix that worked on the PyVRP benchmark."""

OPERATORS_WITH_AI = {"param_local": 0.35, "rewrite": 0.3, "param_tpe": 0.2, "param_lhs": 0.15}

UP = "../.."
"""From a run's directory to its study's."""


def _quoted(path: str) -> str:
    """A path as one token of a stage command: forward slashes (a backslash is
    an escape to the command parser), quoted when it has a space."""
    posix = path.replace("\\", "/")
    return f'"{posix}"' if " " in posix else posix


def _base_settings(study: Study, harness: Harness, root: Path | None) -> dict[str, Any]:
    """Every setting's value when it is not tuned: fixed, else the input
    settings file's, else the harness's default."""
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
    base = {}
    for name, setting in harness.settings.items():
        choice = study.settings.get(name)
        if choice is not None and choice.mode == "fixed":
            base[name] = setting.parameter.coerce(choice.value)
        else:
            base[name] = from_file.get(name, setting.parameter.coerce(setting.parameter.default))
    return base


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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """`(evolvekit.yaml as a dict, study-run.json as a dict)` for a run of
    `study` in `<root>/runs/<id>/`. `models` is the engine's `models` section
    when the study lets a model take part (`budget.ai`)."""
    if plan.blocked:
        raise HarnessError(f"plan: {plan.blocked}")
    base = _base_settings(study, harness, root)
    parameters = _parameters(study, harness, base)
    single = len(study.goal) == 1
    time_limit = float(study.limits.time_per_case_s)

    def command(limit: float) -> str:
        runner = f"{UP}/harness/runner.py"
        tail = (
            f"solve --case {{instance}} --seed {{seed}} --values {{params_json}} --study {STUDY_RUN}"
            + (f" --time-limit {limit:g}" if harness.time_limit.accepts else "")
        )
        if harness.application.kind == "python":
            return f"{_quoted(study.application_path)} {runner} {tail}"
        return f"{{python}} {runner} --app {_quoted(study.application_path)} {tail}"

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
    if plan.screening:
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

    ai = study.budget.ai_enabled
    if ai and not models:
        raise HarnessError("budget.ai: AI search help is on, and no model is set up (Settings)")
    problem: dict[str, Any] = {"description": _description(study, harness), "parameters": parameters}
    expressions = [{"expr": c.expr, "says": c.says} for c in study.constraints if c.expr]
    if expressions:
        problem["parameter_constraints"] = expressions
    config: dict[str, Any] = {
        "problem": problem,
        "evaluate": evaluate,
        "search": {
            "operators": dict(OPERATORS_WITH_AI if ai else OPERATORS),
            "children_per_generation": plan.children,
            "generations": plan.generations,
            "seed": 1,
            "big_step_every": 0,
            "scratchpad_every": 0,
            "novelty": {"behavioural": "off" if harness.time_limit.accepts else "auto"},
        },
        "budget": {
            "max_hours": round(plan.max_hours, 4),
            "max_usd": float(study.budget.ai_max_usd) if ai and study.budget.ai_max_usd > 0 else 1.0,
            # Every child that reaches the final stage counts: the default (20
            # a day) would stop a two-hour study in its second round.
            "max_full_evals_per_day": plan.generations * plan.children + 10,
        },
        "stop": {"patience": plan.generations},
    }
    if ai:
        config["models"] = models
    study_run = {
        "study_run": 1,
        "harness": {"id": harness.id, "version": harness.version},
        "name": study.name,
        "time_limit_s": time_limit,
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
        "kpis": {name: _runner_kpi(resolve_kpi(study, harness, name)) for name in study.kpis},
        "guardrails": [
            {"kpi": r.kpi, **({"max": r.max} if r.max is not None else {}), **({"min": r.min} if r.min is not None else {})}
            for r in study.guardrails
        ],
        "inputs": {name: f"{UP}/{path}" for name, path in study.inputs.items()},
        "tables": harness.declared(),
    }
    return config, study_run


def _runner_kpi(resolved: dict[str, Any]) -> dict[str, Any]:
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
) -> Path:
    """Compile `study` into `<root>/runs/<run_id>/` and return that directory."""
    config, study_run = compile_study(study, harness, plan, root=root, models=models)
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / CONFIG).write_text(
        "# Compiled from ../../study.yaml by evolvekit. Runnable as it is:\n"
        f"#   python -m evolvekit run --config {CONFIG} --run-dir .\n"
        + yaml.safe_dump(config, sort_keys=False, allow_unicode=True, width=110),
        encoding="utf-8", newline="\n",
    )
    (run_dir / STUDY_RUN).write_text(json.dumps(study_run, indent=2) + "\n", encoding="utf-8", newline="\n")
    (run_dir / "plan.json").write_text(json.dumps(plan.to_json(), indent=2) + "\n", encoding="utf-8", newline="\n")
    return run_dir
