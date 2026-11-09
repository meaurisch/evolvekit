"""Every try of a run, digested for the Study page's three views.

The Tries view plots each combination by when it finished and by how much
better than the starting point it did; the Cases view shows each study case
with a dot per try; the Settings view shows each tuned setting's values
against the result. All three come from one digest of the run's own files
(`events.jsonl`, `runs.jsonl`, and the final check's `confirm/final/`),
built the same way for any harness:

* a try's result on a case is its goal measure against the starting point's
  on the same case and stage, in percent, positive when better (the run's
  `direction` says which way better is), averaged over the runs of the case;
* a try's own number is the mean over the cases of the furthest stage it
  reached: a *full look* when that is the run's last stage (every study
  case), a *quick look* otherwise;
* the best-so-far line counts finished full looks only, so a lucky quick look
  on a few cases, or a full look half done, never sets it; the status strip's
  one figure is `verdict.best_so_far`, not this line;
* the final check's numbers, as far as it got, come from its own event log
  with `confirm.paired_summary`, the same summary the verdict uses;
* in a code study (a run whose `evolvekit.yaml` has `problem.tree`) each
  combination carries `diff_stat`, the lines its diff adds and removes and
  the files it touches; `None` in any other run.

The digest is cached on the size and time of the files it reads.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from typing import Any

import yaml

__all__ = ["digest", "tree_run"]

_cache: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
_guard = threading.Lock()
MAX_CACHED = 16
CONFIG = "evolvekit.yaml"  # the run's compiled config (`harness.compile.CONFIG`)


def tree_run(run_dir: Path) -> bool:
    """Whether the run searched a source tree: its compiled config has `problem.tree`."""
    try:
        config = yaml.safe_load((run_dir / CONFIG).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return False
    problem = config.get("problem") if isinstance(config, dict) else None
    return isinstance(problem, dict) and problem.get("tree") is not None


def _stamp(run_dir: Path) -> tuple[Any, ...]:
    out = []
    for name in ("events.jsonl", "runs.jsonl", "confirm/final/events.jsonl", "job.json", CONFIG):
        path = run_dir / name
        try:
            st = path.stat()
            out.append((st.st_size, st.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)


def _lines(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue  # a line being written right now
            if isinstance(item, dict):
                out.append(item)
    return out


def _moment(text: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _gain(start: float, value: float, minimize: bool) -> float | None:
    """Percent better than `start`: positive when `value` is better."""
    if not isinstance(start, (int, float)) or not isinstance(value, (int, float)) or start == 0:
        return None
    return (start - value) / abs(start) * 100.0 if minimize else (value - start) / abs(start) * 100.0


def digest(run_dir: Path) -> dict[str, Any]:
    """The run's tries, cases, settings and final check, for the page (cached)."""
    key = str(run_dir.resolve())
    stamp = _stamp(run_dir)
    with _guard:
        hit = _cache.get(key)
        if hit and hit[0] == stamp:
            return hit[1]
    built = _build(run_dir)
    with _guard:
        if len(_cache) >= MAX_CACHED:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (stamp, built)
    return built


def _build(run_dir: Path) -> dict[str, Any]:
    events = _lines(run_dir / "events.jsonl")
    rows = {str(r.get("id")): r for r in _lines(run_dir / "runs.jsonl") if r.get("id")}
    started = [e for e in events if e.get("type") == "run_started"]
    empty: dict[str, Any] = {"available": False, "combinations": [], "cases": [], "rounds": [], "best": [], "running": [],
                             "parameters": [], "now_h": 0.0, "end_h": 0.0, "done": False, "restarts": [], "check": None}
    if not started:
        return empty
    t0 = _moment(started[0].get("ts"))
    if t0 is None:
        return empty
    objective = str(started[0].get("objective") or "objective")
    minimize = str(started[0].get("direction") or "minimize").lower().startswith("min")
    parameters = [p for p in (started[-1].get("parameters") or []) if isinstance(p, dict) and p.get("name")]

    def hours(e: dict[str, Any]) -> float:
        moment = _moment(e.get("ts"))
        return max(0.0, (moment - t0).total_seconds() / 3600.0) if moment else 0.0

    stage_order: list[str] = []
    stage_cases: dict[str, set[str]] = {}
    begun: dict[str, float] = {}
    pending: dict[tuple[str, str, str, Any], float] = {}
    results: dict[tuple[str, str, str], list[float]] = {}  # (candidate, stage, case) -> objective values
    finished_at: dict[str, float] = {}
    failed_evals: dict[str, int] = {}
    round_start: dict[int, float] = {}
    for e in events:
        kind = e.get("type")
        if kind == "generation_started" and isinstance(e.get("generation"), int):
            round_start.setdefault(int(e["generation"]), hours(e))
        elif kind == "stage_started":
            stage = str(e.get("stage") or "")
            if stage and stage not in stage_order:
                stage_order.append(stage)
        elif kind == "eval_started":
            cid = str(e.get("candidate_id") or "")
            begun.setdefault(cid, hours(e))
            pending[(cid, str(e.get("stage")), str(e.get("instance")), e.get("seed"))] = hours(e)
        elif kind == "eval_finished":
            cid, stage, case = str(e.get("candidate_id") or ""), str(e.get("stage") or ""), str(e.get("instance") or "")
            pending.pop((cid, stage, case, e.get("seed")), None)
            if stage and stage not in stage_order:
                stage_order.append(stage)
            stage_cases.setdefault(stage, set()).add(case)
            finished_at[cid] = max(finished_at.get(cid, 0.0), hours(e))
            value = (e.get("kpis") or {}).get(objective) if e.get("ok") else None
            if isinstance(value, (int, float)):
                results.setdefault((cid, stage, case), []).append(float(value))
            else:
                failed_evals[cid] = failed_evals.get(cid, 0) + 1
    if not finished_at:
        return {**empty, "available": True, "now_h": _now_h(t0, events), "parameters": parameters}

    full_stage = max(stage_order, key=lambda s: (len(stage_cases.get(s, ())), stage_order.index(s))) if stage_order else ""
    cases = sorted(stage_cases.get(full_stage, set()))
    seed_row = next((r for r in rows.values() if r.get("operator") == "human-seed"), None)
    base = str(seed_row["id"]) if seed_row else next(iter(finished_at))
    base_value = {(stage, case): fmean(v) for (cid, stage, case), v in results.items() if cid == base}

    count = None
    if tree_run(run_dir):
        from evolvekit import tree  # only a code study needs the tree's tools

        count = tree.diff_stat

    combos = []
    for cid, end in finished_at.items():
        row = rows.get(cid, {})
        per: dict[str, dict[str, float | None]] = {}
        for (who, stage, case), values in results.items():
            if who != cid:
                continue
            gain = _gain(base_value.get((stage, case)), fmean(values), minimize)  # type: ignore[arg-type]
            per.setdefault(stage, {})[case] = None if gain is None else round(gain, 4)
        reached = [s for s in stage_order if s in per]
        furthest = reached[-1] if reached else ""
        full = bool(full_stage) and furthest == full_stage
        gains = [g for g in per.get(furthest, {}).values() if g is not None]
        failed = bool(row.get("rejected")) or not gains
        number = cid.rsplit("-c", 1)[-1]
        combos.append({
            "id": cid, "n": int(number) if number.isdigit() else len(combos) + 1,
            "round": int(row.get("generation") or 0) if isinstance(row.get("generation"), int) else 0,
            "parent": row.get("parent_id"), "start_h": round(begun.get(cid, end), 4), "end_h": round(end, 4),
            "pct": None if failed else round(fmean(gains), 4), "full": full, "failed": failed,
            "cases": {stage: per[stage] for stage in reached}, "params": row.get("params") or {},
            "failed_runs": failed_evals.get(cid, 0),
            "base": cid == base,
            # a try not yet in runs.jsonl has no change to count yet
            "diff_stat": count(row["block"]) if count and isinstance(row.get("block"), str) else None,
        })
    combos.sort(key=lambda c: c["end_h"])

    best, top = [], 0.0
    base_combo = next((c for c in combos if c["base"]), None)
    if base_combo:
        best.append({"h": base_combo["end_h"], "pct": 0.0, "id": base})
    for c in combos:
        # Only a full look that finished every case (its row is in runs.jsonl): a look half done is the
        # mean of the cases it has, which falls as the others come in, and the line must never fall.
        if c["base"] or not c["full"] or c["id"] not in rows or c["pct"] is None or c["pct"] <= top:
            continue
        top = c["pct"]
        best.append({"h": c["end_h"], "pct": c["pct"], "id": c["id"]})
    best_ids = {b["id"] for b in best}
    for c in combos:
        c["best"] = c["id"] in best_ids and not c["base"]

    done = any(e.get("type") == "run_finished" for e in events) and not pending
    now = _now_h(t0, events) if not done else max(c["end_h"] for c in combos)
    stages_need = {s: len(stage_cases.get(s, ())) for s in stage_order}
    running: dict[str, dict[str, Any]] = {}
    for (cid, stage, case, _seed), at in pending.items():
        running.setdefault(cid, {"id": cid, "stage": stage, "since_h": round(begun.get(cid, at), 4), "done": 0,
                                 "need": max(1, stages_need.get(stage, 1))})
    for cid, item in running.items():
        item["done"] = len({case for (who, stage, case) in results if who == cid and stage == item["stage"]})
        item["full"] = item["stage"] == full_stage
    restarts = [round(hours(e), 4) for e in started[1:]]
    rounds = sorted({c["round"] for c in combos})
    starts = {r: round_start.get(r, min(c["start_h"] for c in combos if c["round"] == r)) for r in rounds}
    return {
        "available": True, "objective": objective, "minimize": minimize, "base": base,
        "full_stage": full_stage, "quick_cases": len(stage_cases.get(stage_order[0], ())) if stage_order else 0,
        "cases": cases, "combinations": combos, "best": best,
        "rounds": [{"round": r, "start_h": round(starts[r], 4)} for r in rounds],
        "running": sorted(running.values(), key=lambda r: r["since_h"]), "restarts": restarts,
        "parameters": parameters, "now_h": round(now, 4), "end_h": round(max(now, max(c["end_h"] for c in combos)), 4),
        "done": done, "check": _check(run_dir, objective, minimize),
    }


def _now_h(t0: datetime, events: list[dict[str, Any]]) -> float:
    now = datetime.now(timezone.utc)
    last = _moment(events[-1].get("ts")) if events else None
    if last and (now - last).total_seconds() > 6 * 3600:
        now = last  # a run that stopped long ago: "now" is when it last spoke
    return round(max(0.0, (now - t0).total_seconds() / 3600.0), 4)


def _check(run_dir: Path, objective: str, minimize: bool) -> dict[str, Any] | None:
    """The final check as far as it got: pairs done, per-case gains, and the summary."""
    from evolvekit.confirm import paired_summary

    events = _lines(run_dir / "confirm" / "final" / "events.jsonl")
    if not events:
        return None
    planned = next((e.get("runs_per_candidate") for e in events if e.get("runs_per_candidate")), None)
    pairs: dict[tuple[str, Any], dict[str, float]] = {}
    for e in events:
        if e.get("type") != "eval_finished" or not e.get("ok"):
            continue
        value = (e.get("kpis") or {}).get(objective)
        if not isinstance(value, (int, float)):
            continue
        who = "base" if e.get("candidate_id") == "baseline" else "new"
        pairs.setdefault((str(e.get("instance")), e.get("seed")), {})[who] = float(value)
    per: dict[str, list[float]] = {}
    for (case, _seed), both in pairs.items():
        if "base" in both and "new" in both:
            gain = _gain(both["base"], both["new"], minimize)
            if gain is not None:
                per.setdefault(case, []).append(gain)
    gains = {case: fmean(v) for case, v in per.items()}
    summary = paired_summary(list(gains.values())) if len(gains) >= 1 else {}
    return {"pairs": sum(len(v) for v in per.values()), "runs_per_case": planned, "cases": len(gains),
            "per_case": {k: round(v, 4) for k, v in gains.items()},
            "mean": summary.get("mean"), "ci95": summary.get("ci95"), "chance": summary.get("chance"),
            "worst": summary.get("worst")}
