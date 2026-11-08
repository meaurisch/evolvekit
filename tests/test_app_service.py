"""The app as a service (`evolvekit/app/service.py`): a rotated log, one app per home, the logon
task (never registered for real here, D51: `subprocess` is patched), a rehearsal clock, `app stop`
and `POST /api/shutdown`."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime

import pytest

from evolvekit.app import detach, scheduler, service
from evolvekit.app.running import read_running, write_running
from evolvekit.app.server import AppServer
from evolvekit.app.store import Home
from evolvekit.cli import main

from campaign_helpers import local
from test_app_server import call


def _until(probe, timeout: float = 30.0, every: float = 0.1):
    deadline = time.monotonic() + timeout
    while True:
        found = probe()
        if found:
            return found
        if time.monotonic() >= deadline:
            raise AssertionError(f"not within {timeout} s")
        time.sleep(every)


def _never(argv, **kwargs):
    raise AssertionError(f"nothing is run on this platform: {argv}")


def _done(code: int, out: str = "", err: str = ""):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, code, out, err)

    return run


def test_service_writes_log_and_app_json(tmp_path):
    home = tmp_path / "my home"
    proc = subprocess.Popen([sys.executable, "-m", "evolvekit", "app", "--service", "--home", str(home), "--port", "0"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    try:
        running = _until(lambda: read_running(home), timeout=30)
        assert urllib.request.urlopen(running["url"]).status == 200
        log = _until(lambda: (home / "app.log").is_file() and (home / "app.log").read_text(encoding="utf-8"))
        assert running["url"].split("?")[0] in log and str(home.resolve()) in log
        assert "token=" not in log and "the key is in app.json" in log, "the address without its key"
    finally:
        proc.terminate()
        out, err = proc.communicate(timeout=30)
    assert out == b"", "nothing on the console"


def test_log_rotates(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "LOG_BYTES", 1000)
    logger = service.start_logging(tmp_path)
    try:
        for i in range(200):
            logger.info("line %d %s", i, "x" * 40)
        names = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("app.log"))
        assert names == ["app.log", "app.log.1", "app.log.2"]
    finally:
        service.stop_logging(logger)


def test_redirected_output_lands_in_the_log(tmp_path):
    out, err = sys.stdout, sys.stderr
    logger = service.start_logging(tmp_path, redirect=True)
    try:
        print("a tick said this")
        print("Traceback (most recent call last):", file=sys.stderr)
    finally:
        service.stop_logging(logger)
        sys.stdout, sys.stderr = out, err
    log = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "a tick said this" in log and "Traceback (most recent call last):" in log


def test_install_startup_windows_command(tmp_path):
    home = tmp_path / "my home"
    home.mkdir()
    calls, printed = [], []
    code = service.install_startup(home, platform="win32", run=lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""),
                                   out=printed.append, python=r"C:\Program Files\Py\python.exe")
    assert code == 0
    assert calls == [["schtasks", "/Query", "/TN", "evolvekit app", "/XML"],
                     ["schtasks", "/Create", "/TN", "evolvekit app", "/XML", str(home / "evolvekit-app-task.xml"), "/F"]]
    assert len(printed) == 2, "no task to replace: nothing said of one"
    xml = (home / "evolvekit-app-task.xml").read_bytes().decode("utf-16")
    assert r"<Command>C:\Program Files\Py\python.exe</Command>" in xml   # pythonw.exe when it exists beside it
    assert f'<Arguments>-m evolvekit app --service --home "{home}"</Arguments>' in xml
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml and "<DisallowStartIfOnBatteries>false" in xml
    assert printed[0] == 'registered the task "evolvekit app": at logon it runs'
    assert printed[1] == subprocess.list2cmdline([r"C:\Program Files\Py\python.exe", "-m", "evolvekit", "app", "--service",
                                                  "--home", str(home)])


def _task_of(home: str):
    """`run` for schtasks: /Query answers with the task that starts `home`, /Create succeeds."""
    xml = service.task_xml([r"C:\Py\pythonw.exe", "-m", "evolvekit", "app", "--service", "--home", home], r"DESK\norri")

    def run(argv, **kw):
        out = xml if "/Query" in argv else ""
        return subprocess.CompletedProcess(argv, 0, out, "")
    return run


def test_install_startup_says_it_replaces_another_librarys_task(tmp_path):
    home = tmp_path / "my home"
    home.mkdir()
    printed = []
    other = r"C:\Users\pat\other & home"
    assert service.install_startup(home, platform="win32", run=_task_of(other), out=printed.append,
                                   python=r"C:\Py\python.exe") == 0
    assert printed[2] == (f"it replaces the task for {other}: there is one logon task per user, so that library "
                          "no longer starts at logon")
    printed.clear()
    assert service.install_startup(home, platform="win32", run=_task_of(str(home)), out=printed.append,
                                   python=r"C:\Py\python.exe") == 0
    assert len(printed) == 2, "its own task again: nothing replaced"


def test_the_task_xml_says_everything_d16_asks(tmp_path):
    xml = service.task_xml([r"C:\Py\pythonw.exe", "-m", "evolvekit", "app", "--service", "--home", r"C:\a & b\<home>"],
                           r"DESK\norri")
    assert xml.startswith('<?xml version="1.0" encoding="UTF-16"?>')
    assert 'version="1.2"' in xml and "<UserId>DESK\\norri</UserId>" in xml and "<LogonTrigger>" in xml
    for part in ("<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
                 "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
                 "<LogonType>InteractiveToken</LogonType>", "<RunLevel>LeastPrivilege</RunLevel>",
                 "<Command>C:\\Py\\pythonw.exe</Command>"):
        assert part in xml, part
    assert '--home "C:\\a &amp; b\\&lt;home&gt;"' in xml, "every value XML-escaped"


def test_windowless_is_pythonw_beside_python(tmp_path, monkeypatch):
    python, pythonw = tmp_path / "python.exe", tmp_path / "pythonw.exe"
    python.write_bytes(b"")
    monkeypatch.setattr(sys, "platform", "win32")
    assert service.windowless(str(python)) == str(python), "no pythonw.exe beside it"
    pythonw.write_bytes(b"")
    assert service.windowless(str(python)) == str(pythonw)
    monkeypatch.setattr(sys, "platform", "linux")
    assert service.windowless(str(python)) == str(python)


def test_install_startup_reports_a_refusal(tmp_path, capsys):
    home = tmp_path / "my home"
    home.mkdir()
    code = service.install_startup(home, platform="win32", run=_done(1, "", "ERROR: Access is denied.\n"),
                                   python=r"C:\Py\python.exe")
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR: Access is denied." in err
    assert "you can also start it yourself: " + subprocess.list2cmdline(
        [r"C:\Py\python.exe", "-m", "evolvekit", "app", "--service", "--home", str(home)]) in err


@pytest.mark.parametrize("platform, path", [("darwin", "~/Library/LaunchAgents/evolvekit.app.plist"),
                                            ("linux", "~/.config/systemd/user/evolvekit-app.service")])
def test_install_startup_other_platforms_print_unit(tmp_path, platform, path):
    printed = []
    assert service.install_startup(tmp_path, platform=platform, run=_never, out=printed.append) == 0
    text = "\n".join(printed)
    assert path in text and "--service" in text and str(tmp_path) in text


def test_the_plist_and_the_unit(tmp_path):
    printed = []
    service.install_startup(tmp_path, platform="darwin", run=_never, out=printed.append, python="/usr/bin/python3")
    plist = "\n".join(printed)
    assert "<string>evolvekit.app</string>" in plist and "<key>RunAtLoad</key>" in plist and "<true/>" in plist
    assert ("save it as ~/Library/LaunchAgents/evolvekit.app.plist, then run: "
            "launchctl load ~/Library/LaunchAgents/evolvekit.app.plist") in printed
    printed.clear()
    service.install_startup(tmp_path / "a b", platform="linux", run=_never, out=printed.append, python="/usr/bin/python3")
    unit = "\n".join(printed)
    assert "Description=evolvekit app" in unit and "Restart=on-failure" in unit and "WantedBy=default.target" in unit
    assert f"ExecStart=/usr/bin/python3 -m evolvekit app --service --home '{tmp_path / 'a b'}'" in unit
    assert ("save it as ~/.config/systemd/user/evolvekit-app.service, then run: "
            "systemctl --user enable --now evolvekit-app") in printed


def test_uninstall_startup(tmp_path):
    calls, printed = [], []
    run = lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")  # noqa: E731
    assert service.uninstall_startup(tmp_path, platform="win32", run=run, out=printed.append) == 0
    assert calls == [["schtasks", "/Delete", "/TN", "evolvekit app", "/F"]]
    for platform, words in (("darwin", "launchctl unload ~/Library/LaunchAgents/evolvekit.app.plist"),
                            ("linux", "systemctl --user disable --now evolvekit-app")):
        printed.clear()
        assert service.uninstall_startup(tmp_path, platform=platform, run=_never, out=printed.append) == 0
        text = "\n".join(printed)
        assert words in text and "rm " in text


def test_python_for_runs_is_never_pythonw(tmp_path, monkeypatch):
    (tmp_path / "python.exe").write_bytes(b"")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    assert detach.python_for_runs() == str(tmp_path / "python.exe")


def test_runs_are_launched_with_python_for_runs(tmp_path, monkeypatch):
    from evolvekit.app import jobs

    seen = []
    monkeypatch.setattr(detach, "python_for_runs", lambda: "the-python")
    monkeypatch.setattr(jobs, "_spawn", lambda env, argv, root, run_dir: seen.append(argv) or 4242)
    run_dir = tmp_path / "study" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    jobs._launch(Home(tmp_path / "home"), tmp_path / "study", run_dir, [], with_keys=False)
    assert seen[0][0] == "the-python"


def test_a_second_app_on_the_same_home_opens_the_first(tmp_path, capsys):
    home = tmp_path / "my home"
    home.mkdir()
    write_running(home, pid=os.getpid(), port=8790, url="http://127.0.0.1:8790/?token=k")
    assert main(["app", "--home", str(home), "--no-browser"]) == 0
    assert capsys.readouterr().out.strip() == "the app is already running for this library: http://127.0.0.1:8790/?token=k"


def test_a_second_service_on_the_same_home_exits_quietly(tmp_path, capsys):
    home = tmp_path / "my home"
    home.mkdir()
    write_running(home, pid=os.getpid(), port=8790, url="http://127.0.0.1:8790/?token=k")
    out, err = sys.stdout, sys.stderr
    try:
        assert main(["app", "--service", "--home", str(home)]) == 0
    finally:
        sys.stdout, sys.stderr = out, err
    assert capsys.readouterr().out == ""
    log = (home / "app.log").read_text(encoding="utf-8")
    assert "already running at http://127.0.0.1:8790/ (the key is in app.json)" in log and "token=k" not in log


def test_clock_flag_writes_and_the_home_reads_it(tmp_path):
    home = Home(tmp_path / "my home")
    scheduler.write_clock(home.root, datetime(2026, 11, 13, 17, 30).astimezone(), 60.0)
    assert scheduler.clock_of(home).start is None, "no app runs: the real clock"
    write_running(home.root, pid=os.getpid(), port=1, url="u")
    clock = scheduler.clock_of(home)
    assert clock.simulated and clock.speed == 60.0 and clock.words().startswith("rehearsal clock: Fri 13 Nov 17:30")
    assert main(["app", "--home", str(home.root), "--clock-speed", "60"]) == 2


def test_clock_speed_out_of_range_and_a_bad_clock(tmp_path, capsys):
    home = tmp_path / "my home"
    assert main(["app", "--home", str(home), "--clock", "2026-11-13T17:30", "--clock-speed", "4000"]) == 2
    assert main(["app", "--home", str(home), "--clock", "Friday"]) == 2
    assert not (home / "clock.json").exists()


def test_the_home_says_the_rehearsal_clock(tmp_path):
    server = AppServer(tmp_path / "my home", port=0)
    server.start()
    try:
        assert "clock" not in call(server, "GET", "/api/home")[1]
        scheduler.write_clock(server.home.root, local("2026-11-13 17:30"), 60.0)
        line = call(server, "GET", "/api/home")[1]["clock"]["line"]
        assert line.startswith("rehearsal clock: Fri 13 Nov 17:3") and line.endswith("60 × real time")
    finally:
        server.stop()


def test_study_open_starts_the_service(tmp_path, monkeypatch, capsys):
    from evolvekit import cli

    home = tmp_path / "my home"
    assert main(["study", "new", "Depot A", "--harness", "demo-tour", "--home", str(home)]) == 0
    capsys.readouterr()
    seen = []
    monkeypatch.setattr(detach, "spawn_detached", lambda argv, **kwargs: seen.append((list(argv), kwargs)) or 4242)
    monkeypatch.setattr(detach, "python_for_runs", lambda: "the-python")
    monkeypatch.setattr(cli, "OPEN_WAIT_S", 0.5)
    assert main(["study", "open", "depot-a", "--no-browser", "--home", str(home)]) == 1  # nothing really started
    argv, kwargs = seen[0]
    assert argv == ["the-python", "-m", "evolvekit", "app", "--home", str(home.resolve()), "--service"]
    assert kwargs["log"] == home.resolve() / "app-start.log"


def test_app_stop_shuts_the_service_down(tmp_path):
    home = tmp_path / "my home"
    proc = subprocess.Popen([sys.executable, "-m", "evolvekit", "app", "--service", "--home", str(home), "--port", "0",
                             "--clock", "2026-11-13T17:30", "--clock-speed", "120"], stdin=subprocess.DEVNULL)
    try:
        _until(lambda: read_running(home), timeout=30)
        assert (home / "clock.json").is_file()
        assert main(["app", "stop", "--home", str(home)]) == 0
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    assert not (home / "app.json").exists() and not (home / "clock.json").exists()


def test_shutdown_needs_the_key(tmp_path):
    server = AppServer(tmp_path / "my home", port=0)
    server.start()
    try:
        assert call(server, "POST", "/api/shutdown", token=None)[0] == 403
    finally:
        server.stop()


def test_shutdown_returns_serve_forever(tmp_path):
    import threading

    server = AppServer(tmp_path / "my home", port=0)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    try:
        assert call(server, "POST", "/api/shutdown") == (200, {"stopping": True})
        serving.join(timeout=10)
        assert not serving.is_alive()
    finally:
        server.stop()
    assert read_running(server.home.root) is None


def test_app_stop_with_no_app(tmp_path, capsys):
    assert main(["app", "stop", "--home", str(tmp_path / "my home")]) == 0
    assert capsys.readouterr().out.strip() == "no app is running for this library"


def test_a_stale_clock_is_removed(tmp_path):
    home = Home(tmp_path / "my home")
    scheduler.write_clock(home.root, local("2026-11-13 17:30"), 60.0)
    write_running(home.root, pid=2 ** 22 + 7, port=1, url="u")      # a pid that is gone
    assert not scheduler.clock_of(home).simulated and not (home.root / "clock.json").exists()


# -- fix round 1: the log never breaks the service, and is its owner's alone


def test_a_held_log_never_breaks_printing(tmp_path, monkeypatch):
    """Windows refuses to rename a file another handle holds: the rotation waits, no line is lost,
    and the log's own errors never go back into the log (they once recursed until RecursionError)."""
    monkeypatch.setattr(service, "LOG_BYTES", 500)
    out, err = sys.stdout, sys.stderr
    logger = service.start_logging(tmp_path, redirect=True)
    holder = open(tmp_path / "app.log", "a", encoding="utf-8")   # another handle, as a viewer holds it
    said, failures = [], []

    def ticker():
        try:
            for i in range(120):
                print(f"tick line {i} {'x' * 40}", flush=True)
                said.append(i)
        except BaseException as exc:  # noqa: BLE001 - the test says what ended it
            failures.append(exc)

    try:
        thread = threading.Thread(target=ticker)
        thread.start()
        thread.join(timeout=30)
        print("Traceback (most recent call last):", file=sys.stderr)
    finally:
        holder.close()
        service.stop_logging(logger)
        sys.stdout, sys.stderr = out, err
    assert not failures and len(said) == 120, failures
    text = "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir() if p.name.startswith("app.log"))
    assert "tick line 119 " in text and "Traceback (most recent call last):" in text


def test_an_emit_error_is_not_logged_again(tmp_path):
    out, err = sys.stdout, sys.stderr
    logger = service.start_logging(tmp_path, redirect=True)
    handler = logger.handlers[0]
    try:
        def broken(record):
            raise OSError("the disk is full")

        handler.stream.close()
        handler.stream = type("Broken", (), {"write": broken, "flush": lambda self: None, "close": lambda self: None})()
        print("this line cannot be written")          # handleError goes to the real stderr, not the log
        print("nor this one", file=sys.stderr)
    finally:
        service.stop_logging(logger)
        sys.stdout, sys.stderr = out, err


def test_lines_of_two_threads_never_merge(tmp_path):
    out = sys.stdout
    logger = service.start_logging(tmp_path, redirect=True)
    writer = sys.stdout
    start = threading.Barrier(2)

    def write(name):
        start.wait()
        for i in range(200):
            writer.write(f"{name}-{i}")
            writer.write("\n")

    try:
        threads = [threading.Thread(target=write, args=(name,)) for name in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        service.stop_logging(logger)
        sys.stdout = out
    lines = [line.split(" ", 2)[-1] for line in (tmp_path / "app.log").read_text(encoding="utf-8").splitlines()]
    assert sorted(lines) == sorted([f"{n}-{i}" for n in ("a", "b") for i in range(200)])


@pytest.mark.skipif(os.name != "posix", reason="Windows has no mode bits to set")
def test_the_log_is_private_to_the_user(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "LOG_BYTES", 300)
    logger = service.start_logging(tmp_path)
    try:
        for i in range(40):
            logger.info("line %d %s", i, "x" * 40)
    finally:
        service.stop_logging(logger)
    for path in tmp_path.iterdir():
        if path.name.startswith("app.log"):
            assert path.stat().st_mode & 0o777 == 0o600, path.name


def test_the_address_without_its_key():
    assert service.address("http://127.0.0.1:8790/?token=k") == "http://127.0.0.1:8790/"


def test_a_clock_for_an_app_already_running_is_said_not_set(tmp_path, capsys):
    home = tmp_path / "my home"
    home.mkdir()
    write_running(home, pid=os.getpid(), port=8790, url="http://127.0.0.1:8790/?token=k")
    assert main(["app", "--home", str(home), "--no-browser", "--clock", "2026-11-13T17:30"]) == 0
    said = capsys.readouterr()
    assert said.out.strip() == "the app is already running for this library: http://127.0.0.1:8790/?token=k"
    assert "the rehearsal clock was not set" in said.err and not (home / "clock.json").exists()
