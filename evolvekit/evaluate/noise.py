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
  the *difference* of two candidates on the same seeds does. For a candidate
  with >= 2 seeds the seed also ran, `d_s = x_s - seed_s`; its sample variance
  `v_d` over the shared seeds, df-weighted over candidates.

Every accessor returns the noise of **one side** of a comparison: across, the
variance of one run; paired, `v_d / 2` (the difference of two independent
sides then has `2·sd²` again, so a rate derived for independent sides holds).
Which estimate stands behind an accessor is the stage's repeatability: paired
when `repeatable(key)` is true, across otherwise (false, or not yet measured).
With nothing to pool the noise is 0, which is how every rule read before.

Vectors are pooled the same way, per vector key and per element
(`<kpi>_per_instance`, `<obj>_pct_of_baseline`, an evaluator's own list), each
in its own units. The whole estimate is kept in `work/noise.json`.

**The seed run again** (once per run, `Cascade._check_noise` and `preflight`):
when the final stage's command has a `{seed}`, the seed is run once more on
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
SCHEMA = 1


@dataclass(frozen=True)
class _Pool:
    """What one accessor pools: one value per (entry, element)."""

    variances: list[float]
    dfs: list[int]

    def across(self) -> float | None:
        return fmean(self.variances) if self.variances else None

    def paired(self) -> float | None:
        total = sum(self.dfs)
        if total <= 0:
            return None
        return sum(df * v for df, v in zip(self.dfs, self.variances)) / total / 2.0


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
    }


def _vector_group(entry: Any) -> dict[str, Any]:
    entry = _mapping(entry)
    across, paired = _element(_across_element), _element(_paired_element)
    return {
        "across": _entries(entry.get("across", {}), lambda es: [across(e) for e in _sequence(es)]),
        "paired": _entries(entry.get("paired", {}), lambda es: [paired(e) for e in _sequence(es)]),
    }


def _seed(raw: Any) -> dict[str, Any]:
    """The seed's runs: one unit -- seeds and values only mean something together."""
    seed = _mapping(raw)
    return {
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
    return stage


class Noise:
    """The run's noise estimate, per stage key and per KPI (see the module)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._stages: dict[str, dict[str, Any]] = {}

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

    def observe(self, key: str, candidate_id: str, outcome: "StageOutcome", *, is_seed: bool = False) -> None:
        """Record one candidate's outcome at stage `key` (replacing an earlier
        one of the same id: an interrupted generation evaluated again must not
        count twice). Only a finished outcome counts -- one that failed, was
        raced out or abandoned carries no replicate -- and only one with two or
        more seeds says anything about noise. The seed's runs are kept, one seed
        or more, as the reference of the other candidates' paired differences."""
        # The latest observation of an id is the one that counts, even when it
        # brings no replicate: what it replaced is forgotten first.
        if key in self._stages:
            self._forget(self._stages[key], candidate_id)
        if not outcome.ok or outcome.raced_out or outcome.abandoned:
            return
        stage = self._stage(key)
        if is_seed:
            stage["seed"] = {
                "seeds": list(outcome.seed_list),
                "kpis": {k: list(v) for k, v in outcome.seed_runs.items()},
                "vectors": {k: [list(row) for row in rows] for k, rows in outcome.seed_vectors.items()},
            }
        seeds = len(outcome.seed_list) or outcome.runs
        if seeds < 2:
            return
        for kpi, var in outcome.kpi_var.items():
            entry = stage["kpis"].setdefault(kpi, {"across": {}, "paired": {}})
            entry["across"][candidate_id] = {"runs": seeds, "mean": outcome.kpis.get(kpi), "var": float(var)}
        for name, variances in outcome.vector_var.items():
            means = outcome.vector_kpis.get(name)
            if means is None or len(means) != len(variances):
                means = [None] * len(variances)
            entry = stage["vectors"].setdefault(name, {"across": {}, "paired": {}})
            entry["across"][candidate_id] = [[m, float(v), seeds] for m, v in zip(means, variances)]
        if not is_seed and stage.get("seed"):
            self._pair(stage, candidate_id, outcome)

    def _pair(self, stage: dict[str, Any], candidate_id: str, outcome: "StageOutcome") -> None:
        """The paired entries: differences to the seed's run on the same seed."""
        reference = stage["seed"]
        where = {seed: j for j, seed in enumerate(reference["seeds"])}
        shared = [(i, where[seed]) for i, seed in enumerate(outcome.seed_list) if seed in where]
        if len(shared) < 2:
            return
        df = len(shared) - 1
        for kpi, runs in outcome.seed_runs.items():
            base = reference["kpis"].get(kpi)
            if base is None or len(base) != len(reference["seeds"]) or len(runs) != len(outcome.seed_list):
                continue
            d = [runs[i] - base[j] for i, j in shared]
            entry = stage["kpis"].setdefault(kpi, {"across": {}, "paired": {}})
            entry["paired"][candidate_id] = {"df": df, "var": variance(d)}
        for name, rows in outcome.seed_vectors.items():
            base_rows = reference["vectors"].get(name)
            if base_rows is None or len(base_rows) != len(reference["seeds"]) or len(rows) != len(outcome.seed_list):
                continue
            width = len(rows[0]) if rows else 0
            if any(len(row) != width for row in rows) or any(len(row) != width for row in base_rows):
                continue  # vectors of another length are other elements
            elements = []
            for e in range(width):
                d = [rows[i][e] - base_rows[j][e] for i, j in shared]
                elements.append([df, variance(d)])
            entry = stage["vectors"].setdefault(name, {"across": {}, "paired": {}})
            entry["paired"][candidate_id] = elements

    def repeatable(self, key: str) -> bool | None:
        stage = self._stages.get(key)
        return None if stage is None else stage.get("repeatable")

    def set_repeatable(self, key: str, value: bool) -> None:
        self._stage(key)["repeatable"] = bool(value)

    # -- reading ---------------------------------------------------------

    def _kpi_pool(self, key: str, kpi: str, kind: str, *, calibration: bool = True) -> _Pool:
        entry = self._stages.get(key, {}).get("kpis", {}).get(kpi)
        if entry is None:
            return _Pool([], [])
        if kind == "across":
            chosen = list(_across(entry["across"], lambda e: e["runs"] >= 2, calibration=calibration).values())
            return _Pool([e["var"] for e in chosen], [e["runs"] - 1 for e in chosen])
        chosen = [e for e in entry["paired"].values() if e["df"] >= 1]
        return _Pool([e["var"] for e in chosen], [e["df"] for e in chosen])

    def _element_pool(self, key: str, name: str, index: int, kind: str, *, calibration: bool = True) -> _Pool:
        entry = self._stages.get(key, {}).get("vectors", {}).get(name)
        if entry is None:
            return _Pool([], [])

        def usable(elements: list) -> bool:
            if index >= len(elements) or elements[index] is None:
                return False
            return elements[index][2] >= 2 if kind == "across" else elements[index][0] >= 1

        group = _across(entry["across"], usable, calibration=calibration) if kind == "across" else {
            cid: elements for cid, elements in entry["paired"].items() if usable(elements)
        }
        variances, dfs = [], []
        for elements in group.values():
            element = elements[index]
            variances.append(element[1])
            dfs.append(element[2] - 1 if kind == "across" else element[0])
        return _Pool(variances, dfs)

    def sd(self, key: str, kpi: str) -> float:
        """One side's noise of one run of the stage-level value of `kpi`."""
        if self.repeatable(key):
            value = self._kpi_pool(key, kpi, "paired").paired()
        else:
            value = self._kpi_pool(key, kpi, "across").across()
        return math.sqrt(value) if value is not None else 0.0

    def sd_across(self, key: str, kpi: str) -> float:
        """The across-seed estimate, whatever the stage's repeatability."""
        value = self._kpi_pool(key, kpi, "across").across()
        return math.sqrt(value) if value is not None else 0.0

    def vector_sd(self, key: str, vector_key: str, index: int) -> float | None:
        """`sd` for one element of a vector KPI, in that vector's units;
        `None` when nothing is known about that element."""
        if self.repeatable(key):
            paired = self._element_pool(key, vector_key, index, "paired").paired()
            if paired is not None:
                return math.sqrt(paired)
            # Repeatable, with across entries for the element but no pair yet
            # (only the seed replicated): no paired noise is measured, and on a
            # repeatable stage that reads as 0 -- today. At `seeds: 1` there is
            # no across entry either, so: None. The calibration is never read
            # on a repeatable stage, not even as "replicated".
            across = self._element_pool(key, vector_key, index, "across", calibration=False).across()
            return 0.0 if across is not None else None
        across = self._element_pool(key, vector_key, index, "across").across()
        return math.sqrt(across) if across is not None else None

    def cv(self, key: str, kpi: str) -> float:
        """The relative one-side noise of one run on one element: per instance
        on a per-instance stage (`<kpi>_per_instance`), else from the scalar
        entries. Elements whose mean is 0 have no ratio and are left out."""
        name = f"{kpi}_per_instance"
        entry = self._stages.get(key, {}).get("vectors", {}).get(name)
        if entry is not None and (entry["across"] or entry["paired"]):
            return self.vector_cv(key, name)
        stage = self._stages.get(key, {})
        entry = stage.get("kpis", {}).get(kpi)
        if entry is None:
            return 0.0
        ratios: list[float] = []
        if self.repeatable(key):
            base = (stage.get("seed") or {}).get("kpis", {}).get(kpi)
            mean = fmean(base) if base else 0.0
            if mean != 0.0:
                ratios = [e["var"] / 2.0 / mean**2 for e in entry["paired"].values() if e["df"] >= 1]
        else:
            ratios = [
                e["var"] / e["mean"] ** 2
                for e in _across(entry["across"], lambda e: e["runs"] >= 2).values()
                if e["mean"] is not None and e["mean"] != 0.0
            ]
        return math.sqrt(fmean(ratios)) if ratios else 0.0

    def vector_cv(self, key: str, vector_key: str) -> float:
        """`cv` over every (entry, element) of one vector KPI."""
        stage = self._stages.get(key, {})
        entry = stage.get("vectors", {}).get(vector_key)
        if entry is None:
            return 0.0
        ratios: list[float] = []
        if self.repeatable(key):
            rows = (stage.get("seed") or {}).get("vectors", {}).get(vector_key) or []
            means = [fmean(column) for column in zip(*rows)] if rows else []
            for elements in entry["paired"].values():
                for i, element in enumerate(elements):
                    if element is None or element[0] < 1 or i >= len(means) or means[i] == 0.0:
                        continue
                    ratios.append(element[1] / 2.0 / means[i] ** 2)
        else:
            replicated = _across(entry["across"], lambda es: any(e is not None and e[2] >= 2 for e in es))
            for elements in replicated.values():
                for element in elements:
                    if element is None or element[2] < 2 or element[0] is None or element[0] == 0.0:
                        continue
                    ratios.append(element[1] / element[0] ** 2)
        return math.sqrt(fmean(ratios)) if ratios else 0.0

    def score_sd(self, key: str, score: "ScoreConfig", seeds: int) -> float:
        """One candidate's stage score, a mean over `seeds` runs: the weighted
        sum's sd, `sqrt(Σ w_k² · sd_k²) / √seeds`, over `score.weights` only:
        the penalty terms are taken to carry no noise. Levels compare goal by
        goal and have no such sum: 0."""
        if score.levels:
            return 0.0
        weights = score.weights or {score.objective: 1.0}
        total = sum(w * w * self.sd(key, kpi) ** 2 for kpi, w in weights.items())
        return math.sqrt(total) / math.sqrt(max(1, seeds))

    def known(self, key: str) -> bool:
        """Any estimate, across or paired, with at least one degree of freedom."""
        stage = self._stages.get(key)
        if stage is None:
            return False
        for entry in stage["kpis"].values():
            if any(e["runs"] >= 2 for e in entry["across"].values()):
                return True
            if any(e["df"] >= 1 for e in entry["paired"].values()):
                return True
        for entry in stage["vectors"].values():
            for elements in entry["across"].values():
                if any(e is not None and e[2] >= 2 for e in elements):
                    return True
            for elements in entry["paired"].values():
                if any(e is not None and e[0] >= 1 for e in elements):
                    return True
        return False

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
        stage = self._stage(key)
        m = max(1, int(instances))
        for kpi, raw in kpi_values.items():
            values = [_number(v) for v in raw]
            if len(values) < 2:
                continue
            mean, var, runs = fmean(values), variance(values), len(values)
            entry = stage["kpis"].setdefault(kpi, {"across": {}, "paired": {}})
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
        values = [_number(v) for v in values]
        m = max(1, int(instances))
        if len(values) < 2 or not 0 <= instance < m:
            return
        row: list[list | None] = [None] * m
        row[instance] = [fmean(values), variance(values), len(values)]
        vector = self._stage(key)["vectors"].setdefault(vector_key, {"across": {}, "paired": {}})
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
