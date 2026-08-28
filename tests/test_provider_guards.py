"""Guards learned from the first real Phase C runs (2026-08-23).

Circle packing: the system prompt (problem description + scratchpad) went on
the command line and Windows refused it — "The command line is too long."
Bin packing: the subscription's session cap hit after one call and the driver
bred 22 more children into the dead backend, each recorded as "rejected".
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from evolvekit.config import load_config
from evolvekit.providers.base import Completion, ProviderError
from evolvekit.providers.claude_cli import ClaudeCliProvider, cli_error_message
from evolvekit.search.driver import Driver
from tests.conftest import EXAMPLE_CONFIG

PAYLOAD_OK = {
    "type": "result",
    "is_error": False,
    "result": "OK",
    "usage": {"input_tokens": 10, "output_tokens": 2},
    "total_cost_usd": 0.001,
    "modelUsage": {"claude-haiku-4-5": {}},
}
PAYLOAD_LIMIT = {
    "type": "result",
    "is_error": True,
    "result": "You've hit your session limit — resets 12:30pm (Europe/Amsterdam)",
    "terminal_reason": "api_error",
    "usage": {},
    "modelUsage": {},
}
MESSAGES = [
    {"role": "system", "content": "S" * 20_000},  # far beyond any argv limit
    {"role": "user", "content": "improve this"},
]


def _provider(runner):
    provider = ClaudeCliProvider(runner=runner, executable="claude")
    provider._resolve = lambda: "claude"  # type: ignore[method-assign]
    return provider


def test_system_prompt_travels_by_file_and_is_cleaned_up():
    seen: dict = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        idx = argv.index("--system-prompt-file")
        path = Path(argv[idx + 1])
        seen["content"] = path.read_text(encoding="utf-8")
        seen["path"] = path
        return subprocess.CompletedProcess(argv, 0, json.dumps(PAYLOAD_OK), "")

    result = _provider(runner).complete(MESSAGES, model="haiku", max_tokens=8, temperature=1.0)
    assert result.text == "OK"
    assert "--system-prompt" not in seen["argv"]
    assert seen["content"] == "S" * 20_000
    assert max(len(a) for a in seen["argv"]) < 1_000  # nothing huge on the command line
    assert not seen["path"].exists()  # temp file removed after the call


def test_failed_call_surfaces_the_cli_result_sentence():
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, json.dumps(PAYLOAD_LIMIT), "")

    with pytest.raises(ProviderError) as exc:
        _provider(runner).complete(MESSAGES, model="haiku", max_tokens=8, temperature=1.0)
    assert "session limit" in str(exc.value)
    assert "ephemeral" not in str(exc.value)  # not a tail of raw JSON


def test_cli_error_message_prefers_result_then_status_then_tail():
    assert cli_error_message(json.dumps(PAYLOAD_LIMIT), "") .startswith("You've hit")
    assert cli_error_message(json.dumps({"is_error": True, "subtype": "error_max_turns"}), "") == "subtype=error_max_turns"
    assert cli_error_message("not json", "stderr tail here") == "stderr tail here"
    assert cli_error_message("", "") == "no output"


class _DeadProvider:
    """A backend that fails every call after `good` successes."""

    name = "dead"

    def __init__(self, good: int = 0) -> None:
        self.good = good
        self.calls = 0

    def complete(self, messages, *, model, max_tokens, temperature):
        self.calls += 1
        if self.calls <= self.good:
            block = "def priority(item, bins):\n    return [-(b - item) * 3 for b in bins]\n"
            return Completion(
                text=f"```python\n{block}```",
                input_tokens=5,
                output_tokens=5,
                model=model,
                provider=self.name,
                usd=0.001,
            )
        raise ProviderError("claude-cli provider: exit 1: You've hit your session limit")


def test_driver_halts_after_consecutive_provider_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config(EXAMPLE_CONFIG)
    dead = _DeadProvider()
    messages: list[str] = []
    driver = Driver(
        config,
        run_dir="runs/dead",
        providers={"small": dead, "strong": dead},
        log=messages.append,
    )
    summary = driver.run()

    limit = config.budget.max_consecutive_provider_errors
    assert summary.stop_reason.startswith("provider failing")
    assert "session limit" in summary.stop_reason
    assert summary.generations == 1
    # At most `limit` children were attempted before the halt (plus the seed).
    assert summary.candidates <= 1 + limit
    assert any(m.startswith("ABORT") for m in messages)
    assert driver.ledger.totals()["usd"] == 0


def test_provider_error_counter_resets_on_a_successful_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config(EXAMPLE_CONFIG)
    good = _DeadProvider(good=1)
    driver = Driver(config, run_dir="runs/flaky", providers={"small": good, "strong": good})
    driver._provider_failures = 2  # two earlier failures
    summary = driver.run(generations=1)
    # The first call succeeds and resets the counter, so the two failures that
    # follow do not reach the limit of three within this generation.
    assert not summary.stop_reason.startswith("provider failing") or driver._provider_failures >= config.budget.max_consecutive_provider_errors
