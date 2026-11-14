"""What was tried on a parent (A3.1): its last five children, newest first, each
one bounded line -- operator, a locally computed change summary, and what came
of it in words that are true of the row the engine wrote."""

from __future__ import annotations

from dataclasses import replace

import pytest

from evolvekit.candidate import Candidate
from evolvekit.config import load_config
from evolvekit.prompts import LEDGER_LINE_LIMIT, LedgerEntry, system_prompt, user_prompt
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.driver import Driver
from evolvekit.search.tried import entry_for, outcome_words, parent_ledger, score_text
from tests.conftest import EXAMPLE_CONFIG

PARENT_BLOCK = "def priority(item, bins):\n    return [b - item for b in bins]\n"
CHILD_BLOCK = "def priority(item, bins):\n    return [item - b for b in bins]\n"


def _row(**fields) -> Candidate:
    base = dict(id="g002-c0005", generation=2, block=CHILD_BLOCK, source="", operator="diff",
                parent_id="g001-c0001", stages_reached=["proxy", "full"], competes=True)
    base.update(fields)
    return Candidate(**base)


PARENT_ROW = _row(id="g001-c0001", generation=1, block=PARENT_BLOCK, parent_id=None, score=1.0)
TWIN = _row(id="g002-c0009", score=1.3, behaviour_signatures={"full": "abc"})
NUMBERS_TWIN = _row(id="g002-c0009", score=1.3,
                    behaviour_values={"full": {"kpis": {"bins": 4.0}, "vectors": {}, "score": 1.3}})

ZERO_DIVISION = ("stage full: exit code 1\nTraceback (most recent call last):\n  File \"x.py\", line 3\n"
                 "ZeroDivisionError: division by zero\n")

CASES = [
    ("best", dict(score=1.21), "g002-c0005", "+0.21 (now the best)"),
    ("lower", dict(score=0.96), None, "-0.04"),
    ("near", dict(score=1.02, novelty="near", near_twin_id="g001-c0003"), None, "+0.02, close to g001-c0003"),
    ("near its parent", dict(score=1.02, novelty="near", near_twin_id="g001-c0001"), None,
     "+0.02, close to this parent"),
    ("failed", dict(rejected=True, competes=False, last_failure=ZERO_DIVISION, score=-1e9), None,
     "failed: `ZeroDivisionError: division by zero`"),
    ("exact twin", dict(rejected=True, competes=False, behaviour_twin_id="g002-c0009",
                        behaviour_signatures={"full": "abc"}), None, "same behaviour as g002-c0009"),
    ("noise twin", dict(rejected=True, competes=False, behaviour_twin_id="g002-c0009",
                        behaviour_signatures={"full": "abd"}), None,
     "same behaviour as g002-c0009, within the noise"),
    ("gated", dict(competes=False, gated="missed_required = 2 > 0", score=1.5), None,
     "broke the gate `missed_required = 2 > 0`"),
    ("raced", dict(competes=False, raced_out="raced out after 4 of 20 instances: 7.10 % behind the best",
                   score=0.9), None, "raced out after 4 instances"),
    ("raced, older wording", dict(competes=False, raced_out="raced out after 3 instances: behind", score=0.9),
     None, "raced out after 3 instances"),
    ("raced, unknown wording", dict(competes=False, raced_out="stopped early", score=0.9), None, "raced out"),
    ("veto", dict(competes=False, veto="the hold-out fell 0.3 below the starting point", score=1.4), None,
     "kept out by the hold-out"),
    ("not promoted", dict(competes=False, stages_reached=["proxy"], score=0.9), None, "not promoted"),
    ("not applied", dict(block=PARENT_BLOCK, rejected=True, competes=False, score=-1e9,
                         reject_reason="could not apply response: SEARCH text not found\nmore",
                         last_failure="could not apply response: SEARCH text not found\nmore"), None,
     "not applied: `could not apply response: SEARCH text not found`"),
    ("not applied, outside the open files", dict(
        block=PARENT_BLOCK, rejected=True, competes=False,
        reject_reason="changes files outside the open files: bench.py (only solver.py may change)"), None,
     "not applied: `changes files outside the open files: bench.py (only solver.py may change)`"),
    ("not applied, the agent", dict(block=PARENT_BLOCK, rejected=True, competes=False, operator="agent_patch",
                                    reject_reason="the agent changed nothing"), None,
     "not applied: `the agent changed nothing`"),
    ("no reply", dict(block=PARENT_BLOCK, rejected=True, competes=False, score=-1e9,
                      reject_reason="provider error: session cap reached",
                      last_failure="provider error: session cap reached"), None,
     "no reply: provider error: session cap reached"),
    ("no reply, refused before the call", dict(
        block=PARENT_BLOCK, rejected=True, competes=False,
        reject_reason="the open files hold 900 lines; a code study shows them whole"), None,
     "no reply: the open files hold 900 lines; a code study shows them whole"),
    ("no reply, the agent's time", dict(block=PARENT_BLOCK, rejected=True, competes=False, operator="agent_patch",
                                        reject_reason="the study has no time left for the agent"), None,
     "no reply: the study has no time left for the agent"),
    ("rounding is no change", dict(score=1.0 + 2.2e-16), None, "+0"),
    ("the score, not the ranking score", dict(score=1.5, ranking_score=1.1), None, "+0.5"),
    ("noise twin, numbers only", dict(rejected=True, competes=False, behaviour_twin_id="g002-c0009",
                                      behaviour_values={"full": {"kpis": {"bins": 4.1}, "vectors": {}}}), None,
     "same behaviour as g002-c0009, within the noise"),
    ("exact twin, numbers only", dict(rejected=True, competes=False, behaviour_twin_id="g002-c0009",
                                      behaviour_values={"full": {"kpis": {"bins": 4.0}, "vectors": {}}}), None,
     "same behaviour as g002-c0009"),
    ("no reply, no text", dict(block=PARENT_BLOCK, rejected=True, competes=False, score=-1e9), None, "no reply"),
    ("no_op", dict(rejected=True, competes=False, novelty="no_op", reject_reason="structurally identical"), None,
     "rejected: no change from this parent"),
    ("duplicate", dict(rejected=True, competes=False, novelty="duplicate", twin_id="g001-c0004",
                       reject_reason="same as g001-c0004"), None, "rejected: same program as g001-c0004"),
    ("duplicate, no twin", dict(rejected=True, competes=False, novelty="duplicate"), None,
     "rejected: same program as an earlier candidate"),
    ("daily cap", dict(rejected=True, competes=False,
                       last_failure="stage full: daily full-evaluation cap reached"), None,
     "not evaluated: the daily cap on final evaluations was reached"),
    ("pre-A2 row", dict(score=1.5, holdout_margin=None, behaviour_values={}), None, "+0.5"),
    ("not ranked", dict(competes=False, score=1.5), None, "not ranked (score 1.5)"),
    ("rejected otherwise", dict(rejected=True, competes=False, reject_reason="odd\nreason", stages_reached=[]),
     None, "rejected: `odd`"),
    ("nothing", dict(stages_reached=[]), None, "no score"),
]


@pytest.mark.parametrize("name, fields, best_id, words", CASES, ids=[c[0] for c in CASES])
def test_every_row_shape_has_its_words(name, fields, best_id, words):
    child = _row(**fields)
    twin = None
    if child.behaviour_twin_id:
        twin = TWIN if child.behaviour_signatures else NUMBERS_TWIN
    assert outcome_words(child, PARENT_ROW, best_id=best_id, twin=twin, final_stage="full") == words


def test_a_twin_the_archive_does_not_hold_reads_plain():
    child = _row(rejected=True, competes=False, behaviour_twin_id="g000-c0042",
                 behaviour_values={"full": {"kpis": {"bins": 4.1}, "vectors": {}}})
    assert outcome_words(child, PARENT_ROW, best_id=None, twin=None, final_stage="full") == (
        "same behaviour as g000-c0042")


def test_a_parent_without_a_fitness_shows_the_child_s_score():
    parent = replace(PARENT_ROW, score=None)
    assert outcome_words(_row(score=1.21), parent, best_id=None, twin=None, final_stage="full") == "score 1.21"


def test_score_changes_are_written_without_exponents():
    assert score_text(1e-05, signed=True) == "+0.00001"
    assert score_text(1234.5, signed=True) == "+1234.5"
    assert score_text(-0.04000000000000004, signed=True) == "-0.04"
    assert score_text(0.0, signed=True) == "+0"
    assert score_text(1234.5, signed=False) == "1234.5"


def test_a_failed_apply_has_no_change_and_a_failed_big_step_is_a_big_step():
    child = _row(block=PARENT_BLOCK, rejected=True, competes=False, operator="big_step/big_step",
                 reject_reason="could not apply response: no fence")
    entry = entry_for(child, PARENT_ROW, best_id=None, twin=None, final_stage="full", tree=False)
    assert entry.change is None and entry.operator == "big_step"
    assert entry.line() == "`g002-c0005` big_step -> not applied: `could not apply response: no fence`"
    applied = entry_for(_row(score=1.1), PARENT_ROW, best_id=None, twin=None, final_stage="full", tree=False)
    assert applied.line() == "`g002-c0005` diff: +1/-1 lines; functions: priority -> +0.1"


def _children(parent_id: str, counters, **fields) -> list[Candidate]:
    return [_row(id=f"g{k:03d}-c{k:04d}", generation=k, parent_id=parent_id, score=1.0 + k / 100, **fields)
            for k in counters]


def test_the_ledger_is_the_last_five_children_newest_first():
    mine = _children("g001-c0001", range(2, 10))
    others = _children("g001-c0099", (10, 11))
    archive = [PARENT_ROW, *others, *reversed(mine)]
    by_id = {c.id: c for c in archive}
    ids = [e.id for e in parent_ledger(archive, PARENT_ROW, by_id=by_id, best_id=None, final_stage="full",
                                        tree=False)]
    assert ids == ["g009-c0009", "g008-c0008", "g007-c0007", "g006-c0006", "g005-c0005"]


def test_children_are_ordered_by_their_counter_not_the_id_string():
    late = _row(id="g010-c10000", generation=10, score=1.0)
    early = _row(id="g009-c9999", generation=9, score=1.0)
    by_id = {c.id: c for c in (PARENT_ROW, late, early)}
    ledger = parent_ledger([PARENT_ROW, early, late], PARENT_ROW, by_id=by_id, best_id=None,
                           final_stage="full", tree=False)
    assert [e.id for e in ledger] == ["g010-c10000", "g009-c9999"]


@pytest.mark.parametrize("failure", [
    "stage full: exit code 1\n" + "x" * 5000,
    "stage full: exit code 1\nTraceback\n  `bad` line\x85\x0b\x1c" + "y`" * 3000 + "\n",
])
def test_a_huge_or_multiline_failure_is_one_bounded_line(failure):
    child = _row(rejected=True, competes=False, last_failure=failure, block=CHILD_BLOCK + "# " + "z" * 300 + "\n")
    entry = entry_for(child, PARENT_ROW, best_id=None, twin=None, final_stage="full", tree=False)
    printed = f"- {entry.line()}"
    assert len(printed) <= LEDGER_LINE_LIMIT
    assert len(printed.splitlines()) == 1
    assert "failed: `" in printed, "the change was cut before the outcome's head"
    assert printed.endswith("...`"), "a cut failure keeps its closing backtick"


def test_a_long_change_is_cut_before_the_outcome():
    entry = LedgerEntry(id="g002-c0005", operator="diff", change="c" * 300, outcome="+0.5 (now the best)")
    line = entry.line()
    assert len(line) == LEDGER_LINE_LIMIT - 2 and line.endswith("... -> +0.5 (now the best)")


def _entries(n: int) -> list[LedgerEntry]:
    return [LedgerEntry(id=f"g{k:03d}-c{k:04d}", operator="diff", change="+1/-1 lines; functions: priority",
                        outcome="w" * 400) for k in range(n, 0, -1)]


def test_the_prompt_carries_the_bounded_ledger_in_place_of_the_failure_section():
    parent = replace(PARENT_ROW, last_failure="stage full: exit code 1\nboom")
    prompt = user_prompt(parent, None, "diff", ledger=_entries(20)[:5])
    assert prompt.index("## What was tried on this parent") < prompt.index("## Your task")
    section = prompt[prompt.index("## What was tried on this parent"):prompt.index("## Your task")]
    bullets = [line for line in section.splitlines() if line.startswith("- ")]
    assert len(bullets) == 5 and all(len(line) <= LEDGER_LINE_LIMIT for line in bullets)
    assert "## Last failure artefact" not in prompt
    bare = user_prompt(parent, None, "diff", ledger=())
    assert "## What was tried on this parent" not in bare and "## Last failure artefact" not in bare
    assert user_prompt(parent, None, "diff", ledger=_entries(9)).count("\n- `") == 5, "never more than five"
    config = load_config(EXAMPLE_CONFIG)
    assert system_prompt(config, "a", "b") == system_prompt(config, "a", "b")


def _driver(tmp_path, responses=("no code here",), **search):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(config, search=replace(config.search, scratchpad_every=0, **search))
    provider = FakeProvider(list(responses), cycle=True)
    return Driver(config, run_dir=tmp_path / "run", providers={"small": provider, "strong": provider}), provider


def test_the_ledger_is_rebuilt_from_runs_jsonl(tmp_path):
    driver, _ = _driver(tmp_path, generations=3, children_per_generation=2, big_step_every=0,
                        operators={"rewrite": 1.0}, inspirations=0,
                        responses=["```python\ndef priority(item, bins):\n    return [b - item for b in bins]\n```",
                                   "no code here"])
    driver.run()
    live = {p.id: driver._parent_ledger(p) for p in driver.archive}
    assert any(live.values()), "some parent has children"
    again, _ = _driver(tmp_path, generations=3, children_per_generation=2, big_step_every=0,
                       operators={"rewrite": 1.0}, inspirations=0)
    again.resume()
    assert {p.id: again._parent_ledger(p) for p in again.archive} == live


def test_the_next_prompt_shows_what_the_parent_s_earlier_children_did(tmp_path):
    seed = _driver(tmp_path / "probe")[0].seed_block
    first = seed.splitlines()[0]
    applies = f"<<<<<<< SEARCH\n{first}\n=======\n{first}\n    bins = [b + 0.0 for b in bins]\n>>>>>>> REPLACE\n"
    driver, provider = _driver(tmp_path, generations=2, children_per_generation=1, big_step_every=0,
                               operators={"diff": 1.0}, inspirations=0, responses=["no code here", applies])
    driver.run()
    first_round = [c for c in driver.archive if c.generation == 1]
    not_applied = next(c for c in first_round if c.reject_reason.startswith("could not apply response"))
    prompts = [call["messages"][1]["content"] for call in provider.calls]
    with_ledger = [i for i, p in enumerate(prompts) if "## What was tried on this parent" in p]
    assert with_ledger and with_ledger[0] == len(first_round), "from the second round's first call on"
    assert f"`{not_applied.id}` diff -> not applied:" in prompts[with_ledger[0]]
