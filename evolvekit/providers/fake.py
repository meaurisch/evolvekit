"""Scripted backend for tests and the offline demo. Never touches the network.

Configured from YAML under `models.<role>.options`:

    options:
      responses: ["...", "..."]      # inline, in order
      responses_path: replies.yaml   # or a YAML list, relative to the config
      cycle: true                    # repeat the script (default) or hold last

A scripted entry is either a plain string, taken from the queue in order, or a
mapping with a `when` substring matched against the prompt:

    - when: "scheduled big step"
      response: |
        ```python
        ...
        ```

Keyed entries are how the offline demo scripts a *specific* call -- the
scratchpad refresh, a crossover, the novelty re-prompt -- without having to
predict how many queued responses everything before it will have eaten. They
are matched in file order, first match wins, each consumed once until the ones
for that key run out, after which the last one repeats.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from evolvekit.providers.base import Completion, Message, ProviderError, estimate_tokens

__all__ = ["FakeProvider"]


class FakeProvider:
    """Replays a fixed list of responses, recording every call it received."""

    name = "fake"

    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        base_dir: Path | None = None,
        cycle: bool = True,
        **options: Any,
    ) -> None:
        scripted: list[Any] = list(responses or [])
        inline = options.get("responses")
        if inline:
            if not isinstance(inline, list):
                raise ProviderError("fake provider: options.responses must be a list")
            scripted += list(inline)
        path = options.get("responses_path")
        if path:
            scripted += _load_responses(Path(path), base_dir)
        if not scripted:
            raise ProviderError(
                "fake provider: no scripted responses; set options.responses or "
                "options.responses_path"
            )
        self._responses = [str(r) for r in scripted if not isinstance(r, dict)]
        self._keyed = [_KeyedResponse.parse(r) for r in scripted if isinstance(r, dict)]
        self._cycle = bool(options.get("cycle", cycle))
        self._index = 0
        self.calls: list[dict[str, Any]] = []
        self.embed_calls: list[dict[str, Any]] = []
        self.embed_dimensions = int(options.get("embed_dimensions", 16))
        self.last_embed_tokens = 0

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> Completion:
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        prompt = "\n".join(m.get("content", "") for m in messages)
        keyed = self._match(prompt)
        if keyed is not None:
            return self._completion(
                keyed, model, sum(len(m.get("content", "")) for m in messages)
            )
        if self._index >= len(self._responses):
            if not self._cycle:
                raise ProviderError(
                    f"fake provider: script exhausted after "
                    f"{len(self._responses)} response(s)"
                )
            self._index = 0
        if not self._responses:
            raise ProviderError(
                "fake provider: every scripted response is keyed and none of "
                "them matched this prompt"
            )
        text = self._responses[self._index]
        self._index += 1
        prompt_chars = sum(len(m.get("content", "")) for m in messages)
        return self._completion(text, model, prompt_chars)

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        """Deterministic hash-seeded vectors. Identical text -> identical vector.

        Components are drawn from [0, 1), so the cosine of two unrelated
        vectors is positive but well below 1 while the cosine of a text with
        itself is exactly 1. That is enough for a test to exercise both sides
        of the near-duplicate threshold without a network.
        """
        self.embed_calls.append({"texts": list(texts), "model": model})
        vectors = [_fake_vector(text, self.embed_dimensions) for text in texts]
        self.last_embed_tokens = sum(estimate_tokens(text) for text in texts)
        return vectors

    def _completion(self, text: str, model: str, prompt_chars: int) -> Completion:
        return Completion(
            text=text,
            input_tokens=estimate_tokens("x" * prompt_chars),
            output_tokens=estimate_tokens(text),
            model=model,
            provider=self.name,
        )


    def _match(self, prompt: str) -> str | None:
        """First keyed entry whose `when` is in the prompt, consumed in order."""
        matching = [entry for entry in self._keyed if entry.when in prompt]
        if not matching:
            return None
        for entry in matching:
            if not entry.used:
                entry.used = True
                return entry.response
        return matching[-1].response


@dataclass
class _KeyedResponse:
    when: str
    response: str
    used: bool = False

    @staticmethod
    def parse(raw: dict[str, Any]) -> "_KeyedResponse":
        unknown = sorted(set(raw) - {"when", "response"})
        if unknown:
            raise ProviderError(
                f"fake provider: keyed response has unknown key(s) {unknown}; "
                "expected `when` and `response`"
            )
        when = raw.get("when")
        response = raw.get("response")
        if not isinstance(when, str) or not when.strip():
            raise ProviderError("fake provider: keyed response needs a `when` string")
        if response is None:
            raise ProviderError(
                f"fake provider: keyed response for {when!r} has no `response`"
            )
        return _KeyedResponse(when=when, response=str(response))


def _load_responses(path: Path, base_dir: Path | None) -> list[Any]:
    resolved = path if path.is_absolute() else (base_dir or Path.cwd()) / path
    if not resolved.is_file():
        raise ProviderError(f"fake provider: responses_path not found: {resolved}")
    data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ProviderError(f"fake provider: {resolved} must contain a YAML list")
    return [item if isinstance(item, dict) else str(item) for item in data]


def _fake_vector(text: str, dimensions: int) -> list[float]:
    """A stable pseudo-random unit-positive vector for `text`."""
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    return [rng.random() for _ in range(max(1, dimensions))]
