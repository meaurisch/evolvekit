"""The meta-scratchpad: when it refreshes, what it costs, what it may not do."""

from __future__ import annotations

from dataclasses import replace

import pytest

from evolvekit.config import load_config
from evolvekit.deltas import change_summary, changed_names, diff_stat, failure_line
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


# -- change summaries ------------------------------------------------------

PARENT = 'BONUS = 20\ndef priority(item, bins):\n    """Old."""\n    return [b - item for b in bins]\ndef helper():\n    return 1\n'
CHILD = 'BONUS = 25\ndef priority(item, bins):\n    """A brand new principle."""\n    return [item - b for b in bins]\ndef helper():\n    return 1\n'


def test_the_change_summary_names_changed_functions_and_constants_and_no_docstring():
    assert diff_stat(PARENT, CHILD) == (3, 3)
    assert changed_names(PARENT, CHILD) == (["priority"], ["BONUS"])
    summary = change_summary(CHILD, PARENT, "g001-c0002")
    assert summary == "+3/-3 lines vs g001-c0002; functions: priority; constants: BONUS"
    assert "brand new" not in summary


def test_added_and_removed_names_are_marked():
    child = PARENT.replace("def helper():\n    return 1\n", "def other():\n    return 2\nWIDTH = 4\n")
    assert changed_names(PARENT, child) == (["+other", "-helper"], ["+WIDTH"])


def test_a_function_whose_numbers_alone_changed_is_marked_and_methods_are_named_by_class():
    parent = "class Rule:\n    def score(self, x):\n        return 0.5 * x\nNEG = -1\n"
    child = "class Rule:\n    def score(self, x):\n        return 0.6 * x\nNEG = -2\n"
    assert changed_names(parent, child) == (["Rule.score (numbers only)"], ["NEG"])
    nested = "def f():\n    def g():\n        return 1\n    return g\n"
    assert changed_names(nested, nested.replace("return 1", "return [1]")) == (["f"], [])


def test_a_comment_only_change_says_so():
    assert change_summary(PARENT + "# note\n", PARENT) == "+1/-0 lines; comments or layout only"


def test_a_summary_without_a_parent_still_says_something():
    assert change_summary("x = 1\n", None) == "no recorded change"
    assert change_summary("x = 1\nprint(x)\n", "x = 1\n") == "+1/-0 lines; other top-level code"


def test_an_unparseable_block_is_summarised_by_its_diff_stat():
    assert change_summary("def f(:\n", "def f():\n    return 1\n", "p") == "+1/-2 lines vs p"
    assert changed_names("def f(:\n", PARENT) is None


def test_typed_parameters_name_the_keys_that_changed():
    s = change_summary("def configure():\n    return {'lr': 0.2}\n", "def configure():\n    return {'lr': 0.1}\n",
                       params={"lr": 0.2, "k": 3}, parent_params={"lr": 0.1, "k": 3})
    assert s.endswith("functions: configure (numbers only); constants: lr")


def _git_section(old, new, body):
    return f"diff --git a/{old} b/{new}\n{body}"


def test_a_tree_candidate_is_summarised_by_files():
    a = _git_section("a.py", "a.py", "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n")
    b = _git_section("my b.py", "my b.py", "--- a/my b.py\n+++ b/my b.py\n@@ -1 +1 @@\n-y = 1\n+y = 2\n")
    gone = _git_section("old.py", "old.py", "deleted file mode 100644\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-z = 1\n")
    moved = _git_section("c.py", "d.py", "similarity index 100%\nrename from c.py\nrename to d.py\n")
    parent = a + b
    child = a + b.replace("y = 2", "y = 3") + gone + moved
    summary = change_summary(child, parent, "p", tree=True)
    assert summary.startswith("+") and " diff lines vs p; files: my b.py, old.py, d.py" in summary
    assert change_summary(a, a + b, tree=True).endswith("diff lines; files: my b.py")
    assert change_summary("not a diff", None, tree=True) == "no recorded change"


def test_summaries_are_bounded():
    many = "".join(f"def f{i}():\n    return {i}\n" for i in range(60))
    other = many.replace("return", "return 1 +")
    s = change_summary(other, many, "p")
    assert len(s) <= 160 and "and 56 more" in s
    long_names = "".join(f"def {'n' * 70}{i}():\n    return {i}\n" for i in range(4))
    s = change_summary(long_names.replace("return", "return 1 +"), long_names, "p")
    assert len(s) == 160 and s.endswith("...")


def test_the_failure_line_is_the_exception_not_the_traceback_header():
    artefact = "stage full: exit code 1\nTraceback (most recent call last):\n  File \"x.py\", line 3\nZeroDivisionError: division by `zero`\n"
    assert failure_line(artefact) == "ZeroDivisionError: division by 'zero'"
    assert failure_line("stage static: forbidden import os") == "forbidden import os"
    assert failure_line("stage full: two lines\x85here\n\n") == "two lines here"
    assert failure_line("") == ""


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
