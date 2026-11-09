"""`python tasks.py dist` and `packaging/bundle_files.py`, without PyInstaller:
the build is patched, the bundle's file list is read from the checkout."""

import importlib.util
import re
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

import evolvekit
import tasks

_spec = importlib.util.spec_from_file_location("evolvekit_bundle_files", tasks.ROOT / "packaging" / "bundle_files.py")
bundle_files = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bundle_files)


def _never(*a, **kw):
    pytest.fail("PyInstaller ran")


def _raise(exc):
    def raiser(*a, **kw):
        raise exc
    return raiser


def _root(tmp_path, monkeypatch):
    shutil.copyfile(tasks.ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    (tmp_path / "packaging").mkdir(); (tmp_path / "packaging" / "evolvekit-app.spec").write_text("# spec\n", encoding="utf-8")
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    return tmp_path


def test_dist_task_refuses_without_extra(tmp_path, monkeypatch, capsys):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr(tasks, "_pyinstaller_version", lambda: None)
    monkeypatch.setattr(tasks.subprocess, "call", _never)
    assert tasks.dist() == 2
    assert 'pip install -e ".[dist]"' in capsys.readouterr().err


def test_dist_task_refuses_a_different_pyinstaller(tmp_path, monkeypatch, capsys):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr(tasks, "_pyinstaller_version", lambda: "1.0.0")
    monkeypatch.setattr(tasks.subprocess, "call", _never)
    assert tasks.dist() == 2
    err = capsys.readouterr().err
    assert "PyInstaller 1.0.0 is installed" in err and f"pins {tasks.pinned_pyinstaller()}" in err
    assert 'pip install -e ".[dist]"' in err


@pytest.mark.parametrize("found", ["6.22.3", None])
def test_dist_task_refuses_without_an_exact_pin(tmp_path, monkeypatch, capsys, found):
    root = _root(tmp_path, monkeypatch)
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    (root / "pyproject.toml").write_text(re.sub(r'"pyinstaller==[^"]*"', '"pyinstaller>=6"', text), encoding="utf-8")
    assert tasks.pinned_pyinstaller() is None
    monkeypatch.setattr(tasks, "_pyinstaller_version", lambda: found)
    monkeypatch.setattr(tasks.subprocess, "call", _never)
    assert tasks.dist() == 2
    err = capsys.readouterr().err
    assert "the dist extra has no exact pin for pyinstaller" in err and "None" not in err


def test_dist_task_refuses_off_windows(tmp_path, monkeypatch, capsys):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(tasks, "_pyinstaller_version", _never)
    monkeypatch.setattr(tasks.subprocess, "call", _never)
    assert tasks.dist() == 2
    assert "pipx install evolvekit" in capsys.readouterr().err


def test_dist_zip_name_carries_the_version():
    assert tasks.zip_name("0.3.0") == "evolvekit-app-0.3.0-win64.zip"
    assert tasks.project_version() == evolvekit.__version__


def test_dist_pin_is_exact():
    pinned = tasks.pinned_pyinstaller()
    assert pinned and all(part.isdigit() for part in pinned.split(".")) and pinned.count(".") == 2


def test_dist_builds_then_zips(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path, monkeypatch)
    monkeypatch.setattr(tasks, "_pyinstaller_version", tasks.pinned_pyinstaller)
    (root / "dist").mkdir(); (root / "dist" / "pyvrp-0.9.0.zip").write_bytes(b"old")
    seen = []
    def build(argv, cwd=None):          # PyInstaller lays out the folder; the pack writes the harness zip
        seen.append(argv)
        if "PyInstaller" in argv:
            folder = root / "dist" / "evolvekit-app"
            (folder / "_internal" / "evolvekit").mkdir(parents=True)
            for name in ("evolvekit-app.exe", "evolvekit-cli.exe", "_internal/evolvekit/x.txt"):
                (folder / name).write_bytes(b"x")
        else:
            (root / "dist" / "pyvrp-1.0.0.zip").write_bytes(b"zip")
            print(f"wrote {root / 'dist' / 'pyvrp-1.0.0.zip'}")
        return 0
    monkeypatch.setattr(tasks.subprocess, "call", build)
    assert tasks.dist() == 0
    assert seen[0][1:4] == ["-m", "PyInstaller", "--noconfirm"] and seen[0][-1] == str(root / "packaging" / "evolvekit-app.spec")
    assert seen[1] == [sys.executable, "-m", "evolvekit", "harness", "pack", str(root / "harnesses" / "pyvrp"),
                       "--out", str(root / "dist")]
    target = root / "dist" / tasks.zip_name(tasks.project_version())
    with zipfile.ZipFile(target) as archive:
        assert sorted(archive.namelist()) == ["evolvekit-app/_internal/evolvekit/x.txt", "evolvekit-app/evolvekit-app.exe",
                                              "evolvekit-app/evolvekit-cli.exe"]
    assert sorted(p.name for p in (root / "dist").glob("pyvrp-*.zip")) == ["pyvrp-1.0.0.zip"], "the old one removed first"
    out = capsys.readouterr().out
    assert str(target) in out and " MB" in out and f"wrote {root / 'dist' / 'pyvrp-1.0.0.zip'}" in out


def test_dist_removes_the_old_folder_and_zip(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    monkeypatch.setattr(tasks, "_pyinstaller_version", tasks.pinned_pyinstaller)
    old = root / "dist" / "evolvekit-app"
    old.mkdir(parents=True); (old / "stale.txt").write_bytes(b"old")
    target = root / "dist" / tasks.zip_name(tasks.project_version())
    target.write_bytes(b"not a zip")
    def build(argv, cwd=None):
        if "PyInstaller" in argv:
            assert not old.exists(), "the old folder is gone before PyInstaller runs"
            assert not target.exists(), "the old zip is gone with it"
            old.mkdir(); (old / "evolvekit-cli.exe").write_bytes(b"x")
        return 0
    monkeypatch.setattr(tasks.subprocess, "call", build)
    assert tasks.dist() == 0
    with zipfile.ZipFile(target) as archive:
        assert archive.namelist() == ["evolvekit-app/evolvekit-cli.exe"]


def test_dist_fails_when_pyinstaller_fails(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    monkeypatch.setattr(tasks, "_pyinstaller_version", tasks.pinned_pyinstaller)
    (root / "dist").mkdir(); (root / "dist" / tasks.zip_name(tasks.project_version())).write_bytes(b"stale")
    calls = []
    monkeypatch.setattr(tasks.subprocess, "call", lambda argv, cwd=None: calls.append(argv) or 3)
    assert tasks.dist() == 3
    assert len(calls) == 1, "neither the zip nor the pack after a failed build"
    assert not (root / "dist" / tasks.zip_name(tasks.project_version())).exists(), "no stale zip after a failed build"


def test_dist_fails_when_the_pyvrp_pack_fails(tmp_path, monkeypatch):
    root = _root(tmp_path, monkeypatch)
    monkeypatch.setattr(tasks, "_pyinstaller_version", tasks.pinned_pyinstaller)
    def build(argv, cwd=None):
        if "PyInstaller" in argv:
            folder = root / "dist" / "evolvekit-app"
            folder.mkdir(parents=True); (folder / "evolvekit-cli.exe").write_bytes(b"x")
            return 0
        return 2
    monkeypatch.setattr(tasks.subprocess, "call", build)
    assert tasks.dist() == 2
    assert (root / "dist" / tasks.zip_name(tasks.project_version())).is_file(), "the app's zip already written"


def test_dist_says_a_running_exe_holds_the_folder(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path, monkeypatch)
    (root / "dist" / "evolvekit-app").mkdir(parents=True)
    monkeypatch.setattr(tasks, "_pyinstaller_version", tasks.pinned_pyinstaller)
    monkeypatch.setattr(tasks.shutil, "rmtree", _raise(PermissionError(32, "in use")))
    monkeypatch.setattr(tasks.subprocess, "call", _never)
    assert tasks.dist() == 1
    assert "app stop" in capsys.readouterr().err


def test_packaging_is_never_a_package():
    assert not (tasks.ROOT / "packaging" / "__init__.py").exists(), "it would shadow the PyPI `packaging` (D15)"


def test_bundle_data_files_cover_package_data_and_built_ins():
    files = {Path(dest, Path(src).name).as_posix() for src, dest in bundle_files.data_files(tasks.ROOT)}
    for wanted in ("evolvekit/app/static/app.html", "evolvekit/app/static/fonts/Overpass-Variable.woff2",
                   "evolvekit/dashboard/index.html", "evolvekit/harness/sdk/evk_harness.py",
                   "evolvekit/harness/templates/AGENTS.md", "evolvekit/skills/shared/evolvekit.md",
                   "harnesses/demo-tour/harness.yaml", "harnesses/demo-tour/samples/town-40.json",
                   "harnesses/prompt-tune/runner.py", "examples/cli-solver/solver.py",
                   "examples/prompt-pipeline/pipeline.py", "examples/prompt-pipeline/prompts/system.md",
                   "examples/prompt-pipeline/items/01-capital.json"):
        assert wanted in files
    assert not any(f.startswith("harnesses/pyvrp/") for f in files), "PyVRP ships as its own zip (D12)"
    assert not any("__pycache__" in f or f.endswith(".pyc") or f.startswith(("tests/", "runs/", "docs/")) for f in files)


def _git(root, *args):
    import subprocess
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=60)


def test_bundle_data_files_are_only_tracked_files(tmp_path):
    root = tmp_path / "checkout ë"
    tracked = ["evolvekit/app/static/app.html", "evolvekit/skills/shared/evolvekit.md",
               "harnesses/demo-tour/harness.yaml", "harnesses/prompt-tune/runner.py",
               "examples/cli-solver/solver.py", "examples/cli-solver/README.md",
               "examples/prompt-pipeline/pipeline.py", "examples/prompt-pipeline/prompts/system.md",
               "examples/prompt-pipeline/items/01-capital.json"]
    untracked = ["evolvekit/app/static/scratch.html", "evolvekit/skills/shared/.env",
                 "harnesses/demo-tour/.env", "harnesses/demo-tour/runs/r1/out.json", "harnesses/prompt-tune/notes ë.txt",
                 "examples/prompt-pipeline/items/02-mine.json", "examples/prompt-pipeline/prompts/.env.local"]
    shutil.copyfile(tasks.ROOT / "pyproject.toml", (root.mkdir() or root) / "pyproject.toml")
    for name in tracked + untracked:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("x\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "pyproject.toml", *tracked)
    files = {Path(dest, Path(src).name).as_posix() for src, dest in bundle_files.data_files(root)}
    assert files == set(tracked), "nothing untracked on the builder's disk (a .env, a scratch run) is ever bundled"


def test_bundle_data_files_need_a_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))   # never a repository above the temp folder
    shutil.copyfile(tasks.ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    with pytest.raises(RuntimeError, match="git checkout"):
        bundle_files.data_files(tmp_path)


def test_bundle_stdlib_modules_leave_out_tests_and_idle():
    names = set(bundle_files.stdlib_modules())
    assert {"sqlite3", "csv", "json.decoder", "email.mime.text", "statistics"} <= names
    assert not any(n.split(".")[0] in bundle_files.EXCLUDED or {"test", "tests", "idle_test"} & set(n.split(".")) for n in names)
    if importlib.util.find_spec("tkinter") is not None:
        assert "tkinter.filedialog" in names
