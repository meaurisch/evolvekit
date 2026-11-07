"""A harness declares `code:` (design §5.1): the source a code study may change,
and what `harness check` verifies about it -- the repository, the open files,
a clean checkout's build, and the samples solved from that checkout."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from evolvekit.harness import HarnessError
from evolvekit.harness.check import _build_argv, _code_checks, check_harness, exit_code
from evolvekit.harness.library import new_harness, point_code_repo
from evolvekit.harness.manifest import Code, load_harness, resolve_code_repo
from harness_toy import MANIFEST, write_toy_harness

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "harnesses" / "demo-tour"
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"
CLI_SOLVER = ROOT / "examples" / "cli-solver"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _harness(tmp_path: Path, **code) -> Path:
    """demo-tour, copied into `tmp_path / "h"`, with these keys of its `code:` replaced."""
    target = tmp_path / "h"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(DEMO, target, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = yaml.safe_load((target / "harness.yaml").read_text(encoding="utf-8"))
    manifest["code"] = {**manifest["code"], **code}
    (target / "harness.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return target


def _toy(tmp_path: Path, code: dict) -> Path:
    """The python toy harness with a `code:` section."""
    target = tmp_path / "toy"
    if target.exists():
        shutil.rmtree(target)
    return write_toy_harness(target, manifest={**MANIFEST, "code": code})


def _worktrees() -> list[str]:
    out = subprocess.run(["git", "worktree", "list", "--porcelain"], cwd=ROOT, capture_output=True, text=True,
                         check=True).stdout
    return sorted(line for line in out.splitlines() if line.startswith("worktree "))


def _status() -> str:
    return subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout


# --- the manifest ----------------------------------------------------------------


def test_manifest_code_section(tmp_path):
    harness = load_harness(DEMO)
    assert harness.version == "1.1.0"
    assert harness.code == Code(label="The solver's source", repo="../../examples/cli-solver", open=("solver.py",),
                                app="{tree}/solver.py")
    assert resolve_code_repo(harness.root, harness.code) == (ROOT / "examples" / "cli-solver").resolve()
    for code, message in (({"open": []}, "code.open: name at least one file"),
                          ({"open": ["../x.py"]}, "code.open: '../x.py' must stay inside code.repo"),
                          ({"app": "solver.py"}, "code.app: must contain {tree}"),
                          ({"colour": "red"}, "code: unknown key(s) ['colour']")):
        with pytest.raises(HarnessError, match=re.escape(message)):
            load_harness(_harness(tmp_path, **code))


def test_manifest_code_rules(tmp_path):
    for code, message in (({"open": ["/etc/passwd"]}, "code.open: '/etc/passwd' must stay inside code.repo"),
                          ({"open": ["lib/../../x.py"]}, "code.open: 'lib/../../x.py' must stay inside code.repo"),
                          ({"open": "solver.py"}, "code.open: expected a list of files or globs"),
                          ({"app": None}, "code.app: a program harness names its program inside the tree"),
                          ({"build_timeout_s": 0}, "code.build_timeout_s: must be > 0"),
                          ({"label": None}, "code.label: required"),
                          ({"repo": None}, "code.repo: required")):
        with pytest.raises(HarnessError, match=re.escape(message)):
            load_harness(_harness(tmp_path, **code))
    built = load_harness(_harness(tmp_path, build="{python} -m pip install -q -e {tree}", build_timeout_s=30))
    assert built.code.build == "{python} -m pip install -q -e {tree}" and built.code.build_timeout_s == 30.0


def test_a_python_harness_builds_its_code_and_names_no_app(tmp_path):
    base = {"label": "The source", "repo": str(CLI_SOLVER), "open": ["solver.py"]}
    with pytest.raises(HarnessError, match=re.escape(
            "code.app: a python harness runs the application's Python; its code reaches it through code.build")):
        load_harness(_toy(tmp_path, {**base, "build": "{python} -m pip install -e {tree}", "app": "{tree}/x.py"}))
    with pytest.raises(HarnessError, match=re.escape("code.build: a python harness needs a build")):
        load_harness(_toy(tmp_path, base))
    harness = load_harness(_toy(tmp_path, {**base, "build": "{python} -m pip install -e {tree}"}))
    assert harness.code.app == "" and harness.code.open == ("solver.py",)


def test_a_harness_without_code_has_none(tmp_path):
    assert load_harness(write_toy_harness(tmp_path / "plain")).code is None


def test_an_absolute_repo_stays_itself(tmp_path):
    harness = load_harness(_harness(tmp_path, repo=str(CLI_SOLVER)))
    assert resolve_code_repo(tmp_path / "elsewhere", harness.code) == CLI_SOLVER.resolve()


def test_the_build_names_the_application_python_for_a_python_harness(tmp_path):
    harness = load_harness(_toy(tmp_path, {"label": "s", "repo": str(CLI_SOLVER), "open": ["solver.py"],
                                           "build": "{python} -m pip install -e {tree}"}))
    app = str(tmp_path / "app python" / "python.exe")
    argv = _build_argv(harness, app, tmp_path / "tree")
    assert Path(argv[0]) == Path(app) and argv[1:4] == ["-m", "pip", "install"] and Path(argv[-1]) == tmp_path / "tree"
    program = load_harness(_harness(tmp_path, build="{python} -c pass"))
    assert _build_argv(program, str(SOLVER), tmp_path / "tree")[0] == sys.executable


# --- harness check ----------------------------------------------------------------


def test_check_code_repo_not_git(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "solver.py").write_text("x = 1\n")
    harness = load_harness(_harness(tmp_path, repo=str(plain)))
    checks = _code_checks(harness, str(SOLVER), tmp_path / "scratch")
    assert [(c.id, c.severity) for c in checks] == [("code.repo", "failure")]
    assert checks[0].message == f"code.repo: {plain.resolve()} is not inside a git repository"
    assert "git init" in checks[0].fix


def test_check_code_repo_missing(tmp_path):
    harness = load_harness(_harness(tmp_path, repo=str(tmp_path / "nowhere")))
    checks = _code_checks(harness, str(SOLVER), tmp_path / "scratch")
    assert [(c.id, c.severity) for c in checks] == [("code.repo", "failure")]
    assert checks[0].message == f"code.repo: {(tmp_path / 'nowhere').resolve()}: no such folder"


def test_check_code_open_matches_nothing(tmp_path):
    harness = load_harness(_harness(tmp_path, repo=str(ROOT / "examples" / "cli-solver"), open=["*.rs"]))
    checks = _code_checks(harness, str(SOLVER), tmp_path / "scratch")
    bad = next(c for c in checks if c.id == "code.open")
    assert bad.severity == "failure" and bad.message == "code.open: no file of the source matches *.rs"
    assert [c.id for c in checks] == ["code.repo", "code.open"], "a failure stops the code checks"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "user.name=t", "-c", "user.email=t@localhost",
                    "-c", "commit.gpgsign=false", "-c", "core.hooksPath=.no-hooks", *args], cwd=cwd, check=True,
                   capture_output=True)


def test_check_code_open_counts_committed_files_only(tmp_path):
    source = tmp_path / "my source"
    source.mkdir()
    _git(source, "init", "-q")
    (source / "solver.py").write_text("x = 1\n", encoding="utf-8")
    _git(source, "add", "solver.py")
    _git(source, "commit", "-q", "--no-verify", "-m", "one")
    (source / "staged.py").write_text("y = 2\n", encoding="utf-8")
    _git(source, "add", "staged.py")  # in the index, not in HEAD: a clean checkout would not have it
    harness = load_harness(_harness(tmp_path, repo=str(source), open=["staged.py"]))
    checks = _code_checks(harness, str(SOLVER), tmp_path / "scratch")
    assert [(c.id, c.ok) for c in checks] == [("code.repo", True), ("code.open", False)]
    assert checks[-1].message == "code.open: no file of the source matches staged.py"


def test_check_a_sample_that_fails_names_the_engineer_s_program(tmp_path):
    harness = load_harness(_harness(tmp_path, repo=str(CLI_SOLVER), app="{tree}/missing.py"))
    checks = _code_checks(harness, str(SOLVER), tmp_path / "scratch")
    assert [(c.id, c.ok) for c in checks] == [("code.repo", True), ("code.open", True), ("code.samples", False)]
    assert f"--app {CLI_SOLVER.resolve() / 'missing.py'} " in checks[-1].fix, "not the removed checkout's path"
    assert "trees" not in checks[-1].fix


def test_check_a_checkout_that_cannot_be_made(tmp_path, monkeypatch):
    from evolvekit import tree

    def refuse(*_a, **_k):
        raise tree.TreeError("the disk is full")

    monkeypatch.setattr(tree, "materialise", refuse)
    harness = load_harness(_harness(tmp_path, repo=str(CLI_SOLVER)))
    checks = _code_checks(harness, str(SOLVER), tmp_path / "scratch")
    assert checks[-1].id == "code.checkout" and checks[-1].severity == "failure"
    assert checks[-1].message.startswith("code.checkout: a clean checkout of ") and "the disk is full" in checks[-1].message


def test_check_code_open_counts_tracked_files(tmp_path):
    harness = load_harness(_harness(tmp_path, repo=str(CLI_SOLVER), open=["solver.py", "data/*.json"]))
    checks = _code_checks(harness, str(SOLVER), tmp_path / "scratch")
    opened = next(c for c in checks if c.id == "code.open")
    assert opened.ok and opened.message.startswith("code.open matches 6 file(s): solver.py, data/a-40.json")


def test_check_warns_one_at_a_time_with_build(tmp_path):
    # Decision 38: "one combination at a time" is a passing line, so the check still ends at exit 0.
    before = _worktrees()
    harness = load_harness(_harness(tmp_path, repo=str(ROOT / "examples" / "cli-solver"), build='{python} -c "pass"'))
    checks = _code_checks(harness, str(SOLVER), tmp_path / "scratch")
    assert {c.id: c.ok for c in checks}["code.build"] is True
    note = next(c for c in checks if c.id == "code.one_at_a_time")
    assert note.ok and note.severity == "pass"
    assert note.message == "code studies on this harness try one combination at a time"
    assert [c.id for c in checks] == ["code.repo", "code.open", "code.build", "code.samples", "code.one_at_a_time"]
    assert exit_code(checks) == 0
    assert not any((tmp_path / "scratch").glob("**/trees/*")), "the checkout is removed"
    assert _worktrees() == before


def test_check_a_build_that_fails_names_its_last_line(tmp_path):
    failing = '{python} -c "import sys; sys.stderr.write(\'error: solver.py does not compile\\n\'); sys.exit(3)"'
    harness = load_harness(_harness(tmp_path, repo=str(CLI_SOLVER), build=failing))
    checks = _code_checks(harness, str(SOLVER), tmp_path / "scratch")
    assert [(c.id, c.severity) for c in checks] == [("code.repo", "pass"), ("code.open", "pass"),
                                                    ("code.build", "failure"), ("code.restore", "warning")]
    assert checks[2].message == "code.build: a clean checkout does not build: exit code 3: error: solver.py does not compile"
    assert checks[2].fix == "run the build by hand in a clean checkout and fix it"
    # The same build fails on the engineer's checkout too: putting the interpreter back is a warning.
    assert "could not be built back into the application's Python" in checks[3].message
    assert checks[3].fix.startswith(f"run the build in {CLI_SOLVER.resolve()} yourself")
    assert not any((tmp_path / "scratch").glob("**/trees/*"))


def test_check_the_build_runs_in_the_tree_and_is_put_back(tmp_path):
    log = tmp_path / "builds.log"
    build = f'{{python}} -c "import os, sys; open(r\'{log}\', \'a\').write(os.getcwd() + \'|\' + sys.argv[1] + \'\\n\')" {{tree}}'
    harness = load_harness(_harness(tmp_path, repo=str(CLI_SOLVER), build=build))
    checks = _code_checks(harness, str(SOLVER), tmp_path / "scratch")
    assert all(c.ok for c in checks), checks
    tree_build, restore = [line.split("|") for line in log.read_text().splitlines()]
    assert Path(tree_build[0]) == Path(tree_build[1]) and "trees" in Path(tree_build[1]).parts
    assert Path(restore[0]) == Path(restore[1]) == CLI_SOLVER.resolve(), "the engineer's checkout is built back"


def test_check_a_python_harness_solves_with_the_application_python(tmp_path):
    harness = load_harness(_toy(tmp_path, {"label": "s", "repo": str(CLI_SOLVER), "open": ["solver.py"],
                                           "build": '{python} -c "pass"'}))
    checks = _code_checks(harness, sys.executable, tmp_path / "scratch")
    assert [c.id for c in checks] == ["code.repo", "code.open", "code.build", "code.samples", "code.one_at_a_time"]
    assert all(c.ok for c in checks), checks


OTHER_PYTHON = getattr(sys, "_base_executable", sys.executable)


@pytest.mark.skipif(os.path.normcase(OTHER_PYTHON) == os.path.normcase(sys.executable)
                    or not Path(OTHER_PYTHON).is_file(), reason="no second interpreter (not run from a venv)")
def test_check_builds_into_the_app_python_and_puts_it_back(tmp_path):
    log = tmp_path / "pythons.log"
    build = f'{{python}} -c "import os, sys; open(r\'{log}\', \'a\').write(sys.executable + \'|\' + os.getcwd() + chr(10))"'
    harness = load_harness(_toy(tmp_path, {"label": "s", "repo": str(CLI_SOLVER), "open": ["solver.py"],
                                           "build": build}))
    checks = _code_checks(harness, OTHER_PYTHON, tmp_path / "scratch")
    assert all(c.ok for c in checks), checks
    (in_tree, tree_cwd), (back, checkout) = [line.split("|") for line in log.read_text().splitlines()]
    assert os.path.normcase(in_tree) == os.path.normcase(back) == os.path.normcase(OTHER_PYTHON), \
        "the --app Python builds the tree, and the checkout is built back into it"
    assert "trees" in Path(tree_cwd).parts and Path(checkout) == CLI_SOLVER.resolve()


def test_a_harness_copied_with_new_keeps_its_source(tmp_path):
    target = new_harness(tmp_path / "mine", source=DEMO)
    text = (target / "harness.yaml").read_text(encoding="utf-8")
    assert "# The source a code study may change" in text, "one line changes; the comments stay"
    copied = load_harness(target)
    assert copied.code.repo == str(CLI_SOLVER.resolve()) and copied.code.open == ("solver.py",)
    assert resolve_code_repo(copied.root, copied.code) == CLI_SOLVER.resolve()
    again = new_harness(tmp_path / "again", source=target)
    assert load_harness(again).code.repo == str(CLI_SOLVER.resolve()), "an absolute repo is copied as it is"
    plain = new_harness(tmp_path / "plain", source=write_toy_harness(tmp_path / "toy-src"))
    assert load_harness(plain).code is None


def test_point_code_repo_rewrites_one_line(tmp_path):
    target = tmp_path / "copy"
    shutil.copytree(DEMO, target, ignore=shutil.ignore_patterns("__pycache__"))
    before = (target / "harness.yaml").read_text(encoding="utf-8").splitlines()
    point_code_repo(target, "..")
    after = (target / "harness.yaml").read_text(encoding="utf-8").splitlines()
    assert [(a, b) for a, b in zip(before, after) if a != b] == [("  repo: ../../examples/cli-solver", '  repo: ".."')]
    assert load_harness(target).code.repo == ".."


def test_demo_tour_loads_with_code(tmp_path):
    before = (_worktrees(), _status())
    checks = _code_checks(load_harness(DEMO), str(SOLVER), tmp_path / "scratch")
    assert [c.id for c in checks] == ["code.repo", "code.open", "code.samples"] and all(c.ok for c in checks)
    assert "solve from a clean checkout of" in checks[-1].message
    assert (_worktrees(), _status()) == before, "the evolvekit checkout is untouched"


@pytest.mark.slow
def test_the_demo_harness_check_has_its_code_lines():
    checks = check_harness(DEMO, str(SOLVER), samples=1)
    ids = [c.id for c in checks]
    assert ids[ids.index("code.repo"):ids.index("code.repo") + 3] == ["code.repo", "code.open", "code.samples"]
    assert ids.index("code.repo") > max(i for i, c in enumerate(ids) if c.startswith("drift"))
    assert exit_code(checks) == 0
