"""Provider registry: config name -> backend instance."""

from __future__ import annotations

from pathlib import Path

from evolvekit.config import ModelConfig
from evolvekit.providers.base import (
    Completion,
    Message,
    Provider,
    ProviderError,
    split_system,
)

__all__ = [
    "Completion",
    "Message",
    "Provider",
    "ProviderError",
    "split_system",
    "build_provider",
]


def build_provider(model_cfg: ModelConfig, *, base_dir: Path | None = None) -> Provider:
    """Instantiate the backend named by `model_cfg.provider`.

    Imports are local so that using the `fake` backend never requires `openai`
    and never touches the `claude` CLI.
    """
    options = dict(model_cfg.options)
    if model_cfg.provider == "fake":
        from evolvekit.providers.fake import FakeProvider

        return FakeProvider(base_dir=base_dir, **options)
    if model_cfg.provider == "azure":
        from evolvekit.providers.azure import AzureOpenAIProvider

        return AzureOpenAIProvider(**options)
    if model_cfg.provider == "openrouter":
        from evolvekit.providers.openrouter import OpenRouterProvider

        return OpenRouterProvider(**options)
    if model_cfg.provider == "claude-cli":
        from evolvekit.providers.claude_cli import ClaudeCliProvider

        return ClaudeCliProvider(**options)
    raise ProviderError(f"unknown provider {model_cfg.provider!r}")
