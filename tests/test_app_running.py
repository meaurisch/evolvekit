import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from evolvekit.app.running import clear_running, read_running, write_running


def test_write_then_read_round_trips(tmp_path):
    write_running(tmp_path, pid=os.getpid(), port=8780, url="http://127.0.0.1:8780/?token=x")
    doc = read_running(tmp_path)
    assert doc["port"] == 8780 and doc["url"].endswith("token=x") and doc["pid"] == os.getpid()
    assert "started_at" in doc and doc["host"] == "127.0.0.1"


def test_stale_pid_reads_as_not_running_and_is_removed(tmp_path):
    (tmp_path / "app.json").write_text(json.dumps({"pid": 999999, "port": 1, "url": "u", "host": "127.0.0.1",
                                                   "started_at": "2026-01-01T00:00:00+00:00"}))
    assert read_running(tmp_path) is None
    assert not (tmp_path / "app.json").exists()


def test_clear_only_removes_own_file(tmp_path):
    write_running(tmp_path, pid=os.getpid(), port=1, url="u")
    clear_running(tmp_path, pid=os.getpid() + 1)
    assert (tmp_path / "app.json").exists()
    clear_running(tmp_path, pid=os.getpid())
    assert not (tmp_path / "app.json").exists()


@pytest.mark.skipif(sys.platform == "darwin", reason="no process start time on macOS")
def test_running_ignores_pid_of_another_program(tmp_path):
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        (tmp_path / "app.json").write_text(json.dumps({"pid": other.pid, "port": 1, "url": "u", "host": "127.0.0.1",
                                                       "started_at": hour_ago}))
        assert read_running(tmp_path) is None
    finally:
        other.kill()
        other.wait()
