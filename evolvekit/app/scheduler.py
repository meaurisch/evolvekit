"""Campaigns: a study with a `schedule` runs in its windows, one session at a time.

`campaign.json` in the study folder says whether the campaign is armed (D3: a
schedule alone never starts a draft). Every few seconds the app's ticker --
and `study wait`/`watch` in a terminal -- calls `tick`, which reads what is on
disk (D2: never an edge it has seen) and, for every armed campaign, the oldest
first (D6):

* inside a span, with nothing running in the home: starts a run, resumes the
  latest one, runs its final check again, or gives a finished run more time,
  never more than the campaign's search time left (D5, D7, D48);
* outside every span: asks a searching run to stop -- never one in its final
  check, nor a session a person started by hand (D4, D47);
* on every tick, inside a span or not: a campaign whose search time is used is
  `complete`, one past its `until` is `ended` (D49), and three failed sessions
  in a row pause it (D8, D48).

A session is one launch of a run (`launch.json`'s `launch`), so `<run>:<n>`
names it. Windows move under the rehearsal clock (`clock.json`, D15); runs keep
real time. The whole tick holds `<home>/scheduler.lock` (D13).
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from evolvekit.app import AppError, jobs
from evolvekit.app.running import APP_FILE, read_running
from evolvekit.events import read_events
from evolvekit.harness import HarnessError
from evolvekit.harness.execute import read_job
from evolvekit.harness.schedule import DAY_WORDS, MONTH_WORDS, Schedule, when_words
from evolvekit.harness.study import Study, load_study
from evolvekit.ledger import _atomic_write, read_jsonl
from evolvekit.lock import pid_alive
from evolvekit.stopping import STOP_REQUEST, active_seconds

__all__ = [
    "ACTIONS_PER_WINDOW", "BY_YOU", "CAMPAIGN", "CLOCK_FILE", "Clock", "FAILED_PAUSE_LINE", "FAILURES_TO_PAUSE", "LOCK",
    "MIN_SESSION_S", "NO_SCHEDULE", "SPAN", "TICK_WRITE_S", "arm", "by_hand", "campaign_done", "campaign_run",
    "campaign_state", "clear_clock",
    "clock_of", "launch_queued", "mark_by_hand", "pause", "read_campaign", "search_left_s", "session_of",
    "scheduled_words", "start_campaign", "stop_by_person", "tick", "tick_lock", "window_left_s", "write_clock",
]

CAMPAIGN = "campaign.json"
"""In the study folder: `{"state": "armed"|"paused"|"complete"|"ended", "armed_at", "reason", "by_hand":
{"run", "session"}|None, "stopped_by_window", "window", "actions", "failed_sessions", "ticked_at"}`;
`counted`: the last session (`<run>:<n>`) whose end the tick has counted, so each is counted once;
`after`: the study's last run before this campaign (a new campaign after a finished one sees only
the runs started after it); `missed`: `{"start", "end"}` of a window no tick saw, kept for the line."""
MIN_SESSION_S = 600.0
"""A continuation shorter than this is not started; a campaign with less search time left is complete (D5)."""
ACTIONS_PER_WINDOW = 3
"""The most launches the tick makes in one span (D8)."""
FAILURES_TO_PAUSE = 3
"""Failed sessions in a row that pause the campaign (D8, D48)."""
TICK_WRITE_S = 60.0
"""`ticked_at` is written at most this often."""
LOCK = "scheduler.lock"
"""In the home: held through every tick (D13)."""
LOCK_STALE_S = 60.0
"""A lock whose process is gone, or an empty or damaged one this old, is taken over."""
LOCK_LIVE_STALE_S = 600.0
"""A lock whose process is alive is taken over only this old: a tick waiting on a slow launcher
(up to `detach.LAUNCH_TIMEOUT_S`) keeps it, and refreshes it before each launch."""
CLOCK_FILE = "clock.json"
"""In the home: the rehearsal clock (D15), honoured while the app that wrote it runs."""
SPAN = "campaign-span.json"
"""In a run folder: `{"until_real": iso, "session": n}`, the real moment the window of a scheduled
session ends; the run keeps the computer awake until then, and through a final check past it
(`awake`, D46). `session` is the launch it was written for, so a later session by hand of the same
run is never kept awake by it."""
FAILED_PAUSE_LINE = "The campaign is paused: its last three sessions failed ({error}). Start it again when the cause is fixed."
NO_SCHEDULE = "the study has no schedule any more"
COMPLETE = "the campaign's search time is used"
ENDED = "its schedule has no window left"
BY_YOU = "stopped by you"
"""The reason of a pause a person asked for (`stop_by_person`, D4)."""


def _home_root(home: Any) -> Path:
    """The home's folder, from a `Home` or a path (a `Path`'s own `.root` is its drive's root)."""
    return Path(home) if isinstance(home, (str, os.PathLike)) else Path(home.root)


def _read(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _moment(raw: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None
    if moment is not None and moment.tzinfo is None:
        moment = moment.astimezone()
    return moment


# ---------------------------------------------------------------------------
# the clock
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Clock:
    """The real clock, or the rehearsal clock: `start` at the real moment `origin`
    (`time.time()`), running `speed` times as fast."""

    start: datetime | None = None
    speed: float = 1.0
    origin: float = 0.0

    @property
    def simulated(self) -> bool:
        return self.start is not None

    def now(self) -> datetime:
        """Aware, in the computer's zone."""
        if self.start is None:
            return datetime.now().astimezone()
        start = self.start if self.start.tzinfo is not None else self.start.astimezone()
        moved = timedelta(seconds=(time.time() - self.origin) * self.speed)
        return (start.astimezone(timezone.utc) + moved).astimezone()

    def words(self) -> str:
        """`"rehearsal clock: Sat 14 Nov 01:10, 60 × real time"`; `""` for the real clock."""
        if self.start is None:
            return ""
        now = self.now()
        return (f"rehearsal clock: {DAY_WORDS[now.weekday()]} {now.day} {MONTH_WORDS[now.month - 1]} "
                f"{now.hour:02d}:{now.minute:02d}, {self.speed:g} × real time")


def write_clock(home_root: Path, start: datetime, speed: float) -> Path:
    """`<home>/clock.json`: the rehearsal clock starts at `start` now, for the app of this process."""
    speed = float(speed)
    if not speed > 0:
        raise ValueError("the clock's speed: more than 0")
    start = start if start.tzinfo is not None else start.astimezone()
    path = Path(home_root) / CLOCK_FILE
    _atomic_write(path, json.dumps({"start": start.isoformat(), "speed": speed, "origin": time.time(),
                                    "pid": os.getpid()}, indent=2) + "\n")
    return path


def clear_clock(home_root: Path) -> None:
    (Path(home_root) / CLOCK_FILE).unlink(missing_ok=True)


def clock_of(home: Any) -> Clock:
    """The rehearsal clock while the app whose pid `clock.json` names still runs (`read_running`),
    else the real clock. A `clock.json` whose process is gone is removed, and so is one beside an
    `app.json` whose app is gone (its writing app ended without its `finally`). One whose process
    lives with no `app.json` is left: it is honoured only beside a live `app.json` that names its
    pid, so it is never read, and the next app clears or rewrites it."""
    root = _home_root(home)
    doc = _read(root / CLOCK_FILE)
    if doc is None:
        return Clock()
    had_app = (root / APP_FILE).is_file()
    app = read_running(root)
    if app is not None and app.get("pid") == doc.get("pid"):
        start = _moment(doc.get("start"))
        try:
            speed, origin = float(doc.get("speed")), float(doc.get("origin"))
        except (TypeError, ValueError):
            return Clock()
        if start is not None and speed > 0:
            return Clock(start, speed, origin)
        return Clock()
    try:
        gone = not pid_alive(int(doc.get("pid") or 0))
    except (TypeError, ValueError):
        gone = True
    if gone or (had_app and app is None):
        clear_clock(root)
    return Clock()


# ---------------------------------------------------------------------------
# campaign.json
# ---------------------------------------------------------------------------


def _blank() -> dict[str, Any]:
    return {"state": "armed", "armed_at": None, "reason": None, "by_hand": None, "stopped_by_window": None,
            "window": None, "actions": 0, "failed_sessions": [], "ticked_at": None, "counted": None, "after": None,
            "missed": None}


def read_campaign(root: Path) -> dict[str, Any] | None:
    """The study's campaign, every key present; None when it has none."""
    doc = _read(Path(root) / CAMPAIGN)
    if doc is None:
        return None
    campaign = {**_blank(), **doc}
    if not isinstance(campaign.get("failed_sessions"), list):
        campaign["failed_sessions"] = []
    if not isinstance(campaign.get("actions"), int):
        campaign["actions"] = 0
    return campaign


def _write_campaign(root: Path, campaign: dict[str, Any]) -> None:
    _atomic_write(Path(root) / CAMPAIGN, json.dumps(campaign, indent=2) + "\n")


def arm(root: Path, *, now: datetime, renew: bool = False) -> dict[str, Any]:
    """Arm the study's campaign at `now`: its windows start runs from the next tick. The failures
    and the window's launches are counted afresh. `renew`: a new campaign, after one that finished
    or ended -- its runs are the ones started from now on (`after`: the study's latest run now), so
    its first window starts a new run and the old one stays a past run."""
    campaign = {**(read_campaign(root) or _blank()), "state": "armed", "armed_at": _iso(now), "reason": None,
                "failed_sessions": [], "stopped_by_window": None, "window": None, "actions": 0, "missed": None}
    if renew:
        dirs = jobs._run_dirs(Path(root))  # noqa: SLF001 - the runs, oldest first, as jobs lists them
        campaign.update(after=dirs[-1].name if dirs else None, by_hand=None, counted=None)
    _write_campaign(root, campaign)
    return campaign


def pause(root: Path, *, reason: str, state: str = "paused") -> None:
    """The campaign stops starting runs: `paused` (or `complete`, `ended`) for `reason`."""
    campaign = {**(read_campaign(root) or _blank()), "state": state, "reason": reason}
    _write_campaign(root, campaign)


def session_of(run_dir: Path) -> int:
    """The run's latest session: `launch.json`'s `launch` (0 when none)."""
    number = (_read(Path(run_dir) / jobs.LAUNCH) or {}).get("launch")
    return number if isinstance(number, int) and not isinstance(number, bool) and number > 0 else 0


def search_left_s(run_dir: Path) -> float:
    """The campaign's search time left on this run: `plan.json`'s `max_hours` less what its
    sessions used (`active_seconds`), at least 0."""
    run_dir = Path(run_dir)
    plan = _read(run_dir / "plan.json") or (read_job(run_dir) or {}).get("plan") or {}
    try:
        total = float(plan.get("max_hours") or 0.0) * 3600.0
    except (TypeError, ValueError):
        total = 0.0
    return max(0.0, total - active_seconds(read_events(run_dir)))


# ---------------------------------------------------------------------------
# the lock
# ---------------------------------------------------------------------------


def _stale(path: Path, held: str) -> bool:
    doc = None
    try:
        doc = json.loads(held)
    except ValueError:
        pass
    if not isinstance(doc, dict):
        # Being written this instant, or damaged: stale only when old.
        try:
            return time.time() - path.stat().st_mtime > LOCK_STALE_S
        except OSError:
            return False
    try:
        pid = int(doc.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid <= 0 or not pid_alive(pid):
        return True
    at = _moment(doc.get("at"))
    return at is None or (datetime.now(timezone.utc) - at).total_seconds() > LOCK_LIVE_STALE_S


def _lock_text() -> str:
    return json.dumps({"pid": os.getpid(), "at": datetime.now(timezone.utc).isoformat()})


def _try_lock(path: Path) -> str | None:
    """Create the lock (`open(..., "x")`), taking over a stale one; the text written, or None
    when another live holder has it."""
    text = _lock_text()
    for _ in range(3):
        try:
            with open(path, "x", encoding="utf-8") as out:
                out.write(text)
            return text
        except FileExistsError:
            pass
        try:
            held = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue  # released meanwhile: try again
        except OSError:
            return None
        if not _stale(path, held):
            return None
        # Take it over: move it aside, which one process wins, and make sure what moved is what
        # was judged stale -- not a live lock another process took meanwhile.
        aside = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.stale")
        try:
            os.replace(path, aside)
        except OSError:
            continue
        try:
            moved = aside.read_text(encoding="utf-8")
        except OSError:
            moved = None
        aside.unlink(missing_ok=True)
        if moved is not None and moved != held:
            try:  # put the live lock back
                with open(path, "x", encoding="utf-8") as out:
                    out.write(moved)
            except OSError:
                pass
            return None
    return None


def _refresh(path: Path, held: str) -> str:
    """The lock's `at` made now, while this holder still has it; the text it now holds."""
    try:
        if path.read_text(encoding="utf-8") != held:
            return held
        text = _lock_text()
        _atomic_write(path, text)
        return text
    except OSError:
        return held


def _release(path: Path, text: str) -> None:
    try:
        if path.read_text(encoding="utf-8") == text:
            path.unlink()
    except OSError:
        pass


@contextmanager
def tick_lock(home: Any, *, wait_s: float = 10.0) -> Iterator[None]:
    """Hold `<home>/scheduler.lock`, waiting up to `wait_s` for a tick that has it: a person's
    launch on a campaign takes it, so no tick sees the session before `mark_by_hand` does."""
    path = _home_root(home) / LOCK
    deadline = time.monotonic() + max(0.0, wait_s)
    while True:
        held = _try_lock(path)
        if held is not None:
            break
        if time.monotonic() >= deadline:
            raise AppError("the scheduler is busy starting or stopping a run: try again in a moment", 409)
        time.sleep(0.1)
    try:
        yield
    finally:
        _release(path, held)


def launch_queued(home: Any) -> str | None:
    """`jobs.launch_queued` under `<home>/scheduler.lock`, so it and another process's tick never
    both launch (D6, D13); None, launching nothing, while another live process holds the lock."""
    path = _home_root(home) / LOCK
    held = _try_lock(path)
    if held is None:
        return None
    try:
        return jobs.launch_queued(home)
    finally:
        _release(path, held)


def mark_by_hand(home: Any, slug: str, run_id: str) -> None:
    """The session a person just launched on `run_id` is theirs: no window's end stops it (D47).
    Called inside `tick_lock`, right after the launch; a no-op unless the campaign is armed. The
    run's `campaign-span.json` goes: a session by hand keeps nothing awake."""
    root = Path(home.studies_dir) / slug
    campaign = read_campaign(root)
    if campaign is None or campaign.get("state") != "armed":
        return
    run_dir = root / "runs" / run_id
    campaign["by_hand"] = {"run": run_id, "session": session_of(run_dir)}
    campaign["missed"] = None  # a session of the campaign ran: the missed window is past news
    _write_campaign(root, campaign)
    (run_dir / SPAN).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# the tick
# ---------------------------------------------------------------------------


def _next_session(run_dir: Path) -> int:
    """The number the run's next launch will have (`jobs._launch_number`, from its `launch.json`)."""
    try:
        previous: str | None = (run_dir / jobs.LAUNCH).read_text(encoding="utf-8")
    except OSError:
        previous = None
    return jobs._launch_number(previous)  # noqa: SLF001 - the one rule of a session's number


def _window_words(span: tuple[datetime, datetime]) -> str:
    start, end = span
    return (f"{DAY_WORDS[start.weekday()]} {start.hour:02d}:{start.minute:02d} – "
            f"{DAY_WORDS[end.weekday()]} {end.hour:02d}:{end.minute:02d}")


def _archived(run_dir: Path) -> bool:
    """Whether the run archived anything past its starting point: a row of a round."""
    try:
        return any(int(row.get("generation") or 0) >= 1 for row in read_jsonl(run_dir / "runs.jsonl"))
    except (OSError, TypeError, ValueError):
        return False


def _ended_with_search(job: dict[str, Any]) -> bool:
    search = job.get("search") or {}
    return job.get("phase") in ("stopped", "failed") and bool(search) and not search.get("aborted")


def _check_unfinished(run_dir: Path, job: dict[str, Any]) -> bool:
    """The search is done and the final check was stopped, or its process died: it is run
    again (D7)."""
    if not _ended_with_search(job):
        return False
    return bool((job.get("final") or {}).get("stopped") or "check" in job
                or (read_job(run_dir) or {}).get("phase") == "check")


def _no_verdict(run_dir: Path, job: dict[str, Any]) -> bool:
    """The latest session (a continuation, say) ended -- stopped by its window, or its process
    gone -- with no rounds of the plan left and no verdict. It goes on while the campaign has
    search time; else its final check is run again, so the campaign never waits for ever."""
    if not _ended_with_search(job) or (job.get("final") or {}).get("decision"):
        return False
    search = job.get("search") or {}
    try:
        planned = int((jobs._plan(run_dir, job) or {}).get("generations") or 0)  # noqa: SLF001 - the run's plan, as can_resume reads it
        done = int(search.get("generations") or 0)
    except (TypeError, ValueError):
        return False
    return planned > 0 and done >= planned


def _in_check(run_dir: Path, state: str | None) -> bool:
    """A run in its final check, or launched for one and not yet saying so (`--check`)."""
    if state == "check":
        return True
    argv = (_read(run_dir / jobs.LAUNCH) or {}).get("argv")
    return state == "starting" and isinstance(argv, list) and "--check" in argv


def _hours(seconds: float) -> str:
    minutes = round(seconds / 60.0)
    return f"{minutes} min" if minutes < 90 else f"{seconds / 3600.0:.1f} h"


class _Tick:
    def __init__(self, home: Any, now: datetime, speed: float, real_now: datetime,
                 calls: dict[str, Callable[..., Any]], lock: Path, held: str) -> None:
        self.home, self.now, self.speed, self.real_now, self.calls = home, now, speed, real_now, calls
        self.lock, self.held = lock, held
        """The lock and the text this tick holds it with (`_refresh` renews it before a launch)."""
        self.said: list[str] = []
        self._busy: bool | None = None

    def busy(self) -> bool:
        """Whether any run in the home has the computer (asked once per tick)."""
        if self._busy is None:
            self._busy = jobs.active_run(self.home) is not None
        return self._busy

    def run(self) -> list[str]:
        armed = []
        for root in jobs._study_roots(Path(self.home.studies_dir)):  # noqa: SLF001 - the home's studies, as jobs reads them
            campaign = read_campaign(root)
            if campaign is not None and campaign.get("state") == "armed":
                when = _moment(campaign.get("armed_at"))
                armed.append((when.timestamp() if when else float("inf"), root.name, root, campaign))
        armed.sort(key=lambda item: item[:2])
        launched = False
        for _, _, root, campaign in armed:
            before = json.dumps(campaign, sort_keys=True)
            self.note_missed(root, campaign)
            if not launched:  # one launch per tick: the studies after it wait for the next, but are stamped
                launched = self.study(root, campaign)
            self.save(root, campaign, before)
        return self.said

    def save(self, root: Path, campaign: dict[str, Any], before: str) -> None:
        """Write the campaign when it changed, with `ticked_at` on the window clock (`now`): when
        it has none, when it is later than `now` (a rehearsal ended or started again), or when
        `TICK_WRITE_S` have passed on that clock since."""
        ticked = _moment(campaign.get("ticked_at"))
        if (ticked is None or ticked.timestamp() > self.now.timestamp()
                or self.now.timestamp() - ticked.timestamp() >= TICK_WRITE_S):
            campaign["ticked_at"] = _iso(self.now)
        if json.dumps(campaign, sort_keys=True) != before:
            _write_campaign(root, campaign)

    def note_missed(self, root: Path, campaign: dict[str, Any]) -> None:
        """Keep the window no tick saw (`missed`), before this tick's stamp hides it: a whole span
        passed since the last tick. It is said until the next span starts or a session launches."""
        try:
            schedule = load_study(root).schedule
        except (HarnessError, OSError, ValueError):
            return
        if schedule is None:
            return
        found = _missed(schedule, campaign, self.now)
        if found is not None:
            campaign["missed"] = {"start": _iso(found[0]), "end": _iso(found[1])}
        elif campaign.get("missed") is not None and _missed_kept(schedule, campaign.get("missed"), self.now) is None:
            campaign["missed"] = None

    def say(self, root: Path, words: str) -> None:
        self.said.append(f"{root.name}: {words}")

    def end(self, root: Path, campaign: dict[str, Any], state: str, reason: str, words: str) -> None:
        campaign.update(state=state, reason=reason)
        self.say(root, words)

    def failed(self, root: Path, campaign: dict[str, Any], key: str, error: str) -> bool:
        """Count a failed session; True when it paused the campaign."""
        campaign["failed_sessions"] = [*campaign["failed_sessions"], key]
        if len(campaign["failed_sessions"]) < FAILURES_TO_PAUSE:
            return False
        reason = FAILED_PAUSE_LINE.format(error=error)
        self.end(root, campaign, "paused", reason, reason)
        return True

    def study(self, root: Path, campaign: dict[str, Any]) -> bool:
        """One campaign's turn; True when it launched (or tried to launch) a session."""
        try:
            study = load_study(root)
        except (HarnessError, OSError, ValueError):
            return False  # a study.yaml being edited: the next tick reads it again
        schedule = study.schedule
        if schedule is None:
            self.end(root, campaign, "paused", NO_SCHEDULE, f"the campaign is paused: {NO_SCHEDULE}")
            return False
        run_dir, job = _latest(root, campaign)  # the campaign's own latest run (`after`)
        state = job.get("phase")
        session = session_of(run_dir) if run_dir is not None else 0
        active = state in jobs.ACTIVE

        hand = self._by_hand(root, campaign)

        # Each session's end is counted once: a failure adds to the list, a clean end empties it.
        if run_dir is not None and state in ("done", "stopped", "failed"):
            key = f"{run_dir.name}:{session}"
            if campaign.get("counted") != key:
                campaign["counted"] = key
                if state == "failed":
                    if self.failed(root, campaign, key, str(job.get("error") or "no error was recorded")):
                        return False
                else:
                    campaign["failed_sessions"] = []

        judged = _judged(run_dir, job, schedule, self.now)
        if judged == "complete":
            self.end(root, campaign, "complete", COMPLETE, f"the campaign is complete: {COMPLETE}")
            return False
        if judged == "ended":
            self.end(root, campaign, "ended", ENDED, f"the campaign has ended: {ENDED}")
            return False

        span = schedule.span_at(self.now)
        if active:
            if span is None and state in ("starting", "search") and not _in_check(run_dir, state):
                mine = hand is not None and hand.get("run") == run_dir.name and hand.get("session") == session
                if not mine and not (run_dir / STOP_REQUEST).exists():
                    try:
                        self.calls["stop"](root, run_dir.name)
                    except AppError:
                        return False  # it ended meanwhile
                    campaign["stopped_by_window"] = run_dir.name
                    self.say(root, f"asked run {run_dir.name} to stop: its window ended")
            return False
        if state == "queued" or span is None or self.busy():
            return False

        window = _iso(span[0])
        if campaign.get("window") != window:
            campaign["window"], campaign["actions"] = window, 0
        if campaign["actions"] >= ACTIONS_PER_WINDOW:
            return False
        action = self._action(root, run_dir, job, span)
        if action is None:
            return False
        name, args, kwargs, words, refused = action
        campaign["actions"] += 1
        _write_campaign(root, campaign)  # counted before the call: a launch that crashes the tick still counts
        self.held = _refresh(self.lock, self.held)  # a slow launcher must not make the lock look stale
        fresh = name == "start" and not kwargs.get("resume")
        if not fresh and run_dir is not None:  # the session's span, before it starts: it reads the file
            self.span_file(run_dir, span, schedule.keep_awake, _next_session(run_dir))
        try:
            answer = self.calls[name](*args, **kwargs)
        except (AppError, HarnessError) as exc:
            if not fresh and run_dir is not None:
                # Refused: that launch's number is not used up, so a later session (by hand,
                # say) would take it and the file with it.
                (run_dir / SPAN).unlink(missing_ok=True)
            if jobs.active_run(self.home) is not None:
                # Another run took the computer between this tick's look and the launch: not a
                # failure of this campaign, nor one of its window's launches.
                campaign["actions"] -= 1
                return True
            self.say(root, f"{refused}: {exc}")
            self.failed(root, campaign, f"launch:{_iso(self.now)}", str(exc))
            return True
        campaign["missed"] = None  # a session of the campaign launched
        new_run = answer.get("run") if fresh and isinstance(answer, dict) else None
        if new_run and jobs._RUN.match(str(new_run)):  # noqa: SLF001 - a run's name, as jobs judges it
            new_dir = root / "runs" / str(new_run)
            self.span_file(new_dir, span, schedule.keep_awake, max(1, session_of(new_dir)))
        self.say(root, f"{words} (window {_window_words(span)})")
        return True

    def span_file(self, run_dir: Path, span: tuple[datetime, datetime], keep_awake: bool, session: int) -> None:
        """`<run>/campaign-span.json`: the real moment this session's window ends (under the rehearsal
        clock, `real_now + (end - now) / speed`), so the run keeps the computer awake till then (D46),
        and the session it is for; removed when the study's `keep_awake` is off. Only into a run
        folder that exists."""
        path = run_dir / SPAN
        if not keep_awake:
            path.unlink(missing_ok=True)
            return
        if not run_dir.is_dir():
            return
        seconds = (span[1].timestamp() - self.now.timestamp()) / self.speed
        until = self.real_now + timedelta(seconds=max(0.0, seconds))
        _atomic_write(path, json.dumps({"until_real": until.isoformat(), "session": session}) + "\n")

    def _by_hand(self, root: Path, campaign: dict[str, Any]) -> dict[str, Any] | None:
        """The campaign's session by hand while it lasts; cleared once it has ended (its run not
        active, or a later session in its `launch.json`)."""
        hand = campaign.get("by_hand")
        if not isinstance(hand, dict):
            campaign["by_hand"] = None
            return None
        run_dir = root / "runs" / str(hand.get("run"))
        state = jobs.phase(run_dir).get("phase") if run_dir.is_dir() else None
        session = session_of(run_dir) if run_dir.is_dir() else 0
        if state == "queued" and hand.get("session") == 0:
            return hand  # started by hand behind another study's run: its session is still to come
        if hand.get("session") == 0 and session == 1:
            hand = campaign["by_hand"] = {**hand, "session": 1}  # that queued run, launched: the same session
        if state in jobs.ACTIVE and session == hand.get("session"):
            return hand
        campaign["by_hand"] = None
        return None

    def _action(self, root: Path, run_dir: Path | None, job: dict[str, Any],
                span: tuple[datetime, datetime]) -> tuple[str, tuple[Any, ...], dict[str, Any], str, str] | None:
        """What the window does with the study's latest run: `(call, args, kwargs, words, refused)`."""
        window_s = (span[1].timestamp() - self.now.timestamp()) / self.speed
        return _next_action(self.home, root, run_dir, job, window_s)


def _judged(run_dir: Path | None, job: dict[str, Any], schedule: Schedule, now: datetime) -> str | None:
    """`"complete"`, `"ended"` or None: what the campaign is, whatever the window, by its latest run
    (D49). Complete: the run is done -- or stopped with a verdict -- and the campaign's search time
    left is under `MIN_SESSION_S` (or a done run cannot go on); ended: past `until`, no run active."""
    state = job.get("phase")
    if run_dir is not None and state == "done" and (search_left_s(run_dir) < MIN_SESSION_S
                                                    or not jobs.can_continue(run_dir, job)):
        return "complete"
    if (run_dir is not None and state == "stopped" and (job.get("final") or {}).get("decision")
            and search_left_s(run_dir) < MIN_SESSION_S):
        return "complete"  # a verdict, and no time to search on: armed, it would wait for ever
    if state not in jobs.ACTIVE and state != "queued" and schedule.past_until(now):
        return "ended"
    return None


def _next_action(home: Any, root: Path, run_dir: Path | None, job: dict[str, Any],
                 window_s: float | None) -> tuple[str, tuple[Any, ...], dict[str, Any], str, str] | None:
    """What a window does with the study's latest run: `(call, args, kwargs, words, refused)`, or
    None. A continuation gets the smaller of `window_s` (real seconds of the window left; None: no
    window bounds it, as for `--now`) and the campaign's search time left (D5)."""
    state = job.get("phase")
    if run_dir is None or state == "unknown" or (state in ("stopped", "failed") and not _archived(run_dir)):
        return "start", (home, root), {}, "started a run", "could not start a run"
    run = run_dir.name
    # A final check that began ended its search: it is checked again, even when the search
    # stopped on its time before the plan's last round (`can_resume` would search on).
    if _check_unfinished(run_dir, job):
        return ("recheck", (home, root, run), {}, f"started the final check of run {run} again",
                f"could not start the final check of run {run} again")
    if jobs.can_resume(run_dir, job):
        return "start", (home, root), {"resume": run}, f"resumed run {run}", f"could not resume run {run}"
    # No verdict and no rounds left: it goes on while there is search time, else checked again.
    if _no_verdict(run_dir, job) and (search_left_s(run_dir) < MIN_SESSION_S
                                      or not jobs.can_continue(run_dir, job)):
        return ("recheck", (home, root, run), {}, f"started the final check of run {run} again",
                f"could not start the final check of run {run} again")
    if jobs.can_continue(run_dir, job):
        seconds = min(search_left_s(run_dir), jobs.MAX_CONTINUE_HOURS * 3600.0)
        if window_s is not None:
            seconds = min(seconds, window_s)
        if seconds < MIN_SESSION_S:
            return None
        return ("continue_run", (home, root, run), {"hours": seconds / 3600.0},
                f"gave run {run} {_hours(seconds)} more search time", f"could not give run {run} more time")
    return None


def tick(home: Any, *, now: datetime, speed: float = 1.0, real_now: datetime | None = None,
         start: Callable[..., Any] = jobs.start, continue_run: Callable[..., Any] = jobs.continue_run,
         stop: Callable[..., Any] = jobs.stop, recheck: Callable[..., Any] = jobs.resume_final_check) -> list[str]:
    """One look at every armed campaign in the home, the oldest first: one line per thing done,
    `"<slug>: <what>"`. `now` is the clock the windows are read on (the rehearsal clock's, when
    on, and the clock `ticked_at` is stamped on), `speed` how much faster it runs than real time;
    `real_now` the real clock (for the span file, Task 4). Returns `[]`
    at once when another live process is ticking."""
    real_now = real_now if real_now is not None else datetime.now().astimezone()
    now = now if now.tzinfo is not None else now.astimezone()
    path = _home_root(home) / LOCK
    held = _try_lock(path)
    if held is None:
        return []
    calls = {"start": start, "continue_run": continue_run, "stop": stop, "recheck": recheck}
    ticking = _Tick(home, now, float(speed), real_now, calls, path, held)
    try:
        return ticking.run()
    finally:
        _release(path, ticking.held)


# ---------------------------------------------------------------------------
# the campaign in words, a person's start and stop
# ---------------------------------------------------------------------------


def _latest(root: Path, campaign: dict[str, Any] | None = None) -> tuple[Path | None, dict[str, Any]]:
    """The study's latest run folder and its phase (`{}` when it has none); with `campaign`, the
    latest of the runs started after its `after` (a new campaign's own), None before its first."""
    dirs = jobs._run_dirs(Path(root))  # noqa: SLF001 - the runs, oldest first, as jobs lists them
    after = (campaign or {}).get("after")
    names = [d.name for d in dirs]
    if after in names:
        dirs = dirs[names.index(after) + 1:]
    run_dir = dirs[-1] if dirs else None
    return run_dir, (jobs.phase(run_dir) if run_dir is not None else {})


def campaign_run(root: Path) -> str | None:
    """The latest run of the study's campaign (after a renewal, only the new campaign's), or None."""
    run_dir, _ = _latest(root, read_campaign(root))
    return run_dir.name if run_dir is not None else None


def _missed_kept(schedule: Schedule, missed: Any, now: datetime) -> datetime | None:
    """The start of the recorded missed window while it is news: no span has started since it
    ended. None once one has (or when nothing is recorded)."""
    if not isinstance(missed, dict):
        return None
    start, end = _moment(missed.get("start")), _moment(missed.get("end"))
    if start is None or end is None:
        return None
    for around in (end, now):
        for begin, _ in schedule.spans(around):
            if end.timestamp() < begin.timestamp() <= now.timestamp():
                return None
    return start.astimezone()


def _missed(schedule: Schedule, campaign: dict[str, Any], now: datetime) -> tuple[datetime, datetime] | None:
    """The latest span that began after the campaign was armed and after its last tick
    (`ticked_at`, on the window clock), and ended before `now`: no tick saw it, so the app (or
    `study wait`) was not running then."""
    marks = [m for m in (_moment(campaign.get("armed_at")), _moment(campaign.get("ticked_at"))) if m is not None]
    if not marks:
        return None
    since = max(marks, key=lambda m: m.timestamp())
    found: tuple[datetime, datetime] | None = None
    for around in (since, now):
        for start, end in schedule.spans(around):
            if since.timestamp() < start.timestamp() and end.timestamp() <= now.timestamp():
                if found is None or start.timestamp() > found[0].timestamp():
                    found = (start, end)
    return found


def campaign_state(root: Path, study: Study, *, now: datetime) -> dict[str, Any] | None:
    """The study's campaign in the words Home, the Study page and `study status` say: None without a
    schedule, else `{"state": "draft"|"armed"|"paused"|"complete"|"ended", "next": iso|None, "edge":
    "start"|"end"|None, "line", "tone": "warn"|None, "missed": iso|None}`. `next` is the next edge
    of the windows (`edge` says which). An armed campaign whose latest run makes it complete or
    ended is said so at once (D49), before the next tick writes it."""
    schedule = study.schedule
    if schedule is None:
        return None
    now = now if now.tzinfo is not None else now.astimezone()
    found = schedule.next_edge(now)
    edge_at, edge = found if found is not None else (None, None)
    out: dict[str, Any] = {"state": "armed", "next": _iso(edge_at) if edge_at is not None else None, "edge": edge,
                           "line": "", "tone": None, "missed": None}
    when = when_words(edge_at, now) if edge_at is not None else ""
    campaign = read_campaign(root)
    if campaign is None:
        line = ("Campaign · not started: its first window would be " + when if edge == "start"
                else "Campaign · not started: its window is open now, until " + when if edge == "end"
                else f"Campaign · not started: {ENDED}")
        return {**out, "state": "draft", "line": line}
    state = campaign.get("state")
    if state == "armed":
        run_dir, job = _latest(root, campaign)
        state = _judged(run_dir, job, schedule, now) or "armed"
    if state in ("complete", "ended", "paused"):
        out.update(state=state, next=None, edge=None)
    if state == "complete":
        return {**out, "line": "Campaign finished"}
    if state == "ended":
        until = schedule.until
        return {**out, "line": f"Campaign · ended on {until.day} {MONTH_WORDS[until.month - 1]}" if until
                else "Campaign · ended"}
    if state == "paused":
        reason = str(campaign.get("reason") or BY_YOU)
        # A pause for failures is a whole sentence (FAILED_PAUSE_LINE): shown as it is.
        line = reason if reason.startswith("The campaign is paused") else f"Campaign · paused: {reason}"
        return {**out, "line": line, "tone": "warn"}
    tail = (f"next window {when}" if edge == "start" else f"this window ends {when}" if edge == "end" else ENDED)
    # The window the tick recorded as missed, while no span has started since; else one passing
    # now with no tick at all (`study status` with no app open).
    missed = _missed_kept(schedule, campaign.get("missed"), now)
    if missed is None:
        found = _missed(schedule, campaign, now)
        missed = found[0] if found is not None else None
    if missed is not None:
        return {**out, "missed": _iso(missed), "tone": "warn",
                "line": f"Campaign · the window of {when_words(missed, now)} was missed: the app was not running · {tail}"}
    return {**out, "line": f"Campaign · {tail}"}


def campaign_done(root: Path, study: Study, *, now: datetime) -> bool:
    """Whether a campaign has its answer: complete or ended (judged as every tick judges them), or
    paused -- and no run of the study is active or queued. A window's stop is not an end. A study
    whose schedule was taken out is judged by its stored campaign (the tick pauses it then)."""
    if study.schedule is None:
        stored = read_campaign(root)
        if stored is None or stored.get("state") == "armed":
            return False
    else:
        state = campaign_state(root, study, now=now)
        if state is None or state["state"] not in ("complete", "ended", "paused"):
            return False
    phase = _latest(root)[1].get("phase")
    return phase not in jobs.ACTIVE and phase != "queued"


def window_left_s(root: Path, run_dir: Path, *, now: datetime | None = None) -> float | None:
    """Real seconds until the window of the run's session ends, when that end will stop it: the
    study's campaign is armed, the run is its latest, the session is not one by hand, and the window
    clock (`clock_of` the home, or `now`) is inside a span. None when no window bounds it."""
    root, run_dir = Path(root), Path(run_dir)
    campaign = read_campaign(root)
    if campaign is None or campaign.get("state") != "armed":
        return None
    try:
        study = load_study(root)
    except (HarnessError, OSError, ValueError):
        return None
    dirs = jobs._run_dirs(root)  # noqa: SLF001 - the runs, oldest first, as jobs lists them
    if study.schedule is None or not dirs or dirs[-1].name != run_dir.name:
        return None
    hand, session = campaign.get("by_hand"), session_of(run_dir)
    if isinstance(hand, dict) and hand.get("run") == run_dir.name and (
            hand.get("session") == session or (hand.get("session") == 0 and session == 1)):
        return None  # a person's session: no window's end stops it (D47)
    clock = clock_of(root.parent.parent)
    now = now if now is not None else clock.now()
    span = study.schedule.span_at(now)
    if span is None:
        return None
    return max(0.0, (span[1].timestamp() - now.timestamp()) / clock.speed)


def by_hand(home: Any, root: Path, launch: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """A session a person launches (a resume, a continuation, a final check again): `launch()`
    inside `tick_lock`, then `mark_by_hand` when the study's campaign is armed (D4, D47), so no tick
    sees the session before it is theirs. `launch`'s answer. A study whose campaign is not armed
    launches as it always did, without the lock."""
    if (read_campaign(root) or {}).get("state") != "armed":
        return launch()
    with tick_lock(home):
        answer = launch()
        run = answer.get("run") if isinstance(answer, dict) else None
        if run:
            mark_by_hand(home, Path(root).name, str(run))
    return answer


def scheduled_words(state: dict[str, Any] | None, now: datetime) -> str:
    """When a scheduled study starts, in the words `study start` and the page say: "next window
    Sat 00:00", or inside a window "its window is open now, until Sun 23:59"."""
    moment = _moment((state or {}).get("next"))
    if moment is None:
        return ENDED
    when = when_words(moment.astimezone(), now)
    return f"its window is open now, until {when}" if state.get("edge") == "end" else f"next window {when}"


def _finished(root: Path, campaign: dict[str, Any] | None, schedule: Schedule, now: datetime) -> str | None:
    """"finished" or "ended" when the study's campaign has its answer (stored, or judged now), so a
    start begins a new campaign; None otherwise (no campaign yet, armed, paused)."""
    if campaign is None:
        return None
    stored = campaign.get("state")
    run_dir, job = _latest(root, campaign)
    judged = _judged(run_dir, job, schedule, now)
    if stored == "ended" or judged == "ended":
        return "ended"
    if stored == "complete" or judged == "complete":
        return "finished"
    return None


def start_campaign(home: Any, root: Path, *, now: datetime, at_once: bool, start: Callable[..., Any] | None = None,
                   continue_run: Callable[..., Any] | None = None,
                   recheck: Callable[..., Any] | None = None) -> dict[str, Any]:
    """A person starts a scheduled study (D3). `at_once=False`: the campaign is armed, and its
    windows start the sessions: `{"scheduled": True, "renewed", "words", "campaign":
    campaign_state(...)}`. `at_once` (`--now`): what a tick would do inside a window -- start,
    resume, check again or continue with the campaign's search time left -- whatever the window,
    and the campaign armed with that session by hand (D4, D47), the launch and the mark inside
    `tick_lock`: the launch's answer, `"renewed"` and `"campaign"`. A campaign that finished or
    ended is begun anew (`renewed`: "finished" or "ended"): its next session is a new run, and
    the old one stays a past run. `start`, `continue_run` and `recheck` default to `jobs`' own."""
    root = Path(root)
    study = load_study(root)
    schedule = study.schedule
    if schedule is None:
        raise AppError("the study has no schedule: start it as usual", 409)
    if study.per_group:  # D37: a campaign runs one search, so it is refused before it is armed
        from evolvekit.harness.study import PER_GROUP_SCHEDULE

        raise AppError(PER_GROUP_SCHEDULE, 409)
    if schedule.past_until(now):
        raise AppError("its schedule has no window left: set a later Until in When it runs (or in study.yaml) "
                       "to run it again", 409)
    calls = {"start": start or jobs.start, "continue_run": continue_run or jobs.continue_run,
             "recheck": recheck or jobs.resume_final_check}
    with tick_lock(home):
        campaign = read_campaign(root)
        renewed = _finished(root, campaign, schedule, now)
        if not at_once:
            arm(root, now=now, renew=renewed is not None)
            state = campaign_state(root, study, now=now)
            return {"scheduled": True, "renewed": renewed, "words": scheduled_words(state, now), "campaign": state}
        if jobs.running(root):
            raise AppError("the study is already running", 409)
        run_dir, job = (None, {}) if renewed is not None else _latest(root, campaign)
        # Nothing of the latest run can go on: a new run, as a new campaign's first session.
        action = _next_action(home, root, run_dir, job, None) or (
            "start", (home, root), {}, "started a run", "could not start a run")
        name, args, kwargs, _, _ = action
        path = root / CAMPAIGN
        try:
            before: str | None = path.read_text(encoding="utf-8")
        except OSError:
            before = None
        arm(root, now=now, renew=renewed is not None or (name == "start" and not kwargs))
        try:
            answer = calls[name](*args, **kwargs)
        except BaseException:
            # Nothing was launched: the campaign is as it was.
            if before is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, before)
            raise
        run = answer.get("run") or (run_dir.name if run_dir is not None else None)
        if run:
            mark_by_hand(home, root.name, str(run))
    return {**answer, "renewed": renewed, "campaign": campaign_state(root, study, now=now)}


def stop_by_person(home: Any, root: Path, run: str | None) -> dict[str, Any]:
    """A person's stop (D4): the campaign is paused ("stopped by you"), and the run is asked to
    stop (or taken out of the queue) when it is active or queued: `{"paused": True, **jobs.stop}`,
    else `{"paused": True, "stopping": False}`. Under `tick_lock`, so no tick launches between."""
    root = Path(root)
    with tick_lock(home):
        pause(root, reason=BY_YOU)
        if run:
            state = jobs.phase(jobs._run_dir(root, run)).get("phase")  # noqa: SLF001 - the one check of a run's name
            if state in jobs.ACTIVE or state == "queued":
                return {"paused": True, **jobs.stop(root, run)}
    return {"paused": True, "stopping": False}
