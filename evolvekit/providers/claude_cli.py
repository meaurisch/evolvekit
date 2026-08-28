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
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any

from evolvekit.providers.base import (
    Completion,
    Message,
    ProviderError,
    split_system,
)

__all__ = ["ClaudeCliProvider", "parse_cli_json"]

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
        argv = [self._resolve(), *BASE_FLAGS, "--model", model]
        argv += ["--disallowedTools", DISALLOWED_TOOLS]
        # The system prompt goes through a file: a problem description plus a
        # 40-line scratchpad blew past the Windows command-line limit in the
        # first circle-packing run ("The command line is too long.").
        if system_file:
            argv += ["--system-prompt-file", system_file]
        elif system:
            argv += ["--system-prompt", system]
        return argv

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> Completion:
        # max_tokens/temperature have no CLI equivalent; the loop's output
        # bound is enforced by the prompt instead. Recorded here so the
        # signature stays honest across backends.
        del max_tokens, temperature
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

    usage = payload.get("usage") or {}
    # Cached tokens are still billed (at a different rate); counting them keeps
    # the token cap honest. total_cost_usd carries the exact money.
    input_tokens = sum(
        int(usage.get(key, 0) or 0)
        for key in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )
    output_tokens = int(usage.get("output_tokens", 0) or 0)

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
