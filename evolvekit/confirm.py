"""Is the improvement real? A paired comparison on seeds the search never saw.

A search selects on noise. Whatever it reports as its best was, among other
things, lucky on the seeds it was evaluated on, so the run's own "improvement"
is optimistic by construction -- the status document says as much next to the
number. `confirm` is the measurement that is not:

    python -m evolvekit confirm --config tuning.yaml --run-dir runs/x \\
        --seeds 1001,1002,1003 [--candidates best] [--instances "fresh/*.vrp"]

It takes the baseline (the run's seed candidate) and one or more candidates of
the run, and runs the final stage's command for every one of them on every
instance and every given seed -- under the stage's own `workers`, `pin_cpus`,
`timeout` and `retries`, and **interleaved**: the configurations' runs for one
(instance, seed) are queued next to each other, so that whatever the machine
does over the hours (a laptop throttles) happens to all of them alike.

The unit of the statistics is the instance. Per instance, the log ratio to the
baseline -- `log base - log mine` when lower is better, the other way round
when higher is -- is averaged over the seeds; over instances that gives a mean
with a 95 % confidence interval (paired, Student's t) and an exact Wilcoxon
signed-rank test. Every percentage is of the baseline's value, from the log
quantity, so halving and doubling are one step either way and the noisy
baseline run is no denominator. A confidence interval that includes zero is
reported as what it is.

Two searches of one problem -- another operator mix, a model among the
operators, last month's run -- are compared the same way: `ID@OTHER_RUN_DIR`
names a candidate of another run, and `--against` names what the candidates
are compared with instead of the seed:

    python -m evolvekit confirm --run-dir runs/b --seeds 2001,2002,2003 \\
        --candidates g011-c0085 --against g012-c0096@runs/a

Everything lands in `<run_dir>/confirm/<label>/`: every run's result
(`results.json`), the comparison (`comparison.json`, `comparison.md`), the logs
of every run, and an event log and heartbeat of its own -- so `status` and the
dashboard can watch a five-hour confirmation like any other run. Finished runs
are cached there, so an interrupted confirmation picks up where it stopped.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import fmean
from typing import TYPE_CHECKING, Any, Callable, Sequence

from evolvekit.candidate import SEED_OPERATOR, Candidate, splice_block
from evolvekit.config import Config, ConfigError, StageConfig
from evolvekit.evaluate.cache import EvalCache
from evolvekit.evaluate.fanout import Job, run_instance_stage
from evolvekit.evaluate.stages import Configuration, on_tree, restore_build, run_build_stage, run_static_stage
from evolvekit.events import EventLog, Heartbeat
from evolvekit.leaderboard import rank
from evolvekit.ledger import read_jsonl
from evolvekit.lock import run_lock
from evolvekit.stats import _betainc, _t95, paired_summary, t_cdf, wilcoxon_signed_rank  # noqa: F401  (re-exported: importers below)
from evolvekit.stats import log_gain, margin_log, pct_from_log, posterior_mean_t

if TYPE_CHECKING:  # `evolvekit.tree` is imported only for a tree problem
    from evolvekit.tree import Tree

__all__ = [
    "confirm", "Comparison", "ConfirmStopped", "paired_summary", "wilcoxon_signed_rank", "render_markdown",
    "t_cdf", "decide", "decision_of", "verdict_summary", "identical_runs", "USE_CHANCE", "USE_MARGIN_PCT",
    "USE_MARGIN_CHANCE", "WORSE_CHANCE", "RULE_WORDS",
]

BASELINE = "baseline"

USE_CHANCE = 0.95
"""`decide` says "use" from this posterior chance of a gain on ..."""

USE_MARGIN_PCT = 1.0
"""... and only when the chance of losing more than this many % ..."""

USE_MARGIN_CHANCE = 0.95
"""... is under 1 - this (the chance of a gain above `-USE_MARGIN_PCT` is at least this)."""

WORSE_CHANCE = 0.1
"""`decide` says "worse" up to this chance of a gain: P(gain < 0) >= 0.90."""

_LEGACY_USE_CHANCE = 0.9
"""The rule a comparison written before the posterior was decided by: "use"
from a one-sided t chance of 0.9 with the 95 % lower bound within 1 %."""

RULE_WORDS = (
    "Use when the chance of a gain is at least 19 in 20 "
    "and the chance of losing more than 1 % is under 1 in 20."
)
"""The rule in one sentence, as the page states it."""

PRIOR_FROM_RUNS = "from the search's repeated runs"
PRIOR_FROM_DATA = "from the data"
FREQUENTIST_LABEL = "frequentist 95 % confidence interval and Wilcoxon test, for engineers"


def decide(summary: dict[str, Any]) -> str:
    """One fixed rule on a `verdict_summary`, for a planner who has to choose:
    "use" when the posterior chance of a gain is at least `USE_CHANCE` and the
    chance of a gain above -`USE_MARGIN_PCT` % is at least `USE_MARGIN_CHANCE`;
    "worse" when the chance of a gain is `WORSE_CHANCE` or less; "keep"
    (today's settings) in between. "single" with one instance, where no
    chance can be given, and "none" with nothing to compare. `RULE_WORDS`
    says the same in a sentence.

    A summary without `chance_margin` was written before the posterior: it
    is decided by the rule it was decided by then (a chance of 0.9 and the
    95 % lower bound within 1 %), so a stored comparison keeps its answer."""
    n = int(summary.get("n") or 0)
    if n == 0:
        return "none"
    if n == 1:
        return "single"
    chance = float(summary["chance"])
    if chance <= WORSE_CHANCE:
        return "worse"
    if "chance_margin" not in summary:
        return "use" if chance >= _LEGACY_USE_CHANCE and float(summary["worst"]) >= -USE_MARGIN_PCT else "keep"
    if chance >= USE_CHANCE and float(summary["chance_margin"]) >= USE_MARGIN_CHANCE:
        return "use"
    return "keep"


def decision_of(result: dict[str, Any]) -> str:
    """What one candidate's comparison says: `keep` when every run gave
    exactly the starting point's result (its change was not exercised, so
    there is nothing to decide on); by the levels when the study scores by
    levels; else `decide` on its `verdict_summary`."""
    if result.get("identical"):
        return "keep"
    if "levels" in result:
        return "use" if result["levels"]["confirmed"] else "keep"
    return decide(result["summary"])


def runs_can_help(result: dict[str, Any]) -> bool:
    """Whether more runs per case could still open the answer. A1.3 splits
    the spread into its within- and between-case shares; until then, yes."""
    return True


def _prior_of(seed_row: dict[str, Any], objective: str) -> tuple[float | None, str]:
    """The prior scale of the per-case log gains and its label: the starting
    point's run-to-run coefficient of variation of the objective when the
    search measured one (to first order, the sd of the log objective), else
    None: `verdict_summary` then takes the spread of the gains themselves."""
    cv = (seed_row.get("kpi_cv") or {}).get(objective) if isinstance(seed_row.get("kpi_cv"), dict) else None
    if isinstance(cv, (int, float)) and math.isfinite(cv) and cv > 0:
        return float(cv), PRIOR_FROM_RUNS
    return None, PRIOR_FROM_DATA


def verdict_summary(d: Sequence[float], *, prior_scale: float | None, direction: str,
                    prior: str | None = None) -> dict[str, Any]:
    """The one place that decides: per-case log gains `d` to the numbers a
    person is shown and the decision they are read by.

    The posterior of the true mean gain (`stats.posterior_mean_t`) with the
    given prior scale, or -- `prior_scale=None` -- the sample sd of `d`
    itself, labelled "from the data". `mean` is the posterior mean, `ci95`
    its 95 % credible interval, `chance` P(gain > 0), `chance_margin`
    P(gain > the 1 % margin) and `worst` the 5 % quantile, the one-sided
    bound the rule reads -- all in percent of the starting point's value
    (`pct_from_log`); `log` and `posterior` keep the log-scale numbers, and
    `paired_mean` the plain mean of the cases. The frequentist interval and
    Wilcoxon test are under `frequentist`, for engineers. `decision` is
    `decide` of this summary and `verdict` says it in a sentence."""
    d = [float(x) for x in d]
    n = len(d)
    wins, losses = sum(1 for x in d if x > 0), sum(1 for x in d if x < 0)
    if n == 0:
        out: dict[str, Any] = {"n": 0, "mean": None, "ci95": None, "chance": None, "chance_margin": None,
                               "worst": None, "wins": 0, "losses": 0, "verdict": "nothing to compare"}
        out["decision"] = decide(out)
        return out
    if n == 1:
        out = {"n": 1, "mean": pct_from_log(d[0], direction), "paired_mean": pct_from_log(d[0], direction),
               "ci95": None, "chance": None, "chance_margin": None, "worst": None, "wins": wins, "losses": losses,
               "log": {"mean": d[0], "sd": None, "ci95": None}, "verdict": "one instance: no chance can be given"}
        out["decision"] = decide(out)
        return out
    sd = statistics.stdev(d)
    label = PRIOR_FROM_DATA if prior_scale is None else (prior or PRIOR_FROM_RUNS)
    scale = sd if prior_scale is None else float(prior_scale)
    post = posterior_mean_t(d, scale)
    low, high = post.interval95()
    worst_log = post.quantile_low(0.05)
    chance = post.p_above(0.0)
    chance_margin = post.p_above(margin_log(USE_MARGIN_PCT, direction))
    dbar = fmean(d)
    out = {
        "n": n,
        "mean": pct_from_log(post.mu, direction),
        "paired_mean": pct_from_log(dbar, direction),
        "ci95": [pct_from_log(low, direction), pct_from_log(high, direction)],
        "chance": chance,
        "chance_margin": chance_margin,
        "worst": pct_from_log(worst_log, direction),
        "wins": wins, "losses": losses,
        "log": {"mean": post.mu, "paired_mean": dbar, "sd": sd, "ci95": [low, high], "worst": worst_log},
        "prior_scale": scale,
        "prior": label,
        "posterior": {"mu": post.mu, "scale": post.scale, "df": post.df},
        "frequentist": {**_pct_summary(paired_summary(d), direction), "label": FREQUENTIST_LABEL},
    }
    decision = decide(out)
    lose = f"losing more than {USE_MARGIN_PCT:g} % of {1 - chance_margin:.2f}"
    if decision == "use":
        verdict = f"probably better: a chance of a gain of {chance:.2f}, and of {lose} (prior {label})"
    elif decision == "worse":
        verdict = f"probably WORSE: a chance of a gain of {chance:.2f} (prior {label})"
    else:
        verdict = f"not proven: a chance of a gain of {chance:.2f}, and of {lose} (prior {label})"
    out["decision"], out["verdict"] = decision, verdict
    return out


# --------------------------------------------------------------------------
# the comparison
# --------------------------------------------------------------------------


@dataclass
class Comparison:
    """What `confirm` measured, and what it makes of it."""

    label: str
    objective: str
    direction: str
    stage: str
    seeds: list[int]
    instances: list[str]
    baseline_id: str
    candidates: list[str]
    runs: list[dict[str, Any]] = field(default_factory=list)
    """One entry per evaluator run: configuration, instance, seed, ok, kpis."""
    per_candidate: dict[str, dict[str, Any]] = field(default_factory=dict)
    levels: list[dict[str, Any]] = field(default_factory=list)
    """`evaluate.score.levels`, described: the comparison is then made level
    by level as well (`_analyse_levels`). Empty for a single objective."""
    prior_scale: float | None = None
    """The prior scale of the per-case log gains (`_prior_of`); None: from the data."""
    prior: str = PRIOR_FROM_DATA
    """Where the prior scale came from, in words."""
    looks: int = 1
    """How many times this comparison's answer has been read (written): a
    check extended or run again under one label reads it again."""

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label, "objective": self.objective, "direction": self.direction,
            "stage": self.stage, "seeds": self.seeds, "instances": self.instances,
            "baseline_id": self.baseline_id, "candidates": self.candidates,
            "prior": self.prior, "prior_scale": self.prior_scale, "looks": self.looks,
            "levels": self.levels, "per_candidate": self.per_candidate,
        }


def _find(rows: list[dict[str, Any]], spec: str, option: str) -> dict[str, Any]:
    """One candidate by id: `ID` is of this run, `ID@RUN_DIR` of another run of
    the same problem -- the winner of an earlier search, of a different operator
    mix, of last month. It is named `ID@<that directory's name>` from here on,
    because two runs number their candidates alike -- with parent directories
    added where two directories share a name (`_disambiguate`)."""
    cid, _, other = spec.partition("@")
    if not other:
        row = next((r for r in rows if str(r.get("id")) == cid), None)
        if row is None:
            raise ValueError(f"{option}: the run directory holds no candidate {cid!r}")
        return row
    other_dir = Path(other)
    if not (other_dir / "runs.jsonl").is_file():
        raise ValueError(f"{option}: {other_dir} holds no run (there is no runs.jsonl in it)")
    row = next((r for r in read_jsonl(other_dir / "runs.jsonl") if str(r.get("id")) == cid), None)
    if row is None:
        raise ValueError(f"{option}: {other_dir} holds no candidate {cid!r}")
    return {**row, "id": f"{cid}@{other_dir.resolve().name}", "_id": cid, "_run_dir": str(other_dir.resolve())}


def _disambiguate(rows: Sequence[dict[str, Any]]) -> None:
    """Rename candidates of other runs whose directories share a name.

    `runs/a/run` and `runs/b/run` both give `ID@run`: two configurations under
    one name, which the comparison then refuses as "compared with itself" or
    "compared twice". Each clashing name grows by parent directories --
    `ID@a/run`, `ID@b/run` -- until the directories are told apart. The same
    candidate of the same directory named twice keeps one name, and is still
    refused as the duplicate it is."""
    others = [row for row in rows if row.get("_run_dir")]
    depth = {id(row): 1 for row in others}

    def name(row: dict[str, Any]) -> str:
        parts = Path(row["_run_dir"]).parts
        return f"{row['_id']}@{Path(*parts[-depth[id(row)]:]).as_posix()}"

    while True:
        by_name: dict[str, list[dict[str, Any]]] = {}
        for row in others:
            by_name.setdefault(name(row), []).append(row)
        clashing = [
            row for group in by_name.values() if len({r["_run_dir"] for r in group}) > 1 for row in group
            if depth[id(row)] < len(Path(row["_run_dir"]).parts)
        ]
        if not clashing:
            break
        for row in clashing:
            depth[id(row)] += 1
    for row in others:
        row["id"] = name(row)


def _select(rows: list[dict[str, Any]], wanted: str) -> list[dict[str, Any]]:
    """`best`, `top:N`, or a comma-separated list of candidate ids (`_find`)."""
    if wanted == "best" or wanted.startswith("top:"):
        count = 1 if wanted == "best" else int(wanted.split(":", 1)[1])
        ranked = [r for r in rank(rows, len(rows)) if r.get("operator") != SEED_OPERATOR]
        if not ranked:
            raise ValueError("the run has no fully evaluated candidate besides its seed: nothing to confirm")
        return ranked[:count]
    return [_find(rows, spec.strip(), "--candidates") for spec in wanted.split(",") if spec.strip()]


def _looks_before(out_dir: Path) -> int:
    """How many times the comparison under this label was read before: its
    `comparison.json`'s `looks` (1 for one written before it said so), 0
    with no file. A stopped comparison writes nothing, so it is no look."""
    path = out_dir / "comparison.json"
    if not path.is_file():
        return 0
    try:
        looks = json.loads(path.read_text(encoding="utf-8")).get("looks", 1)
    except (OSError, ValueError, AttributeError):
        return 1
    return int(looks) if isinstance(looks, int) and looks >= 1 else 1


class ConfirmStopped(RuntimeError):
    """The comparison was stopped before it finished (`stop` was set). Its
    finished runs are cached: running the same confirmation again resumes it."""


def confirm(
    config: Config,
    run_dir: str | Path,
    *,
    seeds: Sequence[int],
    candidates: str = "best",
    instances: Sequence[str] | None = None,
    label: str = "confirm",
    against: str | None = None,
    log: Callable[[str], None] = print,
    stop: threading.Event | None = None,
) -> Comparison:
    """Run the comparison described in the module docstring; returns it.

    `stop`, once set, stops the runs in flight and starts no other, and the
    comparison ends with `ConfirmStopped` instead of a verdict on half the
    pairs."""
    run_dir = Path(run_dir)
    rows = list(read_jsonl(run_dir / "runs.jsonl"))
    if against is not None:
        seed_row = _find(rows, against.strip(), "--against")
    else:
        # The last seed row: a run that aborted on its seed evaluates it again
        # when it is run again, and the earlier row is the failed attempt.
        seed_row = next((r for r in reversed(rows) if r.get("operator") == SEED_OPERATOR), None)
    if seed_row is None:
        raise ValueError(f"{run_dir} holds no seed candidate: there is no baseline to compare with")
    if not seeds:
        raise ValueError("--seeds: name at least one seed the search never used")
    twice = sorted({seed for seed in seeds if list(seeds).count(seed) > 1})
    if twice:
        # The same seed twice is the same run twice: a pair counted double,
        # and an interval narrower than the evidence.
        raise ValueError(f"--seeds: {', '.join(map(str, twice))} is given twice")
    stage = config.final_stage
    if stage.kind != "command" or not stage.fans_out:
        raise ConfigError(
            f"evaluate.stages: `confirm` compares instance by instance, so the final stage "
            f"({stage.id!r}) has to list `instances` and run once per instance"
        )
    if "{seed}" not in stage.command:
        raise ConfigError(f"stage {stage.id!r}: the command has no {{seed}} placeholder, so every seed would be the same run")
    searched = set(range(stage.seeds))
    reused = sorted(searched & set(seeds))
    if reused and instances is None:
        log(f"warning: seed(s) {reused} are the ones the search selected on; a confirmation on them is not one")
    if instances is not None:
        stage = _with_instances(config, stage, instances)

    chosen = _select(rows, candidates)
    _disambiguate([seed_row, *chosen])
    names = [str(r["id"]) for r in chosen]
    twice = sorted({n for n in names if names.count(n) > 1})
    if twice:
        raise ValueError(f"--candidates: {twice} would be compared twice under one name")
    if any(str(r["id"]) == str(seed_row["id"]) for r in chosen):
        raise ValueError(f"--against: {seed_row['id']} would be compared with itself")
    out_dir = run_dir / "confirm" / label
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    events = EventLog(out_dir)
    heartbeat = Heartbeat(out_dir, session=events.session)
    prior_scale, prior = _prior_of(seed_row, config.evaluate.score.objective)
    comparison = Comparison(
        label=label, objective=config.evaluate.score.objective, direction=config.evaluate.score.direction,
        stage=stage.id, seeds=list(seeds), instances=list(stage.instance_names()),
        baseline_id=str(seed_row["id"]), candidates=[str(r["id"]) for r in chosen],
        levels=[level.describe() for level in config.evaluate.score.levels],
        prior_scale=prior_scale, prior=prior, looks=_looks_before(out_dir) + 1,
    )

    def observer_for(name: str) -> Callable[..., None]:
        def observe(type: str, **fields: Any) -> None:
            events.emit(type, candidate_id=name, stage=stage.id, private=False, **fields)
            if type == "eval_finished":
                comparison.runs.append({
                    "configuration": name, "instance": fields.get("instance"), "seed": fields.get("seed"),
                    "ok": bool(fields.get("ok")), "cached": bool(fields.get("cached")),
                    "kpis": fields.get("kpis") or {}, "failure": fields.get("failure"),
                })
        return observe

    # The directory's own lock: `status` reads "alive" off it, and two
    # confirmations under one label would overwrite each other's results (and,
    # for a tree problem, each other's trees).
    with run_lock(out_dir):
        try:
            jobs: list[Job] = []
            made: list[Tree | None] = []
            for index, (name, row) in enumerate([(BASELINE, seed_row), *[(str(r["id"]), r) for r in chosen]]):
                # A name like `ID@a/run` is for people; files are named by one that is
                # safe as a file name (and unique, whatever it folded together).
                safe = re.sub(r"[^A-Za-z0-9@._-]+", "_", name)
                if any(job.candidate_id == safe for job in jobs):
                    safe = f"{safe}.{index}"
                path, configuration, tree_made = _materialise(config, row, work, safe)
                # On a tree, `{app}` is the program inside it and the cache keys it by its commit.
                jobs.append(Job(safe, path, configuration=configuration, observer=observer_for(name),
                                **on_tree(config.problem, tree_made)))
                made.append(tree_made)
            _compare(config, comparison, stage, jobs, made, work=work, seeds=seeds, label=label, log=log,
                     events=events, heartbeat=heartbeat, stop=stop)
        finally:
            if config.problem.tree is not None:
                _put_trees_back(config, work, log)
    return comparison


def _compare(
    config: Config, comparison: Comparison, stage: StageConfig, jobs: list[Job], made: list[Tree | None], *,
    work: Path, seeds: Sequence[int], label: str, log: Callable[[str], None], events: EventLog,
    heartbeat: Heartbeat, stop: threading.Event | None,
) -> None:
    """Every configuration's runs, then the statistics and the files."""
    out_dir = work.parent
    planned = len(jobs) * len(stage.instances) * len(seeds)
    log(
        f"confirm '{label}': {len(jobs)} configuration(s) x {len(stage.instances)} instance(s) x "
        f"{len(seeds)} seed(s) = {planned} run(s) of stage {stage.id}, {stage.workers} at a time"
    )
    events.emit(
        "run_started", objective=comparison.objective, direction=comparison.direction, kind="confirm",
        label=label, seeds=list(seeds), baseline_id=comparison.baseline_id, candidates=comparison.candidates,
        stages=[{"id": stage.id, "kind": stage.kind, "timeout_s": stage.timeout, "seeds": len(seeds),
                 "final": True, "instances": list(stage.instance_names()), "normalize": None,
                 "workers": stage.workers, "pin_cpus": list(stage.pin_cpus), "retries": stage.retries}],
    )
    heartbeat.start(phase="evaluating")
    try:
        events.emit("stage_started", stage=stage.id, private=False, candidates=[BASELINE, *comparison.candidates],
                    runs_per_candidate=len(stage.instances) * len(seeds), workers=stage.workers)

        def run(batch: list[Job]) -> None:
            run_instance_stage(
                batch, stage, out_dir=work / "stage_out", cwd=config.base_dir,
                required_kpis=config.evaluate.required_kpis, cache=EvalCache(work / "cache"),
                seeds=list(seeds), keep_going=True, stop=stop,
            )

        tree = config.problem.tree
        if tree is not None and tree.build:
            # One shared interpreter holds one build: each tree is built right
            # before its own runs, the starting point first.
            build = _build_stage(config)
            names = [BASELINE, *comparison.candidates]
            for job, tree_made, name in zip(jobs, made, names):
                if stop is not None and stop.is_set():
                    break
                assert tree_made is not None
                outcome = run_build_stage(tree_made, build, config.problem, cancel=stop)
                if outcome.abandoned:
                    break
                if not outcome.ok:
                    if name == BASELINE:
                        raise ValueError(f"the starting point no longer builds: {outcome.failure}")
                    log(f"{name}: {outcome.failure}; its runs count as failed")
                    comparison.runs.extend(
                        {"configuration": name, "instance": instance, "seed": seed, "ok": False, "cached": False,
                         "kpis": {}, "failure": outcome.failure}
                        for instance in stage.instance_names() for seed in seeds
                    )
                    continue
                run([job])
        else:
            run(jobs)
        events.emit("stage_finished", stage=stage.id, private=False, candidates=len(jobs),
                    failed=sum(1 for r in comparison.runs if not r["ok"]), duration_s=None)
        if stop is not None and stop.is_set():
            raise ConfirmStopped(
                f"the comparison '{label}' was stopped; its finished runs are kept, and running it "
                "again finishes it"
            )
    except BaseException as exc:
        interrupted = isinstance(exc, (KeyboardInterrupt, ConfirmStopped))
        events.emit("run_interrupted" if interrupted else "run_crashed", error=f"{type(exc).__name__}: {exc}")
        heartbeat.stop(phase="interrupted" if interrupted else "crashed")
        raise

    _analyse(comparison)
    (out_dir / "results.json").write_text(json.dumps(comparison.runs, indent=1), encoding="utf-8")
    (out_dir / "comparison.json").write_text(json.dumps(comparison.to_json(), indent=1), encoding="utf-8")
    (out_dir / "comparison.md").write_text(render_markdown(comparison), encoding="utf-8", newline="\n")
    events.emit("run_finished", stop_reason="comparison finished", candidates=len(jobs))
    heartbeat.stop(phase="finished")


def _build_stage(config: Config) -> StageConfig:
    """The config's `builtin-build` stage: a tree with a build has one (`config._tree_rules`)."""
    return next(s for s in config.evaluate.stages if s.kind == "builtin-build")


def _put_trees_back(config: Config, work: Path, log: Callable[[str], None]) -> None:
    """The final check's trees removed, and the engineer's checkout built back
    into the shared interpreter (decision 31); neither ever raises."""
    from evolvekit import tree

    try:
        tree.remove_all(work.parent)
    except Exception as exc:  # noqa: BLE001 - it must not hide the comparison's own error
        log(f"warning: the final check's folders were not all removed ({exc})")
    restore_build(config.problem, log=log)


def _with_instances(config: Config, stage: StageConfig, instances: Sequence[str]) -> StageConfig:
    expanded: list[str] = []
    for entry in instances:
        if any(ch in entry for ch in "*?["):
            matches = sorted(p.relative_to(config.base_dir).as_posix() for p in config.base_dir.glob(entry))
            if not matches:
                raise ConfigError(f"--instances: the pattern {entry!r} matches no file under {config.base_dir}")
            expanded.extend(matches)
        else:
            expanded.append(entry)
    return replace(stage, instances=tuple(expanded))


def _materialise(
    config: Config, row: dict[str, Any], work: Path, name: str
) -> tuple[Path, Configuration | None, Tree | None]:
    """The candidate as a module on disk and, for a declared parameter space,
    as the flags and the JSON file a command is given -- validated again, the
    way the static stage validates it in a run. For a tree problem, its diff
    checked out as a tree under `<work>/trees/` (its folder is `{candidate}`)."""
    candidate = Candidate.from_record(row)
    if config.problem.tree is not None:
        from evolvekit import tree

        try:
            made = tree.materialise(work.parent, repo=tree.Repo(config.problem.tree.repo),
                                    base=config.problem.tree.base, diff=candidate.block, candidate_id=name)
        except tree.TreeError as exc:
            raise ValueError(f"{name}: the recorded change no longer applies -- {exc}") from None
        return made.path, None, made
    source = splice_block(
        config.problem.skeleton_source(), candidate.block, config.problem.block_start, config.problem.block_end
    )
    directory = work / "candidates"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(source, encoding="utf-8", newline="\n")
    static = next((s for s in config.evaluate.stages if s.kind == "builtin-static"), None)
    if static is None or config.problem.parameters is None:
        return path, None, None
    outcome = run_static_stage(path, source, static, config.problem)
    if not outcome.ok or outcome.params is None:
        raise ValueError(f"{name}: the recorded candidate no longer passes the static stage -- {outcome.failure}")
    json_path = path.with_suffix(".params.json")
    json_path.write_text(json.dumps(outcome.params, indent=2) + "\n", encoding="utf-8", newline="\n")
    flags = tuple(config.problem.parameters.render_flags(outcome.params))
    return path, Configuration(flags=flags, json_path=json_path), None


_PAIR_COUNTS = ("finished_pairs", "candidate_failed_pairs", "baseline_failed_pairs", "both_failed_pairs",
                "nonpositive_pairs")


def identical_runs(runs: Sequence[dict[str, Any]], candidate: str, kpis: Sequence[str],
                   instances: Sequence[str], seeds: Sequence[int], baseline: str = BASELINE) -> bool:
    """Whether every planned (instance, seed) pair gave exactly the baseline's
    result: both sides finished it, and every one of `kpis` is equal. Such a
    candidate measured nothing but the baseline again -- its change was never
    exercised (a fixed setting, or a code path the cases never reach) -- so no interval
    or chance can say anything about it. `runs` are a comparison's (or its
    `results.json`): configuration, instance, seed, ok, kpis."""
    if not kpis or not instances or not seeds:
        return False
    found: dict[tuple[str, str, int], dict[str, Any]] = {}
    for run in runs:
        if run.get("ok") and run.get("configuration") in (candidate, baseline):
            found[(str(run["configuration"]), str(run.get("instance")), int(run.get("seed") or 0))] = run.get("kpis") or {}
    for instance in instances:
        for seed in seeds:
            base, mine = found.get((baseline, str(instance), int(seed))), found.get((candidate, str(instance), int(seed)))
            if base is None or mine is None:
                return False
            for kpi in kpis:
                a, b = base.get(kpi), mine.get(kpi)
                if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or float(a) != float(b):
                    return False
    return True


def _analyse(comparison: Comparison) -> None:
    """Per candidate: the per-instance gains and their summary.

    The gain of a pair is the log ratio `s * (log base - log mine)`, s = +1
    to minimise and -1 to maximise; per instance its mean over the seeds
    (`log_gain`, with `seed_var` the variance of the gains it is the mean of
    and `pairs` their count), shown as `improvement_pct`, in percent of the
    baseline's value. The summary is `verdict_summary` of the instances' log
    gains -- the posterior, the decision and its sentence -- with its mean,
    interval and worst case in percent too (`log` keeps them on the log
    scale) and the frequentist interval under `frequentist`.

    A failed run is part of the result, not a gap in it. When only one side of
    an (instance, seed) pair failed or timed out, that side lost the pair: it
    is counted as a log gain as large as the largest one measured anywhere
    in this comparison -- a loss when the candidate failed, a win when the
    baseline did -- so a configuration that fails often cannot be judged on its
    lucky runs, and a failure never weighs less than a real result. A pair in
    which both failed, or with a value of 0 or below on either side (a log
    ratio of it is no number), says nothing about either and is left out.
    Every kind is counted, and the summary over finished pairs alone is kept
    beside the result.
    """
    objective, direction = comparison.objective, comparison.direction
    table: dict[str, dict[tuple[str, int], float]] = {}
    for run in comparison.runs:
        if run["ok"] and objective in run["kpis"]:  # a retry that went through replaces the failure before it
            table.setdefault(run["configuration"], {})[(str(run["instance"]), int(run["seed"]))] = float(run["kpis"][objective])
    base = table.get(BASELINE, {})
    kpis = list(dict.fromkeys([objective, *(str(level["kpi"]) for level in comparison.levels)]))

    for cid in comparison.candidates:
        mine = table.get(cid, {})
        measured = [log_gain(base[k], mine[k], direction) for k in base.keys() & mine.keys()]
        largest = max((abs(g) for g in measured if g is not None), default=None)
        per_instance, finished_only = [], []
        for instance in comparison.instances:
            counts = dict.fromkeys(_PAIR_COUNTS, 0)
            gains, finished, pairs = [], [], []
            for key in [(instance, s) for s in comparison.seeds]:
                if key in base and key in mine:
                    gain = log_gain(base[key], mine[key], direction)
                    if gain is None:
                        counts["nonpositive_pairs"] += 1
                        continue
                    counts["finished_pairs"] += 1
                    finished.append(gain)
                    pairs.append((base[key], mine[key]))
                elif key in base:
                    counts["candidate_failed_pairs"] += 1
                    if largest is not None:
                        gains.append(-largest)
                elif key in mine:
                    counts["baseline_failed_pairs"] += 1
                    if largest is not None:
                        gains.append(largest)
                else:
                    counts["both_failed_pairs"] += 1
            gains = finished + gains
            counts["failed_pairs"] = (
                counts["candidate_failed_pairs"] + counts["baseline_failed_pairs"] + counts["both_failed_pairs"]
            )
            if finished:
                finished_only.append(fmean(finished))
            mean = fmean(gains) if gains else None
            per_instance.append({
                "instance": instance, "pairs": len(gains),
                "baseline": fmean(b for b, _ in pairs) if pairs else None,
                "candidate": fmean(c for _, c in pairs) if pairs else None,
                "log_gain": mean,
                "seed_var": statistics.variance(gains) if len(gains) >= 2 else None,
                "improvement_pct": pct_from_log(mean, direction) if mean is not None else None,
                "pair_wins": sum(1 for g in gains if g > 0),
                **counts,
            })
        log_gains = [r["log_gain"] for r in per_instance if r["log_gain"] is not None]
        failed = sum(1 for r in comparison.runs if r["configuration"] in (cid, BASELINE) and not r["ok"])
        planned = len(comparison.instances) * len(comparison.seeds)
        totals = {name: sum(r[name] for r in per_instance) for name in (*_PAIR_COUNTS, "failed_pairs")}
        summary = verdict_summary(log_gains, prior_scale=comparison.prior_scale, direction=direction,
                                  prior=comparison.prior)
        summary["verdict"] += _counted_as(totals, largest, direction)
        comparison.per_candidate[cid] = {
            "per_instance": per_instance,
            "log_gains": log_gains,
            "summary": summary,
            "finished_only": verdict_summary(finished_only, prior_scale=comparison.prior_scale, direction=direction,
                                             prior=comparison.prior),
            "failure_counted_as_log": largest,
            "failure_counted_as_pct": _failure_sizes(largest, direction),
            "pairs": sum(r["pairs"] for r in per_instance),
            "pairs_planned": planned,
            "failed_runs": failed,
            **totals,
            # Every run gave exactly the baseline's result: the change was not exercised.
            "identical": identical_runs(comparison.runs, cid, kpis, comparison.instances, comparison.seeds),
        }
        if comparison.levels:
            comparison.per_candidate[cid]["levels"] = _analyse_levels(comparison, cid)


def _failure_sizes(largest: float | None, direction: str) -> dict[str, float] | None:
    """The imputed pairs in percent, as the comparison used them: a pair only
    the candidate failed counts as `-largest`, a loss of `-pct_from_log(-largest)`;
    one only the baseline failed as `+largest`, a win of `pct_from_log(largest)`.
    The two differ (twice the cost is 100 % more, half of it 50 % less)."""
    if largest is None:
        return None
    return {"loss": -pct_from_log(-largest, direction), "win": pct_from_log(largest, direction)}


def _pct_summary(summary: dict[str, Any], direction: str) -> dict[str, Any]:
    """A `paired_summary` of log gains, its `mean`, `ci95` and `worst` in
    percent of the baseline's value (`pct_from_log`); `chance` is the same on
    either scale. `log` keeps the mean, sd and interval as they were: the sd
    is only there, so no reader puts a log sd beside a percent mean."""
    out = dict(summary)
    out["log"] = {"mean": summary["mean"], "sd": out.pop("sd"), "ci95": summary["ci95"]}

    def pct(value: float | None) -> float | None:
        return None if value is None else pct_from_log(value, direction)

    out["mean"], out["worst"] = pct(summary["mean"]), pct(summary["worst"])
    out["ci95"] = None if summary["ci95"] is None else [pct(v) for v in summary["ci95"]]
    return out


def _analyse_levels(comparison: Comparison, cid: str) -> dict[str, Any]:
    """`evaluate.score.levels`, compared level by level, and a lexicographic verdict.

    Per level, the paired difference per (instance, seed), signed so that
    positive is better, averaged over the seeds of an instance; over
    instances a mean with a 95 % interval. The difference is in percent of the
    baseline's value on the same instance and seed, as for the objective --
    instances of different size then count alike -- unless some baseline
    value is 0: a level is often a count (late tasks, missed tasks) and a
    percentage of 0 is no number, so such a level is compared in the KPI's own
    units, and says so (`unit`). A pair only one side finished counts against
    the side that failed, as large as the largest difference measured on that
    level. A relative tolerance is a fraction of the baseline's value; an
    absolute one is in the KPI's units (in percent of the baseline's mean when
    the level is compared in percent).

    A level is *equal* when its mean difference is within its tolerance; the
    first level that is not equal decides, and the result is `confirmed` only
    when that level is better and its interval lies above zero. The last
    level has no tolerance: whatever the levels above leave equal, it decides.
    """
    rows: list[dict[str, Any]] = []
    parts: list[str] = []
    deciding: int | None = None
    confirmed = False
    judging = True  # until a level decides, or cannot be compared at all
    for index, level in enumerate(comparison.levels):
        kpi, last = str(level["kpi"]), index == len(comparison.levels) - 1
        sign = 1.0 if level.get("direction") == "maximize" else -1.0
        table: dict[str, dict[tuple[str, int], float]] = {}
        for run in comparison.runs:
            if run["ok"] and kpi in run["kpis"]:
                table.setdefault(run["configuration"], {})[(str(run["instance"]), int(run["seed"]))] = float(run["kpis"][kpi])
        base, mine = table.get(BASELINE, {}), table.get(cid, {})
        shared = base.keys() & mine.keys()
        baseline_mean = fmean(base.values()) if base else None
        unit = "percent" if shared and baseline_mean and all(base[k] != 0 for k in shared) else "absolute"

        def difference(key: tuple[str, int]) -> float:
            gain = sign * (mine[key] - base[key])
            return 100.0 * gain / abs(base[key]) if unit == "percent" else gain

        largest = max((abs(difference(k)) for k in shared), default=None)
        per_instance = []
        for instance in comparison.instances:
            differences = []
            for key in [(instance, seed) for seed in comparison.seeds]:
                if key in shared:
                    differences.append(difference(key))
                elif key in base and largest is not None:
                    differences.append(-largest)
                elif key in mine and largest is not None:
                    differences.append(largest)
            if differences:
                per_instance.append(fmean(differences))
        summary = paired_summary(per_instance)
        tolerance = level.get("tolerance")
        tolerance_abs = within = None
        if not last and tolerance is not None:
            relative = bool(level.get("relative"))
            tolerance_abs = float(tolerance) * abs(baseline_mean) if relative and baseline_mean else float(tolerance)
            if unit == "percent":
                within = 100.0 * float(tolerance) if relative else 100.0 * float(tolerance) / abs(baseline_mean)
            else:
                within = tolerance_abs
        mean, interval = summary["mean"], summary.get("ci95")
        if mean is None:
            state = "unknown"
        elif within is not None and abs(mean) <= within:
            state = "equal"
        else:
            state = "better" if mean > 0 else "worse" if mean < 0 else "equal"
        clear = bool(interval) and ((state == "better" and interval[0] > 0) or (state == "worse" and interval[1] < 0))
        rows.append({
            "level": index + 1, "kpi": kpi, "direction": level.get("direction"), "unit": unit,
            "tolerance": tolerance, "relative": bool(level.get("relative")), "tolerance_abs": tolerance_abs,
            "tolerance_in_unit": within, "baseline_mean": baseline_mean, "mean_difference": mean,
            "ci95": interval, "n": summary["n"], "wins": summary["wins"], "losses": summary["losses"],
            "state": state, "clear": clear,
        })
        if not judging:
            continue  # below the deciding level: reported, not judged
        if state == "equal" and not last:
            within = f"{100 * float(tolerance):g} %" if level.get("relative") else f"{float(tolerance):g}"
            parts.append(f"equal on level {index + 1} ({kpi} within {within})")
        elif state in ("better", "worse"):
            deciding, judging = index + 1, False
            confirmed = state == "better" and clear
            parts.append(
                f"{state} on level {index + 1} ({kpi}): "
                + ("clear" if clear else "not distinguishable from the baseline")
            )
        elif state == "equal":
            parts.append(f"equal on level {index + 1} ({kpi})")
        else:
            judging = False
            parts.append(f"level {index + 1} ({kpi}): no pair of runs to compare")
    return {"levels": rows, "deciding": deciding, "verdict": ", ".join(parts), "confirmed": confirmed}


def _counted_as(totals: dict[str, int], largest: float | None, direction: str) -> str:
    """What the verdict has to say about pairs that did not simply finish.
    `largest` is the largest log gain measured; each imputed pair is said in
    the percent it was counted as (`_failure_sizes`)."""
    notes = []
    sizes = _failure_sizes(largest, direction)
    for key, side, what, size in (("candidate_failed_pairs", "the candidate", "a loss", "loss"),
                                  ("baseline_failed_pairs", "the baseline", "a win", "win")):
        if totals[key] and sizes is not None:
            notes.append(f"{totals[key]} pair(s) in which only {side} failed counted as {what} of {sizes[size]:.3g} %")
        elif totals[key]:
            notes.append(f"{totals[key]} pair(s) in which only {side} failed are not in this interval "
                         "(no finished pair to size them by)")
    if totals["both_failed_pairs"]:
        notes.append(f"{totals['both_failed_pairs']} pair(s) in which both failed are not in this interval")
    if totals["nonpositive_pairs"]:
        notes.append(f"{totals['nonpositive_pairs']} pair(s) had a value of 0 or below and are not in this comparison")
    return "".join(f"; {note}" for note in notes)


def render_markdown(comparison: Comparison) -> str:
    lines = [
        f"# Confirmation `{comparison.label}`",
        "",
        f"Stage `{comparison.stage}`, objective `{comparison.objective}` ({comparison.direction}), "
        f"seeds {comparison.seeds}, {len(comparison.instances)} instance(s). Baseline: `{comparison.baseline_id}`. "
        "Improvement is the log ratio of the baseline's and the candidate's value on the same instance and "
        "seed, averaged over the seeds of an instance, and shown in percent of the baseline; positive is better. "
        f"The rule: {RULE_WORDS} The answer was read {comparison.looks} time(s).",
        "",
    ]
    for cid, result in comparison.per_candidate.items():
        s = result["summary"]
        lines += [f"## `{cid}` against the baseline", ""]
        if result.get("levels"):
            lines += _levels_markdown(result["levels"])
        sizes = result.get("failure_counted_as_pct")
        counted = (
            f"{result['pairs']} of {result['pairs_planned']} (instance, seed) pairs are in the comparison, "
            f"{result.get('finished_pairs', 0)} of them finished by both. A pair only one side finished counts "
            "against the side that failed, as large as the largest difference measured"
            + (f" (a loss of {sizes['loss']:.3g} %, a win of {sizes['win']:.3g} %)" if isinstance(sizes, dict) else "")
            + f": only the candidate failed {result.get('candidate_failed_pairs', 0)}, only the baseline "
            f"{result.get('baseline_failed_pairs', 0)}; left out: both failed {result.get('both_failed_pairs', 0)}, "
            f"a value of 0 or below {result.get('nonpositive_pairs', 0)}. {result['failed_runs']} run(s) failed."
        )
        only = result.get("finished_only") or {}
        if only.get("mean") is not None and result.get("failed_pairs"):
            interval_only = f"[{only['ci95'][0]:+.3f}, {only['ci95'][1]:+.3f}]" if only.get("ci95") else "n/a"
            counted += (
                f"\n\nFinished pairs only: mean {only['mean']:+.3f} %, 95 % CI {interval_only} over "
                f"{only['n']} instance(s) -- what the comparison would say if failures cost nothing."
            )
        if s["mean"] is None:
            lines += ["No pair of runs finished for both configurations.", "", counted, ""]
            continue
        if result.get("identical"):
            lines += ["**Every run gave exactly the baseline's result**, on every instance and seed: the "
                      "candidate's change may not be exercised by these runs, so the interval below says "
                      "nothing about it.", ""]
        verdict = s["verdict"][:1].upper() + s["verdict"][1:]
        if s.get("chance") is not None and s.get("ci95"):
            interval = f"[{s['ci95'][0]:+.3f}, {s['ci95'][1]:+.3f}]"
            head = (
                f"**Mean improvement {s['mean']:+.3f} %** (the posterior mean; the paired mean is "
                f"{s['paired_mean']:+.3f} %), 95 % credible interval {interval} over {s['n']} instance(s); "
                f"{s['wins']} better, {s['losses']} worse; chance of a gain {s['chance']:.2f}, of losing more than "
                f"{USE_MARGIN_PCT:g} % {1 - s['chance_margin']:.2f}; prior {s['prior']}. **{verdict}.**"
            )
        else:
            head = f"**Mean improvement {s['mean']:+.3f} %** over {s['n']} instance(s). **{verdict}.**"
        head_lines = [head, ""]
        f = s.get("frequentist") or (s if "wilcoxon" in s else None)  # a summary written before the posterior
        if f is not None and f.get("mean") is not None:
            f_interval = f"[{f['ci95'][0]:+.3f}, {f['ci95'][1]:+.3f}]" if f.get("ci95") else "n/a"
            p = (f.get("wilcoxon") or {}).get("p")
            head_lines += [
                f"Frequentist, for engineers: mean {f['mean']:+.3f} %, 95 % CI {f_interval}, Wilcoxon signed-rank "
                "p = " + ("n/a" if p is None else f"{p:.4f}") + ".",
                "",
            ]
        lines += [
            *head_lines,
            counted,
            "",
            "| instance | baseline | candidate | improvement | pairs won | dropped |",
            "|---|--:|--:|--:|--:|--:|",
        ]
        for row in result["per_instance"]:
            dropped = _dropped(row)
            if row["improvement_pct"] is None:
                lines.append(f"| {row['instance']} | – | – | – | 0 of 0 | {dropped} |")
            else:
                lines.append(
                    f"| {row['instance']} | {row['baseline']:.6g} | {row['candidate']:.6g} | "
                    f"{row['improvement_pct']:+.3f} % | {row['pair_wins']} of {row['pairs']} | {dropped} |"
                )
        lines.append("")
    return "\n".join(lines)


def _levels_markdown(levels: dict[str, Any]) -> list[str]:
    """The lexicographic verdict and one row per level (`_analyse_levels`)."""

    def number(value: Any, unit: str, signed: bool = False) -> str:
        if value is None:
            return "–"
        text = f"{value:+.4g}" if signed else f"{value:.4g}"
        return text + " %" if unit == "percent" else text

    verdict = levels["verdict"]
    lines = [
        "### Level by level",
        "",
        f"**{verdict[:1].upper() + verdict[1:]}.** The first level that is not equal within its "
        "tolerance decides; it is confirmed only when that level is better and its 95 % interval "
        "lies above zero.",
        "",
        "Differences are per instance, averaged over the seeds, positive is better: in percent of "
        "the baseline's value, or in the KPI's own units where some baseline value is 0.",
        "",
        "| level | goal | baseline mean | equal within | mean difference | 95 % CI | verdict |",
        "|--:|---|--:|--:|--:|---|---|",
    ]
    for row in levels["levels"]:
        unit = row.get("unit", "absolute")
        interval = (
            f"[{number(row['ci95'][0], unit, True)}, {number(row['ci95'][1], unit, True)}]" if row.get("ci95") else "n/a"
        )
        lines.append(
            f"| {row['level']} | {row['kpi']} ({row['direction']}) | {number(row.get('baseline_mean'), 'absolute')} | "
            f"{number(row.get('tolerance_in_unit'), unit)} | {number(row.get('mean_difference'), unit, True)} | "
            f"{interval} | {row['state']}{', clear' if row.get('clear') else ''} |"
        )
    lines.append("")
    return lines


def _dropped(row: dict[str, Any]) -> str:
    parts = []
    for key, label in (("candidate_failed_pairs", "candidate failed"), ("baseline_failed_pairs", "baseline failed"),
                       ("both_failed_pairs", "both failed")):
        if row.get(key):
            parts.append(f"{row[key]} {label}")
    if row.get("nonpositive_pairs"):
        parts.append(f"{row['nonpositive_pairs']} not positive")
    return ", ".join(parts) or "–"
