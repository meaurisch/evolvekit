"""How a coding agent is started: a fresh process, not the launching session's child.

Shared by the providers' `act` (design §5.4) and the agent gate script
(`tests/agent_gate.py`), so both drop the same names:

* `without_session` -- the launching coding-agent session's identity
  (`CLAUDECODE`, `CLAUDE_CODE_*`, `CLAUDE_AGENT_SDK_*`, ...). With it a
  one-shot `claude` runs as that session's child, or refuses to start. `KEEP`
  is configuration an engineer's sign-in may need; every `ANTHROPIC_*`
  variable (the key, the endpoint, the model) and `CLAUDE_CLI_PATH`
  (evolvekit's own) stay too.
* `agent_env` -- that, without the keys evolvekit keeps for its own
  providers (`SECRET_NAMES`) except the agent's own sign-in, without the git
  host's tokens (`GIT_HOST_TOKENS`) unless they are that sign-in, and with no
  bytecode written (a `__pycache__` the agent's test run left would be a file
  outside the open files).
* `run_agent` -- the call, under one wall-clock bound that covers the whole
  process tree (`evaluate.process.run_bounded`): a timeout or a Stop request
  ends the agent and everything it started.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping, Sequence

from evolvekit.providers.base import AgentError

__all__ = ["GIT_HOST_TOKENS", "KEEP", "SESSION_NAMES", "SESSION_PREFIXES", "agent_env", "from_session", "run_agent",
           "without_session"]

SESSION_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_PREVIEW_")
SESSION_NAMES = ("CLAUDECODE",)
KEEP = ("CLAUDE_CODE_GIT_BASH_PATH", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH", "CLAUDE_CODE_SKIP_VERTEX_AUTH", "CLAUDE_CODE_SKIP_FOUNDRY_AUTH",
        "CLAUDE_CODE_OAUTH_TOKEN")
"""Sign-in configuration under a session prefix: kept."""
GIT_HOST_TOKENS = ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
"""Tokens that would let the agent push or open a pull request: never in its
environment unless its provider names one in `keep` as its own sign-in."""


def from_session(name: str) -> bool:
    """Whether `name` is the launching session's identity (dropped)."""
    upper = name.upper()
    if upper in KEEP:
        return False
    return upper in SESSION_NAMES or upper.startswith(SESSION_PREFIXES)


def without_session(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """`source` (this process's environment when not given) without the session's identity."""
    return {k: v for k, v in (os.environ if source is None else source).items() if not from_session(k)}


def agent_env(source: Mapping[str, str] | None = None, *, keep: Sequence[str] = ()) -> dict[str, str]:
    """The agent's environment: `without_session`, without evolvekit's secrets
    except `ANTHROPIC_*`, `KEEP` and the names in `keep` (the agent's sign-in)."""
    from evolvekit.evaluate.process import SECRET_NAMES

    kept = {name.upper() for name in (*KEEP, *keep)}
    env = {k: v for k, v in without_session(source).items()
           if (not SECRET_NAMES.search(k) or k.upper().startswith("ANTHROPIC_") or k.upper() in kept)
           and (k.upper() not in GIT_HOST_TOKENS or k.upper() in kept)}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return ""


def run_agent(
    argv: Sequence[str],
    *,
    cwd: str | Path,
    timeout: float,
    env: Mapping[str, str],
    input: str | None = None,
    cancel: threading.Event | None = None,
    **_like_subprocess_run: Any,
) -> subprocess.CompletedProcess:
    """Run one agent call: `input` on stdin, the whole tree ended on `timeout`
    (`subprocess.TimeoutExpired`) or on `cancel` (`AgentError`, unreported). Shaped like
    `subprocess.run`, so a test can stand in for it; its other keywords are ignored.
    A command that cannot start raises `OSError`."""
    from evolvekit.evaluate.process import run_bounded

    with tempfile.TemporaryDirectory(prefix="evolvekit-agent-") as scratch:
        folder = Path(scratch)
        stdin_path = None
        if input is not None:
            stdin_path = folder / "stdin.txt"
            stdin_path.write_bytes(input.encode("utf-8"))
        out, err = folder / "stdout.txt", folder / "stderr.txt"
        done = run_bounded(list(argv), timeout=float(timeout), cwd=cwd, stdout_path=out, stderr_path=err,
                           cancel=cancel, env=dict(env), stdin_path=stdin_path)
        stdout, stderr = _read(out), _read(err)
    if done.error is not None:
        raise OSError(done.error)
    if done.cancelled:  # it ran, and said nothing of what it spent
        raise AgentError("stopped: the study was asked to stop, and the agent was ended", unreported=True)
    if done.timed_out:
        raise subprocess.TimeoutExpired(list(argv), timeout, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(list(argv), done.returncode, stdout, stderr)
