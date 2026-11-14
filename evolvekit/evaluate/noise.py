"""One noise estimate the whole run shares, pooled from the runs' own replicates.

Every candidate of a `seeds: N` stage is run N times; how far those runs spread
is the evaluator's noise, and every rule that compares two numbers needs it.
`Noise` keeps, per stage key (`"<stage>"`, `"<stage>/private"`) and per KPI,
each candidate's own estimate and pools them when read:

* **across** -- a candidate's runs, mean and sample variance over its seeds.
  Pooled as the plain mean of the candidates' variances (each has the same
  `seeds - 1` degrees of freedom in a run, so this is the df-weighted pool).
* **paired** -- on a stage that is *repeatable* (same program + same seed gives
  the same numbers), what matters is not how far the seeds spread but how far
  the *difference* of two candidates on the same seeds does. Every candidate's
  runs per seed are kept (`runs`), and the estimate is the two-way
  (candidate × seed) residual: `x - candidate's mean - seed's mean over the
  candidates + grand mean`, `SS / ((C - 1)(S - 1))` over the C candidates
  that ran the same S >= 2 seeds (Decision 17c). For `x = f(c) + g(s) + e`
  with `e` of variance σ², `E[SS] = (C - 1)(S - 1)·σ²`: it is σ² itself, the
  variance of one side, and the difference of two candidates on one seed has
  `2σ²` -- half the residual variance of a difference. Every candidate's own
  `f` and every seed's `g` (the seed's own run included) average out, so the
  pool grows with the run instead of sharing the seed's interaction term.

Every accessor returns the noise of **one side** of a comparison: across, the
variance of one run; paired, the two-way residual's (the difference of two
independent sides then has `2·sd²` again, so a rate derived for independent
sides holds). Which estimate stands behind an accessor is the stage's
repeatability: paired when `repeatable(key)` is true, across otherwise (false,
or not yet measured). With nothing to pool the noise is 0, which is how every
rule read before.

**Degrees of freedom** (Decision 17b): every estimate says how many it has
(`df`, `vector_df`, `cv_df`, `vector_cv_df`, `score_df`) -- across
`Σ (nᵢ - 1)`, paired `Σ (C - 1)(S - 1)`, the calibration 2 -- and a rule that
reads σ̂ widens its normal quantile to Student's t at that df
(`stats.t_scale`); with nothing estimated the df is `math.inf` (the noise is
0: nothing to widen). A file written before the per-seed runs were kept has
per-candidate differences to the seed (`paired`, `v_d` with its df): each is
still an unbiased `2σ²`, so it joins the pool as `df · v_d / 2`, and the seed's
own runs, which that file kept, become its row of the two-way.

Vectors are pooled the same way, per vector key and per element
(`<kpi>_per_instance`, `<obj>_pct_of_baseline`, an evaluator's own list), each
in its own units. The whole estimate is kept in `work/noise.json`.

**The seed run again** (once per run and command stage, `Cascade._check_noise`;
`preflight` at the final stage): when a stage's command has a `{seed}`, the
seed is run once more on
seed 0 with the cache bypassed, on the instance whose seed run took the median
time (`check_stage`). The same numbers to the signature's digits make the
stage *repeatable*. At `seeds: 1` it also runs on seed 1, and the three runs
calibrate the across-seed estimate where nothing replicates (`calibrate`):
two degrees of freedom, so it is quoted to one significant digit only.
"""

from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, variance
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping, Sequence

from evolvekit.evaluate.signature import round_significant
from evolvekit.ledger import _atomic_write

if TYPE_CHECKING:  # annotations only: this module stays a leaf
    from evolvekit.config import ScoreConfig, StageConfig
    from evolvekit.evaluate.types import StageOutcome

__all__ = [
    "CALIBRATION_ID",
    "NOISE_FILE",
    "Check",
    "Noise",
    "SEED_ID",
    "about_pct",
    "check_seeds",
    "check_stage",
    "measurable",
    "record_check",
    "run_duration",
]

NOISE_FILE = "noise.json"
CALIBRATION_ID = "(calibration)"
"""The entry the seed's extra calibration runs are kept under (A2.1b)."""
SEED_ID = "(seed)"
"""The seed's own runs: the reference of every paired difference."""
SCHEMA = 2
"""2: per-seed `runs` per candidate (the two-way paired estimate). A schema-1
file still loads: its `paired` differences and the seed's runs are read."""


@dataclass(frozen=True)
class _Pool:
    """What one across accessor pools: one value per (entry, element)."""

    variances: list[float]
    dfs: list[int]

    def across(self) -> float | None:
        return fmean(self.variances) if self.variances else None

    @property
    def df(self) -> int:
        return sum(self.dfs)


@dataclass(frozen=True)
class _Paired:
    """One paired estimate: the one-side variance, its degrees of freedom,
    and the mean of the values it came from (for a relative noise)."""

    var: float | None
    df: int
    mean: float | None


def _two_way(tables: Iterable[tuple[Sequence[int], Sequence[float]]]) -> tuple[float, int, float, int]:
    """The two-way (candidate × seed) residual sum of squares of `tables`,
    one `(seeds, values)` per candidate: candidates are grouped by the seeds
    they ran (a two-way table needs the same columns in every row); in a
    group of C rows and S >= 2 seeds, `Σ (x - row mean - column mean + grand
    mean)²` with `(C - 1)(S - 1)` df. Returns `(SS, df, Σ x, count)` -- the
    last two over every row, for the mean the relative noise divides by."""
    groups: dict[tuple[int, ...], list[list[float]]] = {}
    for seeds, values in tables:
        if len(seeds) != len(values) or len(set(seeds)) != len(seeds):
            continue  # a seed twice is no cell of a two-way table
        order = sorted(range(len(seeds)), key=lambda j: seeds[j])
        groups.setdefault(tuple(seeds[j] for j in order), []).append([float(values[j]) for j in order])
    ss, df, total, count = 0.0, 0, 0.0, 0
    for columns, rows in groups.items():
        c, s = len(rows), len(columns)
        total += sum(sum(row) for row in rows)
        count += c * s
        if c < 2 or s < 2:
            continue
        row_means = [fmean(row) for row in rows]
        column_means = [fmean(column) for column in zip(*rows)]
        grand = fmean(row_means)
        ss += sum(
            (x - row_means[i] - column_means[j] + grand) ** 2
            for i, row in enumerate(rows)
            for j, x in enumerate(row)
        )
        df += (c - 1) * (s - 1)
    return ss, df, total, count


def _combined(ss: float, df: int, legacy: Iterable[tuple[int, float]], mean: float | None) -> _Paired:
    """The two-way residual pooled with a schema-1 file's per-candidate
    differences to the seed, `(df, v_d)`: each `v_d / 2` is unbiased for the
    one-side variance, so it adds `df · v_d / 2` and its df."""
    total_ss, total_df = ss, df
    for d, v in legacy:
        if d >= 1:
            total_ss += d * v / 2.0
            total_df += d
    if total_df <= 0:
        return _Paired(None, 0, mean)
    return _Paired(total_ss / total_df, total_df, mean)


def _df_or_inf(df: float) -> float:
    """A df as the rules read it: `math.inf` when nothing was estimated."""
    return float(df) if df > 0 else math.inf


def _empty_stage() -> dict[str, Any]:
    return {"repeatable": None, "kpis": {}, "vectors": {}}


def _across(group: Mapping[str, Any], usable: Callable[[Any], bool], *, calibration: bool = True) -> dict[str, Any]:
    """The across entries of one KPI (or vector) that count: those `usable`,
    and the calibration only while no real candidate has one -- the replicates
    the run measured win over three runs of the seed. `calibration=False`
    leaves it out whatever else there is."""
    chosen = {cid: e for cid, e in group.items() if usable(e)}
    if CALIBRATION_ID in chosen and (not calibration or len(chosen) > 1):
        del chosen[CALIBRATION_ID]
    return chosen


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"not a finite number: {value!r}")
    return float(value)


def _optional(value: Any) -> float | None:
    return None if value is None else _number(value)


def _variance(value: Any) -> float:
    number = _number(value)
    if number < 0:
        raise ValueError(f"not a variance: {value!r}")
    return number


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"not a count: {value!r}")
    return value


def _mapping(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"not a mapping: {value!r}")
    return value


def _sequence(value: Any) -> list:
    if not isinstance(value, list):
        raise ValueError(f"not a list: {value!r}")
    return value


_BAD = (ValueError, TypeError, KeyError, IndexError)
"""What a malformed value of `noise.json` raises while it is checked."""


def _entries(raw: Any, clean: Callable[[Any], Any]) -> dict[str, Any]:
    """`{id: clean(value)}` for a mapping of entries, leaving out each one
    that does not check: one bad number costs that entry, not the file."""
    try:
        raw = _mapping(raw)
    except _BAD:
        return {}
    kept = {}
    for name, value in raw.items():
        try:
            kept[str(name)] = clean(value)
        except _BAD:
            continue
    return kept


def _element(clean: Callable[[list], list]) -> Callable[[Any], list | None]:
    """One element of a vector entry, or `None` (unknown) when it does not
    check: the elements are positional, so a bad one cannot just be dropped."""

    def check(value: Any) -> list | None:
        if value is None:
            return None
        try:
            return clean(_sequence(value))
        except _BAD:
            return None

    return check


def _across_element(e: list) -> list:
    return [_optional(e[0]), _variance(e[1]), _count(e[2])]


def _paired_element(e: list) -> list:
    return [_count(e[0]), _variance(e[1])]


def _seeds(value: Any) -> list[int]:
    return [_count(s) for s in _sequence(value)]


def _kpi_runs(e: Any) -> dict[str, Any]:
    e = _mapping(e)
    seeds, values = _seeds(e["seeds"]), [_number(v) for v in _sequence(e["values"])]
    if len(seeds) != len(values):
        raise ValueError("runs: one value per seed")
    return {"seeds": seeds, "values": values}


def _vector_runs(e: Any) -> dict[str, Any]:
    e = _mapping(e)
    seeds = _seeds(e["seeds"])
    rows = [[_number(v) for v in _sequence(row)] for row in _sequence(e["rows"])]
    if len(seeds) != len(rows):
        raise ValueError("runs: one row per seed")
    return {"seeds": seeds, "rows": rows}


def _kpi_group(entry: Any) -> dict[str, Any]:
    entry = _mapping(entry)
    return {
        "across": _entries(
            entry.get("across", {}),
            lambda e: {"runs": _count(_mapping(e)["runs"]), "mean": _optional(e.get("mean")), "var": _variance(e["var"])},
        ),
        "paired": _entries(
            entry.get("paired", {}), lambda e: {"df": _count(_mapping(e)["df"]), "var": _variance(e["var"])}
        ),
        "runs": _entries(entry.get("runs", {}), _kpi_runs),
    }


def _vector_group(entry: Any) -> dict[str, Any]:
    entry = _mapping(entry)
    across, paired = _element(_across_element), _element(_paired_element)
    return {
        "across": _entries(entry.get("across", {}), lambda es: [across(e) for e in _sequence(es)]),
        "paired": _entries(entry.get("paired", {}), lambda es: [paired(e) for e in _sequence(es)]),
        "runs": _entries(entry.get("runs", {}), _vector_runs),
    }


def _group(stage: dict[str, Any], kind: str, name: str) -> dict[str, Any]:
    return stage[kind].setdefault(name, {"across": {}, "paired": {}, "runs": {}})


def _seed_as_row(stage: dict[str, Any]) -> None:
    """A schema-1 file kept the seed's runs per seed but no candidate's: the
    seed's become its row of the two-way table (`SEED_ID`), where its KPI is
    already in the file, so its interaction term averages out with the rest."""
    seed = stage.get("seed")
    if not seed or "id" in seed:
        return
    seeds = seed["seeds"]
    if len(seeds) >= 2:
        for kpi, values in seed["kpis"].items():
            if kpi in stage["kpis"] and len(values) == len(seeds):
                stage["kpis"][kpi]["runs"].setdefault(SEED_ID, {"seeds": list(seeds), "values": list(values)})
        for name, rows in seed["vectors"].items():
            if name in stage["vectors"] and len(rows) == len(seeds):
                stage["vectors"][name]["runs"].setdefault(SEED_ID, {"seeds": list(seeds), "rows": [list(r) for r in rows]})
    seed["id"] = SEED_ID


def _seed(raw: Any) -> dict[str, Any]:
    """The seed's runs: one unit -- seeds and values only mean something together."""
    seed = _mapping(raw)
    kept: dict[str, Any] = {} if seed.get("id") is None else {"id": str(seed["id"])}
    return {
        **kept,
        "seeds": [_count(s) for s in _sequence(seed.get("seeds", []))],
        "kpis": {str(k): [_number(v) for v in _sequence(vs)] for k, vs in _mapping(seed.get("kpis", {})).items()},
        "vectors": {
            str(k): [[_number(v) for v in _sequence(row)] for row in _sequence(rows)]
            for k, rows in _mapping(seed.get("vectors", {})).items()
        },
    }


def _clean_stage(raw: Any) -> dict[str, Any]:
    """One stage of `noise.json`, checked. A malformed entry is left out and
    the rest kept; a stage that is not a mapping at all raises."""
    raw = _mapping(raw)
    stage = _empty_stage()
    repeatable = raw.get("repeatable")
    stage["repeatable"] = repeatable if isinstance(repeatable, bool) else None
    stage["kpis"] = _entries(raw.get("kpis", {}), _kpi_group)
    stage["vectors"] = _entries(raw.get("vectors", {}), _vector_group)
    if raw.get("seed") is not None:
        try:
            stage["seed"] = _seed(raw["seed"])
        except _BAD:
            pass  # no reference: the candidates' paired entries already made stay
    _seed_as_row(stage)
    return stage


class Noise:
    """The run's noise estimate, per stage key and per KPI (see the module)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._stages: dict[str, dict[str, Any]] = {}
        self._paired_cache: dict[tuple, _Paired] = {}
        """The two-way estimates read since the last change: each is a pass
        over every candidate's runs, and the twin rule reads one per number."""

    @classmethod
    def load(cls, path: Path) -> "Noise":
        """The estimate kept at `path`; empty when the file is missing,
        unreadable or of the wrong shape (a run from before the file: no noise)."""
        noise = cls(path)
        try:
            payload = _mapping(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return noise
        stages = _entries(payload.get("stages", {}), _clean_stage)
        noise._stages = stages
        return noise

    def save(self) -> None:
        if self.path is None:
            return
        payload = {"schema": SCHEMA, "stages": self._stages}
        _atomic_write(self.path, json.dumps(payload, indent=1, sort_keys=True) + "\n")

    # -- recording -------------------------------------------------------

    def _stage(self, key: str) -> dict[str, Any]:
        return self._stages.setdefault(key, _empty_stage())

    def _forget(self, stage: dict[str, Any], candidate_id: str) -> None:
        for group in ("kpis", "vectors"):
            for entry in stage[group].values():
                entry["across"].pop(candidate_id, None)
                entry["paired"].pop(candidate_id, None)
                entry["runs"].pop(candidate_id, None)

    def observe(self, key: str, candidate_id: str, outcome: "StageOutcome", *, is_seed: bool = False) -> None:
        """Record one candidate's outcome at stage `key` (replacing an earlier
        one of the same id: an interrupted generation evaluated again must not
        count twice). Only a finished outcome counts -- one that failed, was
        raced out or abandoned carries no replicate -- and only one with two or
        more seeds says anything about noise. The seed's runs are kept, one seed
        or more, as the reference of the other candidates' paired differences."""
        # The latest observation of an id is the one that counts, even when it
        # brings no replicate: what it replaced is forgotten first.
        self._paired_cache.clear()
        if key in self._stages:
            self._forget(self._stages[key], candidate_id)
        if not outcome.ok or outcome.raced_out or outcome.abandoned:
            return
        stage = self._stage(key)
        if is_seed:
            if stage.get("seed") and "id" not in stage["seed"]:
                self._forget(stage, SEED_ID)  # a schema-1 seed's row, now observed again
            stage["seed"] = {
                "id": candidate_id,
                "seeds": list(outcome.seed_list),
                "kpis": {k: list(v) for k, v in outcome.seed_runs.items()},
                "vectors": {k: [list(row) for row in rows] for k, rows in outcome.seed_vectors.items()},
            }
        seeds = len(outcome.seed_list) or outcome.runs
        if seeds < 2:
            return
        for kpi, var in outcome.kpi_var.items():
            entry = _group(stage, "kpis", kpi)
            entry["across"][candidate_id] = {"runs": seeds, "mean": outcome.kpis.get(kpi), "var": float(var)}
        for name, variances in outcome.vector_var.items():
            means = outcome.vector_kpis.get(name)
            if means is None or len(means) != len(variances):
                means = [None] * len(variances)
            entry = _group(stage, "vectors", name)
            entry["across"][candidate_id] = [[m, float(v), seeds] for m, v in zip(means, variances)]
        self._keep_runs(stage, candidate_id, outcome)

    @staticmethod
    def _keep_runs(stage: dict[str, Any], candidate_id: str, outcome: "StageOutcome") -> None:
        """The candidate's runs per seed, the cells of the two-way paired
        estimate: per KPI its stage-level value of each seed's run, per vector
        each seed's row. Only with two seeds or more, all of them distinct."""
        seed_list = [int(s) for s in outcome.seed_list]
        if len(seed_list) < 2 or len(set(seed_list)) != len(seed_list):
            return
        for kpi, runs in outcome.seed_runs.items():
            if len(runs) == len(seed_list) and all(math.isfinite(v) for v in runs):
                _group(stage, "kpis", kpi)["runs"][candidate_id] = {"seeds": seed_list, "values": [float(v) for v in runs]}
        for name, rows in outcome.seed_vectors.items():
            if len(rows) != len(seed_list) or not rows:
                continue
            width = len(rows[0])
            if any(len(row) != width for row in rows) or not all(math.isfinite(v) for row in rows for v in row):
                continue
            _group(stage, "vectors", name)["runs"][candidate_id] = {
                "seeds": seed_list, "rows": [[float(v) for v in row] for row in rows]
            }

    def pool(self, key: str, candidate_id: str, outcome: "StageOutcome") -> None:
        """One more run of a candidate already observed (the best run again,
        `search.reevaluate_best_every`), merged into its across entries at stage
        `key` (Welford: `n + 1` runs, the mean moved by `(x - mean)/(n + 1)`,
        `M2 += (x - mean)(x - mean')`), per KPI and per vector element, in the
        units the entry has. Only into entries that exist: a candidate of
        `seeds: 1` has no runs of its own here, and its one run was selected as
        the best of many -- paired with a fresh one, it would overstate the
        noise -- so it adds nothing. The paired entries stay: the seed never ran
        on a re-evaluation's seed. A failed outcome adds nothing.

        A limit, kept: at `seeds > 1` the entry's mean is selected too (the
        best's mean of n runs measured high), so the merged variance overstates
        the noise a little: for the best of 8 equal candidates at `seeds: 3`
        the merged entry's variance is about 11 % high (its sd about 5 %) --
        the earlier runs' own variance is unbiased (a normal sample's variance
        does not depend on its mean), the new run's distance to the lucky mean
        is not: E[(x - mean)²] = σ²(1 + (1.42² + 0.34)/3)."""
        if not outcome.ok or outcome.raced_out or outcome.abandoned:
            return
        stage = self._stages.get(key)
        if stage is None:
            return
        self._paired_cache.clear()

        def merged(mean: float, var: float, runs: int, x: float) -> tuple[float, float, int]:
            delta = x - mean
            new_mean = mean + delta / (runs + 1)
            m2 = var * (runs - 1) + delta * (x - new_mean)
            return new_mean, max(0.0, m2 / runs), runs + 1

        for kpi, entry in stage["kpis"].items():
            own = entry["across"].get(candidate_id)
            x = outcome.kpis.get(kpi)
            if own is None or own["runs"] < 2 or own["mean"] is None or x is None or not math.isfinite(x):
                continue
            mean, var, runs = merged(own["mean"], own["var"], own["runs"], float(x))
            entry["across"][candidate_id] = {"runs": runs, "mean": mean, "var": var}
        for name, entry in stage["vectors"].items():
            elements = entry["across"].get(candidate_id)
            values = outcome.vector_kpis.get(name)
            if elements is None or values is None or len(values) != len(elements):
                continue
            joined = []
            for element, x in zip(elements, values):
                if element is None or element[0] is None or element[2] < 2 or not math.isfinite(x):
                    joined.append(element)
                    continue
                mean, var, runs = merged(element[0], element[1], element[2], float(x))
                joined.append([mean, var, runs])
            entry["across"][candidate_id] = joined

    def repeatable(self, key: str) -> bool | None:
        stage = self._stages.get(key)
        return None if stage is None else stage.get("repeatable")

    def set_repeatable(self, key: str, value: bool) -> None:
        self._stage(key)["repeatable"] = bool(value)

    # -- reading ---------------------------------------------------------

    def _kpi_pool(self, key: str, kpi: str, *, calibration: bool = True) -> _Pool:
        entry = self._stages.get(key, {}).get("kpis", {}).get(kpi)
        if entry is None:
            return _Pool([], [])
        chosen = list(_across(entry["across"], lambda e: e["runs"] >= 2, calibration=calibration).values())
        return _Pool([e["var"] for e in chosen], [e["runs"] - 1 for e in chosen])

    def _element_pool(self, key: str, name: str, index: int, *, calibration: bool = True) -> _Pool:
        entry = self._stages.get(key, {}).get("vectors", {}).get(name)
        if entry is None:
            return _Pool([], [])

        def usable(elements: list) -> bool:
            return index < len(elements) and elements[index] is not None and elements[index][2] >= 2

        group = _across(entry["across"], usable, calibration=calibration)
        return _Pool([es[index][1] for es in group.values()], [es[index][2] - 1 for es in group.values()])

    def _kpi_paired(self, key: str, kpi: str) -> _Paired:
        """The two-way paired estimate of `kpi` at `key` (with a schema-1
        file's differences, `_combined`); its mean is the cells' grand mean,
        or the seed's when only differences are known."""
        cached = self._paired_cache.get(("kpi", key, kpi))
        if cached is not None:
            return cached
        stage = self._stages.get(key, {})
        entry = stage.get("kpis", {}).get(kpi)
        if entry is None:
            return _Paired(None, 0, None)
        ss, df, total, count = _two_way((e["seeds"], e["values"]) for e in entry["runs"].values())
        mean = total / count if count else None
        if mean is None:
            base = (stage.get("seed") or {}).get("kpis", {}).get(kpi)
            mean = fmean(base) if base else None
        found = _combined(ss, df, ((e["df"], e["var"]) for e in entry["paired"].values()), mean)
        self._paired_cache[("kpi", key, kpi)] = found
        return found

    def _element_paired(self, key: str, name: str, index: int) -> _Paired:
        """`_kpi_paired` for element `index` of the vector `name`."""
        cached = self._paired_cache.get(("vector", key, name, index))
        if cached is not None:
            return cached
        found = self._element_paired_now(key, name, index)
        self._paired_cache[("vector", key, name, index)] = found
        return found

    def _element_paired_now(self, key: str, name: str, index: int) -> _Paired:
        stage = self._stages.get(key, {})
        entry = stage.get("vectors", {}).get(name)
        if entry is None:
            return _Paired(None, 0, None)
        tables = [
            (e["seeds"], [row[index] for row in e["rows"]])
            for e in entry["runs"].values()
            if all(index < len(row) for row in e["rows"])
        ]
        ss, df, total, count = _two_way(tables)
        mean = total / count if count else None
        if mean is None:
            rows = (stage.get("seed") or {}).get("vectors", {}).get(name) or []
            column = [row[index] for row in rows if index < len(row)]
            mean = fmean(column) if column else None
        legacy = [
            (es[index][0], es[index][1])
            for es in entry["paired"].values()
            if index < len(es) and es[index] is not None
        ]
        return _combined(ss, df, legacy, mean)

    def _width(self, key: str, name: str) -> int:
        """How many elements the vector `name` has anywhere at `key`."""
        entry = self._stages.get(key, {}).get("vectors", {}).get(name)
        if entry is None:
            return 0
        widths = [len(es) for es in entry["across"].values()] + [len(es) for es in entry["paired"].values()]
        widths += [len(row) for e in entry["runs"].values() for row in e["rows"]]
        return max(widths, default=0)

    def sd(self, key: str, kpi: str) -> float:
        """One side's noise of one run of the stage-level value of `kpi`."""
        if self.repeatable(key):
            value = self._kpi_paired(key, kpi).var
        else:
            value = self._kpi_pool(key, kpi).across()
        return math.sqrt(value) if value is not None else 0.0

    def df(self, key: str, kpi: str) -> float:
        """The degrees of freedom behind `sd(key, kpi)`: across
        `Σ (nᵢ - 1)` over the candidates pooled (the calibration's 3 runs: 2),
        paired `Σ (C - 1)(S - 1)`; `math.inf` when nothing is estimated."""
        if self.repeatable(key):
            return _df_or_inf(self._kpi_paired(key, kpi).df)
        return _df_or_inf(self._kpi_pool(key, kpi).df)

    def sd_across(self, key: str, kpi: str) -> float:
        """The across-seed estimate, whatever the stage's repeatability."""
        value = self._kpi_pool(key, kpi).across()
        return math.sqrt(value) if value is not None else 0.0

    def vector_sd(self, key: str, vector_key: str, index: int) -> float | None:
        """`sd` for one element of a vector KPI, in that vector's units;
        `None` when nothing is known about that element."""
        if self.repeatable(key):
            paired = self._element_paired(key, vector_key, index).var
            if paired is not None:
                return math.sqrt(paired)
            # Repeatable, with across entries for the element but no pair yet
            # (only the seed replicated): no paired noise is measured, and on a
            # repeatable stage that reads as 0 -- today. At `seeds: 1` there is
            # no across entry either, so: None. The calibration is never read
            # on a repeatable stage, not even as "replicated".
            across = self._element_pool(key, vector_key, index, calibration=False).across()
            return 0.0 if across is not None else None
        across = self._element_pool(key, vector_key, index).across()
        return math.sqrt(across) if across is not None else None

    def vector_df(self, key: str, vector_key: str, index: int) -> float:
        """The degrees of freedom behind `vector_sd` (`df`, per element)."""
        if self.repeatable(key):
            return _df_or_inf(self._element_paired(key, vector_key, index).df)
        return _df_or_inf(self._element_pool(key, vector_key, index).df)

    def _scalar_ratios(self, key: str, kpi: str) -> list[tuple[float, int]]:
        """`(var / mean², df)` of the scalar `kpi`: per candidate across, one
        pooled pair on a repeatable stage."""
        entry = self._stages.get(key, {}).get("kpis", {}).get(kpi)
        if entry is None:
            return []
        if self.repeatable(key):
            paired = self._kpi_paired(key, kpi)
            if paired.var is None or not paired.mean:
                return []
            return [(paired.var / paired.mean**2, paired.df)]
        return [
            (e["var"] / e["mean"] ** 2, e["runs"] - 1)
            for e in _across(entry["across"], lambda e: e["runs"] >= 2).values()
            if e["mean"] is not None and e["mean"] != 0.0
        ]

    def _vector_ratios(self, key: str, vector_key: str, *, across: bool = False) -> list[tuple[float, int]]:
        """`(var / mean², df)` per (entry, element) of one vector KPI across,
        per element of the pooled two-way estimate on a repeatable stage
        (unless `across`). Elements whose mean is 0 have no ratio."""
        entry = self._stages.get(key, {}).get("vectors", {}).get(vector_key)
        if entry is None:
            return []
        ratios: list[tuple[float, int]] = []
        if self.repeatable(key) and not across:
            for index in range(self._width(key, vector_key)):
                paired = self._element_paired(key, vector_key, index)
                if paired.var is not None and paired.mean:
                    ratios.append((paired.var / paired.mean**2, paired.df))
            return ratios
        replicated = _across(entry["across"], lambda es: any(e is not None and e[2] >= 2 for e in es))
        for elements in replicated.values():
            for element in elements:
                if element is None or element[2] < 2 or element[0] is None or element[0] == 0.0:
                    continue
                ratios.append((element[1] / element[0] ** 2, element[2] - 1))
        return ratios

    @staticmethod
    def _relative(ratios: list[tuple[float, int]], *, weighted: bool) -> float:
        if not ratios:
            return 0.0
        if weighted:
            total = sum(df for _, df in ratios)
            return math.sqrt(sum(r * df for r, df in ratios) / total) if total > 0 else 0.0
        return math.sqrt(fmean(r for r, _ in ratios))

    def _cv_ratios(self, key: str, kpi: str) -> tuple[list[tuple[float, int]], bool]:
        name = f"{kpi}_per_instance"
        entry = self._stages.get(key, {}).get("vectors", {}).get(name)
        if entry is not None and (entry["across"] or entry["paired"] or entry["runs"]):
            return self._vector_ratios(key, name), bool(self.repeatable(key))
        return self._scalar_ratios(key, kpi), bool(self.repeatable(key))

    def cv(self, key: str, kpi: str) -> float:
        """The relative one-side noise of one run on one element: per instance
        on a per-instance stage (`<kpi>_per_instance`), else from the scalar
        entries. Elements whose mean is 0 have no ratio and are left out."""
        ratios, weighted = self._cv_ratios(key, kpi)
        return self._relative(ratios, weighted=weighted)

    def cv_df(self, key: str, kpi: str) -> float:
        """The degrees of freedom behind `cv(key, kpi)`: the sum over what it
        pooled; `math.inf` when nothing is."""
        ratios, _ = self._cv_ratios(key, kpi)
        return _df_or_inf(sum(df for _, df in ratios))

    def vector_cv(self, key: str, vector_key: str, *, across: bool = False) -> float:
        """`cv` over every (entry, element) of one vector KPI; paired per
        element on a repeatable stage, unless `across` (the across-seed one,
        whatever the stage)."""
        repeatable = bool(self.repeatable(key)) and not across
        return self._relative(self._vector_ratios(key, vector_key, across=across), weighted=repeatable)

    def vector_cv_df(self, key: str, vector_key: str, *, across: bool = False) -> float:
        """The degrees of freedom behind `vector_cv`."""
        return _df_or_inf(sum(df for _, df in self._vector_ratios(key, vector_key, across=across)))

    def _score_runs(self, key: str, weights: Mapping[str, float]) -> list[tuple[list[int], list[float]]]:
        """Per candidate that kept runs of every weighted KPI on the same
        seeds: `(seeds, Σ w_k · x_k per run)`, the score's own per-run values."""
        entries = [self._stages.get(key, {}).get("kpis", {}).get(kpi) for kpi in weights]
        if any(entry is None for entry in entries):
            return []
        tables = []
        for cid, first in entries[0]["runs"].items():  # type: ignore[index]
            rows = [entry["runs"].get(cid) for entry in entries]  # type: ignore[index]
            if any(row is None or row["seeds"] != first["seeds"] for row in rows):
                continue
            values = [sum(w * row["values"][j] for w, row in zip(weights.values(), rows))
                      for j in range(len(first["seeds"]))]
            tables.append((list(first["seeds"]), values))
        return tables

    def _score_estimate(self, key: str, score: "ScoreConfig") -> tuple[float, float]:
        """One run's score variance and its df (Decision 18b). One KPI: its
        own (`sd`, `df`). A weighted sum: from the score's own per-run values
        -- across seeds per candidate (`Σ (nᵢ - 1)`), or two-way on a
        repeatable stage -- so the covariance of the measures is in, and the
        df is exact. Without such runs (a calibration, a file from before
        them): `Σ w_k² sd_k²`, on the smallest of the measures' df."""
        weights = dict(score.weights or {score.objective: 1.0})
        if len(weights) == 1:
            (kpi, w), = weights.items()
            return w * w * self.sd(key, kpi) ** 2, self.df(key, kpi)
        cached = self._paired_cache.get(("score", key, tuple(sorted(weights.items())), bool(self.repeatable(key))))
        if cached is not None:
            return cached.var or 0.0, _df_or_inf(cached.df)
        tables = self._score_runs(key, weights)
        var: float | None
        if self.repeatable(key):
            ss, df, _, _ = _two_way(tables)
            var = ss / df if df > 0 else None
        else:
            replicated = [values for _, values in tables if len(values) >= 2]
            var = fmean(variance(v) for v in replicated) if replicated else None
            df = sum(len(v) - 1 for v in replicated)
        if var is None:
            var = sum(w * w * self.sd(key, kpi) ** 2 for kpi, w in weights.items())
            dfs = [self.df(key, kpi) for kpi in weights if self.sd(key, kpi) > 0]
            df = min(dfs) if dfs else 0  # type: ignore[assignment]
        found = _Paired(var, df, None)  # type: ignore[arg-type]
        self._paired_cache[("score", key, tuple(sorted(weights.items())), bool(self.repeatable(key)))] = found
        return var, _df_or_inf(df)

    def score_sd(self, key: str, score: "ScoreConfig", seeds: int) -> float:
        """One candidate's stage score, a mean over `seeds` runs: the sd of
        one run's weighted sum (`_score_estimate`) over `√seeds`, over
        `score.weights` only: the penalty terms are taken to carry no noise.
        Levels compare goal by goal and have no such sum: 0."""
        if score.levels:
            return 0.0
        var, _ = self._score_estimate(key, score)
        return math.sqrt(max(0.0, var)) / math.sqrt(max(1, seeds))

    def score_df(self, key: str, score: "ScoreConfig") -> float:
        """The degrees of freedom behind `score_sd` (`_score_estimate`);
        `math.inf` with no noise (and with levels)."""
        if score.levels:
            return math.inf
        var, df = self._score_estimate(key, score)
        return df if var > 0 else math.inf

    def known(self, key: str) -> bool:
        """Any estimate, across or paired, with at least one degree of freedom."""
        stage = self._stages.get(key)
        if stage is None:
            return False
        for entry in stage["kpis"].values():
            if any(e["runs"] >= 2 for e in entry["across"].values()):
                return True
        for entry in stage["vectors"].values():
            for elements in entry["across"].values():
                if any(e is not None and e[2] >= 2 for e in elements):
                    return True
        return self._paired_known(key)

    def _paired_known(self, key: str) -> bool:
        stage = self._stages.get(key)
        if stage is None:
            return False
        if any(self._kpi_paired(key, kpi).df >= 1 for kpi in stage["kpis"]):
            return True
        return any(
            self._element_paired(key, name, index).df >= 1
            for name in stage["vectors"]
            for index in range(self._width(key, name))
        )

    def read_known(self, key: str) -> bool:
        """Whether the estimate the accessors read has data: on a repeatable
        stage the paired pool only (an across estimate there is reported, not
        read), otherwise any (`known`)."""
        if not self.repeatable(key):
            return self.known(key)
        return self._paired_known(key)

    def source(self, key: str) -> str:
        """Where `sd` of stage `key` comes from: `paired` on a repeatable
        stage, else `replicates` (a candidate's own runs), `calibration` (the
        seed run again, nothing replicated), or `none`."""
        if self.repeatable(key):
            return "paired"
        stage = self._stages.get(key)
        if stage is None:
            return "none"
        found: set[str] = set()
        for entry in stage["kpis"].values():
            found.update(cid for cid, e in entry["across"].items() if e["runs"] >= 2)
        for entry in stage["vectors"].values():
            found.update(
                cid for cid, es in entry["across"].items() if any(e is not None and e[2] >= 2 for e in es)
            )
        if found - {CALIBRATION_ID}:
            return "replicates"
        return "calibration" if found else "none"

    def to_status(self, key: str) -> dict[str, Any]:
        """What `status` shows of stage `key` (`progress.noise`): per KPI the
        one-side noise of one run the rules read (`sd`), the across-seed one
        (`sd_across`, always) and the relative one (`cv`); where `sd` came
        from. Engine words: it is for engineers."""
        source = self.source(key)
        kpis = [] if source == "none" else sorted(self._stages.get(key, {}).get("kpis", {}))
        return {
            "repeatable": self.repeatable(key),
            "source": source,
            "sd": {kpi: self.sd(key, kpi) for kpi in kpis},
            "sd_across": {kpi: self.sd_across(key, kpi) for kpi in kpis},
            "cv": {kpi: self.cv(key, kpi) for kpi in kpis},
        }

    # -- the seed run again ------------------------------------------------

    def calibrate(
        self,
        key: str,
        kpi_values: Mapping[str, Sequence[float]],
        *,
        instance: int | None,
        instances: int,
        seed_means: Mapping[str, Sequence[float]] | None = None,
        elements: Mapping[str, str | None] | None = None,
    ) -> None:
        """The across estimate from the seed's own runs, where no candidate
        replicates (`seeds: 1`): per KPI its values on one instance (or on the
        whole input set, `instance=None`), kept under `CALIBRATION_ID`.

        On a per-instance stage the runs were made on instance `instance` of
        `instances` (M). That element of `<kpi>_per_instance` is known; the
        stage-level value is a mean of M instance runs, which are taken to
        carry the measured relative noise `cv` each, independently, so its
        variance is `cv² · Σ_i seed_mean_i² / M²` over the seed's per-instance
        means in the stage-level value's units (`seed_means[kpi]`: percent of
        the seed under `normalize: baseline`, else raw). Without those means,
        or at a mean of 0 where no ratio exists, the same absolute noise on
        every instance: `var / M`. `elements` names the vector a KPI's element
        goes to (default `<kpi>_per_instance`; `None`: none, for a derived
        scalar such as `<obj>_raw` whose element is the evaluator's own).

        A real candidate's entry for the same KPI makes this one ignored by
        every accessor, and a repeatable stage never reads it: it is still
        reported, as `sd_across`."""
        self._paired_cache.clear()
        stage = self._stage(key)
        m = max(1, int(instances))
        for kpi, raw in kpi_values.items():
            values = [_number(v) for v in raw]
            if len(values) < 2:
                continue
            mean, var, runs = fmean(values), variance(values), len(values)
            entry = _group(stage, "kpis", kpi)
            if instance is None:
                entry["across"][CALIBRATION_ID] = {"runs": runs, "mean": mean, "var": var}
                continue
            if not 0 <= instance < m:
                raise ValueError(f"calibrate: instance {instance} is not one of {m}")
            means = (seed_means or {}).get(kpi)
            if means is not None and len(means) == m and mean != 0.0:
                scalar = var / mean**2 * sum(float(x) ** 2 for x in means) / m**2
                scalar_mean: float | None = fmean(float(x) for x in means)
            else:
                scalar, scalar_mean = var / m, None
            entry["across"][CALIBRATION_ID] = {"runs": runs, "mean": scalar_mean, "var": scalar}
            vector_key = (elements or {}).get(kpi, f"{kpi}_per_instance")
            if vector_key is not None:
                self.calibrate_element(key, vector_key, values, instance=instance, instances=m)

    def calibrate_element(
        self, key: str, vector_key: str, values: Sequence[float], *, instance: int, instances: int
    ) -> None:
        """One element of a vector KPI from the seed's runs on instance
        `instance` of `instances`, in that vector's units; the others unknown."""
        self._paired_cache.clear()
        values = [_number(v) for v in values]
        m = max(1, int(instances))
        if len(values) < 2 or not 0 <= instance < m:
            return
        row: list[list | None] = [None] * m
        row[instance] = [fmean(values), variance(values), len(values)]
        vector = _group(self._stage(key), "vectors", vector_key)
        vector["across"][CALIBRATION_ID] = row


# -- the seed run again: shared by the cascade and preflight ---------------------


def measurable(stage: "StageConfig") -> bool:
    """Can the seed be run again on a chosen seed: a command with `{seed}`."""
    return stage.kind == "command" and "{seed}" in stage.command


def check_seeds(stage: "StageConfig") -> tuple[int, ...]:
    """The extra runs: seed 0 again (the repeatability check) and, only where
    nothing replicates (`seeds: 1`), seed 1 for the calibration."""
    return (0,) if stage.seeds > 1 else (0, 1)


def _median_index(stage: "StageConfig", outcome: "StageOutcome") -> int | None:
    """The instance whose seed run took the median time: sorted by duration
    (ties by position), position `(M - 1) // 2`. `None` without instances."""
    m = len(stage.instances)
    if m == 0:
        return None
    durations = list(outcome.instance_durations)
    if len(durations) != m:
        return (m - 1) // 2  # no timings to go by: the middle one
    return sorted(range(m), key=lambda i: (durations[i], i))[(m - 1) // 2]


def run_duration(stage: "StageConfig", outcome: "StageOutcome") -> float:
    """How long one of the seed's runs took where the check runs it again:
    on the median-time instance, else one run of the whole stage."""
    index = _median_index(stage, outcome)
    if index is not None and len(outcome.instance_durations) == len(stage.instances):
        return float(outcome.instance_durations[index])
    return float(outcome.duration_s) / max(1, outcome.runs)


def check_stage(stage: "StageConfig", outcome: "StageOutcome") -> "StageConfig":
    """The stage the extra runs are made on: on a per-instance stage, only
    the instance whose seed run took the median time -- a typical one, neither
    the quickest nor the one that decides the clock; without instances the
    stage itself, on its whole input set."""
    index = _median_index(stage, outcome)
    if index is None:
        return stage
    return dataclasses.replace(stage, instances=(stage.instances[index],))


@dataclass(frozen=True)
class Check:
    """What the seed run again found at one stage."""

    repeatable: bool
    instance: str | None
    """The instance the extra runs were made on, as people name it; `None`
    on a stage without instances."""
    runs: int
    """The seed's runs that were compared there: its own, and the extra ones."""
    calibrated: bool
    """Whether the runs also calibrated the across estimate (`seeds: 1`)."""


def record_check(
    noise: Noise,
    key: str,
    stage: "StageConfig",
    first: "StageOutcome",
    extra: "StageOutcome",
    *,
    objective: str,
    digits: int,
    ignore: Iterable[str] = (),
) -> Check | None:
    """Compare the seed's first run on seed 0 (`first`, its outcome at
    `stage`) with the repeat (`extra`, the outcome of `check_seeds(stage)` on
    `check_stage(stage, first)`), and record what that says in `noise`.

    Repeatable iff every KPI the repeat reported (but `ignore`d ones, which
    are not about behaviour: a run time) equals the first run's after
    `round_significant(·, digits)`; set on `key` and `key/private` (the same
    command). At `seeds: 1` the three runs -- first, repeat, seed 1 --
    calibrate `key` (`Noise.calibrate`). `None`, and nothing recorded, when
    the two cannot be compared: the seed's first runs have no seed 0, or no
    KPI is in both."""
    if 0 not in first.seed_list or 0 not in extra.seed_list:
        return None
    j0, e0 = first.seed_list.index(0), extra.seed_list.index(0)
    index = _median_index(stage, first)
    skip = set(ignore)
    names = list(first.instance_names)
    m = len(names) if index is not None else 1

    firsts: dict[str, float] = {}
    others: dict[str, list[float]] = {}
    for kpi, runs in extra.seed_runs.items():
        if len(runs) != len(extra.seed_list):
            continue
        if index is not None:
            rows = first.seed_vectors.get(f"{kpi}_per_instance")
            if not rows or len(rows) <= j0 or len(rows[j0]) != m:
                continue
            value = rows[j0][index]
        else:
            base = first.seed_runs.get(kpi)
            if not base or len(base) <= j0:
                continue
            value = base[j0]
        firsts[kpi] = float(value)
        others[kpi] = [float(runs[e0])] + [float(v) for i, v in enumerate(runs) if i != e0]
    compared = [kpi for kpi in firsts if kpi not in skip]
    if not compared:
        return None

    def same(a: float, b: float) -> bool:
        return round_significant(a, digits) == round_significant(b, digits)

    repeatable = all(same(firsts[kpi], others[kpi][0]) for kpi in compared)
    if index is None:
        # An evaluator's own lists on a stage without instances are part of
        # what it does, as in the behaviour signature.
        for name, rows in extra.seed_vectors.items():
            base_rows = first.seed_vectors.get(name)
            if not base_rows or len(base_rows) <= j0 or len(rows) <= e0 or len(rows[e0]) != len(base_rows[j0]):
                continue
            repeatable = repeatable and all(same(a, b) for a, b in zip(base_rows[j0], rows[e0]))

    for target in (key, f"{key}/private"):
        noise.set_repeatable(target, repeatable)
    calibrated = stage.seeds <= 1 and len(extra.seed_list) >= 2
    if calibrated:
        values = {kpi: [firsts[kpi], *others[kpi]] for kpi in firsts}
        seed_means: dict[str, Sequence[float]] | None = None
        elements: dict[str, str | None] = {}
        pct_name = f"{objective}_pct_of_baseline"
        pct = first.vector_kpis.get(pct_name) if index is not None else None
        raw_means = first.vector_kpis.get(f"{objective}_per_instance")
        baseline = pct is not None and len(pct) == m and raw_means is not None and len(raw_means) == m
        if index is not None:
            seed_means = {}
            for kpi in values:
                means = pct if kpi == objective and baseline else first.vector_kpis.get(f"{kpi}_per_instance")
                if means is not None:
                    seed_means[kpi] = means
        if baseline and objective in values:
            # `normalize: baseline`: the objective is in percent of the seed, so
            # are its scalar and its percent vector; `<obj>_raw` and
            # `<obj>_per_instance` stay raw (Decision 4). The reference on the
            # instance is `100 · raw_i / pct_i`; scaling the runs by
            # `100 / ref_i` scales their variance by `(100 / ref_i)²`.
            values[f"{objective}_raw"] = list(values[objective])
            seed_means[f"{objective}_raw"] = raw_means
            elements[f"{objective}_raw"] = None
            if pct[index] != 0.0:
                ref = 100.0 * raw_means[index] / pct[index]
                if ref != 0.0:
                    noise.calibrate_element(key, pct_name, [100.0 * v / ref for v in values[objective]],
                                            instance=index, instances=m)
        noise.calibrate(key, values, instance=index, instances=m, seed_means=seed_means, elements=elements)
    return Check(
        repeatable=repeatable,
        instance=names[index] if index is not None and index < len(names) else None,
        runs=len(first.seed_list) + len(extra.seed_list),
        calibrated=calibrated,
    )


def about_pct(cv: float) -> str:
    """A relative noise in percent, to one significant digit: a calibration
    has 2 degrees of freedom, and more digits would claim what it cannot."""
    return f"{round_significant(100.0 * float(cv), 1):g}"
