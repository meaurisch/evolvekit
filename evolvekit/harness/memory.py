"""What earlier finished studies on an application learned about its settings.

`finished_studies` lists each study's newest finished run on a harness id; `learned` turns the
settings studies among them into facts: how often a setting was tuned, how much it mattered,
the values that won and the direction they moved, and where the searches leaned (`leaned`: the median
of the search-best values, whatever the verdict -- a lean to try, not a winner). Code studies, bake-offs and per-group studies
are listed with their verdicts but give no settings facts (a code study's winner is a diff, a
bake-off has no search, a per-group winner is not one value per setting). Figures are from the
searches ("on the cases they learned from"); only a verdict claims an improvement.

`warm_start` gives a new study the latest `use` study's winning values as its starting point
(and, after three settings studies, tunes the recommended number of settings that went most
with the measure); `priors_for` gives a warm-started run a prior around each winner whose
weight halves with every later study that tuned that setting and shrinks when the new study's
cases are unlike the winner's (design §6.3: a prior never excludes a value in range).
`defaults_study` finds the latest study that started from the harness defaults, whose final
check's runs a warm-started study's verdict is also set against (A4.3c).
"""

from __future__ import annotations

import copy
import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import threading
from statistics import fmean, median, median_low
from typing import Any, Callable, Sequence

from evolvekit.harness import HarnessError
from evolvekit.harness.execute import BAKEOFF, run_study_of
from evolvekit.harness.library import find_harnesses
from evolvekit.harness.manifest import Harness, load_harness
from evolvekit.harness.study import SettingChoice, Study, Warm
from evolvekit.status import build_status

IMPORTANCE_MIN_N = 5
"""A study's importance counts when its correlation rests on at least this many fully evaluated combinations."""
IMPORTANCE_P = 0.1
"""A setting's strength is shown when its studies' permutation p's, combined by Fisher's rule, are under this
(A4.3, S9; decision 2)."""
MIN_PRIOR_WEIGHT = 0.05
"""A prior weaker than this (five studies since its winner) is dropped: it would steer nothing."""
RERANK_AFTER = 3
"""Finished settings studies before `warm_start` chooses the tuned set by importance."""

_CACHE_MAX = 512
_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_LOCK = threading.Lock()
"""A finished run's verdict words (and, when no `document_of` is given, its settings), by `jobs._finished_key`."""


@dataclass(frozen=True)
class Finished:
    slug: str
    name: str
    root: Path
    run: str
    run_dir: Path
    finished_at: datetime
    decision: str
    line: str
    kind: str
    """"settings", "data", "both", "code", "bakeoff" or "per_group"."""
    study: Study
    pinned: Harness
    not_run: bool = False
    """The search stopped before its first round (`verdict.not_run`): listed with its line, and adding up no
    setting -- its "best" is the starting point, which it never searched from (persona p5, I2)."""


def _moment(value: Any, fallback: datetime) -> datetime:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _kind(run_dir: Path, job: dict[str, Any], study: Study) -> str:
    from evolvekit.app import jobs

    if job.get("bakeoff") or (run_dir / BAKEOFF).is_file():
        return "bakeoff"
    if job.get("per_group") or (run_dir / "groups").is_dir():
        return "per_group"
    return jobs._kind_of(study)


def _words(root: Path, run_dir: Path, job: dict[str, Any]) -> dict[str, Any]:
    from evolvekit.app import jobs, verdict

    key = jobs._finished_key(run_dir, job)
    with _LOCK:
        kept = _CACHE.get(key)
    if kept is None:
        try:
            document = verdict.build_verdict(root, run_dir.name)
        except Exception:  # noqa: BLE001 - a damaged run is listed without words, not an error, and not remembered
            return {"decision": "", "line": ""}
        kept = {"decision": str(document.get("decision") or ""), "line": str(document.get("line") or "")}
        with _LOCK:
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.clear()
            _CACHE[key] = kept
    return kept


def finished_studies(home: Any, harness_id: str) -> list[Finished]:
    """Each study on `harness_id` (archived ones too, the trash not): its newest `done` run, oldest first."""
    from evolvekit.app import jobs
    from evolvekit.app import verdict as verdict_doc

    found: list[Finished] = []
    folder = Path(home.studies_dir)
    if not folder.is_dir():
        return found
    for root in sorted(folder.iterdir()):
        if root.name.startswith(".") or not (root / "study.yaml").is_file() or not (root / "harness" / "harness.yaml").is_file():
            continue
        try:
            pinned = load_harness(root / "harness")
        except HarnessError:
            continue
        if pinned.id != harness_id:
            continue
        for run_dir in reversed(jobs._run_dirs(root)):
            job = jobs.phase(run_dir)
            if job.get("phase") != "done":
                continue
            try:
                study = run_study_of(root, run_dir)
            except HarnessError:
                break
            try:
                fallback = datetime.fromtimestamp(run_dir.stat().st_mtime, timezone.utc)
            except OSError:
                fallback = datetime.fromtimestamp(0, timezone.utc)
            at = _moment(job.get("finished_at") or job.get("started_at"), fallback)
            words = _words(root, run_dir, job)
            found.append(Finished(root.name, study.name, root, run_dir.name, run_dir, at, words["decision"], words["line"],
                                  _kind(run_dir, job, study), study, pinned,
                                  not_run=verdict_doc._not_run(job.get("final") or {})))
            break
    return sorted(found, key=lambda f: (f.finished_at, f.slug))


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


def current_harness(home: Any, harness_id: str, finished: list[Finished]) -> Harness | None:
    """The library's newest harness of that id, else the newest pinned copy."""
    ours = [e.harness for e in find_harnesses(Path(home.root)) if e.harness is not None and e.harness.id == harness_id]
    if ours:
        return max(ours, key=lambda h: _version(h.version))
    return max(finished, key=lambda f: f.finished_at).pinned if finished else None


def _number(value: Any) -> float | None:
    return None if isinstance(value, bool) or not isinstance(value, (int, float)) else float(value)


def _figure(value: Any) -> str:
    number = _number(value)
    if number is None:
        return str(value)
    return str(int(number)) if number == int(number) else f"{number:g}"


def _difference(then: Any, now: Any) -> str | None:
    """What changed between two declarations of a setting (`Parameter`), in words; None when nothing."""
    if then.type != now.type:
        return f"its type changed since {{study}} ({then.type}, now {now.type})"
    if (then.low, then.high) != (now.low, now.high):
        return (f"its range changed since {{study}} ({_figure(then.low)}–{_figure(then.high)}, "
                f"now {_figure(now.low)}–{_figure(now.high)})")
    if bool(then.log) != bool(now.log):
        return f"its scale changed since {{study}} ({'log' if then.log else 'linear'}, now {'log' if now.log else 'linear'})"
    if list(then.choices) != list(now.choices):
        return (f"its choices changed since {{study}} ({', '.join(map(str, then.choices))}, "
                f"now {', '.join(map(str, now.choices))})")
    return None


def _items(run_dir: Path, document_of: Callable[[Path], dict] | None) -> list[dict[str, Any]]:
    """The parameters of a run's status document, as much of each as `learned` uses."""
    from evolvekit.app import jobs

    cache = document_of is None
    key = None
    if cache:
        try:
            key = ("items", *jobs._finished_key(run_dir, jobs.phase(run_dir)))
        except Exception:  # noqa: BLE001
            key = None
        if key is not None:
            with _LOCK:
                kept = _CACHE.get(key)
            if kept is not None:
                return kept["items"]
    try:
        document = (document_of or build_status)(run_dir) or {}
    except Exception:  # noqa: BLE001 - a run without a readable status gives no figures, and none is remembered
        return []
    items = []
    for item in ((document.get("parameters") or {}).get("items") or []):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        importance = item.get("importance") or {}
        items.append({"name": str(item["name"]), "default": item.get("default"), "best": item.get("best"),
                      "importance": importance.get("value"), "n": int(importance.get("n") or 0),
                      "p": importance.get("p")})
    if key is not None:
        with _LOCK:
            if len(_CACHE) >= _CACHE_MAX:
                _CACHE.clear()
            _CACHE[key] = {"items": items}
    return items


def learned(home: Any, harness_id: str, *, document_of: Callable[[Path], dict] | None = None) -> dict[str, Any]:
    """What finished studies on `harness_id` learned: see the module's docstring and the plan's Task 5."""
    finished = finished_studies(home, harness_id)
    out: dict[str, Any] = {
        "studies": [{"slug": f.slug, "name": f.name, "run": f.run, "finished_at": f.finished_at.isoformat(),
                     "decision": f.decision, "line": f.line, "kind": f.kind, "not_run": f.not_run} for f in finished],
        "settings": {}, "notes": [],
    }
    current = current_harness(home, harness_id, finished)
    if current is None:
        return out
    seen: dict[str, dict[str, Any]] = {}
    for f in finished:
        if f.kind not in ("settings", "both") or f.not_run:
            continue  # a search that did not run tuned nothing: no count, importance, winner or lean
        gone, changed = [], []
        for item in _items(f.run_dir, document_of):
            name = item["name"]
            if name in f.study.data:
                continue  # a data change is a parameter of the search too, not a setting
            setting = current.settings.get(name)
            if setting is None:
                gone.append(f"{name} (in {f.name}) is not a setting of {current.title} {current.version} any more: left out")
                continue
            entry = seen.setdefault(name, {"label": setting.label, "tuned_in": 0, "importance": None, "importance_n": 0,
                                           "importance_of": 0, "importance_p": None, "winners": [], "direction": None,
                                           "leaned": None, "_values": [], "_starts": [], "_bests": [],
                                           "_type": setting.parameter.type})
            then = f.pinned.settings.get(name)
            difference = _difference(then.parameter, setting.parameter) if then is not None else None
            if difference:
                changed.append(f"{name}: {difference.format(study=f.name)}: that study's figures are left out")
                continue
            entry["tuned_in"] += 1
            entry["_values"].append((item["importance"], item["n"], item["p"]))
            if item["best"] is not None:
                entry["_bests"].append(item["best"])
            if f.decision == "use" and item["best"] is not None:
                entry["winners"].append({"value": item["best"], "study": f.slug, "finished_at": f.finished_at.isoformat()})
                entry["_starts"].append(item["default"])
        out["notes"] += gone + changed
    for name, entry in seen.items():
        values, starts = entry.pop("_values"), entry.pop("_starts")
        entry["leaned"] = _leaned(entry.pop("_bests"), entry.pop("_type"))
        entry.update(_pooled(values))
        moves = set()
        for winner, start in zip(entry["winners"], starts):
            a, b = _number(winner["value"]), _number(start)
            if a is not None and b is not None and a != b:
                moves.add("up" if a > b else "down")
        entry["direction"] = "mixed" if len(moves) == 2 else next(iter(moves), None)
        out["settings"][name] = entry
    return out


def _combined_p(ps: list[Any]) -> float | None:
    """Fisher's combined p of independent studies: -2 * sum(ln p) against chi-squared with 2k degrees
    of freedom, whose survival is exp(-x/2) * sum_{j<k} (x/2)^j / j!. A p that is not a number in
    (0, 1] counts as 1: it was not shown to beat chance. None without a study."""
    if not ps:
        return None
    numbers = [_number(p) for p in ps]
    clean = [p if p is not None and 0.0 < p <= 1.0 else 1.0 for p in numbers]
    half = -sum(math.log(p) for p in clean)
    term, total = 1.0, 1.0
    for j in range(1, len(clean)):
        term *= half / j
        total += term
    return min(1.0, math.exp(-half) * total)


def _pooled(studies: list[tuple[Any, int, Any]]) -> dict[str, Any]:
    """A setting's strength over its studies, each `(|rho|, n, p)` (decision 2, departing from the design's
    "pool over the studies with p < 0.1", which shows a setting that does nothing in about 26 % of cases
    after three studies, at a strength as high as a real one's). A study counts when it has a strength on
    at least `IMPORTANCE_MIN_N` combinations (`importance_of`); their p's are combined by Fisher's rule
    (`importance_p`); when that is under `IMPORTANCE_P` the strength is the mean |rho| over all of them
    (`importance`, over `importance_n` studies), else None and `importance_n` 0."""
    counted = [(float(value), p) for value, n, p in studies if _number(value) is not None and n >= IMPORTANCE_MIN_N]
    combined = _combined_p([p for _, p in counted])
    shown = combined is not None and combined < IMPORTANCE_P
    return {"importance": fmean(v for v, _ in counted) if shown else None,
            "importance_n": len(counted) if shown else 0,
            "importance_of": len(counted), "importance_p": combined}


def _leaned(bests: list[Any], kind: str) -> Any:
    """Where the searches leaned (persona p4, H4): the median of the studies' search-best values -- the
    lower middle of an even count for a whole-number setting -- or, for a choice or an on/off setting,
    the value most of them had (None on a tie); None without a value. From the searches, not checked."""
    if not bests:
        return None
    numbers = [_number(b) for b in bests]
    if kind in ("int", "float") and all(n is not None for n in numbers):
        if kind == "int":
            return int(median_low(numbers))
        return median(numbers)
    counts: dict[str, list[Any]] = {}
    for b in bests:
        counts.setdefault(repr(b), []).append(b)
    ranked = sorted(counts.values(), key=len, reverse=True)
    if len(ranked) > 1 and len(ranked[0]) == len(ranked[1]):
        return None
    return ranked[0][0]


def _settings_studies(finished: list[Finished]) -> list[Finished]:
    return [f for f in finished if f.kind in ("settings", "both")]


def _bounds(parameter: Any, choice: SettingChoice) -> tuple[Any, Any]:
    """A numeric setting's range in a study: its own narrower one, else the harness's (whole numbers for an int)."""
    low = parameter.low if choice.low is None else choice.low
    high = parameter.high if choice.high is None else choice.high
    if parameter.type == "int":
        return int(math.ceil(low)), int(math.floor(high))
    return low, high


def _fits(parameter: Any, choice: SettingChoice, value: Any) -> bool:
    """Whether `value` is a legal value of the setting inside the study's range for it."""
    if parameter.problem_with(value) is not None:
        return False
    if parameter.numeric:
        low, high = _bounds(parameter, choice)
        return low <= value <= high
    return True


def _ranked(harness: Harness, settings: dict[str, Any]) -> list[str]:
    """The recommended settings and those a study measured, the most important first; a setting
    with no importance after, in the harness's rank order."""
    candidates = list(harness.recommended)
    candidates += [n for n in harness.settings
                   if n not in candidates and (settings.get(n) or {}).get("importance") is not None]
    order = {name: i for i, name in enumerate(candidates)}

    def key(name: str) -> tuple[bool, float, int]:
        importance = (settings.get(name) or {}).get("importance")
        return importance is None, -(importance or 0.0), order[name]

    return sorted(candidates, key=key)


def warm_start(home: Any, harness: Harness, study: Study, *,
               document_of: Callable[[Path], dict] | None = None) -> tuple[Study, list[str]]:
    """`study` warm-started from the home's latest `use` settings study on `harness`'s id, and the
    lines to say. Each tuned setting starts from that study's winner when the value lies in this
    study's range; with a template that tunes `recommended` and at least `RERANK_AFTER` finished
    settings studies, the tuned set is the `len(harness.recommended)` settings that went most with
    the measure (when any importance is known at all: without one there is nothing to rank by).
    A study that tunes no setting is left as it is. `harness` is the version the new study is
    made with. `document_of` reads a run's status document (`build_status`, kept per finished
    run, when None)."""
    template = harness.templates.get(study.template) or {}
    vary = template.get("vary") if isinstance(template, dict) else None
    recommended = isinstance(vary, dict) and vary.get("settings") == "recommended" and bool(harness.recommended)
    if not study.tuned_settings() and not recommended:
        return study, ["this template tunes no setting, so there is nothing to warm-start"]
    finished = finished_studies(home, harness.id)
    used = [f for f in _settings_studies(finished) if f.decision == "use"]
    if not used:
        return study, [f"nothing to warm-start from: no finished study on {harness.id} says use; a plain start"]
    source = used[-1]
    facts = learned(home, harness.id, document_of=document_of)["settings"]
    study = copy.deepcopy(study)
    ranked: list[str] = []
    tail: list[str] = []
    measured = len(_settings_studies(finished))
    known = any(entry.get("importance") is not None for name, entry in facts.items() if name in harness.settings)
    if recommended and measured >= RERANK_AFTER and known:
        ranked = _ranked(harness, facts)
        tuned = ranked[:len(harness.recommended)]
        kept = {n: c for n, c in study.settings.items() if n not in tuned and c.mode != "tune"}
        study.settings = {**{n: (study.settings[n] if n in study.settings and study.settings[n].mode == "tune"
                                 else SettingChoice("tune")) for n in tuned}, **kept}
        tail.append(f"tuned: the {len(tuned)} settings that went most with the measure in {measured} studies: "
                    + ", ".join(tuned))
    winners = {name: w["value"] for name, entry in facts.items() for w in entry["winners"] if w["study"] == source.slug}
    said, outside = [], []
    for name in study.tuned_settings():
        setting = harness.settings.get(name)
        if setting is None or name not in winners:
            continue
        parameter, choice, value = setting.parameter, study.settings[name], winners[name]
        if _fits(parameter, choice, value):
            choice.start = parameter.coerce(value)
            said.append(f"{name} {_figure(choice.start)}")
        elif parameter.numeric and parameter.problem_with(value) is None:
            low, high = _bounds(parameter, choice)
            outside.append(f"{name}: {source.name}'s winner {_figure(value)} lies outside this study's range "
                           f"{_figure(low)}–{_figure(high)}; it starts where it would without the warm start")
        else:
            outside.append(f"{name}: {source.name}'s winner {_figure(value)} is not a value it can take now; "
                           "it starts where it would without the warm start")
    study.warm = Warm(from_slug=source.slug, run=source.run, at=source.finished_at.isoformat(), ranked=ranked)
    lines = [f"warm start from {source.name} (use): " + (", ".join(said) if said else "none of its values fits this study")]
    lines += outside
    if said:
        lines.append(f"the starting point is now {source.name}'s winner: the final check compares the result with it")
    return study, lines + tail


def case_digest(path: Path) -> str | None:
    """The sha256 of a case file's content, None when it cannot be read: two studies hold back
    the same case when the content is the same, whatever the file is called."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _from_the_defaults(f: Finished) -> bool:
    """Whether a finished study's starting point was the harness defaults (A4.3c, Q18): a settings
    study (or settings and data) whose search ran, not warm-started, with no tuned setting started
    by hand and no setting given by the study itself (fixed, or from an input settings file)."""
    from evolvekit.harness.compile import _given_settings

    if f.kind not in ("settings", "both") or f.not_run or f.study.warm is not None:
        return False
    if any(choice.mode == "tune" and choice.start is not None for choice in f.study.settings.values()):
        return False
    try:
        return not _given_settings(f.study, f.pinned, f.root)
    except HarnessError:
        return False


def defaults_study(home: Any, study: Study, cases: Sequence[Path]) -> tuple[Finished, Path] | None:
    """The latest finished study on `study`'s harness whose starting point was the harness
    defaults (`_from_the_defaults`) and whose final check kept its runs
    (`confirm/final/results.json`), with that file; of those, the latest that held back a case
    with the content of one of `cases` (the new study's held-back case files) when there is one.
    None when there is none."""
    found = [(f, f.run_dir / "confirm" / "final" / "results.json") for f in finished_studies(home, study.harness_id)
             if _from_the_defaults(f)]
    found = [(f, results) for f, results in found if results.is_file()]
    if not found:
        return None
    wanted = {digest for digest in map(case_digest, cases) if digest}
    sharing = [(f, results) for f, results in found
               if wanted & {case_digest(f.root / case) for case in f.study.test}]
    return (sharing or found)[-1]


def _study_harness(home: Any, study: Study, finished: list[Finished]) -> Harness | None:
    """The harness version the study was made with, from the library; else the newest of its id."""
    for entry in find_harnesses(Path(home.root)):
        if entry.harness is not None and entry.harness.id == study.harness_id \
                and entry.harness.version == study.harness_version:
            return entry.harness
    for f in reversed(finished):
        if f.pinned.version == study.harness_version:
            return f.pinned
    return current_harness(home, study.harness_id, finished)


def _studies_since(finished: list[Finished], winner: Finished, name: str, slug: str | None) -> int:
    """The finished studies after `winner` that tuned `name`: settings studies (or settings and
    data) whose search ran and that had it among their tuned settings; never the winner's own, nor
    the new study's (`slug`), which the `1 +` of the weight counts once. A code study, a bake-off,
    a per-group study or a search that did not run learned nothing about the setting."""
    return sum(1 for f in finished
               if f.finished_at > winner.finished_at and f.slug not in (winner.slug, slug)
               and f.kind in ("settings", "both") and not f.not_run and name in f.study.tuned_settings())


def priors_for(home: Any, study: Study, *, slug: str | None = None,
               document_of: Callable[[Path], dict] | None = None,
               features: Callable[[Path, Study], dict | None] | None = None) -> dict[str, dict]:
    """`{name: {"value", "weight", "studies_since", "similarity", "left_out"}}` for each setting
    `study` tunes that a `use` study won: the latest winner, at weight `0.5 ** (1 + k) * similarity`.
    `k` (`studies_since`, `_studies_since`) counts the later finished studies that tuned the setting;
    the new study is the first since, counted once (`slug`, its own folder name, is never counted
    again when it is run again). `similarity` (`groups.compare`, rounded to three places in the
    result) compares the cases of the winner's study with the new study's, from the features their
    kept tables give (`features`, `groups.study_features` when None: never a solve; the new study's
    read once, at the first winner); 1.0 when either has none, or without a `slug`. `left_out`
    names the features that could not be standardised (no spread inside either study). Dropped
    below `MIN_PRIOR_WEIGHT`, and when the value is outside the study's range. `compile_study`
    reads `value` and `weight`; `write_run` records the applied ones in `study-run.json`."""
    from evolvekit.harness import groups

    read = features or groups.study_features
    finished = finished_studies(home, study.harness_id)
    facts = learned(home, study.harness_id, document_of=document_of)["settings"]
    harness = _study_harness(home, study, finished)
    if harness is None:
        return {}
    by_slug = {f.slug: f for f in finished}
    new: list[Any] = []  # the new study's features, read once, at the first winner (none without a winner)
    factors: dict[str, tuple[float, list[str]]] = {}
    priors: dict[str, dict] = {}
    for name in study.tuned_settings():
        entry, setting = facts.get(name), harness.settings.get(name)
        if not entry or not entry["winners"] or setting is None:
            continue
        winner = by_slug.get(entry["winners"][-1]["study"])  # `learned` lists them oldest first
        if winner is None:
            continue
        value = entry["winners"][-1]["value"]
        k = _studies_since(finished, winner, name, slug)
        if not new:
            new.append(read(Path(home.studies_dir) / slug, study) if slug else None)
        if winner.slug not in factors:
            factors[winner.slug] = groups.compare(read(winner.root, winner.study), new[0]) if new[0] else (1.0, [])
        factor, left_out = factors[winner.slug]
        weight = 0.5 ** (1 + k) * factor
        if weight < MIN_PRIOR_WEIGHT or not _fits(setting.parameter, study.settings[name], value):
            continue
        priors[name] = {"value": setting.parameter.coerce(value), "weight": weight, "studies_since": k,
                        "similarity": round(factor, 3), "left_out": list(left_out)}
    return priors
