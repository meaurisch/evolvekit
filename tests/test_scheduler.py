"""The scheduler tick (`evolvekit/app/scheduler.py`): campaigns start, resume,
re-check, continue and stop runs at their windows' edges, one at a time, the
oldest campaign first; failures are counted once per session; the app's
ticker and `study wait`/`watch` tick it."""

from __future__ import annotations

import json
import os
import re
import socket
import time
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from evolvekit.app import scheduler
from evolvekit import cli
from evolvekit.app import AppError, jobs
from evolvekit.app.server import AppServer
from evolvekit.harness.study import load_study, save_study
from evolvekit.stopping import STOP_REQUEST

from campaign_helpers import Recorder, campaign_study, local, write_run

LAUNCHERS = ("start", "continue_run", "recheck")
"""What `start_campaign` launches with (it never stops a run)."""


def test_starts_at_window_start(tmp_path):
    home, root = campaign_study(tmp_path)
    rec = Recorder()
    assert scheduler.tick(home, now=local("2026-11-13 23:59"), **rec.calls_for()) == []
    said = scheduler.tick(home, now=local("2026-11-14 00:00:05"), **rec.calls_for())
    assert rec.names() == ["start"] and rec.calls[0][1] == (home, root) and rec.calls[0][2] == {}
    assert said == [f"{root.name}: started a run (window Sat 00:00 – Sun 23:59)"]


def test_unarmed_study_is_left_alone(tmp_path):
    home, root = campaign_study(tmp_path)
    (root / scheduler.CAMPAIGN).unlink()
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [], "a schedule alone never starts a draft"


def test_resumes_unfinished_plan_before_continuing(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "stopped", generations=2, planned=6)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [("start", (home, root), {"resume": "r1"})]


def test_continues_finished_run_with_window_hours(tmp_path):
    home, root = campaign_study(tmp_path, windows=({"days": ["sat"], "from": "00:00", "to": "06:00"},))
    write_run(root, "r1", "done", generations=6, planned=6, max_hours=1.0, used_s=900, final={"decision": "keep"})
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 03:00"), **rec.calls_for())
    assert rec.calls == [("continue_run", (home, root, "r1"), {"hours": pytest.approx(0.75)})], "the search time left, under 3 h of window"
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 03:00"), speed=6.0, **rec.calls_for())
    assert rec.calls[0][2]["hours"] == pytest.approx(0.5), "3 h of rehearsal clock at 6 x is half an hour"


def test_campaign_never_exceeds_its_search_time(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "done", generations=6, planned=6, max_hours=1.0, used_s=3600 - 300, final={"decision": "keep"})
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 03:00"), **rec.calls_for())
    assert rec.calls == [] and scheduler.read_campaign(root)["state"] == "complete"
    scheduler.tick(home, now=local("2026-11-15 03:00"), **rec.calls_for())
    assert rec.calls == []


def test_stops_at_window_end(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "search", alive=True)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-16 00:01"), **rec.calls_for())
    assert rec.calls == [("stop", (root, "r1"), {})]
    (root / "runs" / "r1" / STOP_REQUEST).write_text("stop\n", encoding="utf-8")
    scheduler.tick(home, now=local("2026-11-16 00:02"), **rec.calls_for())
    assert len(rec.calls) == 1, "asked once"
    assert scheduler.read_campaign(root)["stopped_by_window"] == "r1"


def test_never_stops_final_check(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "check", alive=True)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-16 00:01"), **rec.calls_for())
    assert rec.calls == []


def test_a_session_started_by_hand_is_not_stopped(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "search", alive=True, launch=3)
    with scheduler.tick_lock(home):
        scheduler.mark_by_hand(home, root.name, "r1")
    assert scheduler.read_campaign(root)["by_hand"] == {"run": "r1", "session": 3}
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-16 00:01"), **rec.calls_for())
    assert rec.calls == []


def test_the_next_scheduled_session_of_that_run_is_stopped(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "stopped", generations=2, planned=6, launch=3)
    campaign = scheduler.read_campaign(root)
    (root / scheduler.CAMPAIGN).write_text(json.dumps({**campaign, "by_hand": {"run": "r1", "session": 3}}), encoding="utf-8")
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [("start", (home, root), {"resume": "r1"})]
    assert scheduler.read_campaign(root)["by_hand"] is None, "the session by hand has ended: cleared"
    write_run(root, "r1", "search", alive=True, launch=4)
    scheduler.tick(home, now=local("2026-11-16 00:01"), **rec.calls_for())
    assert rec.calls[-1] == ("stop", (root, "r1"), {}), "a later session of the same run keeps the windows"


def test_wakes_inside_window_starts_late(tmp_path):
    home, root = campaign_study(tmp_path)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-13 20:00"), **rec.calls_for())     # Friday: the last tick before the sleep
    scheduler.tick(home, now=local("2026-11-14 09:00"), **rec.calls_for())     # wakes on Saturday morning
    assert rec.names() == ["start"]
    write_run(root, "r1", "search", alive=True)
    scheduler.tick(home, now=local("2026-11-16 00:10"), **rec.calls_for())
    assert rec.names() == ["start", "stop"], "the window still ends at Sunday 23:59: no time carried over"


def test_overlapping_campaigns_take_turns(tmp_path):
    home, a = campaign_study(tmp_path, "A", windows=({"days": ["sat"], "from": "01:00", "to": "03:00"},),
                             armed_at="2026-11-13T09:00:00+01:00")
    _, b = campaign_study(tmp_path, "B", windows=({"days": ["sat"], "from": "01:00", "to": "06:00"},),
                          armed_at="2026-11-13T10:00:00+01:00")
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 01:00"), **rec.calls_for())
    assert rec.calls == [("start", (home, a), {})], "the oldest schedule first, one at a time"
    write_run(a, "a1", "search", alive=True)
    scheduler.tick(home, now=local("2026-11-14 02:00"), **rec.calls_for())
    assert len(rec.calls) == 1, "B waits while A has the computer"
    scheduler.tick(home, now=local("2026-11-14 03:00:05"), **rec.calls_for())
    assert rec.calls[-1] == ("stop", (a, "a1"), {})
    write_run(a, "a1", "stopped", generations=2, planned=6)
    scheduler.tick(home, now=local("2026-11-14 03:01"), **rec.calls_for())
    assert rec.calls[-1] == ("start", (home, b), {}), "B gets the rest of its window"


def test_waits_behind_another_studys_run(tmp_path):
    home, root = campaign_study(tmp_path)
    _, other = campaign_study(tmp_path, "Other")
    (other / scheduler.CAMPAIGN).unlink()
    write_run(other, "o1", "search", alive=True)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == []


def test_past_until_does_nothing(tmp_path):
    home, root = campaign_study(tmp_path)
    study = load_study(root)
    study.schedule = replace(study.schedule, until=date(2026, 11, 13))
    save_study(study, root)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [] and scheduler.read_campaign(root)["state"] == "ended"


def test_rechecks_a_final_check_that_did_not_finish(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "failed", generations=6, planned=6, check=True,
              error="the run's process ended without finishing: the computer restarted")
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [("recheck", (home, root, "r1"), {})]


def test_failing_campaign_pauses_after_three_failures(tmp_path):
    home, root = campaign_study(tmp_path)
    rec = Recorder(start_raises=AppError("the study is not ready yet: cases.test: hold back at least one case", 409))
    for hour in ("10:00", "10:01", "10:02", "10:03"):
        scheduler.tick(home, now=local(f"2026-11-14 {hour}"), **rec.calls_for())
    assert rec.names() == ["start"] * 3
    campaign = scheduler.read_campaign(root)
    assert campaign["state"] == "paused" and campaign["reason"] == scheduler.FAILED_PAUSE_LINE.format(
        error="the study is not ready yet: cases.test: hold back at least one case")


def test_a_failed_session_resumes_the_same_run(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "failed", generations=2, planned=6, error="ValueError: the solver crashed", launch=1)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [("start", (home, root), {"resume": "r1"})]


def test_a_run_that_failed_before_its_first_round_is_started_anew(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "failed", generations=0, planned=6, archived=False, error="ValueError: no seed", launch=1)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [("start", (home, root), {})]
    assert [r["id"] for r in jobs.runs(root)] == ["r1"], "the failed run stays listed (the new one appears when it launches)"


def test_a_failure_is_counted_once(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "failed", generations=2, planned=6, error="ValueError: the solver crashed", launch=1)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-16 00:10"), **rec.calls_for())    # outside: nothing launched
    scheduler.tick(home, now=local("2026-11-16 00:11"), **rec.calls_for())
    assert scheduler.read_campaign(root)["failed_sessions"] == ["r1:1"]


def test_a_clean_session_resets_the_failures(tmp_path):
    home, root = campaign_study(tmp_path)
    campaign = scheduler.read_campaign(root)
    (root / scheduler.CAMPAIGN).write_text(json.dumps({**campaign, "failed_sessions": ["r1:1", "r1:2"]}), encoding="utf-8")
    write_run(root, "r1", "stopped", generations=3, planned=6, launch=3)
    scheduler.tick(home, now=local("2026-11-16 00:10"), **Recorder().calls_for())
    assert scheduler.read_campaign(root)["failed_sessions"] == []


def test_complete_is_judged_outside_a_window(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "done", generations=6, planned=6, max_hours=1.0, used_s=3600 - 60, final={"decision": "use"})
    scheduler.tick(home, now=local("2026-11-16 08:00"), **Recorder().calls_for())     # Monday: the check ended after Sunday
    assert scheduler.read_campaign(root)["state"] == "complete"


def test_tick_once_logs_each_line_with_both_times(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(jobs, "launch_queued", lambda home: None)
    monkeypatch.setattr(scheduler, "tick", lambda home, **kw: ["tours: started a run (window Sat 00:00 – Sun 23:59)"])
    server = AppServer(tmp_path / "my home", port=0)
    try:
        scheduler.write_clock(server.home.root, local("2026-11-14 00:00"), 120.0)
        server.tick_once()
    finally:
        server.stop()
    line = capsys.readouterr().out.strip()
    assert re.fullmatch(r"2026-11-14T00:00:\d\d[+-]\d\d:\d\d rehearsal · \d{4}-\d\d-\d\dT[\d:]{8}[+-]\d\d:\d\d real · "
                        r"tours: started a run \(window Sat 00:00 – Sun 23:59\)", line), line


def test_actions_per_window_are_capped(tmp_path):
    home, root = campaign_study(tmp_path)
    rec = Recorder()   # start "succeeds" but no run appears: as if each run died at once
    for minute in range(6):
        scheduler.tick(home, now=local(f"2026-11-14 10:0{minute}"), **rec.calls_for())
    assert rec.names() == ["start"] * scheduler.ACTIONS_PER_WINDOW


def test_one_tick_at_a_time_across_processes(tmp_path):
    home, root = campaign_study(tmp_path)
    (home.root / scheduler.LOCK).write_text(json.dumps({"pid": os.getppid(), "at": datetime.now(timezone.utc).isoformat()}),
                                            encoding="utf-8")
    rec = Recorder()
    assert scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for()) == [] and rec.calls == []


def test_server_ticks_the_queue_then_the_scheduler(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(jobs, "launch_queued", lambda home: order.append("queue"))

    def broken(home, **kw):
        order.append("schedule")
        raise RuntimeError("a bad study")
    monkeypatch.setattr(scheduler, "tick", broken)
    server = AppServer(tmp_path / "my home", port=0)
    try:
        server.tick_once()          # never raises
    finally:
        server.stop()
    assert order == ["queue", "schedule"]


def test_launch_json_counts_the_sessions(tmp_path, monkeypatch):
    home, root = campaign_study(tmp_path)
    monkeypatch.setattr(jobs, "_spawn", lambda *args, **kwargs: 2 ** 22 + 7)
    run_dir = write_run(root, "r1", "stopped", launch=2)
    jobs._launch(home, root, run_dir, ["--resume"], with_keys=False)
    assert scheduler.session_of(run_dir) == 3, "one more than the launch it replaces"
    queued = root / "runs" / "r2"
    queued.mkdir()
    assert jobs._mark_pending(queued, exclusive=True)
    jobs._launch(home, root, queued, [], with_keys=False, claimed=True)
    assert scheduler.session_of(queued) == 1, "a claimed queued run's first launch"
    assert scheduler.session_of(root / "runs" / "none") == 0


def test_a_stale_lock_is_taken_over(tmp_path):
    home, root = campaign_study(tmp_path)
    lock = home.root / scheduler.LOCK
    lock.write_text(json.dumps({"pid": 2 ** 22 + 7, "at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    rec = Recorder()
    assert scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for()) != [], "its process is gone"
    assert not lock.exists(), "released after the tick"
    lock.write_text(json.dumps({"pid": os.getppid(), "at": "2026-11-08T00:00:00+00:00"}), encoding="utf-8")
    with scheduler.tick_lock(home, wait_s=0):
        assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid(), "hours old: taken over, though its process lives"
    lock.write_text(json.dumps({"pid": os.getppid(), "at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    with pytest.raises(AppError):
        with scheduler.tick_lock(home, wait_s=0.2):
            pass


def test_the_rehearsal_clock_lives_with_its_app(tmp_path):
    clock = scheduler.Clock(local("2026-11-14 01:10"), 60.0, time.time())
    assert clock.simulated and clock.words() == "rehearsal clock: Sat 14 Nov 01:10, 60 × real time"
    assert scheduler.Clock().words() == "" and not scheduler.Clock().simulated
    home = tmp_path / "my home"
    home.mkdir()
    scheduler.write_clock(home, local("2026-11-14 01:10"), 60.0)
    assert not scheduler.clock_of(home).simulated, "no app runs in this home"
    doc = json.loads((home / scheduler.CLOCK_FILE).read_text(encoding="utf-8"))
    (home / scheduler.CLOCK_FILE).write_text(json.dumps({**doc, "pid": 2 ** 22 + 7}), encoding="utf-8")
    assert not scheduler.clock_of(home).simulated and not (home / scheduler.CLOCK_FILE).exists(), "its app is gone"
    server = AppServer(home, port=0)
    try:
        scheduler.write_clock(home, local("2026-11-14 01:10"), 60.0)
        clock = scheduler.clock_of(home)
        assert clock.simulated and clock.speed == 60.0
        assert abs((clock.now() - local("2026-11-14 01:10")).total_seconds()) < 60
    finally:
        server.stop()


def test_a_person_queued_behind_another_study_keeps_the_session(tmp_path):
    home, root = campaign_study(tmp_path)
    run_dir = root / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / jobs.QUEUED).write_text(json.dumps({"queued_at": "2026-11-16T00:00:00+01:00"}), encoding="utf-8")
    scheduler.mark_by_hand(home, root.name, "r1")
    assert scheduler.read_campaign(root)["by_hand"] == {"run": "r1", "session": 0}
    scheduler.tick(home, now=local("2026-11-16 00:01"), **Recorder().calls_for())
    assert scheduler.read_campaign(root)["by_hand"] == {"run": "r1", "session": 0}, "still waiting its turn"
    (run_dir / jobs.QUEUED).unlink()
    write_run(root, "r1", "search", alive=True, launch=1)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-16 00:02"), **rec.calls_for())
    assert rec.calls == [] and scheduler.read_campaign(root)["by_hand"] == {"run": "r1", "session": 1}


def test_a_stopped_final_check_is_checked_again_not_searched_on(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "stopped", generations=4, planned=6, final={"stopped": True})
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [("recheck", (home, root, "r1"), {})], "its search ended when the check began"


def test_no_continuation_in_the_last_minutes_of_a_window(tmp_path):
    home, root = campaign_study(tmp_path, windows=({"days": ["sat"], "from": "00:00", "to": "06:00"},))
    write_run(root, "r1", "done", generations=6, planned=6, max_hours=1.0, used_s=900, final={"decision": "keep"})
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 05:55"), **rec.calls_for())
    assert rec.calls == [] and scheduler.read_campaign(root)["state"] == "armed", "five minutes: the next window"


def test_wait_and_watch_tick_the_scheduler(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(scheduler, "tick", lambda home, **kw: seen.append(kw["now"]) or [])
    home, _ = campaign_study(tmp_path)
    cli._tick_queue(home)
    assert len(seen) == 1 and seen[0].tzinfo is not None


# -- fix round 1


def _stamp(root):
    return scheduler.read_campaign(root)["ticked_at"]


def test_ticked_at_is_on_the_window_clock(tmp_path):
    home, root = campaign_study(tmp_path)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-13 20:00"), speed=120.0, **rec.calls_for())
    assert _stamp(root) == local("2026-11-13 20:00").isoformat(timespec="seconds"), "the clock the spans are read on"
    scheduler.tick(home, now=local("2026-11-13 20:00:30"), speed=120.0, **rec.calls_for())
    assert _stamp(root) == local("2026-11-13 20:00").isoformat(timespec="seconds"), "at most every minute of that clock"
    scheduler.tick(home, now=local("2026-11-13 20:01"), speed=120.0, **rec.calls_for())
    assert _stamp(root) == local("2026-11-13 20:01").isoformat(timespec="seconds")
    scheduler.tick(home, now=local("2026-11-10 08:00"), **rec.calls_for())
    assert _stamp(root) == local("2026-11-10 08:00").isoformat(timespec="seconds"), "a stamp in the future: a rehearsal ended"


def test_every_armed_campaign_is_stamped_after_a_launch(tmp_path):
    home, a = campaign_study(tmp_path, "A", armed_at="2026-11-13T09:00:00+01:00")
    _, b = campaign_study(tmp_path, "B", armed_at="2026-11-13T10:00:00+01:00")
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [("start", (home, a), {})]
    assert _stamp(b) == local("2026-11-14 10:00").isoformat(timespec="seconds"), "B waited, and was looked at"


def test_a_continuation_stopped_by_its_window_goes_on_while_there_is_search_time(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "stopped", generations=8, planned=6, used_s=1200)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [("continue_run", (home, root, "r1"), {"hours": pytest.approx(2400 / 3600)})], \
        "40 minutes of search left: the same run goes on, no check in between"


def test_a_continuation_stopped_by_its_window_is_checked_again(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "stopped", generations=8, planned=6, used_s=3480)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [("recheck", (home, root, "r1"), {})], "two minutes of search left, and no verdict yet"
    write_run(root, "r1", "done", generations=8, planned=6, used_s=3480, final={"decision": "keep"}, launch=2)
    scheduler.tick(home, now=local("2026-11-14 11:00"), **rec.calls_for())
    assert len(rec.calls) == 1 and scheduler.read_campaign(root)["state"] == "complete", "not left armed for ever"


def test_a_continuation_whose_process_died_goes_on_or_is_checked_again(tmp_path):
    home, root = campaign_study(tmp_path)
    run_dir = write_run(root, "r1", "failed", generations=6, planned=6, used_s=1200,
                        error="the run's process ended without finishing: the computer restarted")
    assert jobs.can_continue(run_dir, jobs.phase(run_dir)), "its plan's search is on record"
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [("continue_run", (home, root, "r1"), {"hours": pytest.approx(2400 / 3600)})]
    assert scheduler.read_campaign(root)["failed_sessions"] == ["r1:1"]
    write_run(root, "r1", "failed", generations=6, planned=6, used_s=3480, error="the computer restarted", launch=2)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-15 10:00"), **rec.calls_for())
    assert rec.calls == [("recheck", (home, root, "r1"), {})], "two minutes left: checked again"


def test_a_failed_run_goes_on_only_with_its_plan_searched(tmp_path):
    _, root = campaign_study(tmp_path)
    run_dir = write_run(root, "r1", "failed", generations=6, planned=6)
    job = jobs.phase(run_dir)
    assert jobs.can_continue(run_dir, job)
    assert not jobs.can_continue(run_dir, {**job, "search": {**job["search"], "generations": 4}}), "rounds left: resumed"
    assert not jobs.can_continue(run_dir, {**job, "check": {"seeds": 1}}), "its final check died: checked again"
    assert not jobs.can_continue(run_dir, {**job, "search": {**job["search"], "aborted": True}})


def test_a_launch_lost_to_another_run_is_not_a_failure(tmp_path):
    home, root = campaign_study(tmp_path)
    _, other = campaign_study(tmp_path, "Other")
    (other / scheduler.CAMPAIGN).unlink()
    calls = []

    def start(*args, **kwargs):  # a person started Other between the tick's look and this launch
        calls.append(args)
        write_run(other, "o1", "search", alive=True)
        raise AppError("'Other' is running: continue this one when it has finished", 409)
    said = scheduler.tick(home, now=local("2026-11-14 10:00"), start=start)
    campaign = scheduler.read_campaign(root)
    assert len(calls) == 1 and said == [] and campaign["failed_sessions"] == [] and campaign["actions"] == 0


def test_a_live_lock_is_kept_ten_minutes_and_refreshed_before_a_launch(tmp_path, monkeypatch):
    home, root = campaign_study(tmp_path)
    lock = home.root / scheduler.LOCK
    five_minutes_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    lock.write_text(json.dumps({"pid": os.getppid(), "at": five_minutes_ago}), encoding="utf-8")
    rec = Recorder()
    assert scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for()) == [] and rec.calls == [], \
        "its process lives: a slow launcher, not a crash"
    lock.write_text(json.dumps({"pid": os.getppid(), "at": "2026-11-08T00:00:00+00:00"}), encoding="utf-8")
    taken = []
    real_try = scheduler._try_lock
    monkeypatch.setattr(scheduler, "_try_lock", lambda path: taken.append(real_try(path)) or taken[-1])
    seen = []

    def start(*args, **kwargs):
        seen.append(lock.read_text(encoding="utf-8"))
        return {"run": "x"}
    scheduler.tick(home, now=local("2026-11-14 10:00"), start=start)
    assert taken[0] is not None and seen and seen[0] != taken[0], "refreshed before the launch"
    assert json.loads(seen[0])["pid"] == os.getpid() and not lock.exists(), "and released after the tick"


def test_the_queue_is_launched_under_the_scheduler_lock(tmp_path, monkeypatch):
    home, root = campaign_study(tmp_path)
    launched = []
    monkeypatch.setattr(jobs, "launch_queued", lambda home: launched.append(home))
    monkeypatch.setattr(scheduler, "tick", lambda home, **kw: [])
    lock = home.root / scheduler.LOCK
    lock.write_text(json.dumps({"pid": os.getppid(), "at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    cli._tick_queue(home)
    assert launched == [], "another process is ticking"
    lock.unlink()
    cli._tick_queue(home)
    assert launched == [home] and not lock.exists()


def test_a_final_check_that_is_starting_is_not_stopped(tmp_path):
    home, root = campaign_study(tmp_path)
    run_dir = write_run(root, "r1", "stopped", generations=6, planned=6, final={"stopped": True})
    for flag, expected in (("--check", []), ("--continue", [("stop", (root, "r1"), {})])):
        (run_dir / jobs.LAUNCH).write_text(json.dumps({
            "pid": os.getpid(), "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": socket.gethostname(), "argv": ["-m", "evolvekit.app.job", str(root), "r1", flag], "launch": 2}),
            encoding="utf-8")
        assert jobs.phase(run_dir)["phase"] == "starting"
        rec = Recorder()
        scheduler.tick(home, now=local("2026-11-16 00:01"), **rec.calls_for())
        assert rec.calls == expected, flag


# ---------------------------------------------------------------------------
# Task 3: the campaign's state, start now, a person's stop
# ---------------------------------------------------------------------------


def test_campaign_lines(tmp_path):
    home, root = campaign_study(tmp_path)
    study = load_study(root)
    assert scheduler.campaign_state(root, study, now=local("2026-11-13 18:00"))["line"] == "Campaign · next window Sat 00:00"
    assert scheduler.campaign_state(root, study, now=local("2026-11-14 10:00"))["line"] == "Campaign · this window ends Sun 23:59"
    scheduler.pause(root, reason="stopped by you")
    assert scheduler.campaign_state(root, study, now=local("2026-11-14 10:00"))["line"] == "Campaign · paused: stopped by you"
    (root / scheduler.CAMPAIGN).unlink()
    assert scheduler.campaign_state(root, study, now=local("2026-11-13 18:00"))["line"] == \
        "Campaign · not started: its first window would be Sat 00:00"


def test_campaign_state_says_next_edge_and_none_without_a_schedule(tmp_path):
    home, root = campaign_study(tmp_path)
    state = scheduler.campaign_state(root, load_study(root), now=local("2026-11-13 18:00"))
    assert state["state"] == "armed" and state["edge"] == "start" and state["tone"] is None and state["missed"] is None
    assert state["next"] == local("2026-11-14 00:00").isoformat()
    inside = scheduler.campaign_state(root, load_study(root), now=local("2026-11-14 10:00"))
    assert inside["edge"] == "end" and inside["next"] == local("2026-11-15 23:59").isoformat()
    study = load_study(root)
    study.schedule = None
    save_study(study, root)
    assert scheduler.campaign_state(root, load_study(root), now=local("2026-11-14 10:00")) is None


def test_missed_window_is_said(tmp_path):
    home, root = campaign_study(tmp_path, windows=({"days": ["sat"], "from": "00:00", "to": "06:00"},
                                                   {"days": ["mon"], "from": "01:00", "to": "06:00"}))
    scheduler.tick(home, now=local("2026-11-13 20:00"), **Recorder().calls_for())   # the last tick: Friday
    state = scheduler.campaign_state(root, load_study(root), now=local("2026-11-15 12:00"))
    assert state["line"] == "Campaign · the window of Sat 00:00 was missed: the app was not running · next window Mon 01:00"
    assert state["tone"] == "warn" and state["missed"] == local("2026-11-14 00:00").isoformat()
    write_run(root, "r1", "search", alive=True)
    scheduler.tick(home, now=local("2026-11-14 03:00"), **Recorder().calls_for())   # a tick inside the window
    assert scheduler.campaign_state(root, load_study(root), now=local("2026-11-15 12:00"))["missed"] is None


def test_campaign_finished_line(tmp_path):
    home, root = campaign_study(tmp_path)
    study = load_study(root)
    scheduler.pause(root, reason=scheduler.COMPLETE, state="complete")
    state = scheduler.campaign_state(root, study, now=local("2026-11-14 10:00"))
    assert state["line"] == "Campaign finished" and state["state"] == "complete" and state["tone"] is None
    failed = scheduler.FAILED_PAUSE_LINE.format(error="the run could not be started: no Python")
    scheduler.pause(root, reason=failed)
    state = scheduler.campaign_state(root, study, now=local("2026-11-14 10:00"))
    assert state["line"] == failed and state["state"] == "paused" and state["tone"] == "warn"


def test_an_ended_campaign_says_its_last_day(tmp_path):
    home, root = campaign_study(tmp_path)
    study = load_study(root)
    study.schedule = replace(study.schedule, until=date(2026, 12, 31))
    save_study(study, root)
    scheduler.pause(root, reason=scheduler.ENDED, state="ended")
    assert scheduler.campaign_state(root, load_study(root), now=local("2027-01-04 10:00"))["line"] == "Campaign · ended on 31 Dec"


def test_campaign_done(tmp_path):
    home, root = campaign_study(tmp_path)
    study = load_study(root)
    now = local("2026-11-16 08:00")
    assert not scheduler.campaign_done(root, study, now=now), "armed, nothing run yet"
    write_run(root, "r1", "done", generations=6, planned=6, used_s=3600 - 60, final={"decision": "use"})
    assert scheduler.campaign_done(root, study, now=now), "the search time is used and the run is done: complete"
    write_run(root, "r1", "search", alive=True)
    scheduler.pause(root, reason="stopped by you")
    assert not scheduler.campaign_done(root, study, now=now), "paused, but its run is still active"
    write_run(root, "r1", "stopped", generations=2, planned=6)
    assert scheduler.campaign_done(root, study, now=now)


def test_a_stopped_run_with_a_verdict_and_no_time_left_completes_the_campaign(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "stopped", generations=6, planned=6, used_s=3600 - 120, final={"decision": "keep"})
    assert scheduler.campaign_done(root, load_study(root), now=local("2026-11-14 10:00"))
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [] and scheduler.read_campaign(root)["state"] == "complete", "never left armed for ever"


def test_start_now_overrides(tmp_path):
    home, root = campaign_study(tmp_path)
    (root / scheduler.CAMPAIGN).unlink()
    rec = Recorder()
    answer = scheduler.start_campaign(home, root, now=local("2026-11-13 18:00"), at_once=True, **rec.calls_for(only=("start",)))
    assert rec.calls == [("start", (home, root), {})] and scheduler.read_campaign(root)["by_hand"]["run"] == answer["run"]
    assert scheduler.read_campaign(root)["state"] == "armed" and answer["campaign"]["state"] == "armed"


def test_start_now_does_what_the_window_would(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "stopped", generations=2, planned=6)
    rec = Recorder()
    scheduler.start_campaign(home, root, now=local("2026-11-13 18:00"), at_once=True, **rec.calls_for(LAUNCHERS))
    assert rec.calls == [("start", (home, root), {"resume": "r1"})]
    write_run(root, "r1", "done", generations=6, planned=6, used_s=1800, final={"decision": "keep"}, launch=2)
    rec = Recorder()
    scheduler.start_campaign(home, root, now=local("2026-11-13 18:00"), at_once=True, **rec.calls_for(LAUNCHERS))
    assert rec.calls == [("continue_run", (home, root, "r1"), {"hours": pytest.approx(0.5)})], \
        "the campaign's search time left, whatever the window"
    assert scheduler.read_campaign(root)["by_hand"] == {"run": "r1", "session": 2}
    write_run(root, "r1", "done", generations=6, planned=6, used_s=3600 - 60, final={"decision": "keep"}, launch=3)
    rec = Recorder()
    answer = scheduler.start_campaign(home, root, now=local("2026-11-13 18:00"), at_once=True, **rec.calls_for(LAUNCHERS))
    assert rec.calls == [("start", (home, root), {})] and answer["renewed"] == "finished", \
        "a finished campaign begins anew: a new run at once, never a refusal"


# ---------------------------------------------------------------------------
# fix round 1: a removed schedule, a new campaign, a missed window kept, the lock
# ---------------------------------------------------------------------------


def test_a_removed_schedule_ends_the_wait(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "stopped", generations=2, planned=6)
    study = load_study(root)
    study.schedule = None
    save_study(study, root)
    assert not scheduler.campaign_done(root, load_study(root), now=local("2026-11-14 10:00")), "armed until a tick sees it"
    scheduler.tick(home, now=local("2026-11-14 10:00"), **Recorder().calls_for())
    assert scheduler.read_campaign(root)["state"] == "paused"
    assert scheduler.campaign_done(root, load_study(root), now=local("2026-11-14 10:00"))


def test_a_finished_campaign_begins_anew(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "done", generations=6, planned=6, used_s=3600 - 60, final={"decision": "use"})
    scheduler.tick(home, now=local("2026-11-13 18:00"), **Recorder().calls_for())
    assert scheduler.read_campaign(root)["state"] == "complete"
    answer = scheduler.start_campaign(home, root, now=local("2026-11-13 18:00"), at_once=False)
    assert answer["scheduled"] and answer["renewed"] == "finished" and answer["words"] == "next window Sat 00:00"
    assert answer["campaign"]["state"] == "armed" and answer["campaign"]["line"] == "Campaign · next window Sat 00:00"
    assert scheduler.read_campaign(root)["after"] == "r1" and scheduler.campaign_run(root) is None
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 00:00:05"), **rec.calls_for())
    assert rec.calls == [("start", (home, root), {})], "the next window starts a new run; r1 stays a past run"
    assert scheduler.read_campaign(root)["state"] == "armed"


def test_a_start_inside_the_window_says_so(tmp_path):
    home, root = campaign_study(tmp_path)
    answer = scheduler.start_campaign(home, root, now=local("2026-11-14 10:00"), at_once=False)
    assert answer["words"] == "its window is open now, until Sun 23:59" and answer["renewed"] is None


def test_a_schedule_with_no_window_left_is_refused_in_words_that_work(tmp_path):
    home, root = campaign_study(tmp_path)
    study = load_study(root)
    study.schedule = replace(study.schedule, until=date(2026, 11, 1))
    save_study(study, root)
    for at_once in (False, True):
        with pytest.raises(AppError, match="set a later Until"):
            scheduler.start_campaign(home, root, now=local("2026-11-13 18:00"), at_once=at_once,
                                     **Recorder().calls_for(LAUNCHERS))


def test_the_apps_first_tick_after_a_missed_window_keeps_the_line(tmp_path):
    home, root = campaign_study(tmp_path, windows=({"days": ["sat"], "from": "00:00", "to": "06:00"},
                                                   {"days": ["mon"], "from": "01:00", "to": "06:00"}))
    scheduler.tick(home, now=local("2026-11-13 20:00"), **Recorder().calls_for())   # Friday: the last tick
    scheduler.tick(home, now=local("2026-11-15 12:00"), **Recorder().calls_for())   # Sunday: the app is back
    assert scheduler.read_campaign(root)["missed"]["start"] == local("2026-11-14 00:00").isoformat()
    scheduler.tick(home, now=local("2026-11-15 12:01:05"), **Recorder().calls_for())
    line = "Campaign · the window of Sat 00:00 was missed: the app was not running · next window Mon 01:00"
    assert scheduler.campaign_state(root, load_study(root), now=local("2026-11-15 12:02"))["line"] == line
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-16 01:00:05"), **rec.calls_for())
    assert rec.names() == ["start"] and scheduler.read_campaign(root)["missed"] is None, "a session launched"
    assert scheduler.campaign_state(root, load_study(root), now=local("2026-11-16 02:00"))["line"] == \
        "Campaign · this window ends Mon 06:00"


def test_a_missed_window_is_old_news_once_the_next_one_starts(tmp_path):
    home, root = campaign_study(tmp_path, windows=({"days": ["sat"], "from": "00:00", "to": "06:00"},
                                                   {"days": ["mon"], "from": "01:00", "to": "06:00"}))
    campaign = scheduler.read_campaign(root)
    campaign.update(ticked_at=local("2026-11-16 08:00").isoformat(),
                    missed={"start": local("2026-11-14 00:00").isoformat(), "end": local("2026-11-14 06:00").isoformat()})
    (root / scheduler.CAMPAIGN).write_text(json.dumps(campaign), encoding="utf-8")
    assert scheduler.campaign_state(root, load_study(root), now=local("2026-11-16 08:00"))["missed"] is None, \
        "Monday's window started since: Saturday's is not said any more"


def test_by_hand_takes_the_lock_only_on_an_armed_campaign(tmp_path):
    home, root = campaign_study(tmp_path)
    (root / scheduler.CAMPAIGN).unlink()
    lock = home.root / scheduler.LOCK
    lock.write_text(json.dumps({"pid": os.getpid(), "at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    assert scheduler.by_hand(home, root, lambda: {"run": "r1"}) == {"run": "r1"}, "no campaign: no wait on the lock"
    assert not (root / scheduler.CAMPAIGN).exists()
    lock.unlink()


def test_the_api_starts_and_stops_a_scheduled_study(tmp_path, monkeypatch):
    from test_app_server import call

    home, root = campaign_study(tmp_path)
    slug = root.name
    (root / scheduler.CAMPAIGN).unlink()
    solver = Path(__file__).resolve().parents[1] / "examples" / "cli-solver" / "solver.py"
    home.save_step(slug, {"application": {"path": str(solver), "version": ""}})  # ready to start
    monkeypatch.setattr(scheduler, "tick", lambda *a, **k: [])
    monkeypatch.setattr(jobs, "_spawn", lambda env, argv, root, run_dir: 2 ** 22 + 7)
    server = AppServer(home.root, port=0)
    server.start()
    try:
        status, answer = call(server, "POST", f"/api/studies/{slug}/start", {})
        assert status == 200 and answer["scheduled"] is True and "run" not in answer, answer
        assert scheduler.read_campaign(root)["state"] == "armed" and not (root / "runs").exists()
        status, answer = call(server, "POST", f"/api/studies/{slug}/start", {"now": "yes"})
        assert status == 400 and "now" in answer["error"]
        status, answer = call(server, "POST", f"/api/studies/{slug}/stop?run=nothing-here", {})
        assert status == 404 and scheduler.read_campaign(root)["state"] == "armed", "a bad name pauses nothing"
        status, answer = call(server, "POST", f"/api/studies/{slug}/stop", {})
        assert status == 200 and answer == {"paused": True, "stopping": False}
        assert scheduler.read_campaign(root)["state"] == "paused"
        status, answer = call(server, "POST", f"/api/studies/{slug}/start", {"now": True})
        assert status == 200 and answer["run"] and answer["campaign"]["state"] == "armed", answer
        assert scheduler.read_campaign(root)["by_hand"] == {"run": answer["run"], "session": 1}
    finally:
        server.stop()


def test_start_scheduled_only_arms(tmp_path):
    home, root = campaign_study(tmp_path)
    (root / scheduler.CAMPAIGN).unlink()
    rec = Recorder()
    answer = scheduler.start_campaign(home, root, now=local("2026-11-13 18:00"), at_once=False, **rec.calls_for(LAUNCHERS))
    assert rec.calls == [] and answer["scheduled"] is True
    assert answer["campaign"]["line"] == "Campaign · next window Sat 00:00"
    assert scheduler.read_campaign(root)["state"] == "armed"


def test_arming_over_a_live_run_never_stops_it(tmp_path):
    """A run the person started before the campaign was armed is theirs (D4): the next tick outside
    a window leaves it going."""
    home, root = campaign_study(tmp_path)
    (root / scheduler.CAMPAIGN).unlink()
    write_run(root, "r1", "search", alive=True)
    rec = Recorder()
    scheduler.start_campaign(home, root, now=local("2026-11-16 12:00"), at_once=False, **rec.calls_for(LAUNCHERS))
    assert scheduler.read_campaign(root)["by_hand"] == {"run": "r1", "session": 1}
    said = scheduler.tick(home, now=local("2026-11-16 12:00:05"), **rec.calls_for())   # Monday noon: outside
    assert rec.calls == [] and said == []
    assert not (root / "runs" / "r1" / STOP_REQUEST).exists()
    assert scheduler.window_left_s(root, root / "runs" / "r1", now=local("2026-11-14 10:00")) is None


def test_a_failed_run_that_cannot_go_on_pauses_the_campaign(tmp_path):
    """Its search aborted on the plan's last round: nothing of it can go on, so the campaign pauses
    and says why, and a start begins a new run."""
    home, root = campaign_study(tmp_path)
    run_dir = write_run(root, "r1", "failed", generations=6, planned=6, error="provider failing")
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job["search"]["aborted"] = True
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    rec = Recorder()
    said = scheduler.tick(home, now=local("2026-11-14 00:00:05"), **rec.calls_for())
    line = "The campaign is paused: its last run cannot go on (provider failing). Start it again to begin a new run."
    assert rec.calls == [] and said == [f"{root.name}: {line}"]
    campaign = scheduler.read_campaign(root)
    assert campaign["state"] == "paused" and campaign["reason"] == line
    study = load_study(root)
    assert scheduler.campaign_state(root, study, now=local("2026-11-14 00:05"))["line"] == line
    assert scheduler.campaign_done(root, study, now=local("2026-11-14 00:05"))
    scheduler.start_campaign(home, root, now=local("2026-11-14 00:06"), at_once=False, **rec.calls_for(LAUNCHERS))
    scheduler.tick(home, now=local("2026-11-14 00:07"), **rec.calls_for())
    assert rec.calls == [("start", (home, root), {})], "started again: a new run"


def test_a_first_campaign_over_a_run_that_cannot_go_on_starts_a_new_run(tmp_path):
    home, root = campaign_study(tmp_path)
    (root / scheduler.CAMPAIGN).unlink()
    run_dir = write_run(root, "r1", "failed", generations=6, planned=6, error="provider failing")
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job["search"]["aborted"] = True
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    rec = Recorder()
    scheduler.start_campaign(home, root, now=local("2026-11-13 18:00"), at_once=False, **rec.calls_for(LAUNCHERS))
    scheduler.tick(home, now=local("2026-11-14 00:00:05"), **rec.calls_for())
    assert rec.calls == [("start", (home, root), {})] and scheduler.read_campaign(root)["state"] == "armed"


def test_a_stopped_run_whose_search_aborted_pauses_the_campaign(tmp_path):
    home, root = campaign_study(tmp_path)
    run_dir = write_run(root, "r1", "stopped", generations=6, planned=6)
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job["search"].update(aborted=True, stop_reason="the model backend kept failing")
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    scheduler.tick(home, now=local("2026-11-14 10:00"), **Recorder().calls_for())
    assert scheduler.read_campaign(root)["reason"] == scheduler.STUCK_PAUSE_LINE.format(
        error="the model backend kept failing")


def test_a_window_too_short_to_go_on_does_not_pause(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "stopped", generations=6, planned=6, max_hours=1.0, used_s=900, final={"decision": "keep"})
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-15 23:55"), **rec.calls_for())     # five minutes of the window left
    assert rec.calls == [] and scheduler.read_campaign(root)["state"] == "armed"


def test_a_persons_stop_pauses_the_campaign(tmp_path):
    home, root = campaign_study(tmp_path)
    assert scheduler.stop_by_person(home, root, None) == {"paused": True, "stopping": False}
    assert scheduler.read_campaign(root)["state"] == "paused" and scheduler.read_campaign(root)["reason"] == "stopped by you"
    write_run(root, "r1", "search", alive=True)
    scheduler.arm(root, now=local("2026-11-14 10:00"))
    answer = scheduler.stop_by_person(home, root, "r1")
    assert answer == {"paused": True, "stopping": True, "run": "r1"} and (root / "runs" / "r1" / STOP_REQUEST).exists()
    assert scheduler.read_campaign(root)["state"] == "paused"


def test_a_persons_resume_continue_and_check_again_are_by_hand(tmp_path, monkeypatch):
    from test_app_server import call

    home, root = campaign_study(tmp_path)
    slug = root.name
    monkeypatch.setattr(scheduler, "tick", lambda *a, **k: [])  # no window launches anything here
    monkeypatch.setattr(jobs, "_spawn", lambda env, argv, root, run_dir: 2 ** 22 + 7)  # launched: a pid gone at once
    server = AppServer(home.root, port=0)
    server.start()
    try:
        write_run(root, "r1", "stopped", generations=2, planned=6, launch=1)
        status, answer = call(server, "POST", f"/api/studies/{slug}/start", {"resume": "r1"})
        assert status == 200, answer
        assert scheduler.read_campaign(root)["by_hand"] == {"run": "r1", "session": 2}
        write_run(root, "r1", "done", generations=6, planned=6, used_s=600, final={"decision": "keep"}, launch=2)
        # A continue at once (`now`): without it, a scheduled study's more time waits for its window (H5).
        status, answer = call(server, "POST", f"/api/studies/{slug}/continue", {"run": "r1", "hours": 1, "now": True})
        assert status == 200, answer
        assert scheduler.read_campaign(root)["by_hand"] == {"run": "r1", "session": 3}
        write_run(root, "r1", "stopped", generations=6, planned=6, used_s=600, final={"stopped": True}, launch=3)
        status, answer = call(server, "POST", f"/api/studies/{slug}/final-check?run=r1", {})
        assert status == 200, answer
        assert scheduler.read_campaign(root)["by_hand"] == {"run": "r1", "session": 4}
        # On a study whose campaign is not armed, campaign.json is not touched.
        scheduler.pause(root, reason="stopped by you")
        before = (root / scheduler.CAMPAIGN).read_text(encoding="utf-8")
        write_run(root, "r1", "stopped", generations=2, planned=6, launch=4)
        status, answer = call(server, "POST", f"/api/studies/{slug}/start", {"resume": "r1"})
        assert status == 200, answer
        assert (root / scheduler.CAMPAIGN).read_text(encoding="utf-8") == before
    finally:
        server.stop()


# -- keep-awake: the span file a scheduled session keeps the computer awake for (D46)


def test_tick_writes_the_span_file(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "stopped", generations=2, planned=6)
    real = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
    scheduler.tick(home, now=local("2026-11-15 23:29"), speed=60.0, real_now=real, **Recorder().calls_for())
    span = json.loads((root / "runs" / "r1" / scheduler.SPAN).read_text(encoding="utf-8"))
    assert datetime.fromisoformat(span["until_real"]) == real + timedelta(minutes=30) / 60, "30 rehearsal minutes at 60 x"
    assert span["session"] == 2, "the resume is the run's second launch"


def test_the_span_file_of_a_new_run_follows_its_start(tmp_path):
    home, root = campaign_study(tmp_path)
    real = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)

    def start(home_, root_, **kwargs):
        (root_ / "runs" / "r9").mkdir(parents=True)
        return {"run": "r9"}

    scheduler.tick(home, now=local("2026-11-15 22:59"), real_now=real, start=start)
    span = json.loads((root / "runs" / "r9" / scheduler.SPAN).read_text(encoding="utf-8"))
    assert datetime.fromisoformat(span["until_real"]) == real + timedelta(hours=1)
    scheduler.tick(home, now=local("2026-11-15 22:59"), real_now=real, start=lambda *a, **k: {"run": "gone"})
    assert not (root / "runs" / "gone").exists(), "never a run folder made for the file"


def test_the_span_file_for_a_recheck_and_a_continuation(tmp_path):
    home, root = campaign_study(tmp_path)
    real = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
    write_run(root, "r1", "stopped", generations=6, planned=6, final={"stopped": True}, check=True)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 23:00"), real_now=real, **rec.calls_for())
    assert rec.names() == ["recheck"] and (root / "runs" / "r1" / scheduler.SPAN).is_file()
    (root / "runs" / "r1" / scheduler.SPAN).unlink()
    write_run(root, "r1", "done", generations=6, planned=6, max_hours=2.0, used_s=1200, final={"decision": "use"})
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-15 12:00"), real_now=real, **rec.calls_for())
    assert rec.names() == ["continue_run"] and (root / "runs" / "r1" / scheduler.SPAN).is_file()


def test_no_span_file_without_keep_awake(tmp_path):
    home, root = campaign_study(tmp_path)
    study = load_study(root)
    study.schedule = replace(study.schedule, keep_awake=False)
    save_study(study, root)
    run_dir = write_run(root, "r1", "stopped", generations=2, planned=6)
    (run_dir / scheduler.SPAN).write_text('{"until_real": "2026-11-08T10:00:00+00:00"}', encoding="utf-8")
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-15 23:29"), **rec.calls_for())
    assert rec.names() == ["start"] and rec.calls[0][2] == {"resume": "r1"}
    assert not (run_dir / scheduler.SPAN).exists()


def test_a_session_by_hand_has_no_span_file(tmp_path):
    home, root = campaign_study(tmp_path)
    run_dir = write_run(root, "r1", "stopped", generations=2, planned=6)
    scheduler.tick(home, now=local("2026-11-15 23:29"), **Recorder().calls_for())
    assert (run_dir / scheduler.SPAN).is_file(), "the last scheduled session's"
    scheduler.mark_by_hand(home, root.name, "r1")
    assert not (run_dir / scheduler.SPAN).exists()


def test_a_refused_launch_leaves_no_span_file(tmp_path):
    home, root = campaign_study(tmp_path)
    run_dir = write_run(root, "r1", "stopped", generations=2, planned=6)
    rec = Recorder(start_raises=AppError("the application is not there", 409))
    scheduler.tick(home, now=local("2026-11-15 23:29"), **rec.calls_for())
    assert rec.names() == ["start"] and not (run_dir / scheduler.SPAN).exists()


# -- persona p4 round 1: the campaign's history, more time for the next window, the plan on the windows


def _starts_r1(root: Path):
    """A `start` that makes run r1 live, as `jobs.start` would."""
    def start(home_, root_, **kwargs):
        write_run(root, "r1", "search", alive=True)
        return {"run": "r1", "started": True}
    return start


def _at(text: str) -> str:
    return local(text).isoformat(timespec="seconds")


def test_the_campaign_keeps_its_sessions_on_the_window_clock(tmp_path):
    """H3: when in the weekend did it run? Each session the tick launches is kept from the tick's
    `now` (the rehearsal clock's, 120 x) to the last tick before the one that sees it ended (its
    `ticked_at`: fix round 1, m7)."""
    home, root = campaign_study(tmp_path)
    calls = {**Recorder().calls_for(), "start": _starts_r1(root)}
    scheduler.tick(home, now=local("2026-11-14 00:04"), speed=120.0, **calls)
    assert scheduler.read_campaign(root)["sessions"] == [
        {"run": "r1", "session": 1, "from": _at("2026-11-14 00:04"), "to": None, "by_hand": False}]
    scheduler.tick(home, now=local("2026-11-14 12:00"), speed=120.0, **calls)
    scheduler.tick(home, now=local("2026-11-15 09:27"), speed=120.0, **calls)
    assert scheduler.read_campaign(root)["sessions"][0]["to"] is None, "still going"
    write_run(root, "r1", "done", generations=6, planned=6, max_hours=1.0, used_s=3600, final={"decision": "keep"})
    scheduler.tick(home, now=local("2026-11-15 09:37"), speed=120.0, **calls)
    campaign = scheduler.read_campaign(root)
    assert campaign["state"] == "complete" and campaign["sessions"][0]["to"] == _at("2026-11-15 09:27")
    state = scheduler.campaign_state(root, load_study(root), now=local("2026-11-16 08:39"))
    assert state["history_line"] == "Ran in its windows: Sat 00:04 – Sun 09:27"
    assert state["history_by_run"] == {"r1": "Ran in its windows: Sat 00:04 – Sun 09:27"}


def test_a_continuation_is_a_session_of_its_own(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "done", generations=6, planned=6, max_hours=2.0, used_s=1200, final={"decision": "keep"})
    scheduler.tick(home, now=local("2026-11-14 10:00"), **Recorder().calls_for())
    assert scheduler.read_campaign(root)["sessions"] == [
        {"run": "r1", "session": 2, "from": _at("2026-11-14 10:00"), "to": None, "by_hand": False}]
    write_run(root, "r1", "search", alive=True, launch=2)
    scheduler.tick(home, now=local("2026-11-14 11:00"), **Recorder().calls_for())
    assert scheduler.read_campaign(root)["sessions"][0]["to"] is None, "the continuation is going"


def test_a_session_by_hand_is_kept_on_the_rehearsal_clock(tmp_path):
    """H3: a person's session joins the history from the clock the app keeps (the rehearsal's here)."""
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "done", generations=6, planned=6, max_hours=1.0, used_s=900, final={"decision": "keep"})
    server = AppServer(home.root, port=0)  # its app.json is what makes the rehearsal clock honoured
    try:
        scheduler.write_clock(home.root, local("2026-11-14 10:00"), 120.0)

        def launch():
            write_run(root, "r1", "search", alive=True, launch=2)
            return {"run": "r1", "continued": True}

        scheduler.by_hand(home, root, launch)
    finally:
        server.stop()
    (entry,) = scheduler.read_campaign(root)["sessions"]
    assert {k: entry[k] for k in ("run", "session", "to", "by_hand")} == {"run": "r1", "session": 2, "to": None,
                                                                         "by_hand": True}
    assert abs(datetime.fromisoformat(entry["from"]) - local("2026-11-14 10:00")) < timedelta(minutes=10), \
        "the rehearsal's time, not the real one"


def test_the_history_line_says_each_session(tmp_path):
    home, root = campaign_study(tmp_path)
    study = load_study(root)
    assert scheduler.campaign_state(root, study, now=local("2026-11-16 08:39"))["history_line"] is None, "nothing ran yet"
    write_run(root, "r1", "search", alive=True, launch=2)
    campaign = scheduler.read_campaign(root)
    campaign["sessions"] = [
        {"run": "r1", "session": 1, "from": _at("2026-11-14 00:04"), "to": _at("2026-11-14 06:00"), "by_hand": False},
        {"run": "r1", "session": 2, "from": _at("2026-11-14 10:00"), "to": None, "by_hand": True}]
    (root / scheduler.CAMPAIGN).write_text(json.dumps(campaign), encoding="utf-8")
    state = scheduler.campaign_state(root, study, now=local("2026-11-14 10:30"))
    assert state["history_line"] == "Ran in its windows: Sat 00:04 – Sat 06:00 and on Sat 10:00 – … by hand"
    campaign["sessions"] = campaign["sessions"][1:]
    (root / scheduler.CAMPAIGN).write_text(json.dumps(campaign), encoding="utf-8")
    assert scheduler.campaign_state(root, study, now=local("2026-11-14 10:30"))["history_line"] == \
        "Ran by hand on Sat 10:00 – …"
    (root / scheduler.CAMPAIGN).unlink()
    assert scheduler.campaign_state(root, study, now=local("2026-11-14 10:30"))["history_line"] is None, "a draft"


def _finished_campaign(tmp_path):
    """Persona p4's weekend: 0.3 h of search time, all used, the campaign complete on Sunday."""
    home, root = campaign_study(tmp_path)
    study = load_study(root)
    study.budget.hours = 0.3
    save_study(study, root)
    run_dir = write_run(root, "r1", "done", generations=17, planned=17, max_hours=0.2746, used_s=0.27 * 3600,
                        final={"decision": "keep"})
    plan = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    (run_dir / "plan.json").write_text(json.dumps({**plan, "hours": 0.3}), encoding="utf-8")
    scheduler.tick(home, now=local("2026-11-15 09:27"), **Recorder().calls_for())
    assert scheduler.read_campaign(root)["state"] == "complete"
    return home, root


def test_more_time_for_a_scheduled_study_goes_to_the_next_window(tmp_path):
    """H5: Monday's Continue on a weekend study raises its search time and waits for Saturday; it
    never runs now, outside a window."""
    home, root = _finished_campaign(tmp_path)
    rec = Recorder()
    answer = scheduler.continue_campaign(home, root, "r1", 0.3, now=local("2026-11-16 08:39"))
    assert answer["scheduled"] is True and answer["run"] == "r1" and answer["hours"] == pytest.approx(0.3)
    assert answer["next"] == _at("2026-11-21 00:00") and answer["edge"] == "start"
    assert answer["words"] == "Scheduled 18 min more for the next window (Sat 00:00)"
    assert answer["campaign"]["state"] == "armed" and answer["campaign"]["line"] == "Campaign · next window Sat 00:00"
    assert load_study(root).budget.hours == pytest.approx(0.6), "saved to study.yaml"
    campaign = scheduler.read_campaign(root)
    assert campaign["state"] == "armed" and campaign["after"] is None, "the same campaign's run, not a new one"
    assert scheduler.campaign_left_s(root / "runs" / "r1") == pytest.approx((0.2746 - 0.27 + 0.3) * 3600)
    scheduler.tick(home, now=local("2026-11-16 09:00"), **rec.calls_for())
    assert rec.calls == [], "never now, outside a window"
    scheduler.tick(home, now=local("2026-11-21 00:00:05"), **rec.calls_for())
    assert rec.calls == [("continue_run", (home, root, "r1"), {"hours": pytest.approx(0.3046, abs=1e-3)})]


def test_more_time_inside_a_window_says_so_and_a_short_one_is_refused(tmp_path):
    home, root = _finished_campaign(tmp_path)
    with pytest.raises(AppError) as short:
        scheduler.continue_campaign(home, root, "r1", 0.1, now=local("2026-11-16 08:39"))
    assert short.value.status == 400 and "at least 10 min" in str(short.value)
    assert load_study(root).budget.hours == pytest.approx(0.3), "nothing saved"
    answer = scheduler.continue_campaign(home, root, "r1", 0.5, now=local("2026-11-21 10:00"))
    assert answer["words"] == "Scheduled 30 min more: its window is open now, until Sun 23:59"
    write_run(root, "r2", "search", alive=True)
    with pytest.raises(AppError) as going:
        scheduler.continue_campaign(home, root, "r1", 0.5, now=local("2026-11-21 10:00"))
    assert going.value.status == 409


def test_a_finished_campaign_renewed_still_continues_the_run_it_is_given(tmp_path):
    """A start after the finished campaign armed a new one (`after` = r1); more time for r1 brings it
    back into the campaign rather than starting a new run."""
    home, root = _finished_campaign(tmp_path)
    scheduler.start_campaign(home, root, now=local("2026-11-16 08:00"), at_once=False, **Recorder().calls_for(LAUNCHERS))
    assert scheduler.read_campaign(root)["after"] == "r1"
    scheduler.continue_campaign(home, root, "r1", 0.3, now=local("2026-11-16 08:39"))
    assert scheduler.read_campaign(root)["after"] is None and scheduler.campaign_run(root) == "r1"


def test_the_api_continue_of_a_scheduled_study_is_scheduled(tmp_path, monkeypatch):
    from test_app_server import call

    home, root = _finished_campaign(tmp_path)
    slug = root.name
    monkeypatch.setattr(scheduler, "tick", lambda *a, **k: [])
    launched = []
    monkeypatch.setattr(jobs, "_spawn", lambda env, argv, root, run_dir: launched.append(argv) or 2 ** 22 + 7)
    server = AppServer(home.root, port=0)
    server.start()
    try:
        status, answer = call(server, "POST", f"/api/studies/{slug}/continue", {"run": "r1", "hours": 0.3})
        assert status == 200 and answer["scheduled"] is True and answer["words"].startswith("Scheduled 18 min more"), answer
        assert launched == [], "nothing runs now"
        status, answer = call(server, "POST", f"/api/studies/{slug}/continue", {"run": "r1", "hours": 0.3, "now": "yes"})
        assert status == 400 and "now" in answer["error"]
    finally:
        server.stop()


def test_a_scheduled_plan_says_when_it_ends_on_its_windows():
    """H1: the plan of a weekend study, read on Friday 17:38: it starts at the window and ends where
    its hours, spread over the windows, run out -- on the window clock (a rehearsal's 120 x too)."""
    from evolvekit.harness.schedule import PRESETS, Schedule

    weekend = Schedule.parse(PRESETS["weekends"]["schedule"])
    friday = local("2026-11-13 17:38")
    window = scheduler.plan_window(weekend, 0.3, now=friday)
    assert window == {"line": "starts Sat 00:00; about 18 min of search; done around Sat 00:18",
                      "starts": _at("2026-11-14 00:00"), "done": _at("2026-11-14 00:18")}
    assert scheduler.plan_window(weekend, 0.3, now=friday, speed=120.0)["line"] == \
        "starts Sat 00:00; about 18 min of search; done around Sun 12:00"
    assert scheduler.plan_window(weekend, 0.3, now=local("2026-11-14 10:00"))["line"] == \
        "starts now; about 18 min of search; done around Sat 10:18"
    nights = Schedule.parse(PRESETS["nights"]["schedule"])
    assert scheduler.plan_window(nights, 6, now=friday)["line"] == \
        "starts Mon 01:00; about 6 h of search; done around Tue 02:00"
    short = Schedule.parse({**PRESETS["weekends"]["schedule"], "until": "2026-11-15"})
    assert scheduler.plan_window(short, 60, now=friday) == {
        "line": "starts Sat 00:00; about 60 h of search: more than its windows give before its Until (15 Nov)",
        "starts": _at("2026-11-14 00:00"), "done": None}
    assert scheduler.plan_window(short, 1, now=local("2026-11-16 08:00"))["line"] == "its schedule has no window left"
    assert scheduler.schedule_hours(weekend, now=friday) == pytest.approx(47.98, abs=0.01)
    assert scheduler.schedule_hours(nights, now=friday) == pytest.approx(25.0)
    assert scheduler.schedule_hours(weekend, now=friday, speed=120.0) == pytest.approx(0.4, abs=0.001), \
        "real hours of search: at 120 x a weekend is 24 real minutes"


def test_the_plan_and_the_study_of_a_scheduled_study_carry_the_window_clock(tmp_path):
    from test_app_server import call

    home, root = _finished_campaign(tmp_path)
    server = AppServer(home.root, port=0)
    server.start()
    try:
        status, plan = call(server, "GET", f"/api/studies/{root.name}/plan")
        assert status == 200 and plan["window"]["line"].startswith("starts "), plan
        assert plan["window"]["line"] in plan["summary"] and "done around" not in plan["summary"].replace(
            plan["window"]["line"], "")
        status, document = call(server, "GET", f"/api/studies/{root.name}")
        assert status == 200 and document["schedule_hours"] == pytest.approx(47.98, abs=0.01)
        assert document["campaign_left_s"] == pytest.approx((0.2746 - 0.27) * 3600)
        assert "history_line" in document["campaign"]
    finally:
        server.stop()


def test_the_study_names_its_next_window_and_a_waiting_new_run(tmp_path):
    """Persona p4 (wave H-app), H5: the scheduled Continue's button says when ("in the next window
    (Sat 00:00)") although a finished campaign has no `next`; it is not offered while a renewed campaign
    waits to start a fresh run after the latest one (`after` is that run). H1: Set up reads the
    rehearsal's speed to say a window's hours on both clocks."""
    from test_app_server import call

    home, root = _finished_campaign(tmp_path)
    server = AppServer(home.root, port=0)
    server.start()
    try:
        scheduler.write_clock(home.root, local("2026-11-16 08:39"), 120.0)
        status, document = call(server, "GET", f"/api/studies/{root.name}")
        assert status == 200 and document["campaign"]["next"] is None, "a finished campaign"
        assert document["next_window"] == {"at": _at("2026-11-21 00:00"), "edge": "start", "words": "Sat 00:00"}
        assert document["new_run_waits"] is False
        assert document["clock"]["speed"] == 120.0 and document["clock"]["line"].startswith("rehearsal clock: Mon 16 Nov")
        campaign = json.loads((root / scheduler.CAMPAIGN).read_text(encoding="utf-8"))
        (root / scheduler.CAMPAIGN).write_text(json.dumps({**campaign, "state": "armed", "after": "r1"}), encoding="utf-8")
        status, document = call(server, "GET", f"/api/studies/{root.name}")
        assert document["new_run_waits"] is True
    finally:
        server.stop()
    plain = AppServer(home.root, port=0)  # no rehearsal: no clock in the document
    plain.start()
    try:
        (home.root / "clock.json").unlink()
        status, document = call(plain, "GET", f"/api/studies/{root.name}")
        assert status == 200 and "clock" not in document
    finally:
        plain.stop()


def test_a_draft_card_names_what_is_missing(tmp_path):
    """Persona p4, H6: "Set up not finished" did not say what was missing. A draft's Home card carries
    its first problem (`missing`), None when nothing is."""
    from test_app_server import call

    home, root = campaign_study(tmp_path)
    import yaml

    (root / scheduler.CAMPAIGN).unlink()  # a draft: never scheduled
    raw = yaml.safe_load((root / "study.yaml").read_text(encoding="utf-8"))
    raw["application"] = {"path": str(Path(__file__).resolve().parents[1] / "examples" / "cli-solver" / "solver.py"),
                          "version": "1.0"}
    raw["step"] = 7
    (root / "study.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    server = AppServer(home.root, port=0)
    server.start()
    try:
        status, got = call(server, "GET", "/api/home")
        (card,) = got["studies"]
        assert status == 200 and card["kind"] == "draft" and card["missing"] is None, card
        raw["goal"]["levels"] = []
        (root / "study.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        status, got = call(server, "GET", "/api/home")
        (card,) = got["studies"]
        assert card["missing"]["step"] == 5 and card["missing"]["key"].startswith("goal"), card
    finally:
        server.stop()


def test_a_session_that_ended_while_the_app_was_off_ends_at_its_last_tick(tmp_path):
    """Fix round 1, m7: the run ended with no app ticking. Read on Monday, the history says it
    ended at the campaign's last tick (Sat 12:00), not "…"; and Monday's first tick writes that
    too, never Monday."""
    home, root = campaign_study(tmp_path)
    calls = {**Recorder().calls_for(), "start": _starts_r1(root)}
    scheduler.tick(home, now=local("2026-11-14 00:04"), **calls)
    scheduler.tick(home, now=local("2026-11-14 12:00"), **calls)
    write_run(root, "r1", "done", generations=6, planned=6, max_hours=1.0, used_s=3600, final={"decision": "keep"})
    before = (root / scheduler.CAMPAIGN).read_text(encoding="utf-8")
    state = scheduler.campaign_state(root, load_study(root), now=local("2026-11-16 08:39"))
    assert state["history_line"] == "Ran in its windows: Sat 00:04 – Sat 12:00"
    assert (root / scheduler.CAMPAIGN).read_text(encoding="utf-8") == before, "a read writes nothing"
    scheduler.tick(home, now=local("2026-11-16 08:39"), **calls)
    assert scheduler.read_campaign(root)["sessions"][0]["to"] == _at("2026-11-14 12:00")


def test_the_plan_of_a_campaign_with_a_finished_run_spreads_what_is_left(tmp_path):
    """Fix round 1, m9: once its run is finished, the next window goes on with the campaign's
    search time left and a final check, not the whole plan again."""
    home, root = _finished_campaign(tmp_path)
    study = load_study(root)
    monday = local("2026-11-16 08:39")
    assert scheduler.campaign_plan_hours(root, study, 0.3, 37.0, now=monday) == 0.3, \
        "a complete campaign: a start begins a new run"
    scheduler.continue_campaign(home, root, "r1", 0.3, now=monday)
    left = scheduler.campaign_left_s(root / "runs" / "r1")
    assert scheduler.campaign_plan_hours(root, load_study(root), 0.6, 37.0, now=monday) == \
        pytest.approx((left + 37.0) / 3600.0)


def test_the_runs_route_carries_the_campaign(tmp_path):
    """Fix round 1: the open Study page asks the runs route every few seconds; it brings the campaign
    as it is now, so the page follows a campaign that finishes."""
    from test_app_server import call

    home, root = _finished_campaign(tmp_path)
    server = AppServer(home.root, port=0)
    server.start()
    try:
        status, body = call(server, "GET", f"/api/studies/{root.name}/runs")
        assert status == 200 and body["campaign"]["state"] == "complete" and body["campaign"]["line"] == "Campaign finished"
        assert "history_line" in body["campaign"]
    finally:
        server.stop()
