"""OpenRouter backend -- the OpenAI client pointed at OpenRouter's base URL.

Credentials come from the environment only:

    OPENROUTER_API_KEY   the key
    OPENROUTER_BASE_URL  optional override, defaults to the public endpoint

Everything about the request, the bounded retry and the error classification is
`openai_shape`, shared with the `azure` backend. The only differences here are
the client construction and `max_tokens` (OpenRouter has never wanted
`max_completion_tokens`, so there is no fallback to make).
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from evolvekit.providers.base import Completion, Message, ProviderError
from evolvekit.providers.openai_shape import (
    completion_from_chat,
    embed_openai,
    request_with_retry,
)

__all__ = ["OpenRouterProvider", "DEFAULT_BASE_URL"]

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider:
    """Chat completions through `openai.OpenAI` with an OpenRouter base URL."""

    name = "openrouter"

    def __init__(
        self,
        *,
        client: Any | None = None,
        sleep: Callable[[float], None] | None = None,
        **options: Any,
    ) -> None:
        self._options = options
        self._sleep = sleep or time.sleep
        self.last_embed_tokens = 0
        self._client = client if client is not None else self._build_client()

    def _build_client(self) -> Any:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - depends on install
            raise ProviderError(
                "the 'openai' package is required for the openrouter provider"
            ) from exc
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ProviderError(
                "openrouter provider: missing environment variable "
                "OPENROUTER_API_KEY; copy .env.example to .env and fill it in"
            )
        return OpenAI(
            api_key=api_key,
            base_url=os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL),
        )

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> Completion:
        response = request_with_retry(
            lambda: self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
            provider=self.name,
            sleep=self._sleep,
        )
        return completion_from_chat(response, model=model, provider=self.name)

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        """OpenAI-compatible embeddings through the configured base URL."""
        return embed_openai(
            self._client, texts, model=model, owner=self, sleep=self._sleep
        )
