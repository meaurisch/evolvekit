"""Groups of cases: which cases are alike, so a study can learn settings per
kind of case (design §6.4).

- **Features** describe a case before anything solves it: the harness's
  `features:` (one SQL value per case over the request tables), else every
  request table's row count (`rows_<table>`) and the mean of every numeric
  request column that is not private (`mean_<table>_<column>`). The request
  tables come from the SDK's own `solve --tables-out` at the starting values,
  with the preview's time limit (decision D33), kept per case file in
  `<study>/groups/tables/` and solved again only when the case or the
  application changes.
- **Tags** are what people say a case is (`cases/<file>.tags`, one line, from
  the app's Cases list). As soon as one case has a tag, tags decide, and the
  untagged cases form `untagged` (D35).
- **k-means** over the cases to learn from only (the held-back cases never
  shape the groups they are checked in): their features with every count
  (`rows_*`) as `log1p` (`transformed`), standardised; k-means++ starts and 20
  seeded restarts; the best silhouette among k = 2 and 3 whose groups each keep
  two cases to learn from (D36). A grouping that leaves any group with fewer is
  no grouping, and neither is one whose best silhouette is below
  `MIN_SILHOUETTE` (0.5, "reasonable structure"): all cases are one group. A
  number of groups asked for (`k`) skips the 0.5.
- **The rule** (`rule_stump`): the one split over the raw features of the cases
  to learn from that best tells their groups apart. The held-back cases are put
  in groups by it (`assign`), so the final check validates the rule a planner
  will deploy; a held-back case it cannot read (its feature missing or not
  finite) goes to the nearest group's centre, else the first group, and a note
  says so. `accuracy` says how often the rule agrees with the groups: on the
  cases to learn from, with their own groups; on the held-back cases, with the
  nearest group's centre.
- **Small groups merge** (`merge_small_groups`): a group with fewer than
  `MIN_HELD_BACK` held-back cases cannot be checked, so a per-group run joins it
  to its nearest group first -- by its features, or, with tags only, the group
  with the most held-back cases -- and records the merge (`merged_from`); a
  grouping by the rule then fits the rule again and places the held-back cases
  by it again.

`compute` writes `<study>/groups.json` (`"format": 2`); `read_groups` reads it
back while it still describes the study's cases. A format-1 file grouped by
features placed its held-back cases by k-means, so it is computed again; one
grouped by tags means the same and is read as it is.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import shutil
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from evolvekit.harness import HarnessError, execute
from evolvekit.harness.compile import base_settings, runner_input, runner_settings
from evolvekit.harness.library import slugify
from evolvekit.harness.manifest import Harness
from evolvekit.harness.sdk import evk_harness as evk
from evolvekit.harness.study import Study, load_study
from evolvekit.ledger import _atomic_write

__all__ = [
    "SolveFailed", "COUNT_PREFIX", "FORMAT", "GROUPS", "KS", "MIN_HELD_BACK", "MIN_SILHOUETTE", "MIN_TRAINING",
    "RESTARTS", "TAGS", "TAG_MAX",
    "assign", "case_tables", "compute", "features_of", "kmeans", "read_groups", "read_tag", "rule_lines", "rule_stump",
    "silhouette", "compare", "similarity", "study_features", "Z2_CAP", "cache_stump", "stump_of",
    "merge_small_groups", "slug_of",
    "standardise", "transformed", "write_tag",
]

GROUPS = "groups.json"
"""`<study>/groups.json`: the groups `compute` found, and how."""
TAGS = ".tags"
"""The tag of `cases/a.json` is the first line of `cases/a.json.tags`."""
MIN_TRAINING = 2
"""Cases to learn from that every group needs."""
MIN_HELD_BACK = 2
"""Held-back cases a group needs to be checked on its own; a group with fewer joins another (`merge_small_groups`)."""
KS = (2, 3)
"""The numbers of groups tried when none is given."""
RESTARTS = 20
TAG_MAX = 40
STEPS = 100
"""Lloyd's steps at most, per restart."""
FOLDER = "groups"
"""`<study>/groups/tables/`: the request tables of each case, kept."""
ONE_GROUP = "a group needs at least two cases to learn from; all cases are one group"
FORMAT = 2
"""`groups.json`'s format: 2 places the held-back cases by the rule (`assign`)."""
MIN_SILHOUETTE = 0.5
"""The best silhouette that groups by features need when no number of groups is asked for
(Kaufman & Rousseeuw's "reasonable structure"); below it, all cases are one group."""
COUNT_PREFIX = "rows_"
"""Features named so are counts: they enter the clustering as `log1p` (`transformed`)."""
NO_SPLIT_PREFIX = "no one-split rule tells these "
UNREAD = "the rule cannot read it"
UNPLACED = "no feature places it"
NO_RULE = "no single measure tells the kinds apart, so no rule could be checked; all cases are one group"


class SolveFailed(HarnessError):
    """The application did not solve a case for its tables (as a failed preview: exit 1)."""


# ---------------------------------------------------------------------------
# tags
# ---------------------------------------------------------------------------


def _tag_path(root: Path, case: str) -> Path:
    path = Path(root).joinpath(*str(case).replace("\\", "/").split("/"))
    return path.with_name(path.name + TAGS)


def _has_slug(tag: str) -> bool:
    """Whether the tag names a group's folder of its own (`slug_of`): a letter or a digit."""
    return re.search(r"[a-z0-9]", tag.lower()) is not None


def read_tag(root: Path, case: str) -> str:
    """The case's tag: the first line of its `.tags` file with a BOM, CR and
    surrounding space stripped (a file written by hand on Windows reads the
    same); `""` when there is none."""
    try:
        data = _tag_path(root, case).read_bytes()
    except OSError:
        return ""
    text = data.decode("utf-8-sig", errors="replace").replace("\ufeff", "")
    for line in text.replace("\r", "\n").split("\n"):
        if line.strip():
            return line.strip()[:TAG_MAX].strip()
    return ""


def write_tag(root: Path, case: str, tag: str) -> None:
    """Give the case a tag (one line, LF); `""` takes it away."""
    tag = str(tag).strip()
    if "\n" in tag or "\r" in tag:
        raise HarnessError(f"{case}: a tag is one line")
    if len(tag) > TAG_MAX:
        raise HarnessError(f"{case}: a tag is at most {TAG_MAX} characters, this one is {len(tag)}")
    if tag and not _has_slug(tag):
        raise HarnessError(f"{case}: a tag needs a letter or a digit")
    path = _tag_path(root, case)
    if not tag:
        path.unlink(missing_ok=True)
        return
    _atomic_write(path, tag + "\n")


# ---------------------------------------------------------------------------
# the request tables of a case, and its features
# ---------------------------------------------------------------------------


def case_tables(root: Path, case: str, *, say: Callable[[str], None] | None = None) -> Path:
    """The case's tables as `solve --tables-out` writes them, at the starting
    values and the preview's time limit: `<study>/groups/tables/<file>.sqlite`,
    solved again only when the case file or the application changes. `say`
    hears one line before each solve."""
    root = Path(root)
    study = load_study(root)
    harness = execute.study_harness(root)
    name = Path(case).name
    folder = root / FOLDER / "tables"
    target, stamp_file = folder / f"{name}.sqlite", folder / f"{name}.json"
    try:
        stat = (root / case).stat()
    except OSError:
        raise HarnessError(f"{case}: the case file is not there") from None
    stamp = [stat.st_size, stat.st_mtime_ns, study.application_path]
    if _cached(root, study, case) is not None:
        return target
    if say is not None:
        say(f"solving {Path(case).stem} for its features …")
    # The case as it is: no data change, no rule; one folder per solve, so two never meet.
    kept = replace(study, constraints=[], data={})
    work = root / FOLDER / "work" / f"{name}.{os.getpid()}.{threading.get_ident()}"
    shutil.rmtree(work, ignore_errors=True)
    up = "../../.."
    try:
        study_run = runner_input(kept, harness, runner_settings(kept, harness, root), kpis={}, guardrails=False, up=up)
        result = execute._solve_once(  # noqa: SLF001 -- the preview's one solve, kept per case
            root, kept, harness, work, case=case, values=execute._starting_values(kept, base_settings(kept, harness, root)),  # noqa: SLF001
            study_run=study_run, time_limit=execute.preview_time_limit(kept, harness), tables_out=True, up=up,
        )
        if not result.get("ok"):
            raise SolveFailed(f"{case}: the solve that shows its tables failed: {result.get('error')}")
        if not (work / "tables.sqlite").is_file():
            raise SolveFailed(f"{case}: the solve wrote no tables")
        folder.mkdir(parents=True, exist_ok=True)
        os.replace(work / "tables.sqlite", target)
        _atomic_write(stamp_file, json.dumps(stamp) + "\n")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target


def _cached(root: Path, study: Study, case: str) -> Path | None:
    """The case's kept tables while their stamp still matches the case file and the application
    (as `case_tables` checks it); `None` otherwise. Never solves."""
    name = Path(case).name
    folder = Path(root) / FOLDER / "tables"
    target, stamp_file = folder / f"{name}.sqlite", folder / f"{name}.json"
    try:
        stat = (Path(root) / case).stat()
        if target.is_file() and json.loads(stamp_file.read_text(encoding="utf-8")) == \
                [stat.st_size, stat.st_mtime_ns, study.application_path]:
            return target
    except (OSError, ValueError):
        pass
    return None


def _defaults(harness: Harness) -> dict[str, tuple[str, str]]:
    """The features without `features:`: `{name: (sql, words)}`."""
    out: dict[str, tuple[str, str]] = {}
    for table in harness.request_tables:
        out[f"rows_{table}"] = (f'SELECT COUNT(*) FROM "{table}"', f"the number of rows in {table}")
        for column in harness.tables[table].columns.values():
            if column.type in ("int", "float") and not column.private:
                out[f"mean_{table}_{column.name}"] = (f'SELECT AVG("{column.name}") FROM "{table}"',
                                                     f"the mean {column.name} in {table}")
    return out


def _features(harness: Harness) -> dict[str, tuple[str, str]]:
    if harness.features:
        return {name: (f.sql, f.describe or name.replace("_", " ")) for name, f in harness.features.items()}
    return _defaults(harness)


def features_of(root: Path, case: str, *, tables: Callable[[Path, str], Path] = case_tables) -> dict[str, float]:
    """The case's features: each declared feature's first value, else the row
    counts and column means (`_defaults`)."""
    root = Path(root)
    harness = execute.study_harness(root)
    path = tables(root, case)
    source = sqlite3.connect(path)
    db = sqlite3.connect(":memory:")
    try:
        source.backup(db)
    finally:
        source.close()  # nothing keeps the kept file open (Windows)
    clock = evk.guard(db)
    try:
        out = {}
        for name, (sql, _) in _features(harness).items():
            try:
                out[name] = evk.query_value(db, sql, clock=clock)[0]
            except evk.HarnessStop as exc:
                raise HarnessError(f"features.{name}: {case}: {exc}") from None
        return out
    finally:
        db.close()


def study_features(root: Path, study: Study) -> dict[str, dict[str, float]] | None:
    """Each case's features (cases to learn from and held-back cases) from the tables `study groups`
    kept for it (`<study>/groups/tables/`, while their stamp matches); `None` when any case has none
    kept, or its features cannot be read. Never solves a case (a warm start reads, it does not run)."""
    root = Path(root)
    cases = list(study.training) + list(study.test)
    if not cases:
        return None
    kept = {case: _cached(root, study, case) for case in cases}
    if any(path is None for path in kept.values()):
        return None
    try:
        return {case: features_of(root, case, tables=lambda _root, c: kept[c]) for case in cases}
    except (HarnessError, OSError, sqlite3.Error, ValueError):
        return None


Z2_CAP = 9.0
"""Each feature's squared distance counts at most this (three sds), so one nearly fixed feature cannot
take the whole prior away (decision 14)."""


def compare(a: dict[str, dict[str, float]] | None,
            b: dict[str, dict[str, float]] | None) -> tuple[float, list[str]]:
    """`(similarity, left_out)` of two studies' cases (`{case: features}`), decisions 5 and 14.

    Over the features every case of both has as a finite number (`transformed`, so counts on a log
    scale): `z² = ((centre_a - centre_b) / sd)²`, `sd` the within-study pooled sd (population form:
    each case's deviation from its own study's centre -- an sd over both studies would contain the
    shift itself), each `z²` capped at `Z2_CAP`; `d² = max(0, mean z² - (1/n_a + 1/n_b))`, the mean
    so the factor does not fall with the number of features, less what sampling alone gives two
    studies of the same kind; the similarity is `exp(-d²/2)`, in (0, 1]. A feature with no spread
    inside either study cannot be standardised: it is left out and named in `left_out`. 1.0 when
    either side is `None` or empty, or no feature is left."""
    if not a or not b:
        return 1.0, []
    rows_a = [transformed(row) for row in a.values() if isinstance(row, dict)]
    rows_b = [transformed(row) for row in b.values() if isinstance(row, dict)]
    if len(rows_a) != len(a) or len(rows_b) != len(b):
        return 1.0, []
    names = [n for n in rows_a[0] if all(_finite(row.get(n)) for row in rows_a + rows_b)]
    z2: list[float] = []
    left_out: list[str] = []
    for name in names:
        xs, ys = [float(row[name]) for row in rows_a], [float(row[name]) for row in rows_b]
        mean_a, mean_b = sum(xs) / len(xs), sum(ys) / len(ys)
        within = (sum((x - mean_a) ** 2 for x in xs) + sum((y - mean_b) ** 2 for y in ys)) / (len(xs) + len(ys))
        sd, gap = math.sqrt(within), abs(mean_a - mean_b)
        if sd <= 1e-12 * max(1.0, abs(mean_a), abs(mean_b)):
            left_out.append(name)
            continue
        z2.append(min(Z2_CAP, (gap / sd) ** 2))
    if not z2:
        return 1.0, left_out
    d2 = max(0.0, sum(z2) / len(z2) - (1 / len(rows_a) + 1 / len(rows_b)))
    return math.exp(-d2 / 2), left_out


def similarity(a: dict[str, dict[str, float]] | None, b: dict[str, dict[str, float]] | None) -> float:
    """The similarity of `compare`, without the features it left out."""
    return compare(a, b)[0]


# ---------------------------------------------------------------------------
# k-means, in the standard library
# ---------------------------------------------------------------------------


def _spread(points: list[list[float]]) -> list[tuple[int, float, float]]:
    """`(column, mean, sd)` of every column with a spread."""
    if not points:
        return []
    n, out = len(points), []
    for j in range(len(points[0])):
        values = [float(p[j]) for p in points]
        mean = sum(values) / n
        sd = math.sqrt(sum((v - mean) ** 2 for v in values) / n)
        if sd > 1e-12 * max(1.0, abs(mean)):
            out.append((j, mean, sd))
    return out


def standardise(points: list[list[float]]) -> list[list[float]]:
    """Each column as z-scores; a column with no spread is dropped."""
    columns = _spread(points)
    return [[(float(p[j]) - mean) / sd for j, mean, sd in columns] for p in points]


def transformed(features: dict[str, Any]) -> dict[str, Any]:
    """The features as the clustering reads them: every finite value of a count (a name starting
    `COUNT_PREFIX`) as `log1p`, so 30 and 60 rows are as far apart as 300 and 600; a count below 0,
    a value not finite and every other feature unchanged. Clustering, centres and nearest groups
    only: the rule reads the raw values (a monotone change moves neither its split nor its
    misplaced count, and it speaks in the case's own units)."""
    return {name: math.log1p(value) if name.startswith(COUNT_PREFIX) and _finite(value) and value >= 0 else value
            for name, value in features.items()}


def _d2(a: list[float], b: list[float]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b))


def _starts(points: list[list[float]], k: int, rng: random.Random) -> list[list[float]]:
    """k-means++: each next centre drawn with chance in proportion to its squared distance from the nearest so far."""
    centres = [list(rng.choice(points))]
    while len(centres) < k:
        d2 = [min(_d2(p, c) for c in centres) for p in points]
        total = sum(d2)
        if total <= 0:
            centres.append(list(rng.choice(points)))
            continue
        r, acc, chosen = rng.random() * total, 0.0, None
        for p, d in zip(points, d2):
            acc += d
            if acc > r:
                chosen = p
                break
        if chosen is None:
            chosen = max(zip(points, d2), key=lambda pd: pd[1])[0]
        centres.append(list(chosen))
    return centres


def _lloyd(points: list[list[float]], centres: list[list[float]]) -> tuple[list[int], float]:
    k, labels = len(centres), None
    for _ in range(STEPS):
        new = [min(range(k), key=lambda j: (_d2(p, centres[j]), j)) for p in points]
        if new == labels:
            break
        labels = new
        for j in range(k):
            members = [p for p, label in zip(points, labels) if label == j]
            if members:  # an empty group keeps its centre
                centres[j] = [sum(column) / len(members) for column in zip(*members)]
    assert labels is not None
    return labels, sum(_d2(p, centres[label]) for p, label in zip(points, labels))


def kmeans(points: list[list[float]], k: int, *, restarts: int = RESTARTS, seed: int = 0) -> list[int]:
    """Each point's group (0..k-1, numbered by first appearance): the restart
    with the least within-group sum of squares."""
    if not 1 <= k <= len(points):
        raise ValueError(f"k must be between 1 and the number of points ({len(points)}), got {k}")
    points = [[float(x) for x in p] for p in points]
    rng = random.Random(seed)
    best: tuple[float, list[int]] | None = None
    for _ in range(max(1, restarts)):
        labels, within = _lloyd(points, _starts(points, k, rng))
        if best is None or within < best[0] - 1e-12:
            best = (within, labels)
    assert best is not None
    order: dict[int, int] = {}
    for label in best[1]:
        order.setdefault(label, len(order))
    return [order[label] for label in best[1]]


def silhouette(points: list[list[float]], labels: list[int]) -> float:
    """The mean silhouette: near 1 when each point is much nearer its own
    group than the next; 0 for a single group (or a point alone in its own)."""
    groups = sorted(set(labels))
    if len(groups) < 2:
        return 0.0
    points = [[float(x) for x in p] for p in points]
    total = 0.0
    for i, p in enumerate(points):
        mean_to: dict[int, float] = {}
        for g in groups:
            others = [q for j, q in enumerate(points) if labels[j] == g and j != i]
            if others:
                mean_to[g] = sum(math.sqrt(_d2(p, q)) for q in others) / len(others)
        if labels[i] not in mean_to:
            continue  # alone in its group: 0
        a = mean_to.pop(labels[i])
        if not mean_to:
            continue
        b = min(mean_to.values())
        total += (b - a) / max(a, b) if max(a, b) > 0 else 0.0
    return total / len(points)


# ---------------------------------------------------------------------------
# the groups
# ---------------------------------------------------------------------------


def slug_of(name: str) -> str:
    """A group's folder name."""
    return slugify(name)


def _split(study: Study, members: set[str]) -> dict[str, list[str]]:
    return {"training": [c for c in study.training if c in members], "test": [c for c in study.test if c in members]}


def _fits(named: dict[str, dict[str, list[str]]]) -> bool:
    return all(len(group["training"]) >= MIN_TRAINING for group in named.values())


def _stamps(root: Path, cases: list[str]) -> dict[str, list[int] | None]:
    """Each case file's `[size, mtime_ns]`: a case replaced under its name is another case."""
    out: dict[str, list[int] | None] = {}
    for case in cases:
        try:
            stat = (Path(root) / case).stat()
            out[case] = [stat.st_size, stat.st_mtime_ns]
        except OSError:
            out[case] = None
    return out


Space = tuple[list[str], list[tuple[int, float, float]]]


def _space(features: dict[str, dict[str, Any]], training: list[str]) -> Space | None:
    """The clustering's space over the cases to learn from: the feature names (the first such
    case's) and `(index, mean, sd)` of each `transformed` one that spreads over the cases that have
    every one of them finite; `None` when there is none."""
    names, usable = _usable(features, training)
    points = [[float(transformed(features[c])[n]) for n in names] for c in usable]
    columns = _spread(points)
    return (names, columns) if columns else None


def _usable(features: dict[str, dict[str, Any]], training: list[str]) -> tuple[list[str], list[str]]:
    """The feature names (the first case's) that some case to learn from has as a finite number,
    and the cases to learn from that have every one of them so."""
    have = [c for c in training if isinstance(features.get(c), dict) and features[c]]
    if not have:
        return [], []
    names = [n for n in features[have[0]] if any(_finite(features[c].get(n)) for c in have)]
    return names, [c for c in have if all(_finite(features[c].get(n)) for n in names)]


def _clean(row: dict[str, Any]) -> dict[str, Any]:
    """A case's features as `groups.json` keeps them: a value that is not a finite number is `None`
    (JSON has no NaN)."""
    return {name: value if _finite(value) else None for name, value in row.items()}


def _project(space: Space, row: dict[str, Any]) -> list[float | None]:
    """The case in the space's standardised coordinates; `None` where its feature is missing or not finite."""
    names, columns = space
    values = transformed(row)
    return [(float(values[names[j]]) - mean) / sd if _finite(values.get(names[j])) else None for j, mean, sd in columns]


def _centres(space: Space, features: dict[str, dict[str, Any]], named: dict[str, Any]) -> dict[str, list[float]]:
    """Each group's centre over its cases to learn from that have every coordinate."""
    out: dict[str, list[float]] = {}
    for name, part in named.items():
        own = [point for c in part.get("training") or [] if isinstance(features.get(c), dict)
               and None not in (point := _project(space, features[c]))]
        if own:
            out[name] = [sum(column) / len(own) for column in zip(*own)]
    return out


def _nearest(space: Space | None, centres: dict[str, list[float]], row: dict[str, Any]) -> str | None:
    """The group whose centre is nearest the case, over the coordinates it has (ties: the first);
    `None` when it has none."""
    if space is None or not centres:
        return None
    point = _project(space, row)
    used = [i for i, value in enumerate(point) if value is not None]
    if not used:
        return None
    return min(centres, key=lambda name: sum((point[i] - centres[name][i]) ** 2 for i in used))


def _place(doc: dict[str, Any]) -> None:
    """A grouping by features, in place: the rule (`rule_stump`) fitted over its groups' cases to
    learn from (raw features), every held-back case put in a group by it (`assign`) -- one it
    cannot read goes to the nearest group's centre, else the first group, and a note says so --
    and `accuracy`. With one group, every held-back case is in it and there is no rule; with more
    groups than one split tells apart (an asked-for k of 4 or 5), each goes to the nearest."""
    named, notes = doc["groups"], doc.setdefault("notes", [])
    features = doc.get("features") or {}
    tests = [c for part in named.values() for c in part.get("test") or []]
    order = [c for c in doc.get("cases") or [] if c in set(tests)]
    order += [c for c in tests if c not in order]
    first = next(iter(named))
    # A merge places the cases again: what an earlier placing said no longer holds.
    said_before = (NO_SPLIT_PREFIX, *(f"{c}: {why}, so it goes to " for c in order for why in (UNREAD, UNPLACED)))
    notes[:] = [n for n in notes if not n.startswith(said_before)]
    if len(named) == 1:
        named[first]["test"] = order
        doc.update(assigned="one", stump=None)
        doc.pop("accuracy", None)
        return
    every = {c: name for name, part in named.items() for c in part.get("training") or []}
    _, fitted = _usable(features, list(every))  # a case to learn from without every feature shapes no rule
    labels = {c: every[c] for c in fitted}
    stump = rule_stump(features, labels) if len(named) <= 3 and len(set(labels.values())) == len(named) else None
    space = _space(features, list(every))
    centres = _centres(space, features, named) if space is not None else {}
    if stump is None:
        note = f"{NO_SPLIT_PREFIX}{len(named)} groups apart, so each held-back case goes to the nearest"
        if note not in notes:
            notes.append(note)
    placed: dict[str, list[str]] = {name: [] for name in named}
    right = read = 0
    for case in order:
        row = features.get(case) if isinstance(features.get(case), dict) else {}
        by_rule = assign(stump, row) if stump is not None else None
        near = _nearest(space, centres, row)
        read += by_rule is not None
        right += by_rule is not None and by_rule == near
        group = by_rule
        if group is None:
            group, how = (near, "the nearest") if near is not None else (first, "the first")
            said = UNREAD if stump is not None else UNPLACED
            note = f"{case}: {said}, so it goes to {group}, {how}"
            if note not in notes:
                notes.append(note)
        placed[group].append(case)
    for name, part in named.items():
        part["test"] = placed[name]
    doc["assigned"], doc["stump"] = ("stump" if stump is not None else "nearest"), stump
    if stump is None:
        doc.pop("accuracy", None)
        return
    # Only the cases the rule read: the notes name the others.
    doc["accuracy"] = {"training": {"right": len(labels) - stump["misplaced"], "of": len(labels)},
                       "held_back": {"right": right, "of": read}}


def _centroids(doc: dict[str, Any]) -> dict[str, list[float]] | None:
    """Each group's centroid in standardised features: for format 2 over the
    cases to learn from only, `transformed` (the clustering's own space); for
    a format-1 document over every case of `doc["features"]`. `None` without
    features (tags only), or when no feature tells the cases apart. A group
    none of whose cases has features has none."""
    features = doc.get("features") or {}
    if doc.get("format") == FORMAT:
        training = [c for part in doc["groups"].values() for c in part.get("training") or []]
        space = _space(features, training)
        return None if space is None else _centres(space, features, doc["groups"])
    cases = [c for c, row in features.items() if isinstance(row, dict) and row]
    if not cases:
        return None
    names = list(features[cases[0]])
    cases = [c for c in cases if all(n in features[c] for n in names)]
    points = standardise([[features[c][n] for n in names] for c in cases])
    if not points or not points[0]:
        return None
    where = dict(zip(cases, points))
    out: dict[str, list[float]] = {}
    for name, part in doc["groups"].items():
        own = [where[c] for c in list(part.get("training") or []) + list(part.get("test") or []) if c in where]
        if own:
            out[name] = [sum(column) / len(own) for column in zip(*own)]
    return out


def merge_small_groups(doc: dict[str, Any]) -> dict[str, Any]:
    """A new doc in which no group has fewer than `MIN_HELD_BACK` held-back
    cases (the argument is not changed). While more than one group exists and
    one is too small, the one with the fewest held-back cases (ties: the last
    in order) joins its nearest other group: by the distance between centroids
    in standardised features, or, without features (tags only), the group
    with the most held-back cases (ties: the first in order). The target keeps
    its name and place; its cases are its own followed by the merged group's;
    `merged_from` lists the names merged into it; `notes` says each merge. A
    document whose held-back cases the rule placed (`assigned` `stump` or
    `nearest`) fits the rule again after each merge and places them again
    (`_place`): its `stump`, groups' `test` and `accuracy` follow."""
    out = json.loads(json.dumps(doc))
    named = out["groups"]
    by_rule = out.get("format") == FORMAT and out.get("assigned") in ("stump", "nearest")
    centres = _centroids(out)
    while len(named) > 1:
        sizes = {name: len(part.get("test") or []) for name, part in named.items()}
        small = min(reversed(list(named)), key=lambda name: sizes[name])
        if sizes[small] >= MIN_HELD_BACK:
            break
        others = [name for name in named if name != small]
        if centres is not None and small in centres and all(name in centres for name in others):
            target = min(others, key=lambda name: _d2(centres[small], centres[name]))
            how = "the nearest by its features"
        else:
            target = max(others, key=lambda name: (sizes[name], -others.index(name)))
            how = "the one with the most held-back cases"
        n = sizes[small]
        out.setdefault("notes", []).append(
            f"{small} has {n} held-back case{'s' if n != 1 else ''}, so it joins {target}, {how}")
        part, into = named.pop(small), named[target]
        into["training"] = list(into.get("training") or []) + list(part.get("training") or [])
        into["test"] = list(into.get("test") or []) + list(part.get("test") or [])
        into["merged_from"] = list(into.get("merged_from") or []) + [small] + list(part.get("merged_from") or [])
        if by_rule:
            _place(out)
        if centres is not None:
            centres = _centroids(out)
    out["k"] = len(named)
    return out


def _tag_groups(study: Study, cases: list[str], tags: dict[str, str],
                notes: list[str]) -> dict[str, dict[str, list[str]]]:
    """The groups the tags make. Tags that share a folder name (`slug_of`: "Big"
    and "big", "north side" and "north-side") are one group, named by the first
    spelling in the cases' order; untagged cases form `untagged`, last."""
    label_of: dict[str, str] = {}
    spellings: dict[str, list[str]] = {}
    key_of: dict[str, str] = {}
    for case in cases:
        tag = tags[case]
        if tag and not _has_slug(tag):
            notes.append(f"{case}: its tag {tag!r} has no letter or digit, so it counts as untagged")
            tag = ""
        label = tag or "untagged"
        key = slug_of(label)
        label_of.setdefault(key, label)
        if label not in spellings.setdefault(key, []):
            spellings[key].append(label)
        key_of[case] = key
    for key, spelled in spellings.items():
        if len(spelled) > 1:
            others = ", ".join(repr(s) for s in spelled[1:])
            notes.append(f"{others} counted as {spelled[0]!r}: the same group")
    untagged = slug_of("untagged")
    order = sorted((key for key in label_of if key != untagged), key=lambda key: (label_of[key].lower(), key))
    order += [untagged] if untagged in label_of else []
    return {label_of[key]: _split(study, {c for c in cases if key_of[c] == key}) for key in order}


def compute(root: Path, *, k: int | None = None, tables: Callable[[Path, str], Path] = case_tables,
            features: bool = True) -> dict[str, Any]:
    """Group the study's training and held-back cases, write `groups.json` and return it.
    `features=False` (a per-group run's start): when tags decide the groups, no case is solved
    for its features, and `features` stays empty; without tags it has no effect."""
    wanted = features
    root = Path(root)
    study = load_study(root)
    harness = execute.study_harness(root)
    if k is not None and k < 2:
        raise HarnessError(f"k: at least 2 groups, got {k}")
    cases = list(study.training) + list(study.test)
    if not cases:
        raise HarnessError("cases: there are no cases to group yet; add some first")
    tags = {case: read_tag(root, case) for case in cases}
    by_tags = any(tags.values())
    notes: list[str] = []
    features: dict[str, dict[str, float]] = {}
    for case in cases:
        if not by_tags:
            features[case] = features_of(root, case, tables=tables)
            continue
        if not wanted:
            continue
        try:  # the tags decide; a case that cannot be solved only lacks its features
            features[case] = features_of(root, case, tables=tables)
        except HarnessError as exc:
            notes.append(f"features left out: {exc}")
    features = {case: _clean(row) for case, row in features.items()}
    names = list(next(iter(features.values()), {}))
    words = {name: said for name, (_, said) in _features(harness).items()}
    method, named, score = "one", {}, None
    if by_tags:
        named = _tag_groups(study, cases, tags, notes)
        if _fits(named):
            method = "tags"
        else:
            notes.append(ONE_GROUP)
    else:  # the cases to learn from alone make the groups; counts on a log scale
        used, training = _usable(features, list(study.training))
        if [n for n in names if n not in used] and training:
            notes.append(f"{', '.join(n for n in names if n not in used)}: not a number in any case to learn from, "
                         "so not used to tell them apart")
        left = [c for c in study.training if c not in training]  # placed after the groups are made
        raw = [[transformed(features[c])[n] for n in used] for c in training]
        spread = _spread(raw)
        kept = {j for j, _, _ in spread}
        same = [n for j, n in enumerate(used) if j not in kept]
        points = standardise(raw)
        if not spread:
            notes.append("the cases' features are the same in every case; all cases are one group")
        else:
            if same:
                notes.append(f"{', '.join(same)}: the same in every case, so not used to tell them apart")
            best, labels, said = _choose(points, k=k)
            notes += said
            score = round(best, 4) if best is not None else None
            if labels is not None:
                # Named in the order of their mean of the first feature.
                means = {g: sum(points[i][0] for i in range(len(training)) if labels[i] == g)
                         / sum(1 for label in labels if label == g) for g in set(labels)}
                order = sorted(means, key=lambda g: (means[g], g))
                named = {f"group {n + 1}": {"training": [training[i] for i in range(len(training)) if labels[i] == g],
                                            "test": []} for n, g in enumerate(order)}
                learned = {c: name for name, part in named.items() for c in part["training"]}
                if len(named) <= 3 and rule_stump(features, learned) is None:
                    notes.append(NO_RULE)
                    named = {}
                else:
                    method = "features"
                    named[next(iter(named))]["test"] = list(study.test)  # until the rule places them (`_place`)
                    _join(named, left, features, study, notes)
        if method != "features":
            notes += [f"{case}: some of its features are not numbers, so it did not shape the groups" for case in left]
    if method == "one":
        named = {"all": _split(study, set(cases))}
    doc: dict[str, Any] = {
        "format": FORMAT, "method": method, "assigned": "tags" if method == "tags" else "one", "k": len(named),
        "silhouette": score, "groups": named,
        "features": features, "describe": {name: words.get(name, name) for name in names}, "notes": notes,
        "cases": cases, "tags": {case: tag for case, tag in tags.items() if tag}, "stamps": _stamps(root, cases),
    }
    if method != "tags":
        doc["min_silhouette"] = MIN_SILHOUETTE
    if method == "features":
        _place(doc)
    _atomic_write(root / GROUPS, json.dumps(doc, indent=2) + "\n")
    return doc


def _join(named: dict[str, dict[str, list[str]]], left: list[str], features: dict[str, dict[str, Any]],
          study: Study, notes: list[str]) -> None:
    """Cases to learn from that some feature of theirs left out of the clustering join a group
    after it is made: the rule's (fitted without them), else the nearest centre's, else the first;
    a note says each. Every group's cases to learn from stay in the study's order."""
    if not left:
        return
    labels = {c: name for name, part in named.items() for c in part["training"]}
    stump = rule_stump(features, labels) if len(named) <= 3 else None
    space = _space(features, list(labels))
    centres = _centres(space, features, named) if space is not None else {}
    for case in left:
        row = features.get(case) or {}
        group, how = (assign(stump, row) if stump is not None else None), "by the rule"
        if group is None:
            near = _nearest(space, centres, row)
            group, how = (near, "the nearest") if near is not None else (next(iter(named)), "the first")
        named[group]["training"].append(case)
        notes.append(f"{case}: some of its features are not numbers, so it did not shape the groups; "
                     f"it joins {group}, {how}")
    for part in named.values():
        part["training"] = [c for c in study.training if c in set(part["training"])]


def _choose(points: list[list[float]], *, k: int | None = None) -> tuple[float | None, list[int] | None, list[str]]:
    """Steps 3-4 of `compute` over the standardised points of the cases to learn from:
    `(best silhouette, labels, notes)`. For each number of groups in `KS` (or `k`), k-means and
    its silhouette, when every group keeps `MIN_TRAINING` cases; the best is kept. `labels` is
    `None` -- one group -- when no number fits (best `None`) or, with no `k` asked for, when the
    best silhouette is below `MIN_SILHOUETTE`."""
    notes: list[str] = []
    best: tuple[float, list[int]] | None = None
    for count in ([k] if k is not None else list(KS)):
        if count > len(points):
            notes.append(f"asked for {count} groups; there are only {len(points)} cases to learn from")
            continue
        labels = kmeans(points, count)
        made = len(set(labels))
        if k is not None and made < count:
            notes.append(f"asked for {count} groups; the cases make only {made}")
        if made < 2 or min(labels.count(g) for g in set(labels)) < MIN_TRAINING:
            continue
        value = silhouette(points, labels)
        if best is None or value > best[0]:
            best = (value, labels)
    if best is None:
        return None, None, notes + [ONE_GROUP]
    if best[0] < MIN_SILHOUETTE:
        if k is None:
            return best[0], None, notes + [
                f"the cases do not fall into clear kinds (how well they separate: {best[0]:.2f} of 1, "
                f"at least {MIN_SILHOUETTE} needed); all cases are one group"]
        notes.append(f"asked for {k} groups, so they are kept however clearly they separate "
                     f"({best[0]:.2f} of 1; {MIN_SILHOUETTE} is needed when no number is asked for)")
    return best[0], best[1], notes


def read_groups(root: Path, study: Study) -> dict[str, Any] | None:
    """`groups.json` while it describes the study's cases as they are: the same
    cases on the same sides, with the same tags and the same files; else `None`.
    A format-1 document is read only when tags made it (it means the same under
    format 2); one grouped by features placed its held-back cases by k-means,
    not by the rule, so it is `None` and the groups are computed again. (A run's
    own copy is read whatever its format: `execute._run_groups`.)"""
    root = Path(root)
    try:
        doc = json.loads((root / GROUPS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("groups"), dict):
        return None
    if not (doc.get("format") == FORMAT or (doc.get("format") == 1 and doc.get("method") == "tags")):
        return None
    cases = list(study.training) + list(study.test)
    if doc.get("cases") != cases:
        return None
    try:
        training = {c for g in doc["groups"].values() for c in g["training"]}
        test = {c for g in doc["groups"].values() for c in g["test"]}
    except (KeyError, TypeError):
        return None
    if training != set(study.training) or test != set(study.test):
        return None
    tags = {case: tag for case in cases if (tag := read_tag(root, case))}
    if (doc.get("tags") or {}) != tags:
        return None
    if doc.get("stamps") != _stamps(root, cases):
        return None
    return doc


# ---------------------------------------------------------------------------
# the rule: which cases get which group's settings
# ---------------------------------------------------------------------------


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def rule_stump(features: dict[str, dict[str, float]], labels: dict[str, str]) -> dict[str, Any] | None:
    """The one-split rule over the cases of `labels` (the cases to learn from,
    each with its group): for two groups the feature and threshold (the
    midpoint of neighbouring values) that misplace the fewest cases, for three
    one feature and two thresholds; ties go to the widest gap (for three, the
    narrower of its two), then the feature's name.
    `{"feature", "thresholds", "sides": [group names, low to high], "misplaced"}`;
    None when no feature separates the cases at all. Raises ValueError for
    other than two or three groups."""
    import itertools

    names = list(dict.fromkeys(labels.values()))
    k = len(names)
    if k not in (2, 3):
        raise ValueError(f"a one-split rule tells two or three groups apart, not {k}")
    cases = [case for case in labels if case in features]
    best: tuple[tuple[int, float, str], dict[str, Any]] | None = None
    for feature in sorted({name for case in cases for name in features[case]}):
        values = [(float(features[case][feature]), labels[case]) for case in cases if _finite(features[case].get(feature))]
        if len(values) != len(cases):
            continue  # a feature some case lacks tells nothing about that case
        values.sort(key=lambda pair: pair[0])
        n = len(values)
        cuts = [i for i in range(1, n) if values[i][0] > values[i - 1][0]]  # split before index i
        if len(cuts) < k - 1:
            continue
        before = {g: [0] * (n + 1) for g in names}
        for i, (_, label) in enumerate(values):
            for g in names:
                before[g][i + 1] = before[g][i] + (label == g)
        for sides in itertools.permutations(names):
            for chosen in itertools.combinations(cuts, k - 1):
                bounds = [0, *chosen, n]
                right = sum(before[g][bounds[j + 1]] - before[g][bounds[j]] for j, g in enumerate(sides))
                gap = min(values[i][0] - values[i - 1][0] for i in chosen)
                key = (n - right, -gap, feature)
                if best is None or key < best[0]:
                    best = (key, {"feature": feature,
                                  "thresholds": [(values[i - 1][0] + values[i][0]) / 2 for i in chosen],
                                  "sides": list(sides), "misplaced": n - right})
    return best[1] if best is not None else None


def assign(stump: dict[str, Any], features: dict[str, Any]) -> str | None:
    """The group the one-split rule gives a case: `sides[j]` for the interval its raw value of
    `stump["feature"]` falls in, a threshold passed only by a value above it (as `rule_lines` says
    "above"); `None` when the feature is missing or not finite."""
    value = (features or {}).get(stump["feature"])
    if not _finite(value):
        return None
    return stump["sides"][sum(1 for threshold in stump["thresholds"] if value > threshold)]


def _value(number: float) -> str:
    """A threshold as people write it: 60, 59.5, 0.0123."""
    text = f"{round(float(number), 4):f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def stump_of(doc: dict[str, Any]) -> dict[str, Any] | None:
    """The one-split rule of `groups.json` (`doc`) grouped by features, over the cases to learn
    from (`rule_stump`); None for tags, one group, more than three groups, or no split."""
    named = doc.get("groups") or {}
    if doc.get("method") != "features" or len(named) not in (2, 3):
        return None
    labels = {case: name for name, part in named.items() for case in part.get("training") or []}
    features = {case: values for case, values in (doc.get("features") or {}).items() if case in labels}
    return rule_stump(features, labels)


def cache_stump(path: Path) -> dict[str, Any] | None:
    """Keep the rule's split in a run's copy of `groups.json` (`path`), once, when the run
    finishes: every verdict of the run reads it from there (`rule_lines`) and none computes it
    again. Returns it. A format-2 document grouped by features already holds the stump that
    placed its held-back cases, and is left as it is."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict):
        return None
    if "stump" not in doc:
        doc["stump"] = stump_of(doc)
        _atomic_write(Path(path), json.dumps(doc, indent=2) + "\n")
    return doc["stump"]


def rule_lines(doc: dict[str, Any], decisions: dict[str, str]) -> list[str]:
    """`rule.md`'s sentences: which cases get which group's settings, from
    `groups.json` (`doc`) -- by features a one-split rule (`rule_stump`) over
    the cases to learn from, by tags one line per tag. A group whose own
    decision (`decisions`) is not `use` names today's settings instead, and
    why: the rule claims nothing the groups' own verdicts do not say."""
    named = doc.get("groups") or {}

    def why(name: str) -> str:
        if decisions.get(name) == "single":
            return f"({name}: not proven, one held-back case)"
        if decisions.get(name) in ("pending", "stopped", "", None):
            return f"({name}: no answer yet)"
        return f"({name}: not shown to beat today's)"

    def use(name: str) -> str:
        return f"use the settings of {name}" if decisions.get(name) == "use" else f"use today's settings {why(name)}"

    def those(name: str) -> str:
        return f"those of {name}" if decisions.get(name) == "use" else f"today's settings {why(name)}"

    method = doc.get("method")
    if method == "tags":
        untagged = slug_of("untagged")
        lines = []
        for name, part in named.items():  # a group that others joined (`merge_small_groups`) answers for their tags too
            names = [name, *(part.get("merged_from") or [])]
            tags = [n for n in names if slug_of(n) != untagged]
            said = ([f"tagged {' or '.join(tags)}"] if tags else []) + (["with no tag"] if len(tags) < len(names) else [])
            lines.append(f"For cases {' or '.join(said)}, {use(name)}.")
        return lines
    if method == "one" or len(named) == 1:
        return [f"For every case, {use(name)}." for name in named]
    labels = {case: name for name, part in named.items() for case in part.get("training") or []}
    stump = doc["stump"] if "stump" in doc else stump_of(doc)  # a finished run keeps its own (`cache_stump`)
    if stump is None:  # more groups than one split tells apart: each group by its cases
        return [f"For the cases {', '.join(Path(c).stem for c in (part.get('training') or []) + (part.get('test') or []))}, "
                f"{use(name)}." for name, part in named.items()]
    words = (doc.get("describe") or {}).get(stump["feature"]) or stump["feature"].replace("_", " ")
    sides, cuts = stump["sides"], [_value(t) for t in stump["thresholds"]]
    if len(sides) == 2:
        lines = [f"If {words} is above {cuts[0]}, {use(sides[1])}; otherwise {those(sides[0])}."]
    else:
        lines = [f"If {words} is at most {cuts[0]}, {use(sides[0])}; if it is above {cuts[1]}, {those(sides[2])}; "
                 f"otherwise {those(sides[1])}."]
    accuracy = doc.get("accuracy")
    if isinstance(accuracy, dict):  # format 2: how often the rule agrees, on each side
        learned, held = accuracy.get("training") or {}, accuracy.get("held_back") or {}
        line = (f"The rule puts {learned.get('right')} of the {learned.get('of')} cases the search learned from "
                "in the group they were found in")
        if held.get("of"):
            line += f", and {held.get('right')} of the {held.get('of')} held-back cases in the group they are nearest to"
        lines.append(line + ".")
    elif stump["misplaced"]:  # a format-1 document keeps the sentence it was written with
        lines.append(f"The rule puts {stump['misplaced']} of the {len(labels)} cases to learn from in another group "
                     "than its own.")
    return lines
