"""The neutral LLM interface every backend implements.

Deliberately tiny: one method, one return type. Anything a backend knows that
the loop does not need (streaming, tools, session ids) stops here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "Message",
    "Completion",
    "AgentError",
    "AgentResult",
    "Provider",
    "EmbeddingProvider",
    "ProviderError",
    "split_system",
    "estimate_tokens",
    "embedding_tokens",
    "supports_act",
]

Message = dict[str, str]


class ProviderError(RuntimeError):
    """A backend failed to produce a usable completion."""


@dataclass(frozen=True)
class Completion:
    """One model response plus everything the ledger needs to price it."""

    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str
    usd: float | None = None
    """Provider-reported cost. When present the ledger trusts it over the
    configured price table (the `claude-cli` backend reports real spend)."""
    raw: dict[str, Any] | None = None
    requests: float | None = None
    """Requests counted against a plan rather than dollars (`copilot-cli`:
    Copilot requests). `None` for every backend that bills in money."""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class AgentResult:
    """What one `act` call did: the agent's last words and what it cost.

    `ok` is false when the agent stopped at a limit (turns, money) before it
    said it was done; its edits are still in the folder and are used as they
    are (decision D14). A call that could not run, or failed outright, raises
    `ProviderError` instead (`AgentError` when it ran, with what it cost).

    `input_tokens` is the uncached prompt only: an agent session re-reads its
    whole context every turn, and counting cache reads would spend
    `budget.max_tokens` in a few calls. The cached tokens are kept apart, for
    the record; `usd` bounds the money."""

    ok: bool
    text: str
    usd: float | None = None
    requests: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


class AgentError(ProviderError):
    """An `act` call that ran and then failed: a timeout, a Stop request, a
    crash, an error the agent reported. What it cost, when the tool said so
    (`usd`, `requests`, the tokens); `unreported` when nothing came back, so
    the operator charges the call's cap rather than nothing."""

    def __init__(
        self,
        message: str,
        *,
        usd: float | None = None,
        requests: float | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        unreported: bool = False,
    ) -> None:
        super().__init__(message)
        self.usd = usd
        self.requests = requests
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.unreported = unreported


@runtime_checkable
class Provider(Protocol):
    """Structural type for all backends."""

    name: str

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        response_format: dict[str, Any] | None = None,
    ) -> Completion:
        """`response_format` is the OpenAI-shaped request's field (for the
        assistant, `{"type": "json_object"}`): passed through by the backends
        whose API has it, accepted and ignored by the others."""
        ...


def supports_act(provider: object) -> bool:
    """The optional agent capability (design §5.4), asked for like `embed`:

        act(brief: str, *, cwd: Path, model: str, timeout_s: float,
            read_dirs: Sequence[Path] = (), max_usd: float | None = None,
            cancel: threading.Event | None = None) -> AgentResult

    A coding agent edits the files under `cwd` as `brief` asks, may read the
    folders `read_dirs` names, spends at most `max_usd` when the tool can be
    told so, and is ended (its whole process tree) when `cancel` is set."""
    return callable(getattr(provider, "act", None))


@runtime_checkable
class EmbeddingProvider(Protocol):
    """The optional second capability: turning text into vectors.

    Deliberately *not* part of `Provider`. Only the near-duplicate gate wants
    it, only two of the four backends can offer it, and a Protocol that every
    backend had to satisfy would force `claude-cli` to grow a method whose only
    body is a raise. Ask with `hasattr(provider, "embed")`.

    Implementations set `last_embed_tokens` to whatever the backend reported
    for the most recent call, so the ledger can price it; `embedding_tokens()`
    falls back to an estimate when a backend reports nothing.
    """

    name: str
    last_embed_tokens: int

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]: ...


def split_system(messages: list[Message]) -> tuple[str, list[Message]]:
    """Separate leading system messages from the conversational remainder.

    Backends that take the system prompt out-of-band (the `claude` CLI) need
    this; chat-completions backends do not.
    """
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    return "\n\n".join(system_parts), rest


def estimate_tokens(text: str) -> int:
    """Crude 4-chars-per-token estimate, used only when a backend reports none."""
    return max(1, len(text) // 4)


def embedding_tokens(provider: object, texts: list[str]) -> int:
    """Input tokens for the last `embed()` call: reported if known, else estimated."""
    reported = getattr(provider, "last_embed_tokens", 0)
    try:
        number = int(reported)
    except (TypeError, ValueError):
        number = 0
    return number if number > 0 else sum(estimate_tokens(t) for t in texts)
