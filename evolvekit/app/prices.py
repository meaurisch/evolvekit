"""What a model costs, in USD per million input and output tokens: the one
table the app has, for the assistant's answers and for AI search help's cap.

A price the app does not know is `None`, never a guess: the assistant then
says the cost is not known, and AI search help refuses a dollar cap it could
not hold. OpenRouter and the Claude CLI report what each call cost, so they
need no entry here; an Azure deployment does, under Settings > Models.
"""

from __future__ import annotations

from typing import Any

__all__ = ["KNOWN", "price_of"]

KNOWN: dict[str, tuple[float, float]] = {"anthropic/claude-sonnet-5": (2.0, 10.0)}
"""The models the app suggests, and what they cost when nobody says otherwise."""


def price_of(model: str, extra: dict[str, Any] | None = None) -> tuple[float, float] | None:
    """`(in, out)` per million tokens for `model`, from `extra` (the settings'
    `prices`, which win) or the table; `None` when neither knows it."""
    for table in (extra or {}, KNOWN):
        pair = table.get(model)
        if pair is not None:
            return (float(pair[0]), float(pair[1]))
    return None
