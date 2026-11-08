"""What earlier finished studies on an application learned about its settings.

`finished_studies` lists each study's newest finished run on a harness id; `learned` turns the
settings studies among them into facts: how often a setting was tuned, how much it mattered,
the values that won and the direction they moved. Code studies, bake-offs and per-group studies
are listed with their verdicts but give no settings facts (a code study's winner is a diff, a
bake-off has no search, a per-group winner is not one value per setting). Figures are from the
searches ("on the cases they learned from"); only a verdict claims an improvement.

`warm_start` gives a new study the latest `use` study's winning values as its starting point
(and, after three settings studies, tunes the recommended number of settings that went most
with the measure); `priors_for` gives a warm-started run a prior around each winner whose
weight halves with every study since it (design §6.3: a prior never excludes a value in range).
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import threading
from statistics import fmean
from typing import Any, Callable

from evolvekit.harness import HarnessError
from evolvekit.harness.execute import BAKEOFF, run_study_of
from evolvekit.harness.library import find_harnesses
from evolvekit.harness.manifest import Harness, load_harness
from evolvekit.harness.study import SettingChoice, Study, Warm
from evolvekit.status import build_status

IMPORTANCE_MIN_N = 5
"""A study's importance counts when its correlation rests on at least this many fully evaluated combinations."""
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
                                  _kind(run_dir, job, study), study, pinned))
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
                      "importance": importance.get("value"), "n": int(importance.get("n") or 0)})
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
                     "decision": f.decision, "line": f.line, "kind": f.kind} for f in finished],
        "settings": {}, "notes": [],
    }
    current = current_harness(home, harness_id, finished)
    if current is None:
        return out
    seen: dict[str, dict[str, Any]] = {}
    for f in finished:
        if f.kind not in ("settings", "both"):
            continue
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
                                           "winners": [], "direction": None, "_values": [], "_starts": []})
            then = f.pinned.settings.get(name)
            difference = _difference(then.parameter, setting.parameter) if then is not None else None
            if difference:
                changed.append(f"{name}: {difference.format(study=f.name)}: that study's figures are left out")
                continue
            entry["tuned_in"] += 1
            if item["importance"] is not None and item["n"] >= IMPORTANCE_MIN_N:
                entry["_values"].append(float(item["importance"]))
            if f.decision == "use" and item["best"] is not None:
                entry["winners"].append({"value": item["best"], "study": f.slug, "finished_at": f.finished_at.isoformat()})
                entry["_starts"].append(item["default"])
        out["notes"] += gone + changed
    for name, entry in seen.items():
        values, starts = entry.pop("_values"), entry.pop("_starts")
        entry["importance_n"] = len(values)
        entry["importance"] = fmean(values) if values else None
        moves = set()
        for winner, start in zip(entry["winners"], starts):
            a, b = _number(winner["value"]), _number(start)
            if a is not None and b is not None and a != b:
                moves.add("up" if a > b else "down")
        entry["direction"] = "mixed" if len(moves) == 2 else next(iter(moves), None)
        out["settings"][name] = entry
    return out


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


def priors_for(home: Any, study: Study, *, slug: str | None = None,
               document_of: Callable[[Path], dict] | None = None) -> dict[str, dict]:
    """`{name: {"value", "weight"}}` for each setting `study` tunes that a `use` study won: the
    latest winner, at weight `0.5 ** k`, `k` being 1 + the other finished studies on the harness
    after the winner's (the new study is the first since, counted once: `slug`, the study's own
    folder name, is never counted again when it is run again). Dropped below `MIN_PRIOR_WEIGHT`,
    and when the value is outside the study's range."""
    finished = finished_studies(home, study.harness_id)
    facts = learned(home, study.harness_id, document_of=document_of)["settings"]
    harness = _study_harness(home, study, finished)
    if harness is None:
        return {}
    at = {f.slug: f.finished_at for f in finished}
    priors: dict[str, dict] = {}
    for name in study.tuned_settings():
        entry, setting = facts.get(name), harness.settings.get(name)
        if not entry or not entry["winners"] or setting is None:
            continue
        winner = entry["winners"][-1]  # `learned` lists them oldest first
        won = at.get(winner["study"])
        if won is None:
            continue
        weight = 0.5 ** (1 + sum(1 for f in finished if f.finished_at > won and f.slug not in (winner["study"], slug)))
        if weight < MIN_PRIOR_WEIGHT or not _fits(setting.parameter, study.settings[name], winner["value"]):
            continue
        priors[name] = {"value": setting.parameter.coerce(winner["value"]), "weight": weight}
    return priors
