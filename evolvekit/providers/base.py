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
    "Provider",
    "EmbeddingProvider",
    "ProviderError",
    "split_system",
    "estimate_tokens",
    "embedding_tokens",
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

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


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
    ) -> Completion: ...


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
