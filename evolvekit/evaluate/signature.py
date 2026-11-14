"""Behaviour signatures: never pay twice for a program that does the same thing.

The AST novelty filter (`search/novelty.py`) catches a child that is its parent
*written* differently. It cannot catch a child that is its parent *expressed*
differently -- a monotone re-expression of the same decision rule. The first
real Phase B run (2026-08-22/23, 18 paid calls, $0.83) produced five of them:
`-(b - item) ** 1.01`, `-(b - item) ** 1.5`, `item - b` and friends all pack
every instance exactly the way best fit does, so all five scored the seed's
score to the last digit, all five were promoted to the full stage, and all five
entered the archive. On the bin-packing proxy that costs milliseconds. On the
VRP target problem a full-stage evaluation costs hours.

So: after every *command* stage, fingerprint what the candidate actually did.

* The fingerprint is built from that stage's KPIs, the numbers the evaluator
  itself reported -- nothing problem-specific is hard-coded here.
* Each value is rounded to `evaluate.signature_digits` significant digits
  (default 9) so that floating-point noise in the last bits cannot make two
  identical behaviours look different.
* KPIs in `evaluate.signature_ignore` are left out. The default list is
  `runtime_s`, `complexity` and `static_problems`: timing is not behaviour, and
  size is not behaviour either -- two blocks of different lengths that make the
  same decisions are exactly the case this module exists to catch.
* A list-valued KPI (`bins_per_instance`, say) is included element by element,
  which makes the signature per-instance rather than per-mean and therefore
  much harder to collide with by accident.

A candidate whose signature at a stage equals one already seen at that stage is
a **behavioural duplicate**: it keeps its KPIs and its score in `runs.jsonl` for
the record, but it is not promoted to the next stage and it never enters the
archive.

**A noisy stage: twins by the run's noise, not by the digits** (A2.2). A
stochastic evaluator never fingerprints identically -- a different random
restart, a solver that stopped a microsecond later, and the eighth digit has
moved -- and rounding to fewer digits is no answer: three digits call 0.3 %
apart a twin under a 0.1 % noise and 1.5 % apart novel under a 2 % noise. So
on a stage that is *not repeatable* and has a noise estimate
(`evaluate/noise.py`), the child is compared number by number:

* What is compared is the evaluator's own numbers, one representation of each
  measure, in raw units (`compared_numbers`): on a per-instance stage the
  `<kpi>_per_instance` vectors and nothing derived from them; without
  instances every scalar (but `<obj>_raw`) and every list the evaluator
  reported. `signature_ignore` applies.
* Each noisy number gets the margin `c_m · σ̂ · √(2/seeds)` (`twin_margins`):
  σ̂ one run's noise of that number, `√(2/seeds)` turns it into the sd of the
  difference of two means of `seeds` runs, and `c_m` (`spread_factor`) is
  chosen so that a true twin -- all m noisy numbers off by noise only -- is
  within on every one with probability erf(1), about 84 %, however many
  numbers there are (`c_1 = √2`: the design's `2·σ̂/√seeds`). σ̂ is estimated
  (2 degrees of freedom from a calibration), so `c_m` is Student's t quantile
  at the same probability with σ̂'s df (`stats.t_scale`, Decision 17b): one
  number is then caught in 84 % at any df, and m numbers whose σ̂ share one
  estimate in at least about 84 % (their misses are positively correlated).
  A number with no noise known is compared exactly, at `signature_digits`.
* A child within the margins of an earlier candidate is its twin only when
  that candidate's stage score is at least the child's (`check_values`): it
  behaves like something already seen that did at least as well, and is "of"
  the best such. A child within the noise of everything but ahead of all of it
  is evaluated -- on a noisy stage that is the only way to learn whether it is
  better.

On a repeatable stage (same program, same seed, same numbers) two programs
that behave alike give equal numbers on the same seeds, so the 9-digit hash
stays the rule there, as it does with no noise estimate.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import TYPE_CHECKING, Iterable, Mapping, Sequence

from evolvekit.stats import t_scale

if TYPE_CHECKING:  # annotations only: this module stays a leaf
    from evolvekit.evaluate.types import StageOutcome

__all__ = [
    "round_significant",
    "behaviour_signature",
    "Behaviour",
    "BehaviourIndex",
    "BehaviourVerdict",
    "compared_numbers",
    "spread_factor",
    "twin_margins",
    "within",
]

ERF1 = math.erf(1.0)
"""The catch rate of a true twin on one number at the design's 2σ rule: the
difference of two runs is N(0, 2σ²), and P(|N(0, 2σ²)| <= 2σ) = P(|Z| <= √2)
= erf(1) = 84.27 %. `spread_factor` keeps that rate for m numbers."""

# The defaults for `digits` and `ignore` live in `evolvekit.config`
# (`DEFAULT_SIGNATURE_DIGITS`, `DEFAULT_SIGNATURE_IGNORE`) so that there is
# exactly one place to read them off; this module is a leaf and imports
# nothing from the rest of the package.


def round_significant(value: float, digits: int) -> float:
    """Round to `digits` significant digits. 0, inf and nan pass through."""
    number = float(value)
    if not math.isfinite(number) or number == 0.0:
        return number
    if digits < 1:
        digits = 1
    exponent = math.floor(math.log10(abs(number)))
    return round(number, -(exponent - digits + 1))


def _canonical(value: float, digits: int) -> object:
    rounded = round_significant(value, digits)
    if math.isnan(rounded):
        return "nan"
    if math.isinf(rounded):
        return "inf" if rounded > 0 else "-inf"
    # repr() of a rounded float is stable across runs and platforms, and unlike
    # a raw float it survives the JSON round-trip byte for byte.
    return repr(rounded)


def behaviour_signature(
    kpis: Mapping[str, float],
    vectors: Mapping[str, Sequence[float]] | None = None,
    *,
    ignore: Iterable[str],
    digits: int,
) -> str:
    """A stable 16-hex fingerprint of one stage's reported behaviour.

    Returns `""` when nothing survives the ignore list: a stage that reports
    only runtime has said nothing about behaviour, and an empty signature is
    never a twin of anything.
    """
    skip = set(ignore)
    scalars = {
        str(k): _canonical(float(v), digits)
        for k, v in kpis.items()
        if str(k) not in skip
    }
    listed = {
        str(k): [_canonical(float(x), digits) for x in v]
        for k, v in (vectors or {}).items()
        if str(k) not in skip
    }
    if not scalars and not listed:
        return ""
    payload = json.dumps(
        {"kpis": scalars, "vectors": listed}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class BehaviourVerdict:
    """The answer to "has anything already behaved exactly like this here?"."""

    stage_id: str
    signature: str = ""
    twin_id: str | None = None

    @property
    def duplicate(self) -> bool:
        return self.twin_id is not None

    @property
    def reason(self) -> str | None:
        if self.twin_id is None:
            return None
        return f"behavioural duplicate of {self.twin_id} at stage {self.stage_id}"


@dataclass(frozen=True)
class Behaviour:
    """The numbers the noise rule compares at one stage, and the stage score."""

    kpis: Mapping[str, float]
    vectors: Mapping[str, Sequence[float]]
    score: float = 0.0
    """The candidate's score at that stage: a twin must have done at least as well."""

    def to_json(self) -> dict:
        """`behaviour_values[<stage>]` of a row: what a resumed run compares with."""
        return {
            "kpis": dict(self.kpis),
            "vectors": {k: list(v) for k, v in self.vectors.items()},
            "score": float(self.score),
        }

    @classmethod
    def from_json(cls, raw: object) -> "Behaviour | None":
        """A row's `behaviour_values[<stage>]` back, or `None` when it is not
        of that shape (one bad entry costs that entry, not the resume)."""
        if not isinstance(raw, dict):
            return None
        try:
            kpis = {str(k): float(v) for k, v in dict(raw.get("kpis") or {}).items()}
            vectors = {str(k): [float(x) for x in v] for k, v in dict(raw.get("vectors") or {}).items()}
            score = float(raw.get("score", 0.0))
        except (TypeError, ValueError):
            return None
        return cls(kpis, vectors, score)


def compared_numbers(
    outcome: "StageOutcome", *, ignore: Iterable[str], per_instance: bool, objective: str
) -> Behaviour:
    """The evaluator's own numbers at one stage, one representation of each
    measure, in raw units (Decision 3). With instances: the
    `<kpi>_per_instance` vectors only -- every stage-level scalar is the mean
    of one, and `<obj>_pct_of_baseline` is derived. Without: every scalar but
    `<obj>_raw`, and every list. A KPI in `ignore` is left out, its
    `_per_instance` vector with it. The score is the outcome's own (0 when it
    has none: the caller sets the stage score)."""
    skip = set(ignore)
    suffix = "_per_instance"
    if per_instance:
        vectors = {
            k: [float(x) for x in v]
            for k, v in outcome.vector_kpis.items()
            if k.endswith(suffix) and k not in skip and k[: -len(suffix)] not in skip
        }
        return Behaviour({}, vectors, float(outcome.score or 0.0))
    kpis = {k: float(v) for k, v in outcome.kpis.items() if k not in skip and k != f"{objective}_raw"}
    vectors = {k: [float(x) for x in v] for k, v in outcome.vector_kpis.items() if k not in skip}
    return Behaviour(kpis, vectors, float(outcome.score or 0.0))


def spread_factor(m: int) -> float:
    """`c_m`: with m independent noisy numbers, each difference N(0, 2σ_i²),
    all m are within `c_m·σ_i·√2` with probability erf(1) (about 84 %), the
    rate one number has at the design's 2σ: P(|Z| <= c_m)^m = erf(1), so
    `c_m = Φ⁻¹((1 + erf(1)^(1/m)) / 2)`. `c_1 = √2` exactly."""
    if m <= 1:
        return math.sqrt(2.0)
    return NormalDist().inv_cdf((1.0 + ERF1 ** (1.0 / m)) / 2.0)


def _noise_of(
    value: float, sd: float | None, cv: float | None, sd_df: float = math.inf, cv_df: float = math.inf
) -> tuple[float, float]:
    """One number's one-run noise and the df it is estimated on: its own when
    known and > 0, else the relative noise times its size, else 0 (exact)."""
    if sd is not None and sd > 0:
        return float(sd), float(sd_df)
    if cv is not None and cv > 0:
        return float(cv) * abs(float(value)), float(cv_df)
    return 0.0, math.inf


def twin_margins(
    behaviour: Behaviour,
    sds: Mapping[str, float],
    vector_sds: Mapping[str, Sequence[float | None]],
    seeds: int,
    *,
    cvs: Mapping[str, float] = {},
    dfs: Mapping[str, float] = {},
    vector_dfs: Mapping[str, Sequence[float]] = {},
    cv_dfs: Mapping[str, float] = {},
) -> dict[str, float | list[float]]:
    """Per compared number, how far another candidate's may lie and still be
    the same behaviour: `c_m · σ̂ · √(2/seeds)`, m the count of numbers with a
    σ̂ > 0. σ̂ is `sds[k]` for a scalar and `vector_sds[key][i]` for an element;
    where that is 0 or unknown but `cvs[key]` is known, `cv · |value|` (a
    calibration measured one instance; the others are known relatively).
    A number with no σ̂ at all gets 0.0: compared exactly.

    `dfs`, `vector_dfs` and `cv_dfs` are the degrees of freedom of those
    estimates (`Noise.df`, `vector_df`, `cv_df`; missing: infinite, a known
    σ): `c_m` becomes Student's t quantile at the same probability with the
    number's own df, `c_m · stats.t_scale(c_m, df)`, which is `c_m` itself at
    infinite df."""
    noise: dict[str, tuple[float, float] | list[tuple[float, float]]] = {}
    for k, value in behaviour.kpis.items():
        noise[k] = _noise_of(value, sds.get(k), cvs.get(k), dfs.get(k, math.inf), cv_dfs.get(k, math.inf))
    for k, values in behaviour.vectors.items():
        known = list(vector_sds.get(k) or [])
        known_df = list(vector_dfs.get(k) or [])
        noise[k] = [
            _noise_of(
                v,
                known[i] if i < len(known) else None,
                cvs.get(k),
                known_df[i] if i < len(known_df) else math.inf,
                cv_dfs.get(k, math.inf),
            )
            for i, v in enumerate(values)
        ]
    m = sum(1 for s in noise.values() for x, _ in (s if isinstance(s, list) else [s]) if x > 0)
    c_m = spread_factor(m)
    per_seeds = math.sqrt(2.0 / max(1, int(seeds)))

    def margin(number: tuple[float, float]) -> float:
        sd, df = number
        return c_m * t_scale(c_m, df) * per_seeds * sd if sd > 0 else 0.0

    return {
        k: [margin(x) for x in s] if isinstance(s, list) else margin(s)
        for k, s in noise.items()
    }


def within(
    a: Behaviour, b: Behaviour, margins: Mapping[str, float | Sequence[float]], digits: int
) -> bool:
    """The same measures on both sides, and every number within its margin
    (`|a - b| <= margin` where the margin is > 0, else equal at `digits`
    significant digits)."""
    if set(a.kpis) != set(b.kpis) or set(a.vectors) != set(b.vectors):
        return False

    def close(x: float, y: float, margin: float) -> bool:
        if margin > 0:
            return abs(float(x) - float(y)) <= margin
        return round_significant(x, digits) == round_significant(y, digits)

    for k, x in a.kpis.items():
        margin = margins.get(k, 0.0)
        if not close(x, b.kpis[k], float(margin) if not isinstance(margin, (list, tuple)) else 0.0):
            return False
    for k, xs in a.vectors.items():
        ys = b.vectors[k]
        if len(xs) != len(ys):
            return False
        margin = margins.get(k, ())
        per = list(margin) if isinstance(margin, (list, tuple)) else [float(margin)] * len(xs)
        for i, (x, y) in enumerate(zip(xs, ys)):
            if not close(x, y, per[i] if i < len(per) else 0.0):
                return False
    return True


@dataclass
class BehaviourIndex:
    """Every `(stage, signature)` the run has seen, mapped to the first id,
    and per stage every candidate's compared numbers (`values`).

    Keyed by stage because a proxy stage and a full stage report different
    numbers for the same program; comparing across them would be nonsense.
    The seed is in here like anything else, which is the point -- five of the
    first real run's paid children were the seed wearing a different hat.
    """

    by_key: dict[tuple[str, str], str] = field(default_factory=dict)
    values: dict[str, dict[str, Behaviour]] = field(default_factory=dict)
    """Per stage, per candidate in the order they were added, what the noise
    rule compares (`check_values`). Empty for a candidate from a run written
    before it: that one is a twin target by its hash only."""
    generations: dict[str, int] = field(default_factory=dict)
    """Per candidate, the generation (round) it was evaluated in, when known:
    the noise rule compares a child only with earlier rounds' entries
    (Decision 15). An entry of unknown round counts as earlier."""

    def check(self, stage_id: str, signature: str) -> BehaviourVerdict:
        if not signature:
            return BehaviourVerdict(stage_id=stage_id)
        return BehaviourVerdict(
            stage_id=stage_id,
            signature=signature,
            twin_id=self.by_key.get((stage_id, signature)),
        )

    def check_values(
        self,
        stage_id: str,
        behaviour: Behaviour,
        margins: Mapping[str, float | Sequence[float]],
        digits: int,
        *,
        exclude: str | None = None,
        before: int | None = None,
        exact_digits: int | None = None,
    ) -> BehaviourVerdict:
        """The noise rule (Decision 2): among the entries `within` the margins
        whose stage score is at least the child's, the one with the highest
        score (ties: the first added). None: not a twin -- a child within the
        noise of everything but ahead of all of it is evaluated. `exclude`: the
        child's own id, should it already be in the index. `before`: the
        child's round; entries of earlier rounds are compared within the
        margins, those of its own round (Decision 15) only for numbers equal at
        `exact_digits` (an exact duplicate is a twin in any order; with
        `exact_digits` None, not at all), so the order of evaluation inside a
        round decides nothing a margin would."""
        best_id: str | None = None
        best_score = -math.inf
        for cid, entry in self.values.get(stage_id, {}).items():
            if cid == exclude or entry.score < behaviour.score:
                continue
            if best_id is not None and entry.score <= best_score:
                continue
            same_round = before is not None and self.generations.get(cid, before - 1) >= before
            if same_round:
                if exact_digits is not None and within(behaviour, entry, {}, exact_digits):
                    best_id, best_score = cid, entry.score
                continue
            if within(behaviour, entry, margins, digits):
                best_id, best_score = cid, entry.score
        return BehaviourVerdict(stage_id=stage_id, twin_id=best_id)

    def add(
        self,
        candidate_id: str,
        stage_id: str,
        signature: str,
        behaviour: Behaviour | None = None,
        *,
        generation: int | None = None,
    ) -> None:
        """Remember `signature` as first produced by `candidate_id`, and what
        it did (`behaviour`), the latest for that id, evaluated in round
        `generation` (when given)."""
        if generation is not None:
            self.generations[candidate_id] = int(generation)
        if signature:
            self.by_key.setdefault((stage_id, signature), candidate_id)
        if behaviour is not None:
            self.values.setdefault(stage_id, {})[candidate_id] = behaviour
