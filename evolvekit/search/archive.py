"""MAP-Elites-lite: one elite per cell, plus a global top-k.

The grid is keyed by whatever KPIs `search.archive.descriptors` names -- the
proposal's "(axes-signature, complexity-bucket)" generalised, because the axes
that matter are problem-specific and `complexity` is only ever *one* of them.
Every descriptor is a KPI the evaluator already emits, so adding an axis costs
one line of YAML and no code.

Two things live here that Phase A had no home for:

* **Diversity pressure.** A candidate only displaces the elite of its own cell,
  so a run cannot collapse onto one lineage the way Phase A's "best-so-far or a
  random top-5" did.
* **Parent sampling.** ShinkaEvolve's `sigmoid(z(fitness)) x 1/(1 + children)`:
  good elites are preferred, and every child drawn from a parent makes that
  parent a little less attractive. The z-score keeps the sigmoid meaningful
  whatever units the objective happens to be in.

Fitness here is always the *ranking* score (public minus the hold-out penalty),
never the raw public score.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from statistics import fmean, pstdev
from typing import Any, Iterable, Sequence

from evolvekit.candidate import Candidate
from evolvekit.config import ArchiveConfig, DescriptorConfig

__all__ = ["Archive", "Axis", "Cell", "Placement", "descriptor_value", "sigmoid"]


def sigmoid(x: float) -> float:
    if x < -60.0:
        return 0.0
    if x > 60.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def descriptor_value(candidate: Candidate, kpi: str) -> float:
    """The candidate's value on one descriptor axis.

    KPIs come from the evaluator. `complexity` is special only in that the
    candidate can compute it from its own block when no stage has run yet.
    """
    value = candidate.kpis.get(kpi)
    if value is None and kpi == "complexity":
        value = float(candidate.complexity)
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


@dataclass
class Axis:
    """One descriptor axis, with the range it has actually seen."""

    kpi: str
    bins: int
    lo: float | None
    hi: float | None
    auto: bool

    @staticmethod
    def from_config(cfg: DescriptorConfig) -> "Axis":
        return Axis(kpi=cfg.kpi, bins=cfg.bins, lo=cfg.lo, hi=cfg.hi, auto=cfg.auto)

    def observe(self, value: float) -> bool:
        """Widen an auto axis. Returns True when the range moved."""
        if not self.auto:
            return False
        lo = value if self.lo is None else min(self.lo, value)
        hi = value if self.hi is None else max(self.hi, value)
        moved = (lo, hi) != (self.lo, self.hi)
        self.lo, self.hi = lo, hi
        return moved

    def index(self, value: float) -> int:
        lo = 0.0 if self.lo is None else self.lo
        hi = 0.0 if self.hi is None else self.hi
        if not hi > lo:
            return 0
        scaled = (value - lo) / (hi - lo) * self.bins
        return max(0, min(self.bins - 1, int(scaled)))

    def label(self, index: int) -> str:
        lo = 0.0 if self.lo is None else self.lo
        hi = 0.0 if self.hi is None else self.hi
        if not hi > lo:
            return f"{lo:g}"
        width = (hi - lo) / self.bins
        return f"{lo + index * width:g}..{lo + (index + 1) * width:g}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kpi": self.kpi,
            "bins": self.bins,
            "range": [self.lo, self.hi],
            "auto": self.auto,
        }


@dataclass
class Cell:
    coord: tuple[int, ...]
    elite_id: str
    fitness: float
    occupants: int = 1

    def to_dict(self, archive: "Archive") -> dict[str, Any]:
        elite = archive.members.get(self.elite_id)
        return {
            "coord": list(self.coord),
            "label": archive.cell_label(self.coord),
            "elite": self.elite_id,
            "fitness": self.fitness,
            "score": None if elite is None else elite.score,
            "occupants": self.occupants,
            "children": archive.children.get(self.elite_id, 0),
        }


@dataclass
class Placement:
    """What happened when a candidate was offered to the archive."""

    coord: tuple[int, ...] | None = None
    inserted: bool = False
    became_elite: bool = False
    displaced: str | None = None


@dataclass
class Archive:
    """The grid, its members and their lineage. Small enough to keep in memory."""

    axes: list[Axis]
    top_k: int = 20
    cells: dict[tuple[int, ...], Cell] = field(default_factory=dict)
    members: dict[str, Candidate] = field(default_factory=dict)
    children: dict[str, int] = field(default_factory=dict)
    no_ops: int = 0
    duplicates: int = 0
    near_duplicates: int = 0
    """Children the similarity gate flagged. Unlike the two above they were
    still evaluated, so this is a note about prompt quality, not wasted
    evaluator time."""

    # -- construction ----------------------------------------------------

    @staticmethod
    def from_config(config: ArchiveConfig) -> "Archive":
        return Archive(
            axes=[Axis.from_config(d) for d in config.descriptors],
            top_k=config.top_k,
        )

    @staticmethod
    def from_records(
        records: Iterable[dict[str, Any]], config: ArchiveConfig
    ) -> "Archive":
        """Rebuild the grid from `runs.jsonl` -- the resume path.

        `archive.json` is a snapshot; the JSONL is the truth. Rebuilding from
        the log means a run that died between the last append and the last
        snapshot still comes back consistent.
        """
        archive = Archive.from_config(config)
        for record in records:
            candidate = Candidate.from_record(record)
            if candidate.parent_id:
                archive.note_child(candidate.parent_id)
            if candidate.novelty == "no_op":
                archive.no_ops += 1
            elif candidate.novelty == "duplicate":
                archive.duplicates += 1
            elif candidate.novelty == "near":
                archive.near_duplicates += 1
            archive.add(candidate)
        return archive

    # -- placement -------------------------------------------------------

    def coord_for(self, candidate: Candidate) -> tuple[int, ...]:
        return tuple(
            axis.index(descriptor_value(candidate, axis.kpi)) for axis in self.axes
        )

    def cell_label(self, coord: Sequence[int]) -> str:
        return " x ".join(
            f"{axis.kpi} {axis.label(index)}" for axis, index in zip(self.axes, coord)
        )

    def note_child(self, parent_id: str) -> None:
        """One more mutation attempt spent on `parent_id`."""
        self.children[parent_id] = self.children.get(parent_id, 0) + 1

    def add(self, candidate: Candidate) -> Placement:
        """Offer a candidate to the grid.

        Rejected candidates -- hard rejects and the novelty filter's no-ops and
        duplicates alike -- are recorded in the ledger but never placed: they
        must not become parents.
        """
        if candidate.rejected or candidate.fitness is None:
            return Placement()

        widened = False
        for axis in self.axes:
            widened |= axis.observe(descriptor_value(candidate, axis.kpi))
        self.members[candidate.id] = candidate
        if widened:
            # The re-bin places every member, this one included; doing the
            # normal insert afterwards would count it twice.
            self._rebin()
            coord = tuple(candidate.cell or ())
            cell = self.cells[coord]
            return Placement(
                coord=coord,
                inserted=True,
                became_elite=cell.elite_id == candidate.id,
            )

        coord = self.coord_for(candidate)
        candidate.cell = list(coord)
        fitness = float(candidate.fitness)
        current = self.cells.get(coord)
        if current is None:
            self.cells[coord] = Cell(
                coord=coord, elite_id=candidate.id, fitness=fitness
            )
            return Placement(coord=coord, inserted=True, became_elite=True)
        current.occupants += 1
        if fitness > current.fitness:
            displaced = current.elite_id
            current.elite_id = candidate.id
            current.fitness = fitness
            return Placement(
                coord=coord, inserted=True, became_elite=True, displaced=displaced
            )
        return Placement(coord=coord, inserted=True)

    def _rebin(self) -> None:
        """An auto axis widened; re-place everything the grid already holds."""
        self.cells = {}
        for member in sorted(self.members.values(), key=lambda c: c.id):
            coord = self.coord_for(member)
            member.cell = list(coord)
            fitness = float(member.fitness or 0.0)
            cell = self.cells.get(coord)
            if cell is None:
                self.cells[coord] = Cell(
                    coord=coord, elite_id=member.id, fitness=fitness
                )
                continue
            cell.occupants += 1
            if fitness > cell.fitness:
                cell.elite_id, cell.fitness = member.id, fitness

    # -- views -----------------------------------------------------------

    def elites(self) -> list[Candidate]:
        """One candidate per occupied cell, best first."""
        found = [
            self.members[cell.elite_id]
            for cell in self.cells.values()
            if cell.elite_id in self.members
        ]
        return sorted(found, key=lambda c: (-(c.fitness or 0.0), c.id))

    def top(self, k: int | None = None) -> list[Candidate]:
        limit = self.top_k if k is None else k
        ranked = sorted(
            self.members.values(), key=lambda c: (-(c.fitness or 0.0), c.id)
        )
        return ranked[:limit]

    @property
    def best(self) -> Candidate | None:
        ranked = self.top(1)
        return ranked[0] if ranked else None

    def lineage(self, candidate_id: str, limit: int = 8) -> list[str]:
        """The parent chain, nearest first. A cycle cannot outlive `limit`."""
        chain: list[str] = []
        seen = {candidate_id}
        current = self.members.get(candidate_id)
        while current is not None and current.parent_id and len(chain) < limit:
            chain.append(current.parent_id)
            if current.parent_id in seen:
                break
            seen.add(current.parent_id)
            current = self.members.get(current.parent_id)
        return chain

    def occupancy(self) -> str:
        capacity = 1
        for axis in self.axes:
            capacity *= axis.bins
        dims = " x ".join(f"{a.kpi}[{a.bins}]" for a in self.axes)
        return f"{len(self.cells)}/{capacity} cells occupied ({dims})"

    # -- sampling --------------------------------------------------------

    def weights(self, pool: Sequence[Candidate]) -> list[float]:
        """`sigmoid(z(fitness)) x 1/(1 + children)`, never zero."""
        if not pool:
            return []
        values = [float(c.fitness or 0.0) for c in pool]
        mean = fmean(values)
        spread = pstdev(values) if len(values) > 1 else 0.0
        weights = []
        for candidate, value in zip(pool, values):
            z = 0.0 if spread <= 0 else (value - mean) / spread
            used = self.children.get(candidate.id, 0)
            weights.append(max(1e-6, sigmoid(z) / (1.0 + used)))
        return weights

    def sample_parents(self, count: int, rng: random.Random) -> list[Candidate]:
        """`count` parents drawn from the elites, with replacement."""
        pool = self.elites()
        if not pool:
            return []
        weights = self.weights(pool)
        return [rng.choices(pool, weights=weights, k=1)[0] for _ in range(count)]

    def sample_inspirations(
        self, parent: Candidate, count: int, rng: random.Random
    ) -> list[Candidate]:
        """Up to `count` elites from cells *other* than the parent's."""
        if count <= 0:
            return []
        parent_coord = tuple(parent.cell) if parent.cell else None
        pool = [
            c
            for c in self.elites()
            if c.id != parent.id
            and (parent_coord is None or tuple(c.cell or ()) != parent_coord)
        ]
        if not pool:
            return []
        weights = self.weights(pool)
        chosen: list[Candidate] = []
        for _ in range(min(count, len(pool))):
            pick = rng.choices(pool, weights=weights, k=1)[0]
            index = pool.index(pick)
            pool.pop(index)
            weights.pop(index)
            chosen.append(pick)
        return chosen

    # -- persistence -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        elites = self.elites()
        return {
            "descriptors": [axis.to_dict() for axis in self.axes],
            "occupancy": self.occupancy(),
            "counts": {
                "members": len(self.members),
                "cells": len(self.cells),
                "no_ops": self.no_ops,
                "duplicates": self.duplicates,
                "near_duplicates": self.near_duplicates,
            },
            "cells": [
                cell.to_dict(self)
                for cell in sorted(self.cells.values(), key=lambda c: c.coord)
            ],
            "children": dict(sorted(self.children.items())),
            "lineage": {c.id: self.lineage(c.id) for c in elites},
            "elites": [c.to_record() for c in elites],
            "top_k": [c.to_record() for c in self.top()],
        }
