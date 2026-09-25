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

The unit of the statistics is the instance. Per instance, the relative
difference to the baseline is averaged over the seeds; over instances that
gives a mean with a 95 % confidence interval (paired, Student's t) and an exact
Wilcoxon signed-rank test. A confidence interval that includes zero is reported
as what it is.

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
from dataclasses import dataclass, field, replace
from pathlib import Path
from statistics import fmean, stdev
from typing import Any, Callable, Sequence

from evolvekit.candidate import SEED_OPERATOR, Candidate, splice_block
from evolvekit.config import Config, ConfigError, StageConfig
from evolvekit.evaluate.cache import EvalCache
from evolvekit.evaluate.fanout import Job, run_instance_stage
from evolvekit.evaluate.stages import Configuration, run_static_stage
from evolvekit.events import EventLog, Heartbeat
from evolvekit.leaderboard import rank
from evolvekit.ledger import read_jsonl
from evolvekit.lock import run_lock

__all__ = ["confirm", "Comparison", "paired_summary", "wilcoxon_signed_rank", "render_markdown"]

BASELINE = "baseline"


# --------------------------------------------------------------------------
# statistics: small, exact, standard library
# --------------------------------------------------------------------------

# Two-sided 95 % critical values of Student's t, by degrees of freedom.
_T95 = (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
)


_Z975 = 1.959963984540054
"""The normal quantile the t quantile falls towards as the samples grow."""


def _t95(df: int) -> float:
    """The two-sided 95 % critical value of Student's t with `df` degrees of
    freedom. The table up to 30; beyond it the Cornish-Fisher expansion around
    the normal quantile, which is within 0.0005 of the exact value from 30 on.
    1.96 is the limit, not the value: at 31 it is 2.040, and an interval on
    32 instances drawn with 1.96 is about 4 % narrower than it should be."""
    if df < 1:
        raise ValueError(f"degrees of freedom must be >= 1, got {df}")
    if df <= len(_T95):
        return _T95[df - 1]
    z = _Z975
    return (
        z
        + (z**3 + z) / (4 * df)
        + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df**2)
        + (3 * z**7 + 19 * z**5 + 17 * z**3 - 15 * z) / (384 * df**3)
        + (79 * z**9 + 776 * z**7 + 1482 * z**5 - 1920 * z**3 - 945 * z) / (92160 * df**4)
    )


def wilcoxon_signed_rank(differences: Sequence[float]) -> dict[str, Any]:
    """The exact two-sided Wilcoxon signed-rank test of "the median difference
    is zero". Zeros are dropped, ties share their mean rank; the null
    distribution is enumerated, so there is no normal approximation to distrust
    at n = 10."""
    values = [d for d in differences if d != 0]
    n = len(values)
    if n == 0:
        return {"n": 0, "w_plus": 0.0, "p": None}
    order = sorted(range(n), key=lambda i: abs(values[i]))
    ranks2 = [0] * n  # twice the rank, so that a shared mean rank stays an integer
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(values[order[j + 1]]) == abs(values[order[i]]):
            j += 1
        for k in range(i, j + 1):
            ranks2[order[k]] = (i + 1) + (j + 1)
        i = j + 1
    w_plus2 = sum(r for r, v in zip(ranks2, values) if v > 0)
    total2 = sum(ranks2)
    counts = {0: 1}  # how many sign assignments give each (doubled) rank sum
    for r in ranks2:
        step: dict[int, int] = {}
        for s, c in counts.items():
            step[s] = step.get(s, 0) + c
            step[s + r] = step.get(s + r, 0) + c
        counts = step
    extreme = min(w_plus2, total2 - w_plus2)
    tail = sum(c for s, c in counts.items() if s <= extreme)
    p = min(1.0, 2.0 * tail / (2 ** n))
    return {"n": n, "w_plus": w_plus2 / 2.0, "p": p}


def paired_summary(differences: Sequence[float]) -> dict[str, Any]:
    """Mean, 95 % confidence interval and Wilcoxon test of per-instance differences."""
    n = len(differences)
    if n == 0:
        return {"n": 0, "mean": None, "ci95": None, "sd": None, "wilcoxon": wilcoxon_signed_rank([]),
                "wins": 0, "losses": 0, "verdict": "nothing to compare"}
    mean = fmean(differences)
    wins, losses = sum(1 for d in differences if d > 0), sum(1 for d in differences if d < 0)
    if n < 2:
        return {"n": n, "mean": mean, "ci95": None, "sd": None, "wilcoxon": wilcoxon_signed_rank(differences),
                "wins": wins, "losses": losses, "verdict": "one instance: no interval can be given"}
    sd = stdev(differences)
    half = _t95(n - 1) * sd / math.sqrt(n)
    low, high = mean - half, mean + half
    if low > 0:
        verdict = "better than the baseline: the 95 % interval lies above zero"
    elif high < 0:
        verdict = "WORSE than the baseline: the 95 % interval lies below zero"
    else:
        verdict = "not distinguishable from the baseline: the 95 % interval includes zero"
    return {"n": n, "mean": mean, "sd": sd, "ci95": [low, high], "wilcoxon": wilcoxon_signed_rank(differences),
            "wins": wins, "losses": losses, "verdict": verdict}


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

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label, "objective": self.objective, "direction": self.direction,
            "stage": self.stage, "seeds": self.seeds, "instances": self.instances,
            "baseline_id": self.baseline_id, "candidates": self.candidates,
            "per_candidate": self.per_candidate,
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
) -> Comparison:
    """Run the comparison described in the module docstring; returns it."""
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
    comparison = Comparison(
        label=label, objective=config.evaluate.score.objective, direction=config.evaluate.score.direction,
        stage=stage.id, seeds=list(seeds), instances=list(stage.instance_names()),
        baseline_id=str(seed_row["id"]), candidates=[str(r["id"]) for r in chosen],
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

    jobs = []
    for index, (name, row) in enumerate([(BASELINE, seed_row), *[(str(r["id"]), r) for r in chosen]]):
        # A name like `ID@a/run` is for people; files are named by one that is
        # safe as a file name (and unique, whatever it folded together).
        safe = re.sub(r"[^A-Za-z0-9@._-]+", "_", name)
        if any(job.candidate_id == safe for job in jobs):
            safe = f"{safe}.{index}"
        path, configuration = _materialise(config, row, work, safe)
        jobs.append(Job(safe, path, configuration=configuration, observer=observer_for(name)))

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
    # The directory's own lock: `status` reads "alive" off it, and two
    # confirmations under one label would overwrite each other's results.
    with run_lock(out_dir):
        heartbeat.start(phase="evaluating")
        try:
            events.emit("stage_started", stage=stage.id, private=False, candidates=[BASELINE, *comparison.candidates],
                        runs_per_candidate=len(stage.instances) * len(seeds), workers=stage.workers)
            run_instance_stage(
                jobs, stage, out_dir=work / "stage_out", cwd=config.base_dir,
                required_kpis=(comparison.objective,), cache=EvalCache(work / "cache"),
                seeds=list(seeds), keep_going=True,
            )
            events.emit("stage_finished", stage=stage.id, private=False, candidates=len(jobs),
                        failed=sum(1 for r in comparison.runs if not r["ok"]), duration_s=None)
        except BaseException as exc:
            interrupted = isinstance(exc, KeyboardInterrupt)
            events.emit("run_interrupted" if interrupted else "run_crashed", error=f"{type(exc).__name__}: {exc}")
            heartbeat.stop(phase="interrupted" if interrupted else "crashed")
            raise

        _analyse(comparison)
        (out_dir / "results.json").write_text(json.dumps(comparison.runs, indent=1), encoding="utf-8")
        (out_dir / "comparison.json").write_text(json.dumps(comparison.to_json(), indent=1), encoding="utf-8")
        (out_dir / "comparison.md").write_text(render_markdown(comparison), encoding="utf-8", newline="\n")
        events.emit("run_finished", stop_reason="comparison finished", candidates=len(jobs))
        heartbeat.stop(phase="finished")
    return comparison


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


def _materialise(config: Config, row: dict[str, Any], work: Path, name: str) -> tuple[Path, Configuration | None]:
    """The candidate as a module on disk and, for a declared parameter space,
    as the flags and the JSON file a command is given -- validated again, the
    way the static stage validates it in a run."""
    candidate = Candidate.from_record(row)
    source = splice_block(
        config.problem.skeleton_source(), candidate.block, config.problem.block_start, config.problem.block_end
    )
    directory = work / "candidates"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    path.write_text(source, encoding="utf-8", newline="\n")
    static = next((s for s in config.evaluate.stages if s.kind == "builtin-static"), None)
    if static is None or config.problem.parameters is None:
        return path, None
    outcome = run_static_stage(path, source, static, config.problem)
    if not outcome.ok or outcome.params is None:
        raise ValueError(f"{name}: the recorded candidate no longer passes the static stage -- {outcome.failure}")
    json_path = path.with_suffix(".params.json")
    json_path.write_text(json.dumps(outcome.params, indent=2) + "\n", encoding="utf-8", newline="\n")
    flags = tuple(config.problem.parameters.render_flags(outcome.params))
    return path, Configuration(flags=flags, json_path=json_path)


def _analyse(comparison: Comparison) -> None:
    objective, sign = comparison.objective, (1.0 if comparison.direction == "minimize" else -1.0)
    table: dict[str, dict[tuple[str, int], float]] = {}
    for run in comparison.runs:
        if run["ok"] and objective in run["kpis"]:  # a retry that went through replaces the failure before it
            table.setdefault(run["configuration"], {})[(str(run["instance"]), int(run["seed"]))] = float(run["kpis"][objective])
    base = table.get(BASELINE, {})
    for cid in comparison.candidates:
        mine = table.get(cid, {})
        per_instance = []
        for instance in comparison.instances:
            # A pair is dropped when either run of it failed or timed out, and
            # when the baseline reached 0 (a percentage of it is no number).
            # Both are counted: an interval over fewer pairs than planned has
            # to say so.
            keys = [(instance, s) for s in comparison.seeds]
            finished = [key for key in keys if key in base and key in mine]
            pairs = [(base[key], mine[key]) for key in finished if base[key] != 0]
            dropped = {"failed_pairs": len(keys) - len(finished), "zero_baseline_pairs": len(finished) - len(pairs)}
            if not pairs:
                per_instance.append({"instance": instance, "pairs": 0, "baseline": None, "candidate": None,
                                     "improvement_pct": None, **dropped})
                continue
            gains = [100.0 * sign * (b - c) / abs(b) for b, c in pairs]
            per_instance.append({
                "instance": instance, "pairs": len(pairs),
                "baseline": fmean(b for b, _ in pairs), "candidate": fmean(c for _, c in pairs),
                "improvement_pct": fmean(gains),
                "pair_wins": sum(1 for g in gains if g > 0),
                **dropped,
            })
        differences = [r["improvement_pct"] for r in per_instance if r["improvement_pct"] is not None]
        failed = sum(1 for r in comparison.runs if r["configuration"] in (cid, BASELINE) and not r["ok"])
        planned = len(comparison.instances) * len(comparison.seeds)
        failed_pairs = sum(r["failed_pairs"] for r in per_instance)
        zero_pairs = sum(r["zero_baseline_pairs"] for r in per_instance)
        summary = paired_summary(differences)
        # The statistic and its thresholds stay as they are; the verdict says
        # plainly what the interval leaves out.
        if failed_pairs:
            summary["verdict"] += f"; {failed_pairs} of {planned} pairs failed and are not in this interval"
        if zero_pairs:
            summary["verdict"] += f"; {zero_pairs} of {planned} pairs had a baseline of 0 and are not in this interval"
        comparison.per_candidate[cid] = {
            "per_instance": per_instance,
            "summary": summary,
            "pairs": sum(r["pairs"] for r in per_instance),
            "pairs_planned": planned,
            "failed_runs": failed,
            "failed_pairs": failed_pairs,
            "zero_baseline_pairs": zero_pairs,
        }


def render_markdown(comparison: Comparison) -> str:
    lines = [
        f"# Confirmation `{comparison.label}`",
        "",
        f"Stage `{comparison.stage}`, objective `{comparison.objective}` ({comparison.direction}), "
        f"seeds {comparison.seeds}, {len(comparison.instances)} instance(s). Baseline: `{comparison.baseline_id}`. "
        "Improvement is in percent of the baseline's value on the same instance and seed, averaged over "
        "the seeds of an instance; positive is better.",
        "",
    ]
    for cid, result in comparison.per_candidate.items():
        s = result["summary"]
        lines += [f"## `{cid}` against the baseline", ""]
        counted = (
            f"{result['pairs']} of {result['pairs_planned']} (instance, seed) pairs are in the comparison; "
            f"{result.get('failed_pairs', 0)} failed or timed out, "
            f"{result.get('zero_baseline_pairs', 0)} had a baseline of 0; {result['failed_runs']} run(s) failed."
        )
        if s["mean"] is None:
            lines += ["No pair of runs finished for both configurations.", "", counted, ""]
            continue
        interval = f"[{s['ci95'][0]:+.3f}, {s['ci95'][1]:+.3f}]" if s.get("ci95") else "n/a"
        p = s["wilcoxon"]["p"]
        verdict = s["verdict"].capitalize()
        lines += [
            f"**Mean improvement {s['mean']:+.3f} %**, 95 % CI {interval} over {s['n']} instance(s); "
            f"{s['wins']} better, {s['losses']} worse; Wilcoxon signed-rank p = "
            + ("n/a" if p is None else f"{p:.4f}") + f". **{verdict}.**",
            "",
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


def _dropped(row: dict[str, Any]) -> str:
    parts = []
    if row.get("failed_pairs"):
        parts.append(f"{row['failed_pairs']} failed")
    if row.get("zero_baseline_pairs"):
        parts.append(f"{row['zero_baseline_pairs']} baseline 0")
    return ", ".join(parts) or "–"
