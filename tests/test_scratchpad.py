"""The meta-scratchpad: when it refreshes, what it costs, what it may not do."""

from __future__ import annotations

from dataclasses import replace

import pytest

from evolvekit.config import load_config
from evolvekit.deltas import delta_summary, diff_stat, first_docstring
from evolvekit.prompts import SCRATCHPAD_LIMIT
from evolvekit.providers.base import ProviderError
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.driver import Driver
from evolvekit.search.scratchpad import Scratchpad, build_scratchpad_messages
from tests.conftest import EXAMPLE_CONFIG


class BrokenProvider:
    name = "broken"

    def complete(self, messages, *, model, max_tokens, temperature):
        raise ProviderError("backend down")


# -- the cache -------------------------------------------------------------


def test_it_is_due_every_n_generations_and_never_at_generation_zero(tmp_path):
    pad = Scratchpad(tmp_path, every=3)
    assert [g for g in range(10) if pad.due(g)] == [3, 6, 9]
    assert pad.enabled


def test_zero_turns_it_off(tmp_path):
    pad = Scratchpad(tmp_path, every=0)
    assert not pad.enabled
    assert not any(pad.due(g) for g in range(20))


def test_notes_are_trimmed_to_the_line_budget_and_cached(tmp_path):
    pad = Scratchpad(tmp_path, every=1)
    stored = pad.store("\n".join(f"line {i}" for i in range(200)), generation=4)
    assert len(stored.splitlines()) == SCRATCHPAD_LIMIT
    assert pad.refreshed_at == 4
    on_disk = pad.path.read_text(encoding="utf-8")
    assert "refreshed at generation 4" in on_disk
    assert Scratchpad(tmp_path, every=1).text.endswith(f"line {SCRATCHPAD_LIMIT - 1}\n")


def test_the_digest_never_contains_source_code(tmp_path):
    config = load_config(EXAMPLE_CONFIG)
    messages = build_scratchpad_messages(config, ["c1 score -5.0 -- +2/-1 lines"])
    assert len(messages) == 2
    assert "def priority" not in messages[1]["content"]
    assert "Online bin packing" in messages[1]["content"]
    assert str(SCRATCHPAD_LIMIT) in messages[0]["content"]


def test_a_provider_failure_leaves_the_previous_notes_in_place(tmp_path):
    config = load_config(EXAMPLE_CONFIG)
    pad = Scratchpad(tmp_path, every=1)
    pad.store("the notes so far", generation=1)
    text, completion, messages, error = pad.refresh(
        config=config, provider=BrokenProvider(), entries=["c1"], generation=2
    )
    assert completion is None and error == "backend down"
    assert text == "the notes so far"
    assert pad.refreshed_at == 1
    assert messages, "the attempted messages are still returned for the trace"


# -- delta summaries -------------------------------------------------------


def test_the_delta_summary_is_a_diff_stat_plus_the_first_docstring_line():
    parent = 'def f():\n    """Old."""\n    return 1\n'
    child = 'def f():\n    """New rule.\n\n    Details.\n    """\n    return 2\n'
    added, removed = diff_stat(parent, child)
    assert (added, removed) == (5, 2)
    assert first_docstring(child) == "New rule."
    summary = delta_summary(child, parent, "g001-c0002")
    assert summary == "+5/-2 lines vs g001-c0002 -- New rule."


def test_a_summary_without_a_parent_or_a_docstring_still_says_something():
    assert delta_summary("x = 1\n", None, None) == "no recorded change"
    assert "+1/-1 lines" in delta_summary("x = 2\n", "x = 1\n", None)


def test_summaries_are_bounded():
    long_doc = 'def f():\n    """' + "word " * 200 + '"""\n    return 1\n'
    assert len(delta_summary(long_doc, "def f():\n    return 1\n", "p")) <= 160


def test_an_unparseable_block_has_no_docstring():
    assert first_docstring("def f(:\n") == ""


# -- inside the loop -------------------------------------------------------


@pytest.mark.slow
def test_the_scratchpad_is_refreshed_billed_and_pasted_into_later_prompts(tmp_path):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        search=replace(
            config.search,
            generations=2,
            children_per_generation=1,
            big_step_every=99,
            scratchpad_every=1,
            novelty_retry=False,
            operators={"rewrite": 1.0},
        ),
    )
    provider = FakeProvider(
        [
            {"when": "keeping the running notes", "response": "- residuals matter"},
            "```python\ndef priority(item, bins):\n    return [float(b) for b in bins]\n```",
            "```python\ndef priority(item, bins):\n    return [float(-b) for b in bins]\n```",
        ]
    )
    driver = Driver(
        config,
        run_dir=tmp_path / "pad",
        providers={"small": provider, "strong": provider},
    )
    driver.run()

    assert driver.scratchpad.text == "- residuals matter"
    assert driver.scratchpad.path.is_file()

    usage = [r for r in driver.ledger.usage() if r["operator"] == "scratchpad"]
    assert usage, "the scratchpad call was not billed"
    assert all(r["candidate_id"].startswith("scratchpad-g") for r in usage)
    assert driver.budget.state.usd == pytest.approx(
        sum(r["usd"] for r in driver.ledger.usage())
    )

    bodies = [call["messages"][1]["content"] for call in provider.calls[1:]]
    assert any("- residuals matter" in b for b in bodies)


@pytest.mark.slow
def test_turning_it_off_makes_no_call_at_all(tmp_path):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        search=replace(
            config.search,
            generations=2,
            children_per_generation=1,
            big_step_every=99,
            scratchpad_every=0,
            operators={"rewrite": 1.0},
        ),
    )
    provider = FakeProvider(
        ["```python\ndef priority(item, bins):\n    return [float(b) for b in bins]\n```"]
    )
    driver = Driver(
        config,
        run_dir=tmp_path / "nopad",
        providers={"small": provider, "strong": provider},
    )
    driver.run()
    assert not driver.scratchpad.path.exists()
    assert not [r for r in driver.ledger.usage() if r["operator"] == "scratchpad"]
    for call in provider.calls:
        assert "Notes from earlier generations" not in call["messages"][1]["content"]
