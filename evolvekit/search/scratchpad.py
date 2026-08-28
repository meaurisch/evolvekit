"""The meta-scratchpad: the small model's running notes on what has worked.

Every `search.scratchpad_every` generations the cheap model is shown the
current elites -- their delta summaries and score lines, never their source --
and asked for at most 40 lines of lessons. That text is cached in the run
directory and pasted into every subsequent prompt body.

This is the proposal's replacement for v1's separate reflection call. It costs
one small-model call per M generations instead of one strong-model call per
candidate, and it is the only part of the prompt that carries memory across
generations, which is exactly why it is capped in lines.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from evolvekit.config import Config
from evolvekit.prompts import SCRATCHPAD_LIMIT
from evolvekit.providers.base import Completion, Provider, ProviderError

__all__ = ["Scratchpad", "build_scratchpad_messages", "SCRATCHPAD_INSTRUCTION"]

SCRATCHPAD_INSTRUCTION = """\
You are keeping the running notes for an automated search over one heuristic.

Below are the current elites of the archive: for each, what it changed and how \
it scored. Write the notes the next mutation should have in front of it.

Rules:
- At most {limit} lines. Shorter is better.
- Only claims the scores below support. No code.
- Say which directions paid off and which did not, and name the ids.
- No preamble, no closing remarks: the lines themselves are the whole answer.
"""


def build_scratchpad_messages(
    config: Config, entries: Sequence[str]
) -> list[dict[str, str]]:
    """Two messages: the standing instruction, and the elite digest."""
    problem = config.problem.description.strip().splitlines()
    context = " ".join(problem[:3]).strip()
    body = "\n".join(f"- {entry}" for entry in entries) or "- (no elites yet)"
    return [
        {
            "role": "system",
            "content": SCRATCHPAD_INSTRUCTION.format(limit=SCRATCHPAD_LIMIT),
        },
        {"role": "user", "content": f"Problem: {context}\n\n## Elites\n{body}"},
    ]


class Scratchpad:
    """The cached notes, and the rule for when they are stale."""

    def __init__(self, run_dir: str | Path, every: int) -> None:
        self.path = Path(run_dir) / "scratchpad.md"
        self.every = int(every)
        self.text: str = ""
        self.refreshed_at: int | None = None
        if self.path.is_file():
            self.text = self.path.read_text(encoding="utf-8")

    @property
    def enabled(self) -> bool:
        return self.every > 0

    def due(self, generation: int) -> bool:
        return self.enabled and generation > 0 and generation % self.every == 0

    def store(self, text: str, generation: int) -> str:
        """Trim to the line budget, cache on disk, return what will be shown."""
        lines = [line.rstrip() for line in text.strip().splitlines()]
        trimmed = "\n".join(lines[:SCRATCHPAD_LIMIT]).strip()
        self.text = trimmed
        self.refreshed_at = generation
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            f"<!-- refreshed at generation {generation} -->\n{trimmed}\n",
            encoding="utf-8",
            newline="\n",
        )
        return trimmed

    def refresh(
        self,
        *,
        config: Config,
        provider: Provider,
        entries: Sequence[str],
        generation: int,
    ) -> tuple[str, Completion | None, list[dict[str, str]], str | None]:
        """One small-model call. Returns `(text, completion, messages, error)`.

        A failure here is never fatal: the notes simply stay as they were.
        """
        model_cfg = config.models.by_role("small")
        messages = build_scratchpad_messages(config, entries)
        try:
            completion = provider.complete(
                messages,
                model=model_cfg.model,
                max_tokens=model_cfg.max_tokens,
                temperature=model_cfg.temperature,
            )
        except ProviderError as exc:
            return self.text, None, messages, str(exc)
        return self.store(completion.text, generation), completion, messages, None
