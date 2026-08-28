"""Azure OpenAI backend (Azure OpenAI and Azure AI Foundry resources).

Credentials come from the environment only:

    AZURE_OPENAI_ENDPOINT     https://<resource>.openai.azure.com
    AZURE_OPENAI_API_KEY      the key
    AZURE_OPENAI_API_VERSION  optional, defaults to 2024-10-21

`models.<role>.model` is the **deployment** name, not the model name -- Azure
routes on deployment, and a deployment called `gpt-4o` and a model called
`gpt-4o` are different strings that happen to look alike.

The request shape, the retry and the error classification live in
`openai_shape`, which the `openrouter` backend shares. What is Azure's alone is
the client construction, the environment contract, and the one-shot fallback
from `max_completion_tokens` to `max_tokens` for older API versions.

This backend has never been run against a real Azure endpoint from this repo.
`tests/test_azure_contract.py` drives it through recorded responses instead --
the success shapes for chat and embeddings, and the three failures that a work
laptop actually meets: a 429 with a `Retry-After`, a content filter, and a bad
key.
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

__all__ = ["AzureOpenAIProvider", "AzureProvider", "DEFAULT_API_VERSION"]

DEFAULT_API_VERSION = "2024-10-21"


class AzureOpenAIProvider:
    """Chat completions and embeddings through `openai.AzureOpenAI`."""

    name = "azure"

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
            from openai import AzureOpenAI
        except ImportError as exc:  # pragma: no cover - depends on install
            raise ProviderError(
                "the 'openai' package is required for the azure provider"
            ) from exc
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
        api_key = os.environ.get("AZURE_OPENAI_API_KEY")
        missing = [
            n
            for n, v in (
                ("AZURE_OPENAI_ENDPOINT", endpoint),
                ("AZURE_OPENAI_API_KEY", api_key),
            )
            if not v
        ]
        if missing:
            raise ProviderError(
                f"azure provider: missing environment variable(s) {missing}; "
                "copy .env.example to .env and fill it in"
            )
        return AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=api_key,
            api_version=os.environ.get(
                "AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION
            ),
        )

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> Completion:
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
        }
        try:
            response = self._send(kwargs)
        except TypeError:
            # Older Azure API versions only know `max_tokens`. Exactly one
            # retry -- the post-mortem's nested-retry explosion stops here.
            # It travels through `reraise` so the shared layer does not turn it
            # into a ProviderError before this handler ever sees it.
            kwargs.pop("max_completion_tokens")
            kwargs["max_tokens"] = max_tokens
            response = self._send(kwargs)
        return completion_from_chat(response, model=model, provider=self.name)

    def _send(self, kwargs: dict[str, Any]) -> Any:
        return request_with_retry(
            lambda: self._client.chat.completions.create(**kwargs),
            provider=self.name,
            reraise=(TypeError,),
            sleep=self._sleep,
        )

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        """Azure embeddings. `model` is the *deployment* name, as everywhere else."""
        return embed_openai(
            self._client, texts, model=model, owner=self, sleep=self._sleep
        )


AzureProvider = AzureOpenAIProvider
"""The name this class had before it grew its contract tests. Kept so that a
config, a script or a test written against the old one still resolves."""
