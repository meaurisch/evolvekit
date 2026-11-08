import ast, shutil, sys
from pathlib import Path

import evolvekit
from evolvekit import resources
from evolvekit.harness.library import find_harnesses

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
