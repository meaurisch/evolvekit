"""Temporary git repositories for the tree tests (and the later code-study tests).

Every repository is `<tmp>/my repo` (a space in the path) with the folder of
interest in `app dir/`. The machine's own git settings stay in force (on this
Windows machine `core.autocrlf=true`), so the tests meet what an engineer's
checkout meets; only the identity and signing are fixed, in the repository.
"""

from __future__ import annotations

import difflib
import os
import subprocess
from pathlib import Path

SOLVER = '''import json, os, sys, time
VALUE = 5
started = time.time()
time.sleep(float(os.environ.get("TREE_TEST_SLEEP", "0")))
log = os.environ.get("TREE_TEST_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps([os.path.dirname(os.path.abspath(__file__)), started, time.time()]) + "\\n")
print(json.dumps({"value": VALUE}))
'''

# A test run from inside a git hook inherits GIT_DIR and friends, which would
# point every call below at the evolvekit checkout instead of the test's own.
_LOCATING = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX")


def git_raw(cwd: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if k not in _LOCATING}
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace",
                          env=env, check=True)
    return done.stdout


def git(cwd: Path, *args: str) -> str:
    return git_raw(cwd, *args).strip()


def git_init_commit(top: Path) -> None:
    git(top, "init", "-q", "-b", "main")
    git(top, "config", "user.name", "Test User")
    git(top, "config", "user.email", "test@example.com")
    git(top, "config", "commit.gpgsign", "false")
    git(top, "add", "-A")
    git(top, "commit", "-q", "-m", "base")


def _write(top: Path, rel: str, text: str) -> None:
    path = top / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def make_repo(tmp_path: Path) -> Path:
    top = tmp_path / "my repo"
    top.mkdir()
    _write(top, "app dir/solver.py", SOLVER)
    _write(top, "app dir/bench.py", "print('bench')\n")
    _write(top, "README.md", "top\n")
    git_init_commit(top)
    return top


def change(top: Path, edits: dict[str, str], *, unified: int = 3) -> str:
    """The staged diff of `edits` against HEAD; the repository is left as it was."""
    for rel, text in edits.items():
        _write(top, rel, text)
    git(top, "add", "-A")
    try:
        return git_raw(top, "diff", "--cached", f"--unified={unified}")
    finally:
        git(top, "reset", "-q", "--hard", "HEAD")
        git(top, "clean", "-fdq")


def diff_text(rel: str, old: str, new: str) -> str:
    """A unified diff of one file, made without git (for repositories a test must not edit)."""
    body = difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                fromfile=f"a/{rel}", tofile=f"b/{rel}")
    return f"diff --git a/{rel} b/{rel}\n" + "".join(body)


def head(top: Path) -> str:
    return git(top, "rev-parse", "HEAD")
