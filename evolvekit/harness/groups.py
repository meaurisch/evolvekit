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
- **k-means** over the standardised features, k-means++ starts and 20 seeded
  restarts; the best silhouette among k = 2 and 3 whose groups each keep two
  cases to learn from (D36). A grouping that leaves any group with fewer is
  no grouping: all cases are one group.

`compute` writes `<study>/groups.json`; `read_groups` reads it back while it
still describes the study's cases.
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
    "SolveFailed", "GROUPS", "KS", "MIN_TRAINING", "RESTARTS", "TAGS", "TAG_MAX",
    "case_tables", "compute", "features_of", "kmeans", "read_groups", "read_tag", "rule_lines", "rule_stump", "silhouette",
    "cache_stump", "stump_of",
    "slug_of",
    "standardise", "write_tag",
]

GROUPS = "groups.json"
"""`<study>/groups.json`: the groups `compute` found, and how."""
TAGS = ".tags"
"""The tag of `cases/a.json` is the first line of `cases/a.json.tags`."""
MIN_TRAINING = 2
"""Cases to learn from that every group needs."""
KS = (2, 3)
"""The numbers of groups tried when none is given."""
RESTARTS = 20
TAG_MAX = 40
STEPS = 100
"""Lloyd's steps at most, per restart."""
FOLDER = "groups"
"""`<study>/groups/tables/`: the request tables of each case, kept."""
ONE_GROUP = "a group needs at least two cases to learn from; all cases are one group"


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
    try:
        if target.is_file() and json.loads(stamp_file.read_text(encoding="utf-8")) == stamp:
            return target
    except (OSError, ValueError):
        pass
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
    names = list(next(iter(features.values()), {}))
    words = {name: said for name, (_, said) in _features(harness).items()}
    method, named, score = "one", {}, None
    if by_tags:
        named = _tag_groups(study, cases, tags, notes)
        if _fits(named):
            method = "tags"
        else:
            notes.append(ONE_GROUP)
    else:
        raw = [[features[c][n] for n in names] for c in cases]
        spread = _spread(raw)
        kept = {j for j, _, _ in spread}
        same = [n for j, n in enumerate(names) if j not in kept]
        points = standardise(raw)
        if not spread:
            notes.append("the cases' features are the same in every case; all cases are one group")
        else:
            if same:
                notes.append(f"{', '.join(same)}: the same in every case, so not used to tell them apart")
            best: tuple[float, dict[str, dict[str, list[str]]]] | None = None
            for count in ([k] if k is not None else list(KS)):
                if count > len(cases):
                    notes.append(f"asked for {count} groups; there are only {len(cases)} cases")
                    continue
                labels = kmeans(points, count)
                made = len(set(labels))
                if k is not None and made < count:
                    notes.append(f"asked for {count} groups; the cases make only {made}")
                # Named in the order of their mean of the first feature.
                means = {g: sum(points[i][0] for i in range(len(cases)) if labels[i] == g)
                         / sum(1 for label in labels if label == g) for g in set(labels)}
                order = sorted(means, key=lambda g: (means[g], g))
                candidate = {f"group {n + 1}": _split(study, {cases[i] for i in range(len(cases)) if labels[i] == g})
                             for n, g in enumerate(order)}
                if not _fits(candidate):
                    continue
                value = silhouette(points, labels)
                if best is None or value > best[0]:
                    best = (value, candidate)
            if best is None:
                notes.append(ONE_GROUP)
            else:
                method, score, named = "features", round(best[0], 4), best[1]
    if method == "one":
        named = {"all": _split(study, set(cases))}
    doc = {
        "format": 1, "method": method, "k": len(named), "silhouette": score, "groups": named,
        "features": features, "describe": {name: words.get(name, name) for name in names}, "notes": notes,
        "cases": cases, "tags": {case: tag for case, tag in tags.items() if tag}, "stamps": _stamps(root, cases),
    }
    _atomic_write(root / GROUPS, json.dumps(doc, indent=2) + "\n")
    return doc


def read_groups(root: Path, study: Study) -> dict[str, Any] | None:
    """`groups.json` while it describes the study's cases as they are: the same
    cases on the same sides, with the same tags and the same files; else `None`."""
    root = Path(root)
    try:
        doc = json.loads((root / GROUPS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("format") != 1 or not isinstance(doc.get("groups"), dict):
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
    again. Returns it."""
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
        return [f"For cases {'with no tag' if slug_of(name) == untagged else 'tagged ' + name}, {use(name)}."
                for name in named]
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
    if stump["misplaced"]:
        lines.append(f"The rule puts {stump['misplaced']} of the {len(labels)} cases to learn from in another group "
                     "than its own.")
    return lines
