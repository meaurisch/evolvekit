"""Claude CLI backend -- a Claude Code subscription, driven as a subprocess.

    claude -p --output-format json --model <model> [--system-prompt <s>] ...

The prompt goes in on stdin rather than argv so a 12k-character big-step
prompt cannot trip the Windows command-line length limit. The CLI answers with
a single JSON object; the fields this backend reads are

    result                        the assistant text
    usage.input_tokens            uncached prompt tokens
    usage.cache_creation_input_tokens / cache_read_input_tokens
    usage.output_tokens
    total_cost_usd                real spend, preferred over the price table
    is_error / subtype            failure signalling

No API key is involved: the CLI carries the subscription's own auth. Set
`CLAUDE_CLI_PATH` if the binary is not on PATH.

`act` (design §5.4) runs the CLI as a coding agent in a folder:

    claude -p --output-format json [--max-turns 30] --no-session-persistence
        --strict-mcp-config --disable-slash-commands [--setting-sources project]
        [--permission-prompts none] [--model <model>]
        --allowedTools "Edit Write Read Glob Grep Bash"
        --disallowedTools WebFetch WebSearch Task Agent "Bash(git push:*)" ...
        [--add-dir <the call's brief folder>] [--max-budget-usd <the call's cap>]

The bracketed flags are passed only when the installed CLI's `--help` lists
them (read once per executable): 2.1.304 lists every one but `--max-turns`.
The short prompt goes in on stdin, in the folder, with the launching session's
identity dropped (`agent_run.agent_env`); the whole process tree is ended on
the time limit or a Stop request (`agent_run.run_agent`).

`--setting-sources project` still loads the repository's own
`.claude/settings.json` from the draft (a worktree of the engineer's
repository): its hooks and permission rules apply to the agent.

The folder is a git worktree of the engineer's repository, so `git` there
reaches the engineer's branches, refs, stash and remotes. `ACT_DISALLOWED`
denies the git commands that write them (`Bash(git push:*)` and the like);
that is not airtight -- `git -C . push`, a script that runs git, or an alias
gets past a prefix rule -- and the brief says not to commit either. The git
host's tokens (`GH_TOKEN`, `GITHUB_TOKEN` and the enterprise pair,
`agent_run.GIT_HOST_TOKENS`) are not in the agent's environment: claude does
not need them to sign in.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Sequence

from evolvekit.providers.agent_run import agent_env, run_agent
from evolvekit.providers.base import (
    AgentError,
    AgentResult,
    Completion,
    Message,
    ProviderError,
    split_system,
)

__all__ = ["ClaudeCliProvider", "parse_act_json", "parse_cli_json"]

DEFAULT_TIMEOUT = 600.0

# The loop wants a text completion, not an agent. Everything that could turn
# one call into a session is switched off.
BASE_FLAGS = (
    "-p",
    "--output-format",
    "json",
    "--max-turns",
    "1",
    "--no-session-persistence",
    "--strict-mcp-config",
    "--disable-slash-commands",
)

DISALLOWED_TOOLS = "Bash Edit Write Read Glob Grep WebFetch WebSearch Task"

# `act` (design §5.4): an agent that edits the folder it runs in. It may read,
# search, edit and run commands there; it may not browse, or start subagents
# (`Task` is the older name of `Agent`).
ACT_MAX_TURNS = 30
ALLOWED_TOOLS = "Edit Write Read Glob Grep Bash"
ACT_DISALLOWED = (
    "WebFetch", "WebSearch", "Task", "Agent",
    *(f"Bash(git {command}:*)" for command in (
        "push", "commit", "stash", "config", "checkout", "switch", "reset", "branch", "tag", "worktree", "remote",
    )),
)
"""Each its own argv item (a rule holds a space). The git rules keep the agent
off the engineer's repository, which its folder is a worktree of: not airtight."""
ACT_LIMITS = ("error_max_turns", "error_max_budget_usd")
"""`subtype`s of an agent stopped at a limit: its edits are used as they are (D14)."""
OPTIONAL_ACT_FLAGS = ("--max-turns", "--setting-sources", "--permission-prompts", "--max-budget-usd")
"""Passed only when the installed CLI's `--help` lists them: claude 2.1.304
lists no `--max-turns`, and then the call is bounded by time and money."""
HELP_TIMEOUT = 60.0
_HELP: dict[str, frozenset[str]] = {}
"""The flags each executable's `--help` lists, read once per process."""


class ClaudeCliProvider:
    """One `claude -p` invocation per completion."""

    name = "claude-cli"

    def __init__(
        self,
        *,
        runner: Any | None = None,
        executable: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        **options: Any,
    ) -> None:
        self._runner = runner or subprocess.run
        self._executable = executable or os.environ.get("CLAUDE_CLI_PATH") or "claude"
        self._timeout = float(options.get("timeout", timeout))

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        """There is no embeddings endpoint behind the CLI. Say so, once, clearly.

        Present rather than absent on purpose: `search.novelty.near.method:
        embedding` against this backend is a configuration mistake, and a
        named error beats an `AttributeError` from inside the search loop.
        `config.load_config` rejects the combination before a run starts; this
        is the backstop for anyone building a provider by hand.
        """
        raise ProviderError(
            "claude-cli provider: no embeddings -- the Claude Code CLI exposes "
            "completions only. Use search.novelty.near.method: local, or point "
            "models.embed at the azure or openrouter backend."
        )

    def _resolve(self) -> str:
        found = shutil.which(self._executable)
        if found:
            return found
        if os.path.isfile(self._executable):
            return self._executable
        raise ProviderError(
            f"claude-cli provider: executable {self._executable!r} not found on PATH; "
            "install the Claude Code CLI or set CLAUDE_CLI_PATH"
        )

    def build_argv(
        self, *, model: str, system: str = "", system_file: str | None = None
    ) -> list[str]:
        argv = [self._resolve(), *BASE_FLAGS]
        if model:  # an empty model is the CLI's own default
            argv += ["--model", model]
        argv += ["--disallowedTools", DISALLOWED_TOOLS]
        # The system prompt goes through a file: a problem description plus a
        # 40-line scratchpad blew past the Windows command-line limit in the
        # first circle-packing run ("The command line is too long.").
        if system_file:
            argv += ["--system-prompt-file", system_file]
        elif system:
            argv += ["--system-prompt", system]
        return argv

    # -- act: the coding agent (design §5.4) --------------------------------

    def act_flags(self) -> frozenset[str]:
        """The flags the installed CLI's `--help` lists (empty when it cannot be read)."""
        exe = self._resolve()
        shared = self._runner is subprocess.run  # a test's runner gets its own reading
        cache = _HELP if shared else self.__dict__.setdefault("_help", {})
        if exe not in cache:
            try:
                proc = self._runner([exe, "--help"], capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=HELP_TIMEOUT, stdin=subprocess.DEVNULL)
                text = f"{proc.stdout or ''}\n{proc.stderr or ''}"
            except (OSError, subprocess.SubprocessError):
                text = ""
            cache[exe] = frozenset(re.findall(r"--[a-z][a-z0-9-]*", text))
        return cache[exe]

    def act_cap_note(self) -> str | None:
        """What a run's log says once when this CLI cannot be given a cap per
        call (its `--help` lists no `--max-budget-usd`); None when it can."""
        if "--max-budget-usd" in self.act_flags():
            return None
        return "this claude has no spending limit per call; the study's budget is checked between calls"

    def build_act_argv(
        self, *, model: str, read_dirs: Sequence[Path | str] = (), max_usd: float | None = None
    ) -> list[str]:
        """`claude -p` as an agent in its folder: never waiting for a person
        (`--permission-prompts none`), no MCP servers or user settings, the
        tools of `ALLOWED_TOOLS` pre-approved, `read_dirs` readable, at most
        `max_usd` spent, and at most `ACT_MAX_TURNS` turns -- each optional
        flag only when this CLI's `--help` lists it."""
        flags = self.act_flags()
        argv = [self._resolve(), "-p", "--output-format", "json"]
        if "--max-turns" in flags:
            argv += ["--max-turns", str(ACT_MAX_TURNS)]
        argv += ["--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands"]
        if "--setting-sources" in flags:
            argv += ["--setting-sources", "project"]
        if "--permission-prompts" in flags:
            argv += ["--permission-prompts", "none"]
        if model:  # an empty model is the CLI's own default
            argv += ["--model", model]
        argv += ["--allowedTools", ALLOWED_TOOLS, "--disallowedTools", *ACT_DISALLOWED]
        for folder in read_dirs:
            argv += ["--add-dir", str(folder)]
        if max_usd is not None and "--max-budget-usd" in flags:
            argv += ["--max-budget-usd", f"{max(0.0, float(max_usd)):.4f}"]
        return argv

    def act(
        self,
        brief: str,
        *,
        cwd: Path,
        model: str,
        timeout_s: float,
        read_dirs: Sequence[Path | str] = (),
        max_usd: float | None = None,
        cancel: threading.Event | None = None,
    ) -> AgentResult:
        """One agent run in `cwd`, `brief` on stdin; its whole tree ended on
        `timeout_s` or on `cancel`. A run stopped at a limit is `ok=False`.
        `ProviderError` when it cannot start (nothing spent); `AgentError` when
        it ran and failed -- with its reported cost, or `unreported` (a
        timeout, a Stop, a crash, no JSON) so the operator charges the cap."""
        argv = self.build_act_argv(model=model, read_dirs=read_dirs, max_usd=max_usd)
        runner = self._runner if self._runner is not subprocess.run else run_agent
        try:
            proc = runner(argv, input=brief, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                          timeout=timeout_s, env=agent_env(), cancel=cancel)
        except subprocess.TimeoutExpired as exc:
            raise AgentError(f"claude-cli provider: the agent timed out after {timeout_s:g}s",
                             unreported=True) from exc
        except OSError as exc:
            raise ProviderError(f"claude-cli provider: could not run {argv[0]}: {exc}") from exc
        result = parse_act_json(proc.stdout)
        if result is not None and (proc.returncode == 0 or not result.ok):
            return result
        if result is None:
            reason = (f"stdout was not JSON: {str(proc.stdout)[:200]!r}" if proc.returncode == 0
                      else f"exit {proc.returncode}: {cli_error_message(proc.stdout, proc.stderr)}")
            raise AgentError(f"claude-cli provider: {reason}", unreported=True)
        raise AgentError(
            f"claude-cli provider: exit {proc.returncode}: {cli_error_message(proc.stdout, proc.stderr)}",
            usd=result.usd, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            unreported=result.usd is None,
        )

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        response_format: dict[str, Any] | None = None,
    ) -> Completion:
        # max_tokens/temperature/response_format have no CLI equivalent; the
        # loop's output bound is enforced by the prompt instead. Recorded here
        # so the signature stays honest across backends.
        del max_tokens, temperature, response_format
        system, rest = split_system(messages)
        prompt = "\n\n".join(
            f"[{m.get('role', 'user')}]\n{m.get('content', '')}" if len(rest) > 1
            else m.get("content", "")
            for m in rest
        )
        system_file: str | None = None
        if system:
            handle = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", suffix=".system.md", delete=False
            )
            with handle:
                handle.write(system)
            system_file = handle.name
        argv = self.build_argv(model=model, system_file=system_file)
        try:
            proc = self._runner(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(
                f"claude-cli provider: timed out after {self._timeout:g}s"
            ) from exc
        except OSError as exc:
            raise ProviderError(f"claude-cli provider: could not run {argv[0]}: {exc}") from exc
        finally:
            if system_file:
                try:
                    os.unlink(system_file)
                except OSError:
                    pass
        if proc.returncode != 0:
            raise ProviderError(
                f"claude-cli provider: exit {proc.returncode}: "
                f"{cli_error_message(proc.stdout, proc.stderr)}"
            )
        return parse_cli_json(proc.stdout, fallback_model=model, provider=self.name)


def cli_error_message(stdout: str | None, stderr: str | None) -> str:
    """The human-readable reason a CLI call failed.

    On a failed call the CLI still prints its JSON payload, and the useful
    sentence ("You've hit your session limit — resets 12:30pm") sits in its
    `result` field. Prefer that over a 500-character tail of JSON.
    """
    try:
        payload = json.loads(stdout or "")
    except (TypeError, json.JSONDecodeError):
        payload = None
    if isinstance(payload, dict):
        status = [
            f"{key}={payload[key]}"
            for key in ("api_error_status", "subtype", "terminal_reason")
            if payload.get(key)
        ]
        result = payload.get("result")
        if isinstance(result, str) and result.strip():
            text = result.strip()[:300]
            return f"{text} [{', '.join(status)}]" if status else text
        if status:
            return ", ".join(status)
    return (stderr or stdout or "").strip()[-300:] or "no output"


def _split_tokens(payload: dict[str, Any]) -> tuple[int, int, int]:
    """`(uncached input, cache creation + cache reads, output)` from `usage`."""
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        return 0, 0, 0

    def count(key: str) -> int:
        try:
            return int(usage.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0

    cached = count("cache_creation_input_tokens") + count("cache_read_input_tokens")
    return count("input_tokens"), cached, count("output_tokens")


def _usage_tokens(payload: dict[str, Any]) -> tuple[int, int]:
    # Cached tokens are still billed (at a different rate); counting them keeps
    # the token cap honest for one completion. total_cost_usd carries the exact money.
    uncached, cached, output = _split_tokens(payload)
    return uncached + cached, output


def parse_act_json(stdout: str | None) -> AgentResult | None:
    """One agent run's `--output-format json` payload as an `AgentResult`;
    `None` when stdout is not a JSON object. A run stopped at a limit
    (`ACT_LIMITS`) is `ok=False`, its edits used as they are; any other
    `is_error` raises `AgentError` with the cost it reported. The input tokens
    are the uncached ones (`AgentResult`); cache reads and creation are apart."""
    try:
        payload = json.loads(stdout or "")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    input_tokens, cached, output_tokens = _split_tokens(payload)
    cost = payload.get("total_cost_usd")
    usd = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    ok, text = True, payload.get("result")
    if payload.get("is_error"):
        subtype = payload.get("subtype")
        if subtype not in ACT_LIMITS:
            raise AgentError(
                f"claude-cli provider: the agent reported an error: {cli_error_message(stdout, None)}",
                usd=usd, input_tokens=input_tokens, output_tokens=output_tokens, unreported=usd is None,
            )
        limit = "the turn limit" if subtype == "error_max_turns" else "its spending cap"
        ok, text = False, f"stopped at {limit}; its edits are used as they are"
    return AgentResult(ok=ok, text=text if isinstance(text, str) else "", usd=usd, requests=None,
                       input_tokens=input_tokens, output_tokens=output_tokens, cached_input_tokens=cached)


def parse_cli_json(
    stdout: str, *, fallback_model: str, provider: str = "claude-cli"
) -> Completion:
    """Turn one `--output-format json` payload into a `Completion`."""
    try:
        payload = json.loads(stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProviderError(
            f"claude-cli provider: stdout was not JSON: {str(stdout)[:200]!r}"
        ) from exc
    if not isinstance(payload, dict):
        raise ProviderError("claude-cli provider: expected a JSON object on stdout")
    if payload.get("is_error"):
        raise ProviderError(
            f"claude-cli provider: CLI reported an error: {cli_error_message(stdout, None)}"
        )
    text = payload.get("result")
    if not isinstance(text, str) or not text.strip():
        raise ProviderError("claude-cli provider: payload had no 'result' text")

    input_tokens, output_tokens = _usage_tokens(payload)

    model = fallback_model
    model_usage = payload.get("modelUsage")
    if isinstance(model_usage, dict) and model_usage:
        model = next(iter(model_usage))

    cost = payload.get("total_cost_usd")
    return Completion(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        model=str(model),
        provider=provider,
        usd=float(cost) if isinstance(cost, (int, float)) else None,
        raw={"session_id": payload.get("session_id"), "num_turns": payload.get("num_turns")},
    )
