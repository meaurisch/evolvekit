"""What earlier finished studies on an application learned about its settings.

`finished_studies` lists each study's newest finished run on a harness id; `learned` turns the
settings studies among them into facts: how often a setting was tuned, how much it mattered,
the values that won and the direction they moved. Code studies, bake-offs and per-group studies
are listed with their verdicts but give no settings facts (a code study's winner is a diff, a
bake-off has no search, a per-group winner is not one value per setting). Figures are from the
searches ("on the cases they learned from"); only a verdict claims an improvement.
"""

from __future__ import annotations

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
from evolvekit.harness.study import Study
from evolvekit.status import build_status

IMPORTANCE_MIN_N = 5
"""A study's importance counts when its correlation rests on at least this many fully evaluated combinations."""

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
