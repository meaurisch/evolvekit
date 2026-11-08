"""GitHub Copilot CLI backend -- the user's own Copilot plan, driven as a subprocess.

    copilot -p <prompt> --deny-tool=shell --deny-tool=write --no-ask-user -s [--model=<model>]

Flags, from GitHub's programmatic reference
(docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference):

    -p PROMPT              run one prompt non-interactively, then exit
    -s / --silent          print only the answer, no stats or decoration
    --output-format=json   JSONL, one object per line (fields not documented)
    --model=MODEL          omitted when the model is empty: the CLI's default
    --no-ask-user          never stop to ask the user anything
    --deny-tool=TOOL       tool kinds are shell, write, read, url, memory, MCP names

A non-interactive run already denies every tool that needs approval when no
`--allow-*` flag is given; `--deny-tool=shell` and `--deny-tool=write` say so
again, explicitly. The docs give the `--deny-tool=TOOL` form and the tool kinds
but no deny example, so each tool gets its own flag rather than a list.

The CLI has no system-prompt flag: the system messages are folded into the
prompt text. The prompt goes on the command line (`-p`), because the docs do
not spell out reading it from stdin. On Windows `copilot` is usually the npm
shim `copilot.cmd`; cmd.exe limits a command line to 8191 characters and
re-reads quotes and `&|^%`, so the shim is never run with a prompt: the
package's own entry script is run with `node` instead (CreateProcess allows
32767 characters), and a prompt that still does not fit is refused.

Output is `-s` text by default. `options.output_format: json` asks for JSONL,
whose fields GitHub does not document: it is parsed tolerantly (the last
assistant message wins; premium-request counts are read when present) and read
as plain text when no answer is found in it.

No API key: the CLI carries the user's GitHub sign-in (`copilot`, then
`/login`), or a token in COPILOT_GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN.
`COPILOT_CLI_PATH` overrides the binary (a `.js` path is run with node). The
child runs with `COPILOT_AUTO_UPDATE=false`.

Cost: Copilot counts requests against the plan, not dollars. `usd` is 0.0 so
dollar caps hold; `requests` is 1 per call unless the JSON output reports
premium requests.

`act` (design §5.4) runs it as a coding agent in a folder, unverified against
a real Copilot CLI (see `build_act_argv`):

    copilot [--add-dir <the brief's folder>] -p <short prompt> --allow-tool=write
        --allow-tool=shell --no-ask-user -s [--model=<model>]
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
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
    estimate_tokens,
    split_system,
)

__all__ = [
    "CopilotCliError",
    "CopilotCliProvider",
    "KINDS",
    "classify_failure",
    "parse_copilot_output",
    "resolve_command",
]

DEFAULT_TIMEOUT = 600.0
KINDS = ("missing", "not_signed_in", "policy_blocked", "failed")
DENIED_TOOLS = ("shell", "write")
ACT_TOOLS = ("write", "shell")
"""What `act` allows: the agent edits files and runs commands in its folder."""
SIGN_IN = ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")
"""The CLI's own sign-in, kept in the agent's environment."""
MAX_DIRECT_COMMAND = 30_000
"""Characters allowed on a Windows command line run straight through
CreateProcess (32767), less a margin for quoting."""
MAX_SHIM_COMMAND = 8_000
"""cmd.exe's limit is 8191: only an executable we cannot see behind is run this way."""

MISSING = "GitHub Copilot CLI is not installed: install it with npm install -g @github/copilot"
NOT_SIGNED_IN = "Copilot CLI is not signed in: run `copilot`, then `/login`"
POLICY_BLOCKED = "Your organisation's Copilot policy blocks the CLI"

_POLICY_WORDS = ("access denied by policy settings",)
_SIGN_IN_WORDS = (
    "not logged in",
    "not signed in",
    "/login",
    "authenticate",
    "authentication required",
    "no authentication",
    "login required",
)


class CopilotCliError(ProviderError):
    """A Copilot CLI failure, with `kind` in `KINDS` for the app to read."""

    def __init__(self, message: str, kind: str = "failed") -> None:
        super().__init__(message)
        self.kind = kind if kind in KINDS else "failed"


def classify_failure(stdout: str | None, stderr: str | None) -> CopilotCliError:
    """The error for a failed run: what kind, in one plain sentence."""
    text = f"{stdout or ''}\n{stderr or ''}"
    low = text.lower()
    if any(word in low for word in _POLICY_WORDS):
        return CopilotCliError(POLICY_BLOCKED, "policy_blocked")
    if any(word in low for word in _SIGN_IN_WORDS):
        return CopilotCliError(NOT_SIGNED_IN, "not_signed_in")
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    if not lines:
        lines = [line.strip() for line in (stdout or "").splitlines() if line.strip()]
    last = lines[-1][:300] if lines else "no output"
    return CopilotCliError(f"GitHub Copilot CLI stopped with an error: {last}", "failed")


def _package_entry(shim: Path) -> Path | None:
    """The entry script behind an npm shim, from the package's `bin`.

    npm's global layout: `<prefix>/copilot.cmd` next to
    `<prefix>/node_modules/@github/copilot/package.json`.
    """
    package = shim.parent / "node_modules" / "@github" / "copilot"
    try:
        meta = json.loads((package / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    binary = meta.get("bin")
    if isinstance(binary, dict):
        binary = binary.get("copilot") or next(iter(binary.values()), None)
    if not isinstance(binary, str) or not binary.strip():
        return None
    entry = (package / binary).resolve()
    return entry if entry.is_file() else None


def _node(near: Path | None) -> str | None:
    """`node.exe` next to the shim (what npm's shim prefers), else on PATH."""
    if near is not None:
        for name in ("node.exe", "node"):
            candidate = near / name
            if candidate.is_file():
                return str(candidate)
    return shutil.which("node")


def resolve_command(executable: str) -> tuple[list[str], bool]:
    """The argv prefix that runs Copilot CLI, and whether it goes through cmd.exe.

    A `.cmd` / `.bat` npm shim is looked behind: the package's entry script is
    run with node, so the prompt never passes through cmd.exe.
    """
    found = shutil.which(executable)
    if not found and os.path.isfile(executable):
        found = executable
    if not found:
        raise CopilotCliError(MISSING, "missing")
    path = Path(found)
    suffix = path.suffix.lower()
    if suffix in (".js", ".mjs", ".cjs"):
        node = _node(None)
        if node is None:
            raise CopilotCliError("GitHub Copilot CLI needs Node.js, and node was not found", "missing")
        return [node, str(path)], False
    if suffix in (".cmd", ".bat"):
        entry = _package_entry(path)
        if entry is not None:
            node = _node(path.parent)
            if node is None:
                raise CopilotCliError("GitHub Copilot CLI needs Node.js, and node was not found", "missing")
            return [node, str(entry)], False
        return [str(path)], True
    return [str(path)], False


def _numbers(value: Any, keys: tuple[str, ...]) -> list[float]:
    """Every number under one of `keys`, anywhere in a JSON value."""
    found: list[float] = []
    if isinstance(value, dict):
        for key, inner in value.items():
            if key in keys and isinstance(inner, (int, float)) and not isinstance(inner, bool):
                found.append(float(inner))
            else:
                found.extend(_numbers(inner, keys))
    elif isinstance(value, list):
        for inner in value:
            found.extend(_numbers(inner, keys))
    return found


def _message_text(event: dict[str, Any]) -> str | None:
    kind = str(event.get("type") or event.get("event") or "").lower()
    data = event.get("data") if isinstance(event.get("data"), dict) else event
    role = str(data.get("role") or event.get("role") or "").lower()
    if "delta" in kind or "user" in kind or "tool" in kind or role in ("user", "tool", "system"):
        return None
    if "assistant" in kind or "message" in kind or role == "assistant" or "result" in kind:
        for key in ("content", "text", "message", "result"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return None


_PREMIUM = ("premiumRequests", "premium_requests", "totalPremiumRequests", "total_premium_requests")
_INPUT = ("inputTokens", "input_tokens", "promptTokens", "prompt_tokens")
_OUTPUT = ("outputTokens", "output_tokens", "completionTokens", "completion_tokens")


def _events(stdout: str) -> list[dict[str, Any]]:
    """Every line as a JSON object, or nothing when one line is not."""
    events: list[dict[str, Any]] = []
    for line in (line for line in (stdout or "").splitlines() if line.strip()):
        try:
            event = json.loads(line)
        except ValueError:
            events = []
            break
        if not isinstance(event, dict):
            events = []
            break
        events.append(event)
    return events


def parse_copilot_output(
    stdout: str, *, prompt: str, model: str, jsonl: bool = False, provider: str = "copilot-cli"
) -> Completion:
    """Turn the CLI's stdout into a `Completion`.

    `jsonl` (asked for with `--output-format=json`): the last whole assistant
    message is the answer, and premium requests and tokens are read wherever
    they appear. Output that is not JSONL after all, or JSONL without an
    answer in it, is read as plain text. Plain `-s` output is never parsed:
    the model's answer may itself be JSON.
    """
    events = _events(stdout) if jsonl else []
    texts = [t for t in (_message_text(e) for e in events) if t]
    if not texts:
        events = []

    requests: float = 1.0
    reported_in: list[float] = []
    reported_out: list[float] = []
    used_model = model
    if events:
        text = texts[-1]
        premium = _numbers(events, _PREMIUM)
        if premium:
            requests = max(premium)
        reported_in, reported_out = _numbers(events, _INPUT), _numbers(events, _OUTPUT)
        for event in events:
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            name = event.get("model") or data.get("model")
            if isinstance(name, str) and name.strip():
                used_model = name.strip()
    else:
        text = (stdout or "").strip()
        if not text:
            raise CopilotCliError("GitHub Copilot CLI gave no answer", "failed")
    return Completion(
        text=text,
        input_tokens=int(sum(reported_in)) if reported_in else estimate_tokens(prompt),
        output_tokens=int(sum(reported_out)) if reported_out else estimate_tokens(text),
        model=str(used_model or "copilot-default"),
        provider=provider,
        usd=0.0,
        raw={"json": bool(events)},
        requests=round(requests, 2),
    )


class CopilotCliProvider:
    """One `copilot -p` invocation per completion."""

    name = "copilot-cli"

    def __init__(
        self,
        *,
        runner: Any | None = None,
        executable: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        output_format: str = "text",
        windows: bool | None = None,
        **options: Any,
    ) -> None:
        self._runner = runner or subprocess.run
        self._executable = executable or os.environ.get("COPILOT_CLI_PATH") or "copilot"
        self._timeout = float(options.get("timeout", timeout))
        self._json = str(output_format).lower() == "json"
        self._windows = (os.name == "nt") if windows is None else bool(windows)

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        """There is no embeddings endpoint behind the CLI; see `ClaudeCliProvider.embed`."""
        raise ProviderError(
            "copilot-cli provider: no embeddings -- GitHub Copilot CLI exposes "
            "completions only. Use search.novelty.near.method: local, or point "
            "models.embed at the azure or openrouter backend."
        )

    def _resolve(self) -> tuple[list[str], bool]:
        return resolve_command(self._executable)

    def build_argv(self, prompt: str, *, model: str) -> list[str]:
        prefix, through_cmd = self._resolve()
        if through_cmd:
            # cmd.exe would re-read the prompt's quotes and & | ^ %: never hand it one.
            raise CopilotCliError(
                f"GitHub Copilot CLI at {prefix[0]} could not be run safely: point COPILOT_CLI_PATH "
                "at its program (copilot.exe, or the package's .js file) rather than the .cmd file",
                "missing",
            )
        argv = [*prefix, "-p", prompt]
        argv += [f"--deny-tool={tool}" for tool in DENIED_TOOLS]
        argv += ["--no-ask-user"]
        argv += ["--output-format=json"] if self._json else ["-s"]
        if model:  # an empty model is the CLI's own default
            argv.append(f"--model={model}")
        self._check_length(argv)
        return argv

    def _check_length(self, argv: list[str]) -> None:
        if self._windows:
            length = len(subprocess.list2cmdline(argv))
            if length > MAX_DIRECT_COMMAND:
                raise CopilotCliError(
                    f"The question is too long for GitHub Copilot CLI on Windows ({length} characters; "
                    f"at most {MAX_DIRECT_COMMAND}). Ask in fewer words, or choose another provider in Settings.",
                    "failed",
                )

    # -- act: the coding agent (design §5.4) --------------------------------
    #
    # UNVERIFIED against a real Copilot CLI: none is installed on the machine
    # this was built on. The argv is GitHub's documented programmatic flags
    # (`--allow-tool=write`, `--allow-tool=shell`, `--add-dir`); only its shape
    # is unit-tested.

    def build_act_argv(self, prompt: str, *, model: str, read_dirs: Sequence[Path | str] = ()) -> list[str]:
        """`copilot -p` as an agent in its folder: writing files and running
        commands allowed, never asking anyone, `read_dirs` readable. `prompt` is
        the short prompt naming the brief file (decision 39), never the brief."""
        prefix, through_cmd = self._resolve()
        if through_cmd:
            raise CopilotCliError(
                f"GitHub Copilot CLI at {prefix[0]} could not be run safely: point COPILOT_CLI_PATH "
                "at its program (copilot.exe, or the package's .js file) rather than the .cmd file",
                "missing",
            )
        argv = [*prefix]
        for folder in read_dirs:
            argv += ["--add-dir", str(folder)]
        argv += ["-p", prompt, *(f"--allow-tool={tool}" for tool in ACT_TOOLS), "--no-ask-user", "-s"]
        if model:
            argv.append(f"--model={model}")
        self._check_length(argv)
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
        """One agent run in `cwd`; its whole tree ended on `timeout_s` or on
        `cancel`. One request per call, as `complete` counts it -- a call ended
        by its time limit or a Stop request too (`AgentError` with
        `requests=1.0`); `max_usd` has no meaning for a plan that counts requests."""
        del max_usd
        argv = self.build_act_argv(brief, model=model, read_dirs=read_dirs)
        env = agent_env(keep=SIGN_IN)
        env["COPILOT_AUTO_UPDATE"] = "false"
        runner = self._runner if self._runner is not subprocess.run else run_agent
        try:
            proc = runner(argv, input=None, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, timeout=timeout_s, env=env, cancel=cancel)
        except subprocess.TimeoutExpired as exc:
            raise AgentError(f"GitHub Copilot CLI timed out after {timeout_s:g}s", usd=0.0, requests=1.0,
                             input_tokens=estimate_tokens(brief)) from exc
        except AgentError as exc:  # a Stop request: the request was made
            raise AgentError(str(exc), usd=0.0, requests=1.0, input_tokens=estimate_tokens(brief)) from exc
        except OSError as exc:  # `run_agent` raises OSError when the program cannot start at all
            raise CopilotCliError(MISSING, "missing") from exc
        if proc.returncode != 0:
            raise classify_failure(proc.stdout, proc.stderr)
        text = (proc.stdout or "").strip()
        return AgentResult(ok=True, text=text, usd=0.0, requests=1.0, input_tokens=estimate_tokens(brief),
                           output_tokens=estimate_tokens(text))

    @staticmethod
    def build_prompt(messages: list[Message]) -> str:
        system, rest = split_system(messages)
        conversation = "\n\n".join(
            f"[{m.get('role', 'user')}]\n{m.get('content', '')}" if len(rest) > 1 else m.get("content", "")
            for m in rest
        )
        return f"{system}\n\n{conversation}" if system else conversation

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        response_format: dict[str, Any] | None = None,
    ) -> Completion:
        # No CLI equivalent for these; kept so the signature matches every backend.
        del max_tokens, temperature, response_format
        prompt = self.build_prompt(messages)
        argv = self.build_argv(prompt, model=model)
        env = dict(os.environ, COPILOT_AUTO_UPDATE="false")
        try:
            proc = self._runner(
                argv,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdin=subprocess.DEVNULL,
                timeout=self._timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise CopilotCliError(f"GitHub Copilot CLI timed out after {self._timeout:g}s", "failed") from exc
        except FileNotFoundError as exc:
            raise CopilotCliError(MISSING, "missing") from exc
        except OSError as exc:
            raise CopilotCliError(f"GitHub Copilot CLI could not be started: {exc}", "failed") from exc
        if proc.returncode != 0:
            raise classify_failure(proc.stdout, proc.stderr)
        if not (proc.stdout or "").strip():
            error = classify_failure(proc.stdout, proc.stderr)
            if error.kind != "failed":
                raise error
            raise CopilotCliError("GitHub Copilot CLI gave no answer", "failed")
        return parse_copilot_output(proc.stdout, prompt=prompt, model=model, jsonl=self._json, provider=self.name)
