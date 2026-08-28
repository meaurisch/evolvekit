"""The novelty filter: what counts as the same program, and what it costs.

The two shapes here are the ones the first real run actually produced: the
small model returned worst-fit three times (identical scores of -14.48) and
re-expressed best-fit as itself twice. About 40 % of the paid calls bought
nothing, which is what this filter is for.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from evolvekit.config import load_config
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.driver import Driver
from evolvekit.search.novelty import NoveltyIndex, normalise_block, novelty_hash
from tests.conftest import EXAMPLE_CONFIG

BEST_FIT = '''\
def priority(item, bins):
    """Best fit: prefer the bin that will have the least space left."""
    return [-(b - item) for b in bins]
'''

BEST_FIT_RESTATED = '''\
def priority(item, bins):
    """Minimise the leftover space.

    Completely different words, a different variable name, and one comment.
    """
    # the residual is what would be left over
    return [-(remaining - item) for remaining in bins]
'''

FIRST_FIT = '''\
def priority(item, bins):
    """First fit."""
    return [float(len(bins) - i) for i in range(len(bins))]
'''


# -- normalisation ---------------------------------------------------------


def test_docstrings_comments_and_local_names_do_not_make_a_new_program():
    assert novelty_hash(BEST_FIT) == novelty_hash(BEST_FIT_RESTATED)


def test_a_different_rule_is_a_different_hash():
    assert novelty_hash(BEST_FIT) != novelty_hash(FIRST_FIT)


def test_a_changed_constant_is_a_different_program():
    tweaked = BEST_FIT.replace("-(b - item)", "-(b - item) * 1.5")
    assert novelty_hash(BEST_FIT) != novelty_hash(tweaked)


def test_the_defined_function_name_is_part_of_the_contract():
    renamed = BEST_FIT.replace("def priority", "def pick")
    assert novelty_hash(BEST_FIT) != novelty_hash(renamed)


def test_imported_names_survive_canonicalisation():
    with_math = "import math\n\n\ndef f(x):\n    return math.exp(x)\n"
    with_alias = "import math\n\n\ndef f(y):\n    return math.log(y)\n"
    assert "math" in normalise_block(with_math)
    assert novelty_hash(with_math) != novelty_hash(with_alias)


def test_a_docstring_only_body_does_not_become_a_syntax_error():
    only_doc = 'def f():\n    """Nothing but a docstring."""\n'
    assert normalise_block(only_doc).startswith("Module(")
    assert novelty_hash(only_doc) == novelty_hash('def f():\n    """Other."""\n')


def test_an_unparseable_block_still_hashes():
    broken = "def priority(item, bins:\n    return 1\n"
    digest = novelty_hash(broken)
    assert digest and digest == novelty_hash(broken + "\n\n")
    assert digest != novelty_hash(BEST_FIT)


# -- the index -------------------------------------------------------------


def test_a_child_identical_to_its_parent_is_a_no_op():
    index = NoveltyIndex()
    index.add("seed", BEST_FIT)
    verdict = index.check(BEST_FIT_RESTATED, BEST_FIT, "seed")
    assert verdict.kind == "no_op" and verdict.twin_id == "seed"
    assert not verdict.novel
    assert "seed" in verdict.reason and "seed" in verdict.retry_hint


def test_a_child_identical_to_another_archived_candidate_is_a_duplicate():
    index = NoveltyIndex()
    index.add("seed", BEST_FIT)
    index.add("first-fit", FIRST_FIT)
    restated = FIRST_FIT.replace("i)", "k)").replace("for i in", "for k in")
    verdict = index.check(restated, BEST_FIT, "seed")
    assert verdict.kind == "duplicate" and verdict.twin_id == "first-fit"


def test_a_genuinely_new_block_is_novel():
    index = NoveltyIndex()
    index.add("seed", BEST_FIT)
    verdict = index.check(FIRST_FIT, BEST_FIT, "seed")
    assert verdict.novel and verdict.reason is None


def test_the_index_keeps_the_first_id_that_used_a_structure():
    index = NoveltyIndex()
    index.add("first", BEST_FIT)
    index.add("second", BEST_FIT_RESTATED)
    assert index.lookup(BEST_FIT) == "first"


# -- inside the loop -------------------------------------------------------


def _driver(tmp_path, responses, *, retry: bool):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        search=replace(
            config.search,
            generations=1,
            children_per_generation=1,
            # The example turns adaptive breadth on; these tests are
            # about one child at a time.
            adaptive_children=None,
            big_step_every=99,
            scratchpad_every=0,
            novelty_retry=retry,
            operators={"rewrite": 1.0},
        ),
    )
    provider = FakeProvider(responses, cycle=True)
    return Driver(
        config,
        run_dir=tmp_path / "novelty",
        providers={"small": provider, "strong": provider},
    ), provider


def test_a_no_op_is_rejected_without_a_single_evaluation(tmp_path):
    fenced = f"```python\n{BEST_FIT_RESTATED}```"
    driver, provider = _driver(tmp_path, [fenced], retry=False)
    summary = driver.run()

    child = driver.archive[-1]
    assert child.rejected and child.novelty == "no_op"
    assert child.twin_id == child.parent_id
    assert child.stages_reached == [] and child.kpis == {}
    assert child.score == driver.config.evaluate.failure_score
    assert summary.no_ops == 1 and summary.duplicates == 0
    assert summary.wasted_calls == 1
    assert len(provider.calls) == 1, "novelty_retry: false must not re-prompt"
    # It cost one small-model call and no evaluator time at all.
    assert not (driver.ledger.run_dir / "work" / "candidates" / f"{child.id}.py").exists()


def test_the_retry_asks_for_something_different_and_gives_up_after_one(tmp_path):
    fenced = f"```python\n{BEST_FIT_RESTATED}```"
    driver, provider = _driver(tmp_path, [fenced], retry=True)
    summary = driver.run()

    assert summary.no_ops == 1
    assert len(provider.calls) == 2, "exactly one re-prompt, ever"
    second = provider.calls[1]["messages"][1]["content"]
    assert "structurally identical" in second
    assert driver.archive[-1].parent_id in second
    traces = {p.stem for p in driver.ledger.traces_dir.glob("*.json")}
    assert any(name.endswith(".retry1") for name in traces)


def test_a_novel_retry_rescues_the_child(tmp_path):
    driver, provider = _driver(
        tmp_path,
        [
            f"```python\n{BEST_FIT_RESTATED}```",
            f"```python\n{FIRST_FIT}```",
        ],
        retry=True,
    )
    summary = driver.run()
    child = driver.archive[-1]
    assert not child.rejected
    assert child.novelty is None
    assert summary.no_ops == 0
    assert "First fit" in child.block
    assert len(provider.calls) == 2


def test_twins_are_recorded_in_the_ledger_with_their_twin_id(tmp_path):
    driver, _provider = _driver(
        tmp_path, [f"```python\n{BEST_FIT_RESTATED}```"], retry=False
    )
    driver.run()
    rows = driver.ledger.runs()
    twin = rows[-1]
    assert twin["rejected"] is True
    assert twin["novelty"] == "no_op"
    assert twin["twin_id"] == rows[0]["id"]
    assert twin["twin_id"] in twin["reject_reason"]
    assert twin["score"] is not None  # never a None score, even here


@pytest.mark.parametrize("retry", [True, False])
def test_a_twin_never_becomes_a_parent(tmp_path, retry):
    driver, _provider = _driver(
        tmp_path, [f"```python\n{BEST_FIT_RESTATED}```"], retry=retry
    )
    driver.run()
    twin = driver.archive[-1]
    assert twin.id not in driver.grid.members
    assert twin.id not in {c.id for c in driver.grid.elites()}
