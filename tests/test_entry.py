import json, os, sys
from pathlib import Path

import pytest

from evolvekit import cli, resources
from evolvekit.app import entry
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
])
def test_cli_refuses_to_write_into_the_bundle(tmp_path, monkeypatch, capsys, calls, argv):
    bundle = fake_bundle(monkeypatch, tmp_path / "Zoë Intern" / "evolvekit app", exe=resources.CLI_EXE)
    monkeypatch.chdir(bundle)
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

@pytest.mark.parametrize("form", [["--script"], []])
def test_entry_script_runs_a_file(tmp_path, monkeypatch, form):
    folder = tmp_path / "my scripts ë"; folder.mkdir()
    (folder / "helper_ek.py").write_text("WORD = 'beside'\n", encoding="utf-8")
    script = folder / "run me.py"
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
    monkeypatch.setattr(cli, "main", lambda argv=None: (sys.stdout.write("x"), sys.stdout.flush(), 0)[-1])
    assert entry.main(["study", "list"]) == 0
    assert sys.dont_write_bytecode is True

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
