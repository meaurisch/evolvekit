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

import pytest

from evolvekit.app import scheduler
from evolvekit import cli
from evolvekit.app import AppError, jobs
from evolvekit.app.server import AppServer
from evolvekit.harness.study import load_study, save_study
from evolvekit.stopping import STOP_REQUEST

from campaign_helpers import Recorder, campaign_study, local, write_run


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
