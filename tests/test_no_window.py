"""No console window for a program the app or a run starts (`detach.no_window_flags`): under
`pythonw.exe` (the logon task) or a detached app or run there is no console to share, so each
console program would open a window of its own."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from evolvekit.app import detach

ROOT = Path(__file__).resolve().parents[1] / "evolvekit"
CREATE_NO_WINDOW = 0x08000000

CALL_SITES = {
    "app/api.py": 1, "app/detach.py": 1, "app/detect.py": 1, "app/store.py": 1,
    "evaluate/stages.py": 2, "evaluate/process.py": 2, "harness/check.py": 1, "harness/discover.py": 2,
    "harness/execute.py": 1, "harness/pr.py": 1, "harness/probe.py": 2, "providers/claude_cli.py": 2,
    "providers/copilot_cli.py": 1, "tree.py": 1,
}
"""Each module's calls that start a console program, and how many. `evaluate/process.py`'s are
the Windows tree's (`run_bounded`: the runner's command stages, `agent_run`); its POSIX ones need
no flag."""


def _calls(path: Path) -> list[ast.Call]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if name in ("subprocess.run", "subprocess.Popen", "self._runner"):
            found.append(node)
    return found


def test_no_window_flags():
    assert detach.no_window_flags() == (CREATE_NO_WINDOW if sys.platform == "win32" else 0)


def test_no_window_flags_follow_the_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert detach.no_window_flags() == CREATE_NO_WINDOW
    monkeypatch.setattr(sys, "platform", "linux")
    assert detach.no_window_flags() == 0


@pytest.mark.parametrize("module", sorted(CALL_SITES))
def test_every_call_site_passes_the_flag(module):
    calls = [c for c in _calls(ROOT / module)
             if not (module == "evaluate/process.py" and any(k.arg == "start_new_session" for k in c.keywords))]
    flagged = [c for c in calls if any(k.arg == "creationflags" and "no_window_flags()" in ast.unparse(k.value)
                                       for k in c.keywords)]
    assert len(flagged) == len(calls) == CALL_SITES[module], [ast.unparse(c)[:120] for c in calls if c not in flagged]


def test_the_flag_reaches_the_calls_on_windows(monkeypatch, tmp_path):
    """Patched `subprocess` and platform: each kind of call carries CREATE_NO_WINDOW."""
    from evolvekit import tree
    from evolvekit.app import detect
    from evolvekit.evaluate import stages
    from evolvekit.harness import discover
    from evolvekit.providers.claude_cli import ClaudeCliProvider

    monkeypatch.setattr(sys, "platform", "win32")
    seen: list[tuple[str, int]] = []

    def run(argv, **kwargs):
        seen.append((str(argv[0]), kwargs.get("creationflags", 0)))
        empty = "" if kwargs.get("text") or kwargs.get("encoding") else b""
        return subprocess.CompletedProcess(argv, 0, empty, empty)

    monkeypatch.setattr(subprocess, "run", run)
    tree._git(tmp_path, "status")
    detect._py_launcher()
    discover._ask(["prog"], "--help", 5, "prog")
    stages._import_check(tmp_path / "candidate.py", 5)
    ClaudeCliProvider(runner=run, executable="claude").act_flags()
    assert len(seen) == 5 and all(flags == CREATE_NO_WINDOW for _, flags in seen), seen


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows process tree")
def test_the_windows_tree_starts_with_the_flag(monkeypatch):
    from evolvekit.evaluate import process

    seen = []

    def popen(argv, **kwargs):
        seen.append(kwargs.get("creationflags", 0))
        raise OSError("not started in this test")

    monkeypatch.setattr(process.subprocess, "Popen", popen)
    with pytest.raises(OSError):
        process._WindowsTree.spawn(["prog"], {})
    assert seen and seen[0] & CREATE_NO_WINDOW and seen[0] & 0x00000004, "suspended, and no window"
