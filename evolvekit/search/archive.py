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
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from statistics import fmean, pstdev, quantiles
from typing import Any, Iterable, Sequence

from evolvekit.candidate import Candidate
from evolvekit.config import ArchiveConfig, DescriptorConfig
from evolvekit.space import Parameter, ParameterSpace

__all__ = ["Archive", "Axis", "Cell", "Placement", "cut_points", "descriptor_value", "diff_complexity", "sigmoid"]


def diff_complexity(diff: str) -> int:
    """A diff's size: the lines it adds plus the lines it removes (0 for the empty diff)."""
    if not diff.strip():
        return 0
    from evolvekit.tree import diff_stat  # only for a tree problem

    stat = diff_stat(diff)
    return int(stat["added"]) + int(stat["removed"])


def sigmoid(x: float) -> float:
    if x < -60.0:
        return 0.0
    if x > 60.0:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def descriptor_value(
    candidate: Candidate,
    kpi: str,
    *,
    tree: bool = False,
    param: str | None = None,
    space: ParameterSpace | None = None,
) -> float:
    """The candidate's value on one descriptor axis.

    KPIs come from the evaluator. `complexity` is special only in that the
    candidate can compute it from its own block when no stage has run yet: the
    block's AST size for a skeleton, and for a tree problem (`tree`), where the
    block is a diff, the number of lines it adds and removes.

    A `param` axis reads the candidate's configuration instead: where its value
    sits on the parameter's own unit scale (`Parameter.to_unit`; a choice is its
    index over `len - 1`), clamped to [0, 1]. 0.0 without params or a space.
    """
    if param is not None:
        found = next((p for p in space or () if p.name == param), None)
        if found is None or not candidate.params or param not in candidate.params:
            return 0.0
        try:
            unit = float(found.to_unit(candidate.params[param]))
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0
        return min(1.0, max(0.0, unit)) if math.isfinite(unit) else 0.0
    value = candidate.kpis.get(kpi)
    if value is None and kpi == "complexity":
        value = float(diff_complexity(candidate.block) if tree else candidate.complexity)
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def cut_points(values: Sequence[float], bins: int) -> list[float]:
    """The cut points of a quantile axis: at most `bins - 1`, each halfway
    between two neighbouring distinct values, so two distinct values never
    share a bin unless there are more distinct values than bins.

    With no more distinct values than bins, every gap gets a cut. Otherwise
    each quantile (`statistics.quantiles(values, n=bins, method="inclusive")`)
    moves to the gap just below it -- a value equal to a quantile goes up, as
    `bisect_right` on the quantile itself would put it -- and tied quantiles
    collapse into one cut. When ties leave fewer than `bins - 1` cuts, the bin
    holding the most members is split at the gap nearest its median, until
    there are `bins - 1`.
    """
    distinct = sorted(set(values))
    if len(distinct) < 2 or bins < 2:
        return []
    gaps = [(a + b) / 2.0 for a, b in zip(distinct, distinct[1:])]
    if len(distinct) <= bins:
        return gaps
    chosen = {
        max(0, min(len(gaps) - 1, bisect_left(distinct, q) - 1))
        for q in quantiles(values, n=bins, method="inclusive")
    }
    ordered = sorted(values)
    while len(chosen) < bins - 1:
        bounds = [-math.inf] + [gaps[i] for i in sorted(chosen)] + [math.inf]
        best: tuple[int, int] | None = None   # (members in the bin, gap to cut)
        for low, high in zip(bounds, bounds[1:]):
            inside = [v for v in ordered if low < v < high]
            free = [i for i, gap in enumerate(gaps) if low < gap < high and i not in chosen]
            if not free:
                continue
            median = inside[len(inside) // 2]
            gap = min(free, key=lambda i: (abs(gaps[i] - median), i))
            if best is None or len(inside) > best[0]:
                best = (len(inside), gap)
        if best is None:
            break
        chosen.add(best[1])
    return [gaps[i] for i in sorted(chosen)]


def _parameter_label(parameter: Parameter, index: int, bins: int) -> str:
    """A `param` bin in the parameter's own values: the range its unit-scale
    bounds map back to, or the choices that fall in it."""
    low, high = index / bins, (index + 1) / bins
    if not parameter.numeric:
        names = [
            str(c) for c in parameter.categories
            if min(bins - 1, int(parameter.to_unit(c) * bins)) == index
        ]
        return "/".join(names) if names else "-"
    return f"{parameter.from_unit(low):g}..{parameter.from_unit(high):g}"


@dataclass
class Axis:
    """One descriptor axis.

    An **auto** KPI axis bins by quantiles: `edges` are the `bins - 1` cut points
    of the members' values at the last re-bin (`fit`), so each bin holds about
    as many members and one outlier cannot squash the rest into one bin; `lo`
    and `hi` only record the range seen. A fixed `range` axis, and a `param`
    axis (its range is the parameter's unit scale, [0, 1]), are equal-width
    bins and never re-bin.
    """

    kpi: str
    """The axis's name: the KPI, or for a `param` axis the parameter."""
    bins: int
    lo: float | None
    hi: float | None
    auto: bool
    param: str | None = None
    edges: list[float] = field(default_factory=list)
    parameter: Parameter | None = None
    """The declared parameter of a `param` axis, for labels in its own values."""

    @staticmethod
    def from_config(cfg: DescriptorConfig, space: ParameterSpace | None = None) -> "Axis":
        parameter = None
        if cfg.param is not None:
            parameter = next((p for p in space or () if p.name == cfg.param), None)
        return Axis(
            kpi=cfg.name, bins=cfg.bins, lo=cfg.lo, hi=cfg.hi, auto=cfg.auto, param=cfg.param,
            parameter=parameter,
        )

    def observe(self, value: float) -> bool:
        """Record the range an auto axis has seen. Returns True when it moved."""
        if not self.auto:
            return False
        lo = value if self.lo is None else min(self.lo, value)
        hi = value if self.hi is None else max(self.hi, value)
        moved = (lo, hi) != (self.lo, self.hi)
        self.lo, self.hi = lo, hi
        return moved

    def fit(self, values: Sequence[float]) -> None:
        """New quantile edges for an auto axis (`cut_points`); fewer than two
        distinct values put every member in bin 0."""
        if self.auto:
            self.edges = cut_points(values, self.bins)

    def index(self, value: float) -> int:
        if self.auto:
            return max(0, min(self.bins - 1, bisect_right(self.edges, value)))
        lo = 0.0 if self.lo is None else self.lo
        hi = 0.0 if self.hi is None else self.hi
        if not hi > lo:
            return 0
        scaled = (value - lo) / (hi - lo) * self.bins
        return max(0, min(self.bins - 1, int(scaled)))

    def label(self, index: int) -> str:
        lo = 0.0 if self.lo is None else self.lo
        hi = 0.0 if self.hi is None else self.hi
        if self.parameter is not None:
            return _parameter_label(self.parameter, index, self.bins)
        if self.auto:
            if not self.edges:
                return f"{lo:g}" if not hi > lo else f"{lo:g}..{hi:g}"
            start = lo if index <= 0 else self.edges[min(index, len(self.edges)) - 1]
            end = hi if index >= len(self.edges) else self.edges[index]
            return f"{start:g}..{end:g}"
        if not hi > lo:
            return f"{lo:g}"
        width = (hi - lo) / self.bins
        return f"{lo + index * width:g}..{lo + (index + 1) * width:g}"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kpi": self.kpi,
            "bins": self.bins,
            "range": [self.lo, self.hi],
            "auto": self.auto,
            "edges": list(self.edges),
        }
        if self.param is not None:
            payload["param"] = self.param
        return payload


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
    tree: bool = False
    """A tree problem: a block is a diff, and its `complexity` is its size
    (`diff_complexity`), so differently sized changes spread over the grid."""
    noise_sd: float = 0.0
    """The sd of one finished candidate's fitness (`Noise.score_sd` at the final
    stage), set by the driver before each `add`. A challenger takes a cell only
    when it beats the elite by more than this (`displaces`); 0 is today's `>`."""
    space: ParameterSpace | None = None
    """`problem.parameters`, which a `param` axis reads its values against."""
    rebinned_at: int = 0
    """The member count at the last quantile re-bin of the auto axes; the next
    comes when the count reaches `max(2, 2 * rebinned_at)` (2, 4, 8, ...)."""
    fitted: list[str] = field(default_factory=list)
    """The ids of the members the last re-bin cut its edges from."""

    # -- construction ----------------------------------------------------

    @staticmethod
    def from_config(
        config: ArchiveConfig, *, tree: bool = False, space: ParameterSpace | None = None
    ) -> "Archive":
        return Archive(
            axes=[Axis.from_config(d, space) for d in config.descriptors],
            top_k=config.top_k,
            tree=tree,
            space=space,
        )

    @staticmethod
    def from_records(
        records: Iterable[dict[str, Any]],
        config: ArchiveConfig,
        *,
        tree: bool = False,
        noise_sd: float = 0.0,
        space: ParameterSpace | None = None,
    ) -> "Archive":
        """Rebuild the grid from `runs.jsonl` -- the resume path.

        `archive.json` is a snapshot; the JSONL is the truth. Rebuilding from
        the log means a run that died between the last append and the last
        snapshot still comes back consistent: the members arrive in the same
        order, so the quantile re-bins fall at the same counts. The elite rule
        reads `noise_sd`, the noise as it is now (as the stop policy's replay does).
        """
        archive = Archive.from_config(config, tree=tree, space=space)
        archive.noise_sd = noise_sd
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

    def value_on(self, axis: Axis, candidate: Candidate) -> float:
        return descriptor_value(candidate, axis.kpi, tree=self.tree, param=axis.param, space=self.space)

    def coord_for(self, candidate: Candidate) -> tuple[int, ...]:
        return tuple(axis.index(self.value_on(axis, candidate)) for axis in self.axes)

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
        must not become parents. Neither must a candidate that did not finish
        the final stage (`Candidate.competes`): its score is `failure_score`, or
        comes from a cheaper input set, and is not comparable with an elite's.
        """
        if candidate.rejected or not candidate.competes or candidate.fitness is None:
            return Placement()

        for axis in self.axes:
            axis.observe(self.value_on(axis, candidate))
        self.members[candidate.id] = candidate
        if self._rebin_due():
            # The re-bin places every member, this one included; doing the
            # normal insert afterwards would count it twice.
            self._refit()
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
        if self.displaces(candidate, fitness, current):
            displaced = current.elite_id
            current.elite_id = candidate.id
            current.fitness = fitness
            return Placement(
                coord=coord, inserted=True, became_elite=True, displaced=displaced
            )
        return Placement(coord=coord, inserted=True)

    def update(self, candidate: Candidate) -> None:
        """A member's fitness changed (its runs pooled with a re-evaluation):
        every member is placed again with the elite rule (`_rebin`), as on a
        resume that reads the pooled value from the start. When the change moved
        the member's value on an auto axis and the member was in the last re-bin,
        the edges are cut again from the same members, now with the pooled
        value -- the edges a resume cuts. Not a member: nothing."""
        if candidate.id not in self.members:
            return
        previous = self.members[candidate.id]
        moved = any(
            axis.auto and self.value_on(axis, previous) != self.value_on(axis, candidate)
            for axis in self.axes
        )
        self.members[candidate.id] = candidate
        for axis in self.axes:
            axis.observe(self.value_on(axis, candidate))
        if moved and candidate.id in self.fitted:
            self._refit(self.fitted)
        self._rebin()

    def _rebin_due(self) -> bool:
        """An auto axis re-bins each time the member count doubles: at 2, 4, 8, ..."""
        return any(axis.auto for axis in self.axes) and len(self.members) >= max(2, 2 * self.rebinned_at)

    def _refit(self, ids: Sequence[str] | None = None) -> None:
        """New quantile edges on every auto axis, from every member's value
        (or those of `ids`, the members of the last re-bin)."""
        chosen = sorted(self.members if ids is None else [i for i in ids if i in self.members])
        for axis in self.axes:
            axis.fit([self.value_on(axis, self.members[i]) for i in chosen])
        if ids is None:
            self.rebinned_at = len(self.members)
            self.fitted = chosen

    def _rebin(self) -> None:
        """Re-place everything the grid already holds (new edges, or a changed fitness)."""
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
            if self.displaces(member, fitness, cell):
                cell.elite_id, cell.fitness = member.id, fitness

    def displaces(self, challenger: Candidate, fitness: float, cell: Cell) -> bool:
        """The elite rule: the challenger takes the cell when it beats the elite
        by more than `noise_sd`, or on any gain when its score is the mean of
        more runs (`replicates`; 0, unknown, reads as equal to any)."""
        if fitness > cell.fitness + self.noise_sd:
            return True
        elite = self.members.get(cell.elite_id)
        mine = int(challenger.replicates or 0)
        theirs = int(elite.replicates or 0) if elite is not None else 0
        return mine > 0 and theirs > 0 and mine > theirs and fitness > cell.fitness

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
