"""The candidate record and the EVOLVE-BLOCK splicing that produces one.

The record carries `parent_id`, `inspiration_ids`, `operator` and `model` from
day one even though Phase A only ever fills the first and third: Phase B's
MAP-Elites archive and crossover operator read exactly these fields, and
back-filling a JSONL log after the fact is not a thing anyone enjoys.
"""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "Candidate",
    "BlockError",
    "extract_block",
    "splice_block",
    "complexity_of",
    "block_hash",
]


class BlockError(ValueError):
    """The skeleton's EVOLVE-BLOCK markers are missing or malformed."""


def extract_block(source: str, start: str, end: str) -> tuple[str, str, str]:
    """Split `source` into (prefix, block, suffix) around the marker pair."""
    starts = [i for i, line in enumerate(source.splitlines()) if line.strip() == start]
    ends = [i for i, line in enumerate(source.splitlines()) if line.strip() == end]
    if len(starts) != 1 or len(ends) != 1:
        raise BlockError(
            f"expected exactly one {start!r} and one {end!r} line, "
            f"found {len(starts)} and {len(ends)}"
        )
    if ends[0] <= starts[0]:
        raise BlockError(f"{end!r} appears before {start!r}")
    lines = source.splitlines(keepends=True)
    prefix = "".join(lines[: starts[0] + 1])
    block = "".join(lines[starts[0] + 1 : ends[0]])
    suffix = "".join(lines[ends[0] :])
    return prefix, block, suffix


def splice_block(source: str, new_block: str, start: str, end: str) -> str:
    """Return `source` with its evolve block replaced by `new_block`."""
    prefix, _, suffix = extract_block(source, start, end)
    body = new_block if new_block.endswith("\n") else new_block + "\n"
    return prefix + body + suffix


def complexity_of(block: str) -> int:
    """AST node count -- the Phase B archive's second grid axis."""
    try:
        return sum(1 for _ in ast.walk(ast.parse(block)))
    except SyntaxError:
        return -1


def block_hash(block: str) -> str:
    """Stable 12-hex fingerprint of a block, for de-duplication."""
    normalised = "\n".join(
        line.rstrip() for line in block.strip().splitlines() if line.strip()
    )
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:12]


@dataclass
class Candidate:
    """One point in the search space, plus everything the ledger records."""

    id: str
    generation: int
    block: str
    source: str
    operator: str
    model: str = ""
    provider: str = ""
    parent_id: str | None = None
    inspiration_ids: list[str] = field(default_factory=list)
    role: str = "small"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    # filled in by the cascade
    score: float | None = None
    rejected: bool = False
    reject_reason: str | None = None
    kpis: dict[str, float] = field(default_factory=dict)
    kpi_cv: dict[str, float] = field(default_factory=dict)
    """How much each KPI moved between the runs of a `seeds: N` stage. Empty
    when every stage ran once, which is the deterministic-evaluator case."""
    penalty_total: float = 0.0
    stage_scores: dict[str, float] = field(default_factory=dict)
    stages_reached: list[str] = field(default_factory=list)
    public_score: float | None = None
    private_score: float | None = None
    generalization_gap: float | None = None
    ranking_score: float | None = None
    """What the search ranks by: the public score discounted by the hold-out
    gap (`evaluate.holdout_penalty`). The raw scores above are never touched."""
    last_failure: str | None = None
    feedback: dict[str, str] = field(default_factory=dict)
    """What each command stage's evaluator said in prose, keyed by stage id. A
    failure artefact says why a candidate did not run; this says what it did
    when it did. The deepest non-empty entry is what the next prompt shows."""
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0
    # -- Phase B: archive placement and the novelty filter -----------------
    cell: list[int] | None = None
    novelty: str | None = None
    """`None`; `"no_op"` (structurally identical to its parent) or
    `"duplicate"` (structurally identical to another archived candidate),
    both caught before any evaluation; `"behavioural"` -- structurally
    different, but it produced identical KPIs to an earlier candidate at some
    stage, so it was stopped there; or `"near"` -- similar to but not identical
    to an earlier candidate, which is the one verdict that does **not** reject:
    a near-duplicate is evaluated and flagged."""
    twin_id: str | None = None
    near_twin_id: str | None = None
    """The archived candidate this one came closest to, when the similarity
    gate flagged it. Set together with `similarity` and `novelty="near"`."""
    similarity: float | None = None
    """Cosine similarity to `near_twin_id`, by whichever method
    `search.novelty.near.method` names."""
    behaviour_twin_id: str | None = None
    """The earlier candidate whose behaviour this one reproduced exactly."""
    behaviour_signatures: dict[str, str] = field(default_factory=dict)
    """One fingerprint per command stage reached, keyed by stage id."""

    @property
    def complexity(self) -> int:
        return complexity_of(self.block)

    @property
    def block_hash(self) -> str:
        return block_hash(self.block)

    @property
    def fitness(self) -> float | None:
        """The quantity the archive, the parent sampler and the leaderboard use."""
        return self.ranking_score if self.ranking_score is not None else self.score

    def to_record(self) -> dict[str, Any]:
        """Flat JSON row for `runs.jsonl` -- the source file itself is not inlined."""
        record = asdict(self)
        record.pop("source", None)
        record["complexity"] = self.complexity
        record["block_hash"] = self.block_hash
        return record

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Candidate":
        """Rebuild a candidate from a `runs.jsonl` row.

        `source` is not stored, so it comes back empty; the archive only ever
        needs the block, the scores and the lineage. Splice the block back into
        the skeleton to get a runnable module.
        """
        fields = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        data = {k: v for k, v in record.items() if k in fields}
        data.setdefault("source", "")
        for key in ("id", "generation", "block", "operator"):
            if key not in data:
                raise BlockError(f"run record is missing {key!r}")
        return cls(**data)  # type: ignore[arg-type]
