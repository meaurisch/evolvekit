"""One writer per run directory, and a stale lock that cleans itself up."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace

import pytest

from evolvekit.cli import main
from evolvekit.config import load_config
from evolvekit.lock import RunLock, RunLockError, pid_alive, run_lock
from evolvekit.search.driver import Driver
from tests.conftest import EXAMPLE_CONFIG


def dead_pid() -> int:
    """A pid that certainly is not running: one we just watched exit."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


# -- liveness --------------------------------------------------------------


def test_this_process_is_alive_and_a_finished_one_is_not():
    assert pid_alive(os.getpid())
    assert not pid_alive(dead_pid())
    assert not pid_alive(0) and not pid_alive(-1)


def test_checking_liveness_does_not_kill_anything():
    """os.kill(pid, 0) on Windows would terminate the process it asks about."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert pid_alive(child.pid)
        assert child.poll() is None, "the liveness check killed the process"
    finally:
        child.kill()
        child.wait()


# -- the lock --------------------------------------------------------------


def test_the_lock_records_the_owning_pid(tmp_path):
    with run_lock(tmp_path / "run") as lock:
        payload = json.loads(lock.path.read_text(encoding="utf-8"))
        assert payload["pid"] == os.getpid()
        assert payload["started"] and payload["host"]
    assert not lock.path.exists(), "the lock outlived the run"


def test_a_second_holder_is_refused_with_the_pid_in_the_message(tmp_path):
    with run_lock(tmp_path / "run"):
        with pytest.raises(RunLockError, match=f"pid {os.getpid()}"):
            RunLock(tmp_path / "run").acquire()


def test_a_stale_lock_is_reclaimed_with_a_note(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    stale = dead_pid()
    (run_dir / ".lock").write_text(
        json.dumps({"pid": stale, "started": "yesterday"}), encoding="utf-8"
    )
    lock = RunLock(run_dir).acquire()
    assert lock.reclaimed_from == stale
    assert json.loads(lock.path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    lock.release()


def test_an_unreadable_lock_file_does_not_wedge_the_directory(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / ".lock").write_text("not json at all", encoding="utf-8")
    lock = RunLock(run_dir).acquire()
    assert lock.held
    lock.release()


def test_releasing_a_lock_we_no_longer_own_leaves_it_alone(tmp_path):
    lock = RunLock(tmp_path / "run").acquire()
    lock.path.write_text(json.dumps({"pid": 4242}), encoding="utf-8")
    lock.release()
    assert lock.path.exists(), "another owner's lock was deleted"


# -- inside the loop -------------------------------------------------------


def _tiny(config):
    return replace(
        config,
        search=replace(
            config.search,
            generations=1,
            children_per_generation=1,
            scratchpad_every=0,
        ),
    )


@pytest.mark.slow
def test_a_second_run_on_the_same_directory_refuses(tmp_path):
    config = _tiny(load_config(EXAMPLE_CONFIG))
    driver = Driver(config, run_dir=tmp_path / "shared")
    with run_lock(tmp_path / "shared"):
        with pytest.raises(RunLockError, match="already locked by this process"):
            driver.run()
    # Once the other holder is gone the same driver runs normally.
    summary = driver.run()
    assert summary.seed_score is not None


def test_the_lock_is_released_even_when_the_run_raises(tmp_path, monkeypatch):
    config = _tiny(load_config(EXAMPLE_CONFIG))
    driver = Driver(config, run_dir=tmp_path / "boom")

    def explode(*_args, **_kwargs):
        raise RuntimeError("evaluator on fire")

    monkeypatch.setattr(driver, "_run", explode)
    with pytest.raises(RuntimeError, match="on fire"):
        driver.run()
    assert not (driver.ledger.run_dir / ".lock").exists()


def test_the_cli_reports_a_locked_directory_as_exit_3(tmp_path, capsys):
    run_dir = tmp_path / "cli"
    with run_lock(run_dir):
        code = main(
            [
                "run",
                "--config",
                str(EXAMPLE_CONFIG),
                "--run-dir",
                str(run_dir),
                "--generations",
                "1",
                "--quiet",
            ]
        )
    assert code == 3
    assert "locked" in capsys.readouterr().err
