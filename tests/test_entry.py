import json, os, subprocess, sys, types
from pathlib import Path

import pytest

# Loaded here, unpatched: a module first imported while a test fakes a bundle
# would keep its package-data paths (server.PAGE, dashboard.PAGE) in that bundle.
import evolvekit.app.server  # noqa: F401
import evolvekit.cli  # noqa: F401
import evolvekit.dashboard  # noqa: F401
from evolvekit import cli, resources
from evolvekit.app import entry, service
from evolvekit.app.running import write_running
from evolvekit.app.server import AppServer
from evolvekit.harness.library import default_home
from test_resources import fake_bundle, fake_profile


class _NeverStarts:
    def __init__(self, *args, **kwargs):
        pytest.fail("a second app was started")


def _snapshot(folder: Path) -> dict[str, tuple[bool, int, int]]:
    """Each path under `folder` (relative, posix) -> (is_dir, size, mtime_ns)."""
    snap = {}
    for path in sorted(folder.rglob("*")):
        stat = path.stat()
        snap[path.relative_to(folder).as_posix()] = (path.is_dir(), 0 if path.is_dir() else stat.st_size, stat.st_mtime_ns)
    return snap


@pytest.fixture(autouse=True)
def _restored(tmp_path, monkeypatch):
    """Dispatching changes sys.argv, sys.path, sys.dont_write_bytecode and the working folder: all put back."""
    monkeypatch.setattr(sys, "argv", ["entry"])
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.append(list(argv)) or 0)
    return seen

def test_entry_no_args_runs_app(tmp_path, monkeypatch, calls):
    fake_bundle(monkeypatch, tmp_path / "evolvekit app"); fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    assert entry.main([]) == 0
    assert calls == [["app"]]

@pytest.mark.parametrize("argv, expected", [
    (["doctor", "--json"], ["doctor", "--json"]),
    (["study", "list"], ["study", "list"]),
    (["--version"], ["--version"]),
    (["app", "stop"], ["app", "stop"]),
    (["stop"], ["app", "stop"]),                                   # bare, on evolvekit-app.exe: the app's stop
    (["stop", "--run-dir", "RUN"], ["stop", "--run-dir", "RUN"]),  # with arguments: the CLI's own stop
    (["install-startup"], ["app", "install-startup"]),
    (["--no-browser"], ["app", "--no-browser"]),
    (["--service", "--home", "HOME"], ["app", "--service", "--home", "HOME"]),
    (["--clock", "2026-11-13T17:30", "--clock-speed", "120"], ["app", "--clock", "2026-11-13T17:30", "--clock-speed", "120"]),
])
def test_entry_passthrough(tmp_path, monkeypatch, calls, argv, expected):
    home, run = str(tmp_path / "my home ë"), str(tmp_path / "runs" / "r1")
    fake_bundle(monkeypatch, tmp_path / "evolvekit app"); fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    swap = lambda items: [{"HOME": home, "RUN": run}.get(a, a) for a in items]
    assert entry.main(swap(argv)) == 0
    assert calls == [swap(expected)]

def test_entry_stop_on_the_cli_exe_is_the_clis_own(tmp_path, monkeypatch, calls):
    fake_bundle(monkeypatch, tmp_path / "evolvekit app", exe=resources.CLI_EXE)
    monkeypatch.chdir(tmp_path)
    assert entry.main(["stop"]) == 0
    assert calls == [["stop"]]

@pytest.mark.parametrize("argv", [
    ["skill", "install"], ["init"], ["run"], ["stop"], ["confirm"], ["export", "--out", "best.json"],
    ["dashboard", "--export", "board.html"], ["harness", "new", "mine"],
    ["study", "new", "Depot", "--harness", "demo-tour", "--dir", "here"],
    ["harness", "pack", "_internal/harnesses/demo-tour"],
    # fix round 1: the rest of what writes relative to the working folder
    ["harness", "pack", "OUTSIDE", "--out", "."], ["leaderboard"],
    ["leaderboard", "--run-dir", "OUTSIDE", "--html", "board.html"],
    ["study", "run", "_internal"], ["study", "compile", "_internal"], ["study", "preview", "_internal"],
    ["study", "list", "--home", "here"], ["study", "export", "depot", "--diff", "--home=."],
    ["harness", "install", "x.zip", "--home", "lib"], ["doctor", "--home", "."], ["app", "--home", "."],
    ["init", "OUTSIDE/../evolvekit app"],                        # an absolute path into the bundle
])
def test_cli_refuses_to_write_into_the_bundle(tmp_path, monkeypatch, capsys, calls, argv):
    bundle = fake_bundle(monkeypatch, tmp_path / "Zoë Intern" / "evolvekit app", exe=resources.CLI_EXE)
    monkeypatch.chdir(bundle)
    argv = [a.replace("OUTSIDE", str(tmp_path / "Zoë Intern" / "elsewhere")) for a in argv]
    assert entry.main(argv) == 2 and calls == []
    assert capsys.readouterr().err.strip() == ("error: this is the app's own folder: run evolvekit-cli.exe from a "
                                               "folder of yours (its full path works from anywhere)")

def test_cli_writes_outside_the_bundle_are_allowed(tmp_path, monkeypatch, calls):
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app", exe=resources.CLI_EXE)
    monkeypatch.chdir(bundle)
    assert entry.main(["skill", "install", "--dir", str(tmp_path / "project")]) == 0   # an explicit folder elsewhere
    assert entry.main(["study", "list"]) == 0 and entry.main(["doctor"]) == 0          # read only
    mine = tmp_path / "my work"; mine.mkdir(); monkeypatch.chdir(mine)
    assert entry.main(["init"]) == 0 and entry.main(["run"]) == 0                       # relative to a folder of yours
    assert calls == [["skill", "install", "--dir", str(tmp_path / "project")], ["study", "list"], ["doctor"], ["init"], ["run"]]

@pytest.mark.parametrize("argv", [
    ["preflight"], ["status"], ["export"], ["dashboard"], ["harness", "check", "_internal/harnesses/demo-tour"],
    ["study", "run", "depot"], ["study", "compile", "depot", "--home", "HOME"],   # a study's name: in the home
    ["harness", "pack", "_internal/harnesses/demo-tour", "--out", "ZIPS"],         # --out given: only it counts
    ["init", "-h"], ["skill", "install", "--help"],                                # help prints, writes nothing
    ["study", "list", "--home", "HOME"],
])
def test_cli_reads_and_outside_writes_run_in_the_bundle(tmp_path, monkeypatch, calls, argv):
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app", exe=resources.CLI_EXE)
    monkeypatch.chdir(bundle)
    argv = [{"HOME": str(tmp_path / "home"), "ZIPS": str(tmp_path / "zips")}.get(a, a) for a in argv]
    assert entry.main(argv) == 0 and calls == [argv]

def test_confirm_without_seeds_is_judged_by_its_run_dir(tmp_path, monkeypatch, calls):
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app", exe=resources.CLI_EXE)
    monkeypatch.chdir(bundle)
    assert entry.main(["confirm"]) == 2 and calls == []          # runs/latest would be in the bundle
    monkeypatch.chdir(tmp_path)
    assert entry.main(["confirm"]) == 0 and calls == [["confirm"]]  # cli.main then says --seeds is missing

def test_a_failing_relaxed_parser_refuses_only_inside_the_bundle(tmp_path, monkeypatch, calls):
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app", exe=resources.CLI_EXE)
    def broken(parser):
        raise AttributeError("_actions")
    monkeypatch.setattr(entry, "_relaxed", broken)
    monkeypatch.chdir(bundle)
    assert entry.main(["init", str(tmp_path / "mine")]) == 2 and calls == []
    monkeypatch.chdir(tmp_path)
    assert entry.main(["init"]) == 0 and calls == [["init"]]

def test_writes_into_bundle_is_false_unfrozen(tmp_path):
    assert resources.is_frozen() is False
    assert entry.writes_into_bundle(["init"]) is False and entry.writes_into_bundle(["run"]) is False

@pytest.mark.parametrize("given", ["--home", "--home=", "--hom"])
def test_a_relative_home_is_made_absolute_once(tmp_path, monkeypatch, calls, given):
    fake_bundle(monkeypatch, tmp_path / "evolvekit app"); fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    home = (tmp_path / "rel ë").resolve()
    argv = [given + "rel ë"] if given.endswith("=") else [given, "rel ë"]
    assert entry.main([*argv, "--no-browser"]) == 0
    expected = [given + str(home)] if given.endswith("=") else [given, str(home)]
    assert calls == [["app", *expected, "--no-browser"]]
    assert os.path.samefile(Path.cwd(), home) and not (home / "rel ë").exists()

def test_a_relative_home_is_served_where_it_was_made(tmp_path, monkeypatch):
    fake_bundle(monkeypatch, tmp_path / "evolvekit app"); fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    served = []
    monkeypatch.setattr(AppServer, "serve_forever", lambda self: served.append(self.home.root))
    monkeypatch.setattr("evolvekit.dashboard.open_in_browser", lambda url: None)
    assert entry.main(["--home", "rel", "--port", "8794"]) == 0
    assert len(served) == 1 and os.path.samefile(served[0], tmp_path / "rel")
    assert not (tmp_path / "rel" / "rel").exists()

@pytest.mark.parametrize("windowless", [True, False])
def test_a_home_that_cannot_be_made_is_said(tmp_path, monkeypatch, capsys, calls, windowless):
    fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    (tmp_path / "a file").write_text("", encoding="utf-8")
    boxes = []
    monkeypatch.setattr(entry, "_message_box", boxes.append)
    if windowless:
        monkeypatch.setattr(sys, "stdout", None); monkeypatch.setattr(sys, "stderr", None)
    assert entry.main(["--home", str(tmp_path / "a file" / "home")]) == 1 and calls == []
    said = boxes if windowless else [capsys.readouterr().err.strip()]
    assert len(said) == 1 and said[0].startswith(f"error: the library home {(tmp_path / 'a file' / 'home').resolve()} "
                                                 "cannot be used: ")
    assert boxes == [] or windowless

@pytest.mark.skipif(sys.platform != "win32", reason="a Windows message box")
def test_message_box_is_messageboxw(monkeypatch):
    import ctypes
    shown = []
    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", lambda *a: shown.append(a) or 1)
    entry._message_box("the reason")
    assert shown == [(None, "the reason", "evolvekit", 0x10)]

def test_entry_m_runs_a_module(tmp_path, monkeypatch):
    (tmp_path / "ek_probe_mod.py").write_text(
        "import json, sys\nopen(sys.argv[-1], 'w', encoding='utf-8').write(json.dumps([__name__, sys.argv[1:]]))\n", encoding="utf-8")
    (tmp_path / "ek_exit_mod.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    out = tmp_path / "out ë.json"
    assert entry.main(["-m", "ek_probe_mod", "a b", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == ["__main__", ["a b", str(out)]]
    with pytest.raises(SystemExit) as stopped:
        entry.main(["-m", "ek_exit_mod"])
    assert stopped.value.code == 3
    assert entry.main(["-m"]) == 2

def test_entry_c_runs_code(tmp_path, monkeypatch):
    out = tmp_path / "c.json"
    code = "import json, sys; open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps([__name__, sys.argv]))"
    assert entry.main(["-c", code, str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == ["__main__", ["-c", str(out)]]

@pytest.mark.parametrize("form", ["-m", "-c"])
def test_m_and_c_import_from_the_current_folder(tmp_path, monkeypatch, form):
    name = "ek_local_" + form[1]
    (tmp_path / f"{name}.py").write_text("import sys\nsys.ek_seen = sys.path[0]\n", encoding="utf-8")
    monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(sys, "ek_seen", None, raising=False)
    assert entry.main(["-m", name] if form == "-m" else ["-c", f"import {name}"]) == 0
    assert sys.ek_seen == (os.getcwd() if form == "-m" else "")
    sys.modules.pop(name, None)

@pytest.mark.parametrize("form", [["-c"], ["--script"], ["-u"], ["-X"], ["-B", "doctor"]])
def test_python_forms_without_their_argument_are_usage(tmp_path, monkeypatch, capsys, calls, form):
    assert entry.main(form) == 2 and calls == []
    assert capsys.readouterr().err.startswith("usage: ")

@pytest.mark.parametrize("flags", [["-u"], ["-B"], ["-uB"], ["-X", "utf8"], ["-Xutf8"], ["-W", "ignore"],
                                   ["-u", "-X", "utf8", "-W", "ignore", "-B"]])
def test_pythons_own_flags_are_ignored(tmp_path, monkeypatch, flags):
    out = tmp_path / "flags.json"
    code = "import json, sys; open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps(sys.argv))"
    assert entry.main([*flags, "-c", code, str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == ["-c", str(out)]

@pytest.mark.parametrize("argv", [["-"], ["-u", "-"], ["-", "x"]])
def test_a_program_from_stdin_is_refused(tmp_path, monkeypatch, capsys, calls, argv):
    assert entry.main(argv) == 2 and calls == []
    assert capsys.readouterr().err.strip() == "evolvekit-cli.exe cannot read a program from its input; give a file"

@pytest.mark.parametrize("code, exit_code", [("raise SystemExit('msg')", "msg"), ("raise SystemExit(None)", None),
                                             ("raise SystemExit(4)", 4)])
def test_system_exit_passes_through(tmp_path, monkeypatch, code, exit_code):
    with pytest.raises(SystemExit) as stopped:
        entry.main(["-c", code])
    assert stopped.value.code == exit_code

@pytest.mark.parametrize("code, exit_code, said", [("raise SystemExit('msg')", 1, "msg"),
                                                   ("raise SystemExit(None)", 0, ""),
                                                   ("raise ValueError('boom ë')", 1, "Traceback")])
def test_the_exit_code_is_pythons(tmp_path, code, exit_code, said):
    import subprocess
    checkout = Path(entry.__file__).resolve().parents[2]
    done = subprocess.run([sys.executable, "-m", "evolvekit.app.entry", "-c", code], cwd=checkout,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert done.returncode == exit_code
    assert said in done.stderr and ("ValueError: boom" in done.stderr if said == "Traceback" else True)

@pytest.mark.parametrize("form, name", [(["--script"], "run me.py"), ([], "run me.py"), ([], "RUN ME.PY")])
def test_entry_script_runs_a_file(tmp_path, monkeypatch, form, name):
    folder = tmp_path / "my scripts ë"; folder.mkdir()
    (folder / "helper_ek.py").write_text("WORD = 'beside'\n", encoding="utf-8")
    script = folder / name
    script.write_text("import json, sys, helper_ek\n"
                      "open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps([__name__, helper_ek.WORD, sys.argv]))\n",
                      encoding="utf-8")
    monkeypatch.delitem(sys.modules, "helper_ek", raising=False)
    out = tmp_path / "out.json"
    assert entry.main([*form, str(script), str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == ["__main__", "beside", [str(script), str(out)]]

def test_entry_gives_a_windowless_process_streams(tmp_path, monkeypatch):
    fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    monkeypatch.setattr(sys, "stdout", None); monkeypatch.setattr(sys, "stderr", None)
    seen = []
    monkeypatch.setattr(cli, "main", lambda argv=None: (seen.extend([sys.stdout, sys.stderr]), sys.stdout.write("x"),
                                                        sys.stdout.flush(), sys.stderr.flush(), 0)[-1])
    assert entry.main(["study", "list"]) == 0
    assert sys.dont_write_bytecode is True
    assert len(seen) == 2 and all(handle.closed for handle in seen)   # the devnull handles are closed after
    assert sys.stdout is None and sys.stderr is None

def test_second_start_opens_running_app(tmp_path, monkeypatch):
    fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    home = fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern") / "evolvekit"
    write_running(home, pid=os.getpid(), port=8790, url="http://127.0.0.1:8790/?token=k")
    opened = []
    monkeypatch.setattr("evolvekit.dashboard.open_in_browser", opened.append)
    monkeypatch.setattr("evolvekit.app.server.AppServer", _NeverStarts)
    assert entry.main([]) == 0
    assert opened == ["http://127.0.0.1:8790/?token=k"]

def test_home_is_under_profile_when_frozen(tmp_path, monkeypatch):
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    profile = fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    monkeypatch.chdir(bundle)                                   # a double-click starts in the app's folder
    seen = {}
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.update(cwd=Path.cwd(), home=default_home()) or 0)
    assert entry.main([]) == 0
    assert os.path.samefile(seen["home"], profile / "evolvekit") and os.path.samefile(seen["cwd"], profile / "evolvekit")
    assert bundle.resolve() not in seen["home"].resolve().parents

def test_bundle_folder_holds_no_state(tmp_path, monkeypatch, capsys):
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app", harnesses=("demo-tour",), skills=True)
    profile = fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    monkeypatch.chdir(bundle)
    before = _snapshot(bundle)
    wrote = []
    monkeypatch.setattr("evolvekit.dashboard.open_in_browser", lambda url: None)
    monkeypatch.setattr(AppServer, "serve_forever", lambda self: wrote.append((profile / "evolvekit" / "app.json").is_file()))
    assert entry.main(["harness", "list"]) == 0
    assert entry.main(["study", "new", "Zoë's depot", "--harness", "demo-tour", "--template", "tune"]) == 0
    assert entry.main(["skill", "install", "--dir", str(tmp_path / "project")]) == 0
    assert entry.main(["doctor", "--json"]) in (0, 1)
    assert entry.main([]) == 0 and wrote == [True]   # the app ran, its app.json in the home (cmd_app reads port 0 as the default)
    assert _snapshot(bundle) == before
    assert (profile / "evolvekit" / "studies").is_dir() and not (profile / "evolvekit" / "app.json").exists()

@pytest.mark.parametrize("windowless", [True, False])
@pytest.mark.parametrize("given", [["--home", "."], ["--home", "_internal"], ["--home=."]])
def test_the_app_form_refuses_a_home_in_the_bundle(tmp_path, monkeypatch, capsys, calls, windowless, given):
    bundle = fake_bundle(monkeypatch, tmp_path / "Zoë Intern" / "evolvekit app")
    monkeypatch.chdir(bundle)                                   # `evolvekit-app.exe --home .` started there
    before = _snapshot(bundle)
    boxes = []
    monkeypatch.setattr(entry, "_message_box", boxes.append)
    if windowless:
        monkeypatch.setattr(sys, "stdout", None); monkeypatch.setattr(sys, "stderr", None)
    assert entry.main([*given, "--no-browser"]) == 2 and calls == []
    said = boxes if windowless else [capsys.readouterr().err.strip()]
    home = (bundle / ("_internal" if "_internal" in given else "")).resolve()
    assert said == [_home_in_bundle(home)]
    assert boxes == [] or windowless
    assert _snapshot(bundle) == before and os.path.samefile(Path.cwd(), bundle)


def _home_in_bundle(home: Path) -> str:
    return (f"The library home {home} is inside the app's own folder. Move the app's folder somewhere else "
            "(for example to Downloads or Desktop), or start it with --home <a folder of yours>.")


@pytest.mark.parametrize("windowless", [True, False])
def test_the_app_unzipped_as_the_default_home_is_refused(tmp_path, monkeypatch, capsys, calls, windowless):
    profile = fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    bundle = fake_bundle(monkeypatch, profile / "evolvekit")        # unzipped as %USERPROFILE%\evolvekit
    monkeypatch.chdir(bundle)                                       # a double-click
    before = _snapshot(bundle)
    boxes = []
    monkeypatch.setattr(entry, "_message_box", boxes.append)
    if windowless:
        monkeypatch.setattr(sys, "stdout", None); monkeypatch.setattr(sys, "stderr", None)
    assert entry.main([]) == 2 and calls == []
    said = boxes if windowless else [capsys.readouterr().err.strip()]
    assert said == [_home_in_bundle(default_home())]
    assert boxes == [] or windowless
    assert _snapshot(bundle) == before

def test_an_evolvekit_home_in_the_bundle_is_refused(tmp_path, monkeypatch, capsys, calls):
    fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    monkeypatch.setenv("EVOLVEKIT_HOME", str(bundle / "library"))
    before = _snapshot(bundle)
    assert entry.main(["--no-browser"]) == 2 and calls == []
    assert capsys.readouterr().err.strip() == _home_in_bundle((bundle / "library").resolve())
    assert _snapshot(bundle) == before and not (bundle / "library").exists()

def test_pythons_forms_never_import_the_cli(tmp_path):
    checkout = Path(entry.__file__).resolve().parents[2]
    code = ("import sys; from evolvekit.app import entry; entry.main(['-c', 'pass']); "
            "print('evolvekit.cli' in sys.modules)")
    done = subprocess.run([sys.executable, "-c", code], cwd=checkout, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.split() == ["False"], done.stderr

def test_install_startup_names_the_exe_when_frozen(tmp_path, monkeypatch):
    bundle = fake_bundle(monkeypatch, tmp_path / "Zoë Intern" / "evolvekit app")
    home = tmp_path / "Zoë Intern" / "evolvekit"; home.mkdir(parents=True)
    ok = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", "")
    assert service.install_startup(home, platform="win32", run=ok, out=lambda line: None, user="PC\\zoe") == 0
    xml = (home / service.TASK_XML).read_bytes().decode("utf-16")
    assert f"<Command>{bundle / resources.APP_EXE}</Command>" in xml
    assert f'<Arguments>--service --home "{home}"</Arguments>' in xml

def test_study_open_starts_the_app_exe_when_frozen(tmp_path, monkeypatch, capsys):
    home = tmp_path / "my home"
    assert cli.main(["study", "new", "Depot", "--harness", "demo-tour", "--home", str(home)]) == 0  # before faking: the checkout's demo-tour
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    started = []
    monkeypatch.setattr("evolvekit.app.detach.spawn_detached", lambda argv, **kw: started.append(argv) or 1)
    monkeypatch.setattr(cli, "OPEN_WAIT_S", 0.2)
    assert cli.main(["study", "open", "depot", "--no-browser", "--home", str(home)]) == 1   # nothing really starts
    assert started == [[str(bundle / resources.APP_EXE), "--home", str(home.resolve()), "--service"]]

def test_jobs_launch_runs_the_cli_exe_when_frozen(tmp_path, monkeypatch):
    from evolvekit.app import jobs
    from evolvekit.app.store import Home
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    seen = []
    monkeypatch.setattr(jobs, "_spawn", lambda env, argv, root, run_dir: seen.append(argv) or 4242)
    run_dir = tmp_path / "study" / "runs" / "r1"; run_dir.mkdir(parents=True)
    jobs._launch(Home(tmp_path / "home"), tmp_path / "study", run_dir, [], with_keys=False)
    assert seen[0][:3] == [str(bundle / resources.CLI_EXE), "-m", "evolvekit.app.job"]

@pytest.mark.parametrize("frozen", [False, True])
def test_shortcut_is_crlf_utf8_with_chcp(tmp_path, monkeypatch, capsys, frozen):
    windows = types.ModuleType("os"); windows.__dict__.update(os.__dict__); windows.name = "nt"
    monkeypatch.setattr(cli, "os", windows)        # never os.name itself: pathlib picks its flavour from it (D38)
    monkeypatch.setattr(cli, "_desktop", lambda: tmp_path)
    if frozen:
        bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    home = tmp_path / "Zoë Intern" / "evolvekit"
    assert cli.main(["app", "--shortcut", "--home", str(home)]) == 0
    raw = (tmp_path / "evolvekit.cmd").read_bytes()
    lines = raw.decode("utf-8").split("\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"") and lines[1] == "chcp 65001 >nul"
    if frozen:
        assert f'start "" "{bundle / resources.APP_EXE}" --home "{home.resolve()}"' in lines
    else:
        assert f'"{sys.executable}" -m evolvekit app --home "{home.resolve()}"' in lines and "pause" in lines
