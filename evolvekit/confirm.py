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
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from statistics import fmean
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from evolvekit.candidate import SEED_OPERATOR, Candidate, splice_block
from evolvekit.config import Config, ConfigError, StageConfig
from evolvekit.evaluate.cache import EvalCache
from evolvekit.evaluate.fanout import Job, run_instance_stage
from evolvekit.evaluate.stages import Configuration, on_tree, restore_build, run_build_stage, run_static_stage
from evolvekit.events import EventLog, Heartbeat
from evolvekit.leaderboard import rank, read_reevaluations, read_rows
from evolvekit.ledger import read_jsonl
from evolvekit.lock import run_lock
from evolvekit.stats import _betainc, _t95, paired_summary, t_cdf, wilcoxon_signed_rank  # noqa: F401  (re-exported: importers below)
from evolvekit.stats import holm, log_gain, margin_log, pct_from_log, posterior_mean_t

if TYPE_CHECKING:  # `evolvekit.tree` is imported only for a tree problem
    from evolvekit.tree import Tree

__all__ = [
    "confirm", "Comparison", "ConfirmStopped", "paired_summary", "wilcoxon_signed_rank", "render_markdown",
    "t_cdf", "decide", "decision_of", "verdict_summary", "identical_runs", "USE_CHANCE", "USE_MARGIN_PCT",
    "WORSE_CHANCE", "RULE_WORDS", "LEGACY_RULE_WORDS", "is_posterior", "apply_holm", "comparison_of",
    "read_on_row", "runs_can_help",
]

BASELINE = "baseline"

USE_CHANCE = 0.95
"""`decide` says "use" from this posterior chance of a gain on."""

USE_MARGIN_PCT = 1.0
"""The margin of `chance_margin` (the chance of losing no more than this many %),
kept in `comparison.json` for engineers, and of the rule a comparison written
before A1 was decided by. It does not decide: with x_m < 0, P(gain > x_m) is at
least P(gain > 0), so a chance of a gain of 95 % already passes it."""

WORSE_CHANCE = 0.05
"""`decide` says "worse" up to this chance of a gain, 5 %: P(gain < 0) >= 0.95,
as strict as "use" (decision 14b)."""

_LEGACY_WORSE_CHANCE = 0.1
"""The "worse" of a comparison written before A1: a chance of a gain of 0.10 or less."""

OBJECTIVE_ALONE = "the objective alone, for engineers: it does not decide"
"""With goals in order of importance, the label of the objective's own summary and its decision."""

_LEGACY_USE_CHANCE = 0.9
"""The rule a comparison written before the posterior was decided by: "use"
from a one-sided t chance of 0.9 with the 95 % lower bound within 1 %."""

RULE_WORDS = "Use when the chance of a gain is at least 95 %."
"""The rule in one sentence, as the page states it."""

LEGACY_RULE_WORDS = (
    "The app says use it when the chance of a gain is at least 90 % "
    "and the worst plausible case is within 1 % of today."
)
"""The rule a comparison written before A1 was decided by, as the page stated it then."""

PRIOR = "reference"
"""The posterior's prior, as `comparison.json` names it: p(mu, sigma^2) ~ 1 / sigma^2."""
FREQUENTIST_LABEL = "frequentist 95 % confidence interval and Wilcoxon test, for engineers"
CHANCE_MARGIN_LABEL = "the chance of losing no more than 1 %, for engineers: it does not decide"


def decide(summary: dict[str, Any]) -> str:
    """One fixed rule on a `verdict_summary`, for a planner who has to choose:
    "use" when the posterior chance of a gain is at least `USE_CHANCE`;
    "worse" when the chance of a gain is `WORSE_CHANCE` or less; "keep"
    (today's settings) in between. "single" with one instance, where no
    chance can be given, and "none" with nothing to compare. `RULE_WORDS`
    says the same in a sentence.

    A summary without `posterior` was written before A1: it is decided by
    the rule it was decided by then (a chance of 0.9 and the 95 % lower bound
    within 1 %), so a stored comparison keeps its answer."""
    n = int(summary.get("n") or 0)
    if n == 0:
        return "none"
    if n == 1:
        return "single"
    chance = float(summary["chance"])
    if not is_posterior(summary):
        if chance <= _LEGACY_WORSE_CHANCE:
            return "worse"
        return "use" if chance >= _LEGACY_USE_CHANCE and float(summary["worst"]) >= -USE_MARGIN_PCT else "keep"
    if chance <= WORSE_CHANCE:
        return "worse"
    if chance >= USE_CHANCE and failed_more(summary.get("failed") or {}) is None:
        return "use"
    return "keep"


def failed_more(counts: Mapping[str, Any]) -> int | None:
    """The runs a candidate failed that the starting point finished, when
    there are more of them than the other way round (pairs only the
    candidate failed > pairs only the starting point failed), else None.
    Such a candidate is never "use" (decision 14a): a crash costs at most its
    best finished gain, so a program that crashes on a third of its runs
    could otherwise pass. `counts` is a result or a summary's `failed`:
    `candidate_failed_pairs` and `baseline_failed_pairs`."""
    mine, theirs = int(counts.get("candidate_failed_pairs") or 0), int(counts.get("baseline_failed_pairs") or 0)
    return mine if mine > theirs else None


def failed_words(runs: int) -> str:
    """Why a candidate that failed more than the starting point is not "use", in its sentence."""
    return f"it failed {runs} run{'s' if runs != 1 else ''} the starting point finished"


def is_posterior(summary: dict[str, Any]) -> bool:
    """Whether a summary carries A1's posterior (`verdict_summary`), not a
    comparison written before it: the words of the chance follow the rule
    it was decided by."""
    return isinstance(summary.get("posterior"), dict)


def _chance2(p: float) -> float:
    """A chance to two places, never on the far side of a threshold of the
    rule: rounded, then held on its own side of `USE_CHANCE` and
    `WORSE_CHANCE` (a keep never prints 0.95 nor 0.05), of 1 and of 0 (a
    chance below 1 never prints 1.00, above 0 never 0.00)."""
    shown = round(p, 2)
    if p < USE_CHANCE <= shown:
        shown = USE_CHANCE - 0.01
    if p > WORSE_CHANCE >= shown:
        shown = WORSE_CHANCE + 0.01
    if p < 1.0 <= shown:
        shown = 0.99
    if p > 0.0 >= shown:
        shown = 0.01
    return round(shown, 2)


def decision_of(result: dict[str, Any]) -> str:
    """What one candidate's comparison says: `keep` when every run gave
    exactly the starting point's result (its change was not exercised, so
    there is nothing to decide on); by the levels when the study scores by
    levels (their `decision`; a result written before it had only
    `confirmed`); else `decide` on its `verdict_summary`. A "use" is kept
    when it failed more runs than the starting point (`failed_more`) only by a
    comparison written before A1 (no posterior), which keeps its answer."""
    if result.get("identical"):
        return "keep"
    if "levels" in result:
        levels = result["levels"]
        decision = levels.get("decision") or ("use" if levels["confirmed"] else "keep")
    else:
        decision = decide(result["summary"])
    legacy = "summary" in result and not is_posterior(result["summary"] or {})  # written before A1: its rule
    if decision == "use" and not legacy and failed_more(result) is not None:
        return "keep"  # it failed more runs than the starting point did (decision 14a)
    if decision == "use" and (result.get("holm") or {}).get("passed") is False:
        return "keep"  # its own chance passes the rule; chosen among its rivals, it does not
    return decision


HOLM_ALPHA = 1.0 - USE_CHANCE
"""Holm's level over the candidates or branches of a family: one minus the chance "use" needs."""
BEFORE_HOLM = "its own answer, before the choice among several (Holm): the result's decision decides"
"""A family member's `summary.decision_label`: the result's `decision` is the answer after Holm."""


def apply_holm(results: Mapping[str, dict[str, Any]], *, what: str) -> None:
    """Pay for a choice among several: each result of the family that is
    `use` by its own summary keeps it only when Holm's step-down over the
    family passes on p = 1 - P(gain > 0) at `HOLM_ALPHA` (decision 13: one
    leg, the rule's own chance). The family is the results that are not
    `identical` and carry A1's posterior over two instances or more, or --
    a study scored by levels -- a levels `decision`: its p is one minus the
    deciding level's chance when that decision is `use`, else 1 (it has no
    "use" to keep). With fewer than two members there is no choice and
    nothing changes. Each member gets `result["holm"] = {"k", "what",
    "own", "passed"}` (`own` is its decision before Holm); `decision_of`
    reads it, and `result["decision"]` is what it reads after Holm, every
    result's (R3). In a family, a summary not under levels labels its own
    `decision` as the answer before Holm (`BEFORE_HOLM`). `what` is
    "branches", "candidates" or "kinds of case"."""
    family: dict[str, tuple[float, str]] = {}
    for cid, r in results.items():
        if r.get("identical"):
            continue
        levels = r.get("levels")
        if isinstance(levels, dict):
            if levels.get("decision") is None:
                continue  # a levels result written before A1.5
            row = read_on_row(levels)
            use = levels["decision"] == "use" and row is not None and row.get("chance") is not None
            family[cid] = (1.0 - float(row["chance"]) if use else 1.0, str(levels["decision"]))
        elif ((r.get("summary") or {}).get("n", 0) >= 2 and is_posterior(r["summary"])
              and r["summary"].get("chance") is not None):
            family[cid] = (1.0 - float(r["summary"]["chance"]), decide(r["summary"]))
    if len(family) < 2:
        for r in results.values():
            r.pop("holm", None)
    else:
        passed = holm({cid: p for cid, (p, _own) in family.items()}, HOLM_ALPHA)
        for cid, (_p, own) in family.items():
            results[cid]["holm"] = {"k": len(family), "what": what, "own": own, "passed": passed[cid]}
    for r in results.values():
        summary = r.get("summary")
        if isinstance(summary, dict) and "levels" not in r:
            if "holm" in r:
                summary["decision_label"] = BEFORE_HOLM
            elif summary.get("decision_label") == BEFORE_HOLM:
                summary.pop("decision_label")
        if isinstance(summary, dict) or isinstance(r.get("levels"), dict):
            r["decision"] = decision_of(r)  # the answer after Holm; the summary's is before it


def read_on_row(levels: dict[str, Any]) -> dict[str, Any] | None:
    """The row of the level a levels result's descent stopped on
    (`_analyse_levels`' `read_on`), or None for a result written before it."""
    at, rows = levels.get("read_on"), levels.get("levels") or []
    return rows[at - 1] if isinstance(at, int) and 1 <= at <= len(rows) else None


CASES_LIMIT_SHARE = 0.25
LIMITED_BY_CASES = ("the answer is set by the held-back cases, not by the runs: "
                    "hold back more cases for a firmer answer")


def within_share(per_instance: Sequence[dict[str, Any]]) -> float | None:
    """The share of the spread of the mean gain that more runs could shrink.

    `E[var(d_i)] = tau^2 + mean(sigma_i^2 / n_i)`: the cases' own spread plus
    the runs' noise left in each case's mean. `within` is that second part
    (the run-to-run variance pooled over the cases that have one, divided by
    the gains each case used, averaged over all cases), `between` the
    method-of-moments tau^2, and the share is `within / (within + between)`:
    only `within` falls with more runs. None when it cannot be told (fewer
    than two cases with a gain, or no case with a run-to-run variance);
    0.0 when nothing varies at all (more runs add nothing)."""
    rows = [r for r in per_instance if r.get("log_gain") is not None]
    gains = [r["log_gain"] for r in rows]
    spread = [(max(1, int(r.get("pairs") or 1)) - 1, r["seed_var"]) for r in rows if r.get("seed_var") is not None]
    if len(gains) < 2 or not spread or sum(df for df, _ in spread) <= 0:
        return None
    # The run-to-run variance pooled over the cases that have one (a case with a single
    # gain has none, but its mean is as noisy as the rest): each case's mean carries it / n_i.
    pooled = sum(df * v for df, v in spread) / sum(df for df, _ in spread)
    within = pooled * fmean(1 / max(1, int(r.get("pairs") or 1)) for r in rows)
    between = max(0.0, statistics.variance(gains) - within)
    if within + between <= 0:
        return 0.0
    return within / (within + between)


def runs_can_help(result: dict[str, Any]) -> bool:
    """Whether more runs per case could still open the answer: unless the
    cases' own spread is most of it (`within_share` under `CASES_LIMIT_SHARE`),
    in which case only more held-back cases could. With levels, the gains
    are those of the level the descent stopped on, not the objective's."""
    rows = result.get("per_instance") or []
    row = read_on_row(result["levels"]) if isinstance(result.get("levels"), dict) else None
    if row is not None and row.get("per_case") is not None:
        # Scored by levels: the level the descent stopped on is the one more runs would have to open.
        rows = [{"log_gain": c["gain"], "pairs": c["pairs"], "seed_var": c["seed_var"]} for c in row["per_case"]]
    share = within_share(rows)
    return share is None or share >= CASES_LIMIT_SHARE


def _run_noise_of(seed_row: dict[str, Any], objective: str) -> float | None:
    """The starting point's run-to-run noise of the objective, for engineers:
    its coefficient of variation when the search measured one (to first
    order, the sd of the log objective), else None. It is recorded, not a
    prior: the posterior's spread is the cases' own (`posterior_mean_t`)."""
    cv = (seed_row.get("kpi_cv") or {}).get(objective) if isinstance(seed_row.get("kpi_cv"), dict) else None
    if isinstance(cv, (int, float)) and math.isfinite(cv) and cv > 0:
        return float(cv)
    return None


def verdict_summary(d: Sequence[float], *, direction: str,
                    failed: Mapping[str, int] | None = None) -> dict[str, Any]:
    """The one place that decides: per-case log gains `d` to the numbers a
    person is shown and the decision they are read by.

    The posterior of the true mean gain under the reference prior
    (`stats.posterior_mean_t`: Student's t with n - 1 degrees of freedom
    around the cases' mean, scale s / sqrt(n)). `mean` is its mean (the
    cases' mean), `ci95` its 95 % credible interval (for engineers),
    `range90` its 90 % credible interval (the 5 % and 95 % quantiles: the
    range a person is shown), `chance` P(gain > 0), `chance_margin` P(gain >
    the 1 % margin) and `worst` the 5 % quantile, the one-sided bound the rule
    reads and `range90`'s low end -- all in percent of the starting
    point's value (`pct_from_log`); `log` and `posterior` keep the log-scale
    numbers. The frequentist interval and Wilcoxon test are under
    `frequentist`, for engineers. `decision` is `decide` of this summary and
    `verdict` says it in a sentence. `failed` (`candidate_failed_pairs`,
    `baseline_failed_pairs`) is kept as the summary's `failed`: a candidate
    that failed more runs than the starting point is never "use"
    (`failed_more`), and the sentence says so."""
    d = [float(x) for x in d]
    n = len(d)
    wins, losses = sum(1 for x in d if x > 0), sum(1 for x in d if x < 0)
    if n == 0:
        out: dict[str, Any] = {"n": 0, "mean": None, "ci95": None, "range90": None, "chance": None, "chance_margin": None,
                               "worst": None, "wins": 0, "losses": 0, "verdict": "nothing to compare"}
        out["decision"] = decide(out)
        return out
    if n == 1:
        out = {"n": 1, "mean": pct_from_log(d[0], direction), "ci95": None, "range90": None, "chance": None,
               "chance_margin": None,
               "worst": None, "wins": wins, "losses": losses, "log": {"mean": d[0], "sd": None, "ci95": None},
               "verdict": "one instance: no chance can be given"}
        out["decision"] = decide(out)
        return out
    post = posterior_mean_t(d)
    low, high = post.interval95()
    worst_log, high90 = post.interval90()
    chance = post.p_above(0.0)
    chance_margin = post.p_above(margin_log(USE_MARGIN_PCT, direction))
    out = {
        "n": n,
        "mean": pct_from_log(post.mu, direction),
        "ci95": [pct_from_log(low, direction), pct_from_log(high, direction)],
        "chance": chance,
        "chance_margin": chance_margin,
        "chance_margin_label": CHANCE_MARGIN_LABEL,
        "worst": pct_from_log(worst_log, direction),
        # The range a person is shown: its low end is `worst`, the same number (decision 15).
        "range90": [pct_from_log(worst_log, direction), pct_from_log(high90, direction)],
        "wins": wins, "losses": losses,
        "log": {"mean": post.mu, "sd": statistics.stdev(d), "ci95": [low, high], "range90": [worst_log, high90],
                "worst": worst_log},
        "prior": PRIOR,
        "posterior": {"mu": post.mu, "scale": post.scale, "df": post.df},
        "frequentist": {**_pct_summary(paired_summary(d), direction), "label": FREQUENTIST_LABEL},
    }
    if failed:
        out["failed"] = dict(failed)
    decision = decide(out)
    said = f"a chance of a gain of {_chance2(chance):.2f}"
    more = failed_more(failed or {})
    if more is not None:
        said += f"; {failed_words(more)}"
    verdict = {"use": "probably better", "worse": "probably WORSE"}.get(decision, "not proven") + f": {said}"
    out["decision"], out["verdict"] = decision, verdict + " (reference prior)"
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
    run_noise: float | None = None
    """The starting point's run-to-run noise of the objective (`_run_noise_of`), for engineers."""
    prior: str = PRIOR
    """The posterior's prior, by name."""
    looks: int = 1
    """How many times this comparison's answer has been read (written): a
    check extended or run again under one label reads it again; one whose
    runs all came from the cache measured nothing new and is not another read,
    and another baseline or other candidates under the label start at 1
    (`_looks_before`)."""

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label, "objective": self.objective, "direction": self.direction,
            "stage": self.stage, "seeds": self.seeds, "instances": self.instances,
            "baseline_id": self.baseline_id, "candidates": self.candidates,
            "prior": self.prior, "run_noise": self.run_noise, "looks": self.looks,
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


def selected_seeds(run_dir: Path, stage: StageConfig) -> set[int]:
    """The seeds the search selected on at `stage`: its own `0 .. seeds-1`,
    and every seed the best was re-evaluated on (`reevaluations.jsonl`)."""
    seeds = set(range(stage.seeds))
    for entry in read_reevaluations(run_dir):
        seed = entry.get("seed")
        if isinstance(seed, int) and not isinstance(seed, bool) and entry.get("stage", stage.id) == stage.id:
            seeds.add(seed)
    return seeds


def _select(rows: list[dict[str, Any]], wanted: str) -> list[dict[str, Any]]:
    """`best`, `top:N`, or a comma-separated list of candidate ids (`_find`)."""
    if wanted == "best" or wanted.startswith("top:"):
        count = 1 if wanted == "best" else int(wanted.split(":", 1)[1])
        ranked = [r for r in rank(rows, len(rows)) if r.get("operator") != SEED_OPERATOR]
        if not ranked:
            raise ValueError("the run has no fully evaluated candidate besides its seed: nothing to confirm")
        return ranked[:count]
    return [_find(rows, spec.strip(), "--candidates") for spec in wanted.split(",") if spec.strip()]


def _looks_before(out_dir: Path, *, baseline_id: str, candidates: Sequence[str]) -> int:
    """How many times this comparison was read before under its label: its
    `comparison.json`'s `looks` (1 for one written before it said so), 0
    with no file -- and 0 when that file compared other configurations
    (another `baseline_id` or other `candidates`: a run continued and checked
    again under `final` compares its new best, whose answer was never read).
    A stopped comparison writes nothing, so it is no look."""
    path = out_dir / "comparison.json"
    if not path.is_file():
        return 0
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        looks = stored.get("looks", 1)
    except (OSError, ValueError, AttributeError):
        return 1
    if stored.get("baseline_id") != baseline_id or list(stored.get("candidates") or []) != list(candidates):
        return 0
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
    family: bool = False,
    log: Callable[[str], None] = print,
    stop: threading.Event | None = None,
) -> Comparison:
    """Run the comparison described in the module docstring; returns it.

    `stop`, once set, stops the runs in flight and starts no other, and the
    comparison ends with `ConfirmStopped` instead of a verdict on half the
    pairs.

    `family`: the candidates are a choice among several (a bake-off), so each
    pays for it (`apply_holm`); `candidates="top:N"` with N > 1 always is."""
    run_dir = Path(run_dir)
    # Pooled with the best's re-evaluations (the ranking `_select` reads); the
    # veto is `rank`'s, as before.
    rows = read_rows(run_dir, holdout_penalty=config.evaluate.holdout_penalty, veto=False)
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
    searched = selected_seeds(run_dir, stage)
    reused = sorted(searched & set(seeds))
    if reused and instances is None:
        log(f"warning: seed(s) {reused} are the ones the search selected on; a confirmation on them is not one")
    if instances is not None:
        stage = _with_instances(config, stage, instances)

    chosen = _select(rows, candidates)
    top = candidates.startswith("top:")
    family = family or (top and len(chosen) > 1)
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
        levels=[level.describe() for level in config.evaluate.score.levels],
        run_noise=_run_noise_of(seed_row, config.evaluate.score.objective),
    )
    comparison.looks = _looks_before(out_dir, baseline_id=comparison.baseline_id,
                                     candidates=comparison.candidates) + 1

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
                     events=events, heartbeat=heartbeat, stop=stop,
                     family=("candidates" if top else "branches") if family else None)
        finally:
            if config.problem.tree is not None:
                _put_trees_back(config, work, log)
    return comparison


def _compare(
    config: Config, comparison: Comparison, stage: StageConfig, jobs: list[Job], made: list[Tree | None], *,
    work: Path, seeds: Sequence[int], label: str, log: Callable[[str], None], events: EventLog,
    heartbeat: Heartbeat, stop: threading.Event | None, family: str | None = None,
) -> None:
    """Every configuration's runs, then the statistics and the files.
    `family` names what the candidates are a choice among (`apply_holm`)."""
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

    if comparison.looks > 1 and comparison.runs and all(r.get("cached") for r in comparison.runs):
        comparison.looks -= 1  # every run came from the cache: nothing new was measured, so no new read
    _analyse(comparison)
    if family:
        apply_holm(comparison.per_candidate, what=family)
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


_SMALLEST_FAILURE_LOG = math.log(1.01)
"""What a failure counts as when nothing in the comparison measured any gain: 1 %."""


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
    is counted as a log gain as large as the largest one measured on this
    candidate's own finished pairs -- a loss when the candidate failed, a win
    when the baseline did -- so a configuration that fails often cannot be
    judged on its lucky runs, and a failure never weighs less than a real
    result. When the candidate's finished pairs barely differ from the
    baseline (its own largest is below `log(1.01)`) and it failed a pair the
    baseline finished, a one-sided pair is sized by the largest gain of any
    candidate's finished pairs in this comparison, and by at least `log(1.01)`
    (1 %): a crash is never a loss of 0 %. A pair only the baseline failed is
    then a win of the same size, so failures that strike both programs alike
    (timeouts, a flaky machine) cancel out. When the candidate failed no pair
    the baseline finished nothing is floored: a pair only the baseline failed
    keeps the candidate's own largest, so a change that does nothing gains
    nothing from the baseline's crashes alone. A pair in
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

    def largest_of(cid: str) -> float | None:
        mine = table.get(cid, {})
        measured = [log_gain(base[k], mine[k], direction) for k in base.keys() & mine.keys()]
        return max((abs(g) for g in measured if g is not None), default=None)

    anywhere = max((g for g in map(largest_of, comparison.candidates) if g is not None), default=0.0)
    for cid in comparison.candidates:
        mine = table.get(cid, {})
        largest = largest_of(cid)  # the size of a loss (the candidate failed) and, the same, of a win (the baseline did)
        planned_keys = [(instance, s) for instance in comparison.instances for s in comparison.seeds]
        if (largest is not None and largest < _SMALLEST_FAILURE_LOG
                and any(k in base and k not in mine for k in planned_keys)):
            # Its finished pairs barely differ from the baseline, and a pair the baseline finished and it did
            # not: its crash still counts as a real loss, at least 1 % (decision 14a). A crash of the baseline
            # is then a win of the same size, so failures that strike both alike cancel out (round 4). With no
            # crash of its own nothing is floored, and the baseline's crashes win only its own largest (R1).
            largest = max(largest, anywhere, _SMALLEST_FAILURE_LOG)
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
        one_sided = {key: totals[key] for key in ("candidate_failed_pairs", "baseline_failed_pairs")}
        summary = verdict_summary(log_gains, direction=direction, failed=one_sided)
        summary["verdict"] += _counted_as(totals, largest, direction)
        if comparison.levels:
            summary["decision_label"] = OBJECTIVE_ALONE  # the levels decide (`_analyse_levels`)
        comparison.per_candidate[cid] = {
            "per_instance": per_instance,
            "log_gains": log_gains,
            "summary": summary,
            "finished_only": verdict_summary(finished_only, direction=direction),
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
            levels = comparison.per_candidate[cid]["levels"] = _analyse_levels(comparison, cid)
            more = failed_more(one_sided)
            if more is not None:  # never "use" when it failed more runs than the starting point (decision 14a)
                levels["verdict"] += f"; {failed_words(more)}"
                if levels["decision"] == "use":
                    levels["decision"], levels["confirmed"] = "keep", False


def _failure_sizes(largest: float | None, direction: str) -> dict[str, float] | None:
    """The imputed pairs in percent, as the comparison used them: a pair only
    the candidate failed counts as `-largest`, a loss of `-pct_from_log(-largest)`;
    one only the baseline failed as `+largest`, a win of `pct_from_log(largest)`.
    Both are the same log size: the candidate's own largest gain, or, when it
    failed a pair beside finished pairs that barely differ, that floored at
    1 %. In percent the two differ (twice the cost is 100 % more, half of it
    50 % less)."""
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

    Per level, the paired gain per (instance, seed), positive when better,
    averaged over the seeds of an instance; over instances the posterior of
    its mean (`verdict_summary`: a mean, a 95 % interval and the chance of a
    gain). The gain is the log ratio of the baseline's and the candidate's
    value, as for the objective -- instances of different size then count
    alike -- shown in percent of the baseline's value, unless some value is 0
    or below: a level is often a count (late tasks, missed tasks) and a ratio
    of 0 is no number, so such a level is compared as a plain difference in
    the KPI's own units, and says so (`unit`). A pair only one side finished
    counts against the side that failed, as large as the largest gain
    measured on that level. A relative tolerance is a fraction of the
    baseline's value (0 when that is 0); an absolute one is in the KPI's units. On a level
    compared in percent an absolute tolerance is taken as that percent of
    the baseline's mean (`100 * t / |mean|`) -- an approximation: the
    interval is per-case log ratios shown in percent, and a case far from
    the mean has its own percent for the same absolute difference.

    Every level but the last has a tolerance `w`, and its 95 % interval
    `[lo, hi]` judges it (two one-sided tests, 1 in 40 per side): *equal*
    when `-w <= lo` and `hi <= w`, *better* when `lo > w`, *worse* when
    `hi < -w`, and *undecided* otherwise -- with fewer than two instances
    too, which have no interval. The first level that is not equal decides;
    an undecided one decides `keep`. The last level has no tolerance: its
    state is the sign of its mean. A level that decides `better` or `worse`
    is read by the rule that decides without levels (`decide` on its
    posterior), so "use" means the same with and without them: `decision`
    is `use`, `worse` or `keep`, `confirmed` is `decision == "use"`, and a
    level's `clear` says that the rule spoke on it; when the rule does not
    speak on the last level, the verdict says it "is not proven either way".
    `read_on` is the level the descent stopped on -- the deciding one, else
    the one that could not be compared, else the last -- whose numbers
    (`worst` the 5 % quantile, `chance`) the page shows and whose per-case
    gains (`per_case`) say whether more runs could help (`runs_can_help`).
    """
    rows: list[dict[str, Any]] = []
    parts: list[str] = []
    deciding: int | None = None
    stopped: int | None = None  # a level that could not be compared at all
    decision = "keep"
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
        unit = ("percent" if shared and baseline_mean and all(base[k] > 0 and mine[k] > 0 for k in shared)
                else "absolute")
        direction = "maximize" if sign > 0 else "minimize"

        def difference(key: tuple[str, int]) -> float:
            if unit == "percent":
                return log_gain(base[key], mine[key], direction)  # type: ignore[return-value]  # both > 0
            return sign * (mine[key] - base[key])

        largest = max((abs(difference(k)) for k in shared), default=None)
        per_instance: list[float] = []
        per_case: list[dict[str, Any]] = []
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
                per_case.append({"instance": instance, "gain": per_instance[-1], "pairs": len(differences),
                                 "seed_var": statistics.variance(differences) if len(differences) >= 2 else None})
        summary = _level_summary(per_instance, unit, direction)
        tolerance = level.get("tolerance")
        tolerance_abs = within = None
        if not last and tolerance is not None:
            relative = bool(level.get("relative"))
            # A fraction of a baseline of 0 is 0: only exactly equal is equal (R4).
            tolerance_abs = float(tolerance) * abs(baseline_mean or 0.0) if relative else float(tolerance)
            if unit == "percent":
                within = 100.0 * float(tolerance) if relative else 100.0 * float(tolerance) / abs(baseline_mean)
            else:
                within = tolerance_abs
        mean, interval = summary["mean"], summary["ci95"]
        if mean is None:
            state = "unknown"
        elif within is not None:
            # Two one-sided tests on the 95 % interval, 2.5 % each side.
            if interval is None:
                state = "undecided"
            elif interval[0] >= -within and interval[1] <= within:
                state = "equal"
            elif interval[0] > within:
                state = "better"
            elif interval[1] < -within:
                state = "worse"
            else:
                state = "undecided"
        else:
            state = "better" if mean > 0 else "worse" if mean < 0 else "equal"
        # The rule that decides without levels, on this level's posterior: "use" or "worse" when it speaks.
        spoke = {"better": "use", "worse": "worse"}.get(state) == summary["decision"]
        clear = spoke if last else state in ("better", "worse")
        rows.append({
            "level": index + 1, "kpi": kpi, "direction": level.get("direction"), "unit": unit,
            "tolerance": tolerance, "relative": bool(level.get("relative")), "tolerance_abs": tolerance_abs,
            "tolerance_in_unit": within, "baseline_mean": baseline_mean, "mean_difference": mean,
            "ci95": interval, "range90": summary["range90"], "worst": summary["worst"], "chance": summary["chance"],
            "n": summary["n"],
            "wins": summary["wins"], "losses": summary["losses"], "state": state, "clear": clear,
            "per_case": per_case,
        })
        if not judging:
            continue  # below the deciding level: reported, not judged
        within_words = (f"{100 * float(tolerance):g} %" if level.get("relative") else f"{float(tolerance):g}"
                        ) if tolerance is not None else ""
        if state == "equal" and not last:
            parts.append(f"equal on level {index + 1} ({kpi} within {within_words})")
        elif state in ("better", "worse"):
            deciding, judging = index + 1, False
            decision = summary["decision"] if spoke else "keep"
            parts.append(f"{state} on level {index + 1} ({kpi}): clear" if clear
                         else f"level {index + 1} ({kpi}) is not proven either way")
        elif state == "undecided":
            deciding, judging = index + 1, False
            parts.append(f"level {index + 1} ({kpi}) is not yet decided within {within_words}")
        elif state == "equal":
            parts.append(f"equal on level {index + 1} ({kpi})")
        else:
            judging, stopped = False, index + 1
            parts.append(f"level {index + 1} ({kpi}): no pair of runs to compare")
    return {"levels": rows, "deciding": deciding, "read_on": deciding or stopped or (len(rows) or None),
            "verdict": ", ".join(parts), "decision": decision, "confirmed": decision == "use"}


def _level_summary(gains: Sequence[float], unit: str, direction: str) -> dict[str, Any]:
    """One level's per-instance gains to its mean, 95 % interval, chance of
    a gain and `decide`'s decision. In percent: `verdict_summary` of the log
    gains, as for the objective. In the KPI's own units: the same posterior
    (`posterior_mean_t`) on the plain differences."""
    if unit == "percent":
        s = verdict_summary(gains, direction=direction)
        return {key: s[key] for key in ("n", "mean", "ci95", "range90", "worst", "chance", "wins", "losses", "decision")}
    n = len(gains)
    out: dict[str, Any] = {"n": n, "mean": fmean(gains) if gains else None, "ci95": None, "range90": None, "worst": None,
                           "chance": None, "wins": sum(1 for g in gains if g > 0),
                           "losses": sum(1 for g in gains if g < 0)}
    if n >= 2:
        post = posterior_mean_t(gains)
        out.update(mean=post.mu, ci95=list(post.interval95()), range90=list(post.interval90()), worst=post.quantile_low(0.05),
                   chance=post.p_above(0.0), posterior={"mu": post.mu, "scale": post.scale, "df": post.df})
    out["decision"] = decide(out)
    out.pop("posterior", None)
    return out


def _counted_as(totals: dict[str, int], largest: float | None, direction: str) -> str:
    """What the verdict has to say about pairs that did not simply finish.
    `largest` is the log size of a loss and of a win (`_failure_sizes`);
    each imputed pair is said in the percent it was counted as."""
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


def comparison_of(data: dict[str, Any]) -> Comparison:
    """A `Comparison` from its `to_json` (a `comparison.json` read back), so its
    `comparison.md` can be written again after `apply_holm` changed a result."""
    names = {f.name for f in fields(Comparison)}
    return Comparison(**{key: value for key, value in data.items() if key in names})


def render_markdown(comparison: Comparison) -> str:
    """`comparison.md`. A comparison written before A1 (no candidate's summary
    carries the posterior, `is_posterior`) keeps the page it had: the rule it
    was decided by (`LEGACY_RULE_WORDS`), its interval and its Wilcoxon test."""
    summaries = [r.get("summary") or {} for r in comparison.per_candidate.values()]
    legacy = bool(summaries) and not any(is_posterior(s) for s in summaries)
    lines = [
        f"# Confirmation `{comparison.label}`",
        "",
        f"Stage `{comparison.stage}`, objective `{comparison.objective}` ({comparison.direction}), "
        f"seeds {comparison.seeds}, {len(comparison.instances)} instance(s). Baseline: `{comparison.baseline_id}`. "
        "Improvement is the log ratio of the baseline's and the candidate's value on the same instance and "
        "seed, averaged over the seeds of an instance, and shown in percent of the baseline; positive is better. "
        f"The rule: {LEGACY_RULE_WORDS if legacy else RULE_WORDS} The answer was read {comparison.looks} time(s).",
        "",
    ]
    for cid, result in comparison.per_candidate.items():
        s = result["summary"]
        lines += [f"## `{cid}` against the baseline", ""]
        holm_info = result.get("holm")
        if holm_info:
            # Kinds of case are judged together, each its own answer, not chosen among.
            together = (f"Judged with the {holm_info['k']} {holm_info['what']} together"
                        if holm_info["what"] == "kinds of case" else f"Chosen among {holm_info['k']} {holm_info['what']}")
            lines += [f"{together} (Holm): "
                      f"{'passes' if holm_info['passed'] else 'does not pass'} the rule's chance at its stricter level.", ""]
        if result.get("levels"):
            lines += _levels_markdown(result["levels"], LEGACY_RULE_WORDS if legacy else RULE_WORDS,
                                      verdict=_levels_verdict(result))
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
        held = result.get("holm") or {}
        said = s["verdict"]
        if held.get("passed") is False and held.get("own") == "use":
            said = f"not proven among {held['k']} {held['what']} (Holm)"  # its own chance passes; chosen among rivals it does not
        verdict = said[:1].upper() + said[1:]
        if not is_posterior(s) and s.get("chance") is not None:
            # A summary written before A1: its head as it was then (a t interval and a Wilcoxon test).
            interval = f"[{s['ci95'][0]:+.3f}, {s['ci95'][1]:+.3f}]" if s.get("ci95") else "n/a"
            p = (s.get("wilcoxon") or {}).get("p")
            head = (f"**Mean improvement {s['mean']:+.3f} %**, 95 % CI {interval} over {s['n']} instance(s); "
                    f"{s['wins']} better, {s['losses']} worse; Wilcoxon signed-rank p = "
                    + ("n/a" if p is None else f"{p:.4f}") + f". **{verdict}.**")
            lines += [head, "", counted, "", "| instance | baseline | candidate | improvement | pairs won | dropped |",
                      "|---|--:|--:|--:|--:|--:|", *_instance_rows(result["per_instance"])]
            continue
        if s.get("chance") is not None and s.get("ci95"):
            interval = f"[{s['ci95'][0]:+.3f}, {s['ci95'][1]:+.3f}]"
            head = (
                f"**Mean improvement {s['mean']:+.3f} %** (the paired mean, the posterior's), "
                f"95 % credible interval {interval} over {s['n']} instance(s); "
                f"{s['wins']} better, {s['losses']} worse; chance of a gain {_chance2(s['chance']):.2f}, "
                f"of losing more than {USE_MARGIN_PCT:g} % {1 - s['chance_margin']:.2f} (for engineers: it does "
                f"not decide); prior {s.get('prior', PRIOR)}. **{verdict}.**"
            )
        else:
            head = f"**Mean improvement {s['mean']:+.3f} %** over {s['n']} instance(s). **{verdict}.**"
        if isinstance(result.get("levels"), dict):
            # Goals in order of importance decide: their answer is the bold one, the objective's is the engineers'.
            answer = _LEVELS_ANSWER.get(decision_of(result), "Not proven")
            head = (f"**{answer}: {_levels_verdict(result)}.**\n\n"
                    f"{OBJECTIVE_ALONE[:1].upper() + OBJECTIVE_ALONE[1:]}: "
                    + head.replace("**", "").replace(f" {verdict}.", "").replace("Mean improvement", "mean improvement", 1))
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
        lines += _instance_rows(result["per_instance"])
    return "\n".join(lines)


_LEVELS_ANSWER = {"use": "Probably better", "worse": "Probably WORSE", "keep": "Not proven"}
"""The bold answer of a comparison by goals in order of importance, before its levels' verdict."""


def _instance_rows(per_instance: Sequence[dict[str, Any]]) -> list[str]:
    """The per-instance table's rows, and the blank line after it."""
    lines = []
    for row in per_instance:
        dropped = _dropped(row)
        if row["improvement_pct"] is None:
            lines.append(f"| {row['instance']} | – | – | – | 0 of 0 | {dropped} |")
        else:
            lines.append(
                f"| {row['instance']} | {row['baseline']:.6g} | {row['candidate']:.6g} | "
                f"{row['improvement_pct']:+.3f} % | {row['pair_wins']} of {row['pairs']} | {dropped} |"
            )
    lines.append("")
    return lines


def _levels_verdict(result: dict[str, Any]) -> str:
    """A levels result's verdict, and why it is not the levels' answer when `decision_of` says
    otherwise (R2): every run gave the starting point's result, or Holm held it back."""
    levels, said = result["levels"], str(result["levels"]["verdict"])
    if decision_of(result) == levels.get("decision"):
        return said
    held = result.get("holm") or {}
    if result.get("identical"):
        return f"{said}; every run gave the starting point's result"
    if held.get("passed") is False:
        return f"{said}; not proven among {held['k']} {held['what']} (Holm)"
    return said


def _levels_markdown(levels: dict[str, Any], rule: str = RULE_WORDS, *, verdict: str | None = None) -> list[str]:
    """The lexicographic verdict (`verdict`, else the levels' own) and one row per level
    (`_analyse_levels`); `rule` is the rule the comparison was decided by, in words."""

    def number(value: Any, unit: str, signed: bool = False) -> str:
        if value is None:
            return "–"
        text = f"{value:+.4g}" if signed else f"{value:.4g}"
        return text + " %" if unit == "percent" else text

    verdict = verdict or levels["verdict"]
    lines = [
        "### Level by level",
        "",
        f"**{verdict[:1].upper() + verdict[1:]}.** A level is equal when its 95 % interval lies within "
        "its tolerance, better or worse when the interval lies wholly beyond it, and not yet decided "
        "otherwise; the first level that is not equal decides, and an undecided one keeps today's. "
        "The last level has no tolerance: the sign of its mean says better or worse. The level that "
        f"decides is read by the rule: {rule}",
        "",
        "Differences are per instance, averaged over the seeds, positive is better: the log ratio "
        "shown in percent of the baseline's value, or a plain difference in the KPI's own units where "
        "some value is 0 or below.",
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
