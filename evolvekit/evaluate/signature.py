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
* On a stage with `seeds: N > 1` the KPIs the fingerprint sees are the *means*
  over the N runs, and they are rounded to `evaluate.signature_digits_stochastic`
  (default 3) instead. A stochastic evaluator never fingerprints identically:
  a different random restart, a tie broken by a different hash order, a solver
  that stopped a microsecond later, and the eighth digit has already moved. At
  nine digits every candidate on such a stage is novel and this filter quietly
  stops filtering -- on the VRP problem, where one full-stage evaluation is
  2.5 solver-hours, that is the most expensive way to learn a lesson this
  module already knows. Three significant digits on a mean of N runs is a claim
  about the program; nine is a claim about the dice.
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
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

__all__ = [
    "round_significant",
    "behaviour_signature",
    "BehaviourIndex",
    "BehaviourVerdict",
]

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


@dataclass
class BehaviourIndex:
    """Every `(stage, signature)` the run has seen, mapped to the first id.

    Keyed by stage because a proxy stage and a full stage report different
    numbers for the same program; comparing across them would be nonsense.
    The seed is in here like anything else, which is the point -- five of the
    first real run's paid children were the seed wearing a different hat.
    """

    by_key: dict[tuple[str, str], str] = field(default_factory=dict)

    def check(self, stage_id: str, signature: str) -> BehaviourVerdict:
        if not signature:
            return BehaviourVerdict(stage_id=stage_id)
        return BehaviourVerdict(
            stage_id=stage_id,
            signature=signature,
            twin_id=self.by_key.get((stage_id, signature)),
        )

    def add(self, candidate_id: str, stage_id: str, signature: str) -> None:
        """Remember `signature` as first produced by `candidate_id`."""
        if signature:
            self.by_key.setdefault((stage_id, signature), candidate_id)
