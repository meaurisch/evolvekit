"""Campaign studies and hand-written run folders for the scheduler's tests (not collected).

`campaign_study` makes a demo-tour study with a schedule and an armed
campaign; `write_run` writes a run folder in any phase without running
anything; `Recorder` stands in for `jobs.start`, `continue_run`, `stop` and
`resume_final_check`, so a tick launches nothing."""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from evolvekit.app import scheduler
from evolvekit.app.store import Home
from evolvekit.harness.schedule import Schedule
from evolvekit.harness.study import load_study, save_study

WEEKEND = {"days": ["sat", "sun"], "from": "00:00", "to": "23:59"}
DEAD_PID = 2 ** 22 + 7
"""A pid no process has: a run written with it reads as ended."""


def local(text: str) -> datetime:
    """`"2026-11-14 00:00:05"` (or without seconds) as this computer's wall-clock time, aware."""
    return datetime.fromisoformat(text).astimezone()


def campaign_study(tmp_path: Path, name: str = "Tours", windows: tuple[dict[str, Any], ...] = (WEEKEND,), *,
                   armed_at: str = "2026-11-13T09:00:00+01:00") -> tuple[Home, Path]:
    """A demo-tour study in `Home(tmp_path / "my home")` (the same home on every call), from the
    `tune` template with the sample cases and one held back, `windows` saved as its schedule and
    its campaign armed at `armed_at`."""
    home = Home(tmp_path / "my home")
    slug = home.create_study("demo-tour", "tune", name)
    home.use_samples(slug)
    home.split(slug, 1)
    root = home.study_root(slug)
    study = load_study(root)
    study.schedule = Schedule.parse({"windows": [dict(w) for w in windows]})
    save_study(study, root)
    scheduler.arm(root, now=datetime.fromisoformat(armed_at))
    return home, root


def write_run(root: Path, run_id: str, phase: str, *, generations: int = 2, planned: int = 6, max_hours: float = 1.0,
              used_s: float = 0.0, final: dict[str, Any] | None = None, check: bool = False, error: str | None = None,
              alive: bool = False, launch: int = 1, archived: bool = True) -> Path:
    """A run folder written by hand: `job.json` in `phase`, `plan.json` (`planned` rounds,
    `max_hours`), `runs.jsonl` (the starting point and a row per round done -- the starting point
    only when not `archived`), `events.jsonl` (two events `used_s` apart: the search time used),
    `launch.json` (`"launch": launch`) and a compiled `evolvekit.yaml`. `alive` writes this
    process's pid and a fresh launch time, so `jobs.phase` reads the run as live. Called again on
    the same id it rewrites the run."""
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    started = now if alive else datetime(2026, 11, 1, tzinfo=timezone.utc)
    pid = os.getpid() if alive else DEAD_PID
    plan = {"generations": planned, "rounds": max(0, planned - 2), "children": 2, "hours": max_hours,
            "max_hours": max_hours}
    job: dict[str, Any] = {"phase": phase, "pid": pid, "started_at": started.isoformat(timespec="seconds"),
                           "plan": plan}
    if phase in ("done", "stopped", "failed"):
        job["finished_at"] = (started + timedelta(seconds=used_s)).isoformat(timespec="seconds")
        if generations > 0:
            job["search"] = {"stop_reason": "the plan's rounds are done" if generations >= planned else "stopped on request",
                             "generations": generations, "best_id": f"g{generations}", "aborted": False}
    if final is not None:
        job["final"] = final
    if check:
        job["check"] = {"seeds": 1, "of": 3}
    if error is not None:
        job["error"] = error
    (run_dir / "job.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
    (run_dir / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    seed = {"neighbours": 8, "restart_after": 2000, "accept_worse": 0.0, "init": "random"}
    rows = [{"id": "seed", "operator": "human-seed", "generation": 0, "score": -100.0, "params": seed}]
    if archived:
        rows += [{"id": f"g{g}", "operator": "mutate", "generation": g, "score": -100.0 + g,
                  "params": {**seed, "restart_after": 2000 + g}} for g in range(1, generations + 1)]
    (run_dir / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    first = started
    events = [{"seq": 1, "ts": first.isoformat(), "session": "s1", "type": "run_started"},
              {"seq": 2, "ts": (first + timedelta(seconds=used_s)).isoformat(), "session": "s1", "type": "heartbeat"}]
    (run_dir / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    (run_dir / "launch.json").write_text(json.dumps({
        "pid": pid, "started_at": started.isoformat(timespec="seconds"), "host": socket.gethostname(),
        "argv": ["-m", "evolvekit.app.job", str(root), run_id], "launch": launch}, indent=2), encoding="utf-8")
    (run_dir / "evolvekit.yaml").write_text("# compiled by hand for a test\n", encoding="utf-8")
    return run_dir


class Recorder:
    """`start`, `continue_run`, `stop` and `recheck` that record each call as `(name, args, kwargs)`
    in `calls` and return what `jobs` would -- or, for `start`, raise `start_raises`."""

    def __init__(self, start_raises: Exception | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.start_raises = start_raises

    def start(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("start", args, kwargs))
        if self.start_raises is not None:
            raise self.start_raises
        return {"run": kwargs.get("resume") or "x", **({"resumed": True} if kwargs.get("resume") else {})}

    def continue_run(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("continue_run", args, kwargs))
        return {"run": args[2], "continued": True}

    def stop(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("stop", args, kwargs))
        return {"stopping": True, "run": args[1]}

    def recheck(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("recheck", args, kwargs))
        return {"run": args[2]}

    def calls_for(self, only: list[str] | None = None) -> dict[str, Any]:
        """The recorders as `tick`'s keyword arguments (`only` some of them)."""
        every = {"start": self.start, "continue_run": self.continue_run, "stop": self.stop, "recheck": self.recheck}
        return {name: call for name, call in every.items() if only is None or name in only}

    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]
