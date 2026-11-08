"""The app as a service: no browser, no console, a log in the home, started at logon.

`evolvekit app --service` serves the app for one library home with nothing on a console: what
it says, and what its ticker prints (every campaign start, resume, continuation and stop, and any
traceback), goes to `<home>/app.log`, rotated at 5 MB with two kept. One app per home: a second
start finds the first in `app.json` and leaves it be, so two tickers never share a home.

`evolvekit app install-startup` makes it start at logon:

* Windows: a Task Scheduler task, "evolvekit app", from an XML definition (D16): a logon trigger,
  no time limit, never held back or stopped on battery, one instance. It runs `pythonw.exe`, so no
  console window opens; runs are still launched with `python.exe` (`detach.python_for_runs`).
* macOS and Linux: the launchd agent or systemd user unit is printed, with the commands that
  install it -- nothing is run.

`evolvekit app stop` asks the app of the home to end cleanly (`POST /api/shutdown`, with the key
of its launch), and waits for it to go.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import shlex
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape

from evolvekit.app.running import APP_FILE, read_running
from evolvekit.lock import pid_alive

__all__ = [
    "LOG", "LOG_BACKUPS", "LOG_BYTES", "PLIST_NAME", "STOP_WAIT_S", "TASK_NAME", "TASK_XML", "UNIT_NAME",
    "install_startup", "service_argv", "start_logging", "stop_app", "stop_logging", "task_xml",
    "uninstall_startup", "windowless",
]

LOG = "app.log"
"""In the home: the service's log."""
LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 2
TASK_NAME = "evolvekit app"
"""The Windows logon task."""
TASK_XML = "evolvekit-app-task.xml"
"""In the home: the task's definition, as given to `schtasks /Create /XML`."""
UNIT_NAME = "evolvekit-app.service"
PLIST_NAME = "evolvekit.app.plist"
PLIST_LABEL = "evolvekit.app"
PLIST_PATH = f"~/Library/LaunchAgents/{PLIST_NAME}"
UNIT_PATH = f"~/.config/systemd/user/{UNIT_NAME}"
STOP_WAIT_S = 10.0
"""How long `app stop` waits for the app's process to go."""


def _err(line: str) -> None:
    print(line, file=sys.stderr)


# ---------------------------------------------------------------------------
# the log
# ---------------------------------------------------------------------------


class _LogWriter:
    """A stand-in for `sys.stdout`/`sys.stderr` that logs each whole line it is given."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self._part = ""
        self._lock = threading.Lock()
        self.encoding = "utf-8"
        self.errors = "backslashreplace"

    def write(self, text: str) -> int:
        with self._lock:
            lines = (self._part + str(text)).split("\n")
            self._part = lines.pop()
        for line in lines:
            if line.strip():
                self.logger.info(line.rstrip("\r"))
        return len(text)

    def flush(self) -> None:
        with self._lock:
            part, self._part = self._part, ""
        if part.strip():
            self.logger.info(part.rstrip("\r"))

    def isatty(self) -> bool:
        return False


def start_logging(home_root: Path, *, redirect: bool = False) -> logging.Logger:
    """A logger that writes `"<time> <message>"` lines to `<home>/app.log`, rotated at `LOG_BYTES`
    with `LOG_BACKUPS` kept. With `redirect` (the service), `sys.stdout` and `sys.stderr` are
    replaced by writers that log each line, so the ticker's lines and tracebacks land in the log
    (a `pythonw.exe` process has no streams of its own)."""
    root = Path(home_root)
    root.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"evolvekit.app.service:{root.resolve()}")
    stop_logging(logger)
    handler = RotatingFileHandler(root / LOG, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if redirect:
        sys.stdout = _LogWriter(logger)  # type: ignore[assignment]
        sys.stderr = _LogWriter(logger)  # type: ignore[assignment]
    return logger


def stop_logging(logger: logging.Logger) -> None:
    """Close the logger's files (a file left open cannot be renamed or removed on Windows), and give
    back the process's own streams when they were redirected to it."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if isinstance(stream, _LogWriter) and stream.logger is logger:
            stream.flush()
            setattr(sys, name, getattr(sys, f"__{name}__"))
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


# ---------------------------------------------------------------------------
# the command the service runs
# ---------------------------------------------------------------------------


def windowless(python: str) -> str:
    """On Windows `pythonw.exe` beside `python.exe` when it exists (no console window), else
    `python`."""
    path = Path(python)
    if sys.platform == "win32" and path.name.lower() == "python.exe":
        quiet = path.with_name("pythonw.exe")
        if quiet.is_file():
            return str(quiet)
    return python


def service_argv(home_root: Path, *, python: str | None = None) -> list[str]:
    return [windowless(python or sys.executable), "-m", "evolvekit", "app", "--service", "--home", str(home_root)]


def task_xml(argv: list[str], user: str) -> str:
    """A Task Scheduler 1.2 definition that runs `argv` at `user`'s logon, with no time limit,
    never held back or stopped on battery, one instance at a time, as that user without raised
    rights (D16)."""
    command, arguments, who = escape(argv[0]), escape(subprocess.list2cmdline(argv[1:])), escape(user)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Starts the evolvekit app at logon, so the campaigns of its library keep their windows.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{who}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{who}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _plist(argv: list[str]) -> str:
    strings = "\n".join(f"    <string>{escape(part)}</string>" for part in argv)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{strings}
  </array>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>"""


def _unit(argv: list[str]) -> str:
    return f"""[Unit]
Description=evolvekit app

[Service]
ExecStart={shlex.join(argv)}
Restart=on-failure

[Install]
WantedBy=default.target"""


def _user() -> str:
    name = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if not name:
        import getpass

        name = getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    return f"{domain}\\{name}" if domain else name


def _last_line(done: Any) -> str:
    for text in (getattr(done, "stderr", "") or "", getattr(done, "stdout", "") or ""):
        lines = [line.strip() for line in str(text).splitlines() if line.strip()]
        if lines:
            return lines[-1]
    return f"schtasks ended with exit code {getattr(done, 'returncode', '?')}"


def _schtasks(run: Callable[..., Any], argv: list[str]) -> tuple[bool, str]:
    try:
        done = run(argv, capture_output=True, text=True)
    except OSError as exc:
        return False, f"could not run schtasks: {exc.strerror or exc}"
    return done.returncode == 0, _last_line(done)


def install_startup(home_root: Path, *, platform: str = sys.platform, run: Callable[..., Any] = subprocess.run,
                    out: Callable[[str], None] = print, err: Callable[[str], None] = _err,
                    python: str | None = None, user: str | None = None) -> int:
    """Make the service start at logon. Windows: write `<home>/evolvekit-app-task.xml` (UTF-16, with
    a BOM) and register it as the task "evolvekit app" with `schtasks`; 1, with schtasks' own last
    line and the command to start it by hand, when it refuses. macOS and Linux: print the launchd
    agent or the systemd user unit and how to install it; nothing is run."""
    home_root = Path(home_root)
    argv = service_argv(home_root, python=python)
    if platform == "win32":
        home_root.mkdir(parents=True, exist_ok=True)
        path = home_root / TASK_XML
        path.write_bytes(task_xml(argv, user or _user()).encode("utf-16"))
        ok, said = _schtasks(run, ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(path), "/F"])
        command = subprocess.list2cmdline(argv)
        if not ok:
            err(said)
            err(f"you can also start it yourself: {command}")
            return 1
        out(f'registered the task "{TASK_NAME}": at logon it runs')
        out(command)
        return 0
    if platform == "darwin":
        out(_plist(argv))
        out(f"save it as {PLIST_PATH}, then run: launchctl load {PLIST_PATH}")
        return 0
    out(_unit(argv))
    out(f"save it as {UNIT_PATH}, then run: systemctl --user enable --now {UNIT_NAME.removesuffix('.service')}")
    return 0


def uninstall_startup(home_root: Path, *, platform: str = sys.platform, run: Callable[..., Any] = subprocess.run,
                      out: Callable[[str], None] = print, err: Callable[[str], None] = _err) -> int:
    """Undo `install_startup`. Windows: delete the task (and its XML in the home); elsewhere, print
    the commands that remove the agent or the unit."""
    if platform == "win32":
        ok, said = _schtasks(run, ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
        if not ok:
            err(said)
            return 1
        (Path(home_root) / TASK_XML).unlink(missing_ok=True)
        out(f'removed the task "{TASK_NAME}": the app no longer starts at logon')
        return 0
    if platform == "darwin":
        out("to remove it, run:")
        out(f"launchctl unload {PLIST_PATH}")
        out(f"rm {PLIST_PATH}")
        return 0
    out("to remove it, run:")
    out(f"systemctl --user disable --now {UNIT_NAME.removesuffix('.service')}")
    out(f"rm {UNIT_PATH}")
    return 0


# ---------------------------------------------------------------------------
# stopping it
# ---------------------------------------------------------------------------


def _names(home_root: Path, pid: int) -> bool:
    """Whether `app.json` still names `pid`: a clean stop removes it last thing before the end."""
    try:
        doc = json.loads((home_root / APP_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(doc, dict) and doc.get("pid") == pid


def stop_app(home_root: Path, *, out: Callable[[str], None] = print, err: Callable[[str], None] = _err,
             wait_s: float | None = None) -> int:
    """`evolvekit app stop`: ask the home's app to end (`POST /api/shutdown` with its launch's key
    and its own `Origin`) and wait up to `STOP_WAIT_S` for its process to go -- or for its
    `app.json`, which the app removes as it ends."""
    running = read_running(Path(home_root))
    if running is None:
        out("no app is running for this library")
        return 0
    pid = int(running.get("pid") or 0)
    port = int(running.get("port") or 0)
    token = (parse_qs(urlparse(str(running.get("url") or "")).query).get("token") or [""])[-1]
    asked: str | None = None
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        connection.request("POST", "/api/shutdown", body=b"", headers={
            "Host": f"127.0.0.1:{port}", "Origin": f"http://127.0.0.1:{port}", "X-Evolvekit-Token": token,
            "Content-Length": "0"})
        answer = connection.getresponse()
        answer.read()
        connection.close()
        if answer.status != 200:
            asked = f"the app refused to stop (HTTP {answer.status})"
    except (OSError, http.client.HTTPException) as exc:
        asked = f"could not reach the app: {exc}"  # it may be going already: the wait says
    deadline = time.monotonic() + (STOP_WAIT_S if wait_s is None else wait_s)
    while pid_alive(pid) and _names(Path(home_root), pid):
        if time.monotonic() >= deadline:
            if asked:
                err(asked)
            err(f"the app did not stop within {STOP_WAIT_S if wait_s is None else wait_s:g} s (pid {pid})")
            return 1
        time.sleep(0.1)
    out("stopped the app")
    return 0
