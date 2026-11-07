"""`<home>/app.json`: the app says it is running, so a command can find it.

Written as soon as the server binds its port, removed on a clean stop. A file
whose process is gone -- or is alive but is not the app that wrote it -- counts
as "not running" and is deleted when read.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from evolvekit.lock import pid_alive, pid_started_at

__all__ = ["APP_FILE", "clear_running", "read_running", "write_running"]

APP_FILE = "app.json"
START_SLACK_S = 120.0
"""A process that started this long after the file's `started_at` is not the
app that wrote it: the pid was recycled, by a reboot or by chance."""


def write_running(home_root: Path, *, pid: int, port: int, url: str, host: str = "127.0.0.1") -> Path:
    doc = {"pid": pid, "started_at": datetime.now(timezone.utc).isoformat(), "host": host, "port": port, "url": url}
    path = Path(home_root) / APP_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{APP_FILE}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return path


def read_running(home_root: Path) -> dict[str, Any] | None:
    """The document when the app that wrote it is still running, else `None`
    (and a stale file is deleted)."""
    path = Path(home_root) / APP_FILE
    doc = _load(path)
    if doc is None:
        return None
    if _alive(doc):
        return doc
    try:
        path.unlink()
    except OSError:
        pass
    return None


def clear_running(home_root: Path, *, pid: int) -> None:
    """Remove the file, but only when it names this pid."""
    path = Path(home_root) / APP_FILE
    doc = _load(path)
    if doc is None or doc.get("pid") != pid:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _load(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _alive(doc: dict[str, Any]) -> bool:
    # Twin of evolvekit.app.jobs.run_alive's process rule (pid exists, and its
    # start time agrees with the file); keep the two in step.
    try:
        pid = int(doc.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0 or not pid_alive(pid):
        return False
    try:
        written = datetime.fromisoformat(str(doc.get("started_at")))
    except ValueError:
        return True
    born = pid_started_at(pid)
    if born is None:
        return True
    written = written if written.tzinfo is not None else written.replace(tzinfo=timezone.utc)
    return born <= written + timedelta(seconds=START_SLACK_S)
