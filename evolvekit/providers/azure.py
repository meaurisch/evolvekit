"""Azure OpenAI backend (Azure OpenAI and Azure AI Foundry resources).

Credentials come from the environment only:

    AZURE_OPENAI_ENDPOINT     the resource root. All three forms work:
                                https://<resource>.openai.azure.com          (classic Azure OpenAI)
                                https://<resource>.cognitiveservices.azure.com   (AI Foundry)
                                https://<resource>.services.ai.azure.com         (AI Foundry)
                              A path copied off the Foundry portal
                              (`/openai/v1`, `/models`, a trailing slash) is
                              stripped -- the client builds its own routes.
    AZURE_OPENAI_API_KEY      the key. `AZURE_OPENAI_KEY`, the name some
                              Foundry portal snippets use, is accepted too.
    AZURE_OPENAI_API_VERSION  optional, defaults to 2024-10-21. Foundry
                              features newer than the GA surface (strict
                              json_schema and friends) want 2025-04-01-preview.

With an endpoint but **no key**, the backend falls back to keyless Microsoft
Entra ID auth via `azure.identity.DefaultAzureCredential` -- the route a
corporate Foundry resource with key auth disabled requires. That path needs
`pip install evolvekit[entra]` (or `azure-identity` directly) and whatever
login the machine's policy expects (`az login`, managed identity, ...).

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

# The token scope every Azure AI Foundry / Azure OpenAI resource accepts for
# Entra ID bearer auth, regardless of which of the three endpoint domains it
# answers on.
ENTRA_SCOPE = "https://cognitiveservices.azure.com/.default"

# Path suffixes the Foundry portal's copy-paste snippets append to the
# resource root. `openai.AzureOpenAI` wants the bare root and builds its own
# `/openai/deployments/...` routes, so a pasted suffix would 404 every call.
_ENDPOINT_SUFFIXES = ("/openai/v1", "/openai", "/models")


def _normalise_endpoint(raw: str | None) -> str | None:
    if not raw:
        return raw
    endpoint = raw.strip().rstrip("/")
    for suffix in _ENDPOINT_SUFFIXES:
        if endpoint.endswith(suffix):
            endpoint = endpoint[: -len(suffix)]
            break
    return endpoint


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
        endpoint = _normalise_endpoint(os.environ.get("AZURE_OPENAI_ENDPOINT"))
        api_key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get(
            "AZURE_OPENAI_KEY"
        )
        if not endpoint:
            raise ProviderError(
                "azure provider: missing environment variable(s) "
                "['AZURE_OPENAI_ENDPOINT']; copy .env.example to .env and "
                "fill it in"
            )
        api_version = os.environ.get(
            "AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION
        )
        if api_key:
            return AzureOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=api_version,
            )
        # No key set: keyless Entra ID auth, the route a Foundry resource
        # with key auth disabled requires.
        try:
            from azure.identity import (
                DefaultAzureCredential,
                get_bearer_token_provider,
            )
        except ImportError as exc:
            raise ProviderError(
                "azure provider: missing environment variable(s) "
                "['AZURE_OPENAI_API_KEY'] (or AZURE_OPENAI_KEY) -- copy "
                ".env.example to .env and fill it in. For keyless Entra ID "
                "auth instead, pip install 'evolvekit[entra]'"
            ) from exc
        return AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=get_bearer_token_provider(
                DefaultAzureCredential(), ENTRA_SCOPE
            ),
            api_version=api_version,
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
