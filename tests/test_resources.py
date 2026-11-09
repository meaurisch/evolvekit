import ast, json, os, shutil, sys
from pathlib import Path

import evolvekit
from evolvekit import resources
from evolvekit.cli import main
from evolvekit.harness.library import default_home, find_harnesses, install_harness, pack_harness

PACKAGE = Path(evolvekit.__file__).resolve().parent
CHECKOUT = PACKAGE.parent

def _uses(predicate) -> list[str]:
    """`file:line` of every AST node in the package that `predicate` accepts (string literals are not code)."""
    hits = []
    for source in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        hits += [f"{source.relative_to(PACKAGE).as_posix()}:{node.lineno}" for node in ast.walk(tree) if predicate(node)]
    return hits


def fake_bundle(monkeypatch, root: Path, *, harnesses: tuple[str, ...] = (), skills: bool = False, exe: str | None = None) -> Path:
    """A PyInstaller one-folder bundle laid out under `root`, and this process made to look like it runs from it."""
    from evolvekit.harness.library import _IGNORED
    internal = root / "_internal"
    internal.mkdir(parents=True)
    (root / resources.APP_EXE).write_bytes(b"")
    (root / resources.CLI_EXE).write_bytes(b"")
    for name in harnesses:
        shutil.copytree(CHECKOUT / "harnesses" / name, internal / "harnesses" / name, ignore=_IGNORED)
    if skills:
        shutil.copytree(PACKAGE / "skills", internal / "evolvekit" / "skills", ignore=_IGNORED)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(sys, "executable", str(root / (exe or resources.APP_EXE)))
    return root


def fake_profile(monkeypatch, root: Path) -> Path:
    """A throwaway user profile: home and app-data folders under `root`, no EVOLVEKIT_HOME."""
    root.mkdir(parents=True, exist_ok=True)
    for name in ("USERPROFILE", "HOME"):
        monkeypatch.setenv(name, str(root))
    monkeypatch.setenv("APPDATA", str(root / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(root / "AppData" / "Local"))
    monkeypatch.delenv("EVOLVEKIT_HOME", raising=False)
    return root

def test_package_root_unfrozen_is_the_package():
    assert resources.package_root() == PACKAGE
    assert resources.builtin_harnesses() == CHECKOUT / "harnesses"
    assert resources.bundle_dir() is None and resources.is_frozen() is False

def test_package_root_frozen_uses_meipass(tmp_path, monkeypatch):
    bundle = fake_bundle(monkeypatch, tmp_path / "Zoë Intern" / "evolvekit app")
    assert resources.is_frozen()
    assert resources.package_root() == bundle / "_internal" / "evolvekit"
    assert resources.builtin_harnesses() == bundle / "_internal" / "harnesses"
    assert resources.bundle_dir() == bundle

def test_every_data_lookup_uses_package_root():
    hits = [h for h in _uses(lambda n: isinstance(n, ast.Name) and n.id == "__file__") if not h.startswith("resources.py:")]
    assert hits == [], "package data is found through evolvekit.resources.package_root()"

def test_package_data_paths_are_under_package_root():
    from evolvekit import dashboard
    from evolvekit.app import server
    from evolvekit.harness import library
    from evolvekit.harness.sdk import SDK_PATH
    for path in (server.PAGE, dashboard.PAGE, SDK_PATH, library.AGENTS_TEMPLATE):
        assert path.is_file() and resources.package_root() in path.parents

def test_find_harnesses_offers_the_bundles_built_ins_when_frozen(tmp_path, monkeypatch):
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app", harnesses=("demo-tour",))
    entries = find_harnesses(tmp_path / "my home")
    assert [(e.harness.id, e.built_in) for e in entries] == [("demo-tour", True)]
    assert entries[0].path == bundle / "_internal" / "harnesses" / "demo-tour"

def test_skills_come_from_package_root_when_frozen(tmp_path, monkeypatch):
    from evolvekit import skills
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app", skills=True)
    extra = bundle / "_internal" / "evolvekit" / "skills" / "evolvekit-extra"
    extra.mkdir()
    (extra / "SKILL.md").write_text("---\nname: evolvekit-extra\ndescription: one line\n---\n", encoding="utf-8")
    assert "evolvekit-extra" in skills.available()

ALLOWED_EXECUTABLE = {"resources.py", "harness/sdk/evk_harness.py"}   # the SDK runs under the application's Python

def _sys_executable_lines(tree: ast.AST) -> list[int]:
    """Lines reading `sys.executable`: as `sys.executable`, through an alias (`import sys as s; s.executable`),
    `from sys import executable`, or `getattr(sys, "executable")`."""
    aliases = {"sys"} | {a.asname for n in ast.walk(tree) if isinstance(n, ast.Import)
                         for a in n.names if a.name == "sys" and a.asname}
    def reads(node) -> bool:
        if isinstance(node, ast.Attribute):
            return node.attr == "executable" and isinstance(node.value, ast.Name) and node.value.id in aliases
        if isinstance(node, ast.ImportFrom):
            return node.module == "sys" and any(a.name == "executable" for a in node.names)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2:
            target, name = node.args[:2]
            return (isinstance(target, ast.Name) and target.id in aliases
                    and isinstance(name, ast.Constant) and name.value == "executable")
        return False
    return [node.lineno for node in ast.walk(tree) if reads(node)]

def test_the_sys_executable_walk_sees_every_spelling():
    tree = ast.parse("import sys as s\nfrom sys import executable as e\ns.executable\n"
                     "getattr(sys, 'executable')\nsys.executable\nx = 'sys.executable'\ngetattr(sys, 'path')\n")
    assert sorted(_sys_executable_lines(tree)) == [2, 3, 4, 5]

def test_every_sys_executable_use_goes_through_resources():
    hits = []
    for source in sorted(PACKAGE.rglob("*.py")):
        name = source.relative_to(PACKAGE).as_posix()
        if name not in ALLOWED_EXECUTABLE:
            hits += [f"{name}:{line}" for line in _sys_executable_lines(ast.parse(source.read_text(encoding="utf-8")))]
    assert hits == [], "evolvekit's Python is evolvekit.resources.python_argv()"

def test_python_argv_frozen_and_not(tmp_path, monkeypatch):
    here = sys.executable
    script = str(tmp_path / "Zoë Intern" / "run me.py")
    assert resources.python_argv() == [here]
    assert resources.python_module_argv("evolvekit.app.job") == [here, "-m", "evolvekit.app.job"]
    assert resources.python_script_argv(script) == [here, script]
    assert resources.python_code_argv("pass") == [here, "-c", "pass"]
    assert resources.app_argv() == [here, "-m", "evolvekit", "app"]
    bundle = fake_bundle(monkeypatch, tmp_path / "Zoë Intern" / "evolvekit app")
    cli = str(bundle / resources.CLI_EXE)
    assert resources.python_argv() == [cli], "the console exe, also when the windowless one asks"
    assert resources.python_module_argv("evolvekit.app.job") == [cli, "-m", "evolvekit.app.job"]
    assert resources.python_script_argv(script) == [cli, "--script", script]
    assert resources.python_code_argv("pass") == [cli, "-c", "pass"]
    assert resources.app_argv() == [str(bundle / resources.APP_EXE)]
    (bundle / resources.CLI_EXE).unlink()
    assert resources.python_argv() == [str(bundle / resources.APP_EXE)]

def test_python_argv_is_never_pythonw(tmp_path, monkeypatch):
    (tmp_path / "python.exe").write_bytes(b""); (tmp_path / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    assert resources.python_argv() == [str(tmp_path / "python.exe")]

def test_exe_name_is_the_running_exes(tmp_path, monkeypatch):
    assert resources.exe_name() == Path(sys.executable).name
    fake_bundle(monkeypatch, tmp_path / "evolvekit app", exe=resources.CLI_EXE)
    assert resources.exe_name() == resources.CLI_EXE

def test_stages_python_is_the_cli_exe_when_frozen(tmp_path, monkeypatch):
    from evolvekit.evaluate.stages import build_argv, build_command
    from evolvekit.harness.probe import program_command
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    cli = str(bundle / resources.CLI_EXE)
    assert build_argv("{python} runner.py --seed {seed}", candidate=tmp_path, inputs=(), out=tmp_path / "o.json", seed=1)[:2] == [cli, "runner.py"]
    assert build_command("{python} -m pip install -q {tree}", tree=tmp_path)[:3] == [cli, "-m", "pip"]
    assert program_command(r"C:\Zoë\solver.py") == [cli, "--script", r"C:\Zoë\solver.py"]
    assert program_command(r"C:\Zoë\solver.exe") == [r"C:\Zoë\solver.exe"]

def test_detect_leaves_out_the_bundled_exe_when_frozen(tmp_path, monkeypatch):
    from evolvekit.app import detect
    monkeypatch.setattr(detect, "_py_launcher", lambda: [])
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    listed = detect.candidates(cwd=tmp_path, home=tmp_path / "user")
    assert not any(Path(p).parent == bundle for p in listed)

def test_doctor_says_python_is_bundled_when_frozen(tmp_path, monkeypatch):
    from evolvekit import doctor
    fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    line = doctor._python()
    assert line["ok"] and line["message"].startswith("bundled (Python ")

def test_frozen_paths_with_spaces_and_unicode(tmp_path, monkeypatch, capsys):
    source = tmp_path / "work" / "demo-tour"
    shutil.copytree(CHECKOUT / "harnesses" / "demo-tour", source, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("runner.py", "harness.yaml"):                    # packed from a CRLF checkout (core.autocrlf=true)
        path = source / name
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    (tmp_path / "Downloads ë").mkdir()
    zipped = pack_harness(source, tmp_path / "Downloads ë")
    bundle = fake_bundle(monkeypatch, tmp_path / "Zoë Intern" / "Apps" / "evolvekit app", harnesses=("prompt-tune",))
    profile = fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    home = default_home()
    assert os.path.samefile(home.parent, profile)
    installed = install_harness(zipped, home)
    assert installed.root == home / "harnesses" / "demo-tour-1.1.0"
    entries = {e.harness.id: e for e in find_harnesses(home)}
    assert not entries["demo-tour"].built_in and entries["prompt-tune"].built_in
    assert bundle in entries["prompt-tune"].path.parents
    assert main(["study", "new", "Zoë's depot", "--harness", "demo-tour", "--template", "tune", "--json"]) == 0
    slug = json.loads(capsys.readouterr().out)["slug"]
    runner = home / "studies" / slug / "harness" / "runner.py"
    assert runner.is_file()
    assert resources.python_script_argv(runner) == [str(bundle / resources.CLI_EXE), "--script", str(runner)]
