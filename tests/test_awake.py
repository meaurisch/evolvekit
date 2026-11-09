"""Keep-awake (`evolvekit/app/awake.py`, D46): a scheduled session keeps the computer from
sleeping until its span ends, and through a final check past it; released when the run ends."""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from evolvekit.app import awake, scheduler


def _until(probe, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while not probe():
        if time.monotonic() >= deadline:
            raise AssertionError(f"not within {timeout} s")
        time.sleep(0.01)


def _span(run_dir, until: datetime) -> None:
    (run_dir / scheduler.SPAN).write_text(json.dumps({"until_real": until.isoformat()}), encoding="utf-8")


def test_keep_awake_follows_the_span_file(tmp_path):
    states = []
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "job.json").write_text(json.dumps({"phase": "search"}), encoding="utf-8")
    soon = datetime.now(timezone.utc) + timedelta(seconds=1)
    (run_dir / scheduler.SPAN).write_text(json.dumps({"until_real": soon.isoformat()}), encoding="utf-8")
    with awake.keeping_awake(run_dir, set_state=states.append, poll_s=0.05):
        _until(lambda: states == [True], timeout=5)
        _until(lambda: states == [True, False], timeout=5)                # the span ended mid-search
        (run_dir / "job.json").write_text(json.dumps({"phase": "check"}), encoding="utf-8")
        _until(lambda: states == [True, False, True], timeout=5)          # a final check past the span stays awake
    assert states == [True, False, True, False], "released when the run ends"


def test_no_span_file_keeps_nothing_awake(tmp_path):
    states = []
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "job.json").write_text(json.dumps({"phase": "check"}), encoding="utf-8")
    with awake.keeping_awake(run_dir, set_state=states.append, poll_s=0.02):
        time.sleep(0.2)
    assert True not in states


def test_wanted(tmp_path):
    assert awake.SPAN == scheduler.SPAN, "the run reads the file the tick writes"
    now = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)
    assert not awake.wanted(tmp_path, real_now=now), "no span file"
    _span(tmp_path, now + timedelta(minutes=1))
    assert awake.wanted(tmp_path, real_now=now)
    assert not awake.wanted(tmp_path, real_now=now + timedelta(minutes=2))
    (tmp_path / "job.json").write_text(json.dumps({"phase": "check"}), encoding="utf-8")
    assert awake.wanted(tmp_path, real_now=now + timedelta(minutes=2)), "the final check past the span"
    _span(tmp_path, now + timedelta(minutes=1))
    (tmp_path / "launch.json").write_text(json.dumps({"pid": 1, "launch": 3}), encoding="utf-8")
    assert awake.wanted(tmp_path, real_now=now), "no session in the file: honoured"
    span = {"until_real": (now + timedelta(minutes=1)).isoformat(), "session": 2}
    (tmp_path / scheduler.SPAN).write_text(json.dumps(span), encoding="utf-8")
    assert not awake.wanted(tmp_path, real_now=now), "written for session 2: session 3 is a later one, by hand"
    (tmp_path / "launch.json").write_text(json.dumps({"pid": 1, "launch": 2}), encoding="utf-8")
    assert awake.wanted(tmp_path, real_now=now)
    (tmp_path / scheduler.SPAN).write_text("not json", encoding="utf-8")
    (tmp_path / "job.json").write_text(json.dumps({"phase": "search"}), encoding="utf-8")
    assert not awake.wanted(tmp_path, real_now=now)


def test_set_state_runs_on_one_thread(tmp_path):
    threads = []
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _span(run_dir, datetime.now(timezone.utc) + timedelta(hours=1))
    with awake.keeping_awake(run_dir, set_state=lambda on: threads.append(threading.get_ident()), poll_s=0.02):
        _until(lambda: len(threads) == 1)
        (run_dir / scheduler.SPAN).unlink()
        _until(lambda: len(threads) == 2)
    assert len(threads) == 2 and len(set(threads)) == 1 and threading.get_ident() not in threads


def test_set_state_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(awake, "_kernel32", lambda: pytest.fail("no ctypes.windll touched"))
    awake.set_state(True)
    awake.set_state(False)                         # no ctypes.windll touched


@pytest.mark.skipif(sys.platform != "win32", reason="SetThreadExecutionState is Windows")
def test_set_state_calls_set_thread_execution_state(monkeypatch):
    seen = []
    monkeypatch.setattr(awake, "_kernel32", lambda: SimpleNamespace(SetThreadExecutionState=lambda flags: seen.append(flags) or 1))
    awake.set_state(True)
    awake.set_state(False)
    assert seen == [0x80000000 | 0x00000001, 0x80000000]


def test_the_job_runs_inside_keeping_awake(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from evolvekit.app import job

    entered = []

    @contextmanager
    def keeping(run_dir, **kwargs):
        entered.append(run_dir)
        yield

    monkeypatch.setattr(awake, "keeping_awake", keeping)
    monkeypatch.setattr(job, "final_check", lambda root, run, seeds=None, log=None: {"phase": "done"})
    assert job.main([str(tmp_path / "study"), "r1", "--check"]) == 0
    assert entered == [(tmp_path / "study").resolve() / "runs" / "r1"]
