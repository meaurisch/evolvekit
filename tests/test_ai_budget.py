"""The AI spend stays within its cap (persona round 3, E2): the gate run spent
$1.03 of $1.00. Every claude-cli call is told what is left (`--max-budget-usd`,
when the installed claude lists it), and before any AI call the run checks that
what is left covers the largest call it has seen (a fixed allowance before the
first): when it does not, no more AI changes are written -- the round ends and
the run goes to its final check, saying "the AI budget was used up"."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest
from test_driver_tree import _raw
from tree_helpers import head, make_repo

from evolvekit import tree
from evolvekit.app.verdict import how_it_ran
from evolvekit.budget import AI_USED_UP, FIRST_CALL_USD, BudgetGuard
from evolvekit.config import BudgetConfig, build_config
from evolvekit.ledger import read_jsonl
from evolvekit.providers.base import Completion, ProviderError
from evolvekit.providers.claude_cli import ClaudeCliProvider
from evolvekit.search.driver import Driver

MESSAGES = [{"role": "system", "content": "be terse"}, {"role": "user", "content": "improve this"}]


# -- the guard ---------------------------------------------------------------------


def test_a_call_is_allowed_only_when_what_is_left_covers_the_largest_call_so_far():
    guard = BudgetGuard(BudgetConfig(max_usd=1.0))
    assert guard.room_for_a_call().allowed, "before any call: $1.00 left covers the first-call allowance"
    guard.spend(usd=0.12, tokens=10)
    guard.spend(usd=0.05, tokens=10)
    assert guard.state.largest_usd == pytest.approx(0.12)
    guard.spend(usd=0.74, tokens=10)  # $0.91 spent, $0.09 left, the largest call so far $0.74
    told = guard.room_for_a_call()
    assert not told.allowed and told.reason.startswith(AI_USED_UP)
    assert "$0.09 of $1.00 left" in told.reason and "$0.74" in told.reason
    assert guard.check().allowed, "the plain cap is not reached: only the next call would overspend it"
    small = BudgetGuard(BudgetConfig(max_usd=FIRST_CALL_USD / 2))
    first = small.room_for_a_call()
    assert not first.allowed and first.reason.startswith(AI_USED_UP), "less than one first call's allowance"
    free = BudgetGuard(BudgetConfig(max_usd=1.0))
    for _ in range(5):
        free.spend(usd=0.0, tokens=100)
    assert free.room_for_a_call().allowed, "a provider that bills nothing never runs out"


# -- every claude-cli call is told what is left ----------------------------------------


def _claude(seen: dict, *, help_text: str, stdout: str | None = None) -> ClaudeCliProvider:
    payload = {"result": "OK", "total_cost_usd": 0.02, "usage": {"input_tokens": 10, "output_tokens": 5}}

    def runner(argv, **kwargs):
        if argv[-1] == "--help":
            return subprocess.CompletedProcess(argv, 0, help_text, "")
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload) if stdout is None else stdout, "")

    return ClaudeCliProvider(runner=runner, executable=sys.executable)


def test_a_claude_reply_is_capped_at_what_is_left_when_its_help_lists_the_flag():
    seen: dict = {}
    provider = _claude(seen, help_text="Options:\n  --max-budget-usd <amount>\n")
    provider.complete(MESSAGES, model="m", max_tokens=8, temperature=1.0, max_usd=0.4)
    assert seen["argv"][seen["argv"].index("--max-budget-usd") + 1] == "0.4000"
    provider.complete(MESSAGES, model="m", max_tokens=8, temperature=1.0)
    assert "--max-budget-usd" not in seen["argv"], "no cap asked for, none passed"
    older = _claude(seen, help_text="Options:\n  --model <model>\n")
    older.complete(MESSAGES, model="m", max_tokens=8, temperature=1.0, max_usd=0.4)
    assert "--max-budget-usd" not in seen["argv"], "an older claude without the flag is not given it"


def test_a_claude_reply_stopped_at_its_cap_is_billed():
    stopped = json.dumps({"is_error": True, "subtype": "error_max_budget_usd", "total_cost_usd": 0.4,
                          "usage": {"input_tokens": 70, "output_tokens": 30}})
    provider = _claude({}, help_text="--max-budget-usd", stdout=stopped)
    with pytest.raises(ProviderError) as told:
        provider.complete(MESSAGES, model="m", max_tokens=8, temperature=1.0, max_usd=0.4)
    assert isinstance(told.value.completion, Completion) and told.value.completion.usd == 0.4, \
        "what it spent is charged to the budget"


# -- the run stops writing AI changes ---------------------------------------------------


class Pricey:
    """A provider billing $0.30 a reply, each a new VALUE; records the cap it was told."""

    name = "pricey"

    def __init__(self):
        self.caps: list[float | None] = []

    def complete(self, messages, *, model, max_tokens, temperature, response_format=None, max_usd=None):
        self.caps.append(max_usd)
        value = 100 - len(self.caps)
        reply = f"```diff\n--- a/solver.py\n+++ b/solver.py\n@@ -2 +2 @@\n-VALUE = 5\n+VALUE = {value}\n```"
        return Completion(text=reply, input_tokens=10, output_tokens=5, model=model, provider=self.name, usd=0.3)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_the_run_ends_its_round_when_the_next_call_could_overspend(tmp_path):
    repo = make_repo(tmp_path)
    raw = _raw(repo, head(repo), generations=5)
    raw["budget"] = {"max_usd": 1.0}
    provider = Pricey()
    lines: list[str] = []
    summary = Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run",
                     providers={"small": provider, "strong": provider}, log=lines.append).run()
    try:
        assert provider.caps == pytest.approx([1.0, 0.7, 0.4]), "each call told what is left; no fourth call"
        usd = sum(r["usd"] for r in read_jsonl(tmp_path / "run" / "usage.jsonl"))
        assert usd == pytest.approx(0.9) and usd <= 1.0, "within the cap"
        assert summary.stop_reason.startswith(AI_USED_UP) and not summary.aborted, "the run goes to its final check"
        assert summary.generations == 2, "round 2 ended with the one change it could pay for"
        said = [line for line in lines if line.startswith(AI_USED_UP)]
        assert len(said) == 1 and "no more AI changes" in said[0], lines
    finally:
        tree.remove_all(tmp_path / "run")


def test_how_it_ran_says_the_ai_budget_was_used_up():
    job = {"search": {"generations": 2, "stop_reason": f"{AI_USED_UP}: $0.10 of $1.00 left"}}
    assert how_it_ran(job, {"rounds": 3, "combinations": 21}, 13, True) == \
        "Ran 2 of the 3 planned rounds (13 combinations, about 21 planned): the AI budget was used up."
    over = {"search": {"generations": 4, "stop_reason": "budget.max_usd reached: $1.0309 of $1.0000"}}
    assert how_it_ran(over, {"rounds": 3, "combinations": 21}, 25, True) == \
        "The AI budget was used up in round 4 (25 combinations, about 21 planned)."
    assert how_it_ran({"search": {"generations": 3, "stop_reason": "generations exhausted"}},
                      {"rounds": 3}, 21, True) == ""


class AlwaysCapped:
    """Every reply is stopped at its cap: billed, no text."""

    name = "capped"

    def complete(self, messages, *, model, max_tokens, temperature, response_format=None, max_usd=None):
        stopped = Completion(text="", input_tokens=10, output_tokens=5, model=model, provider=self.name, usd=0.001,
                             raw={"failed": True, "stopped_at": "error_max_budget_usd"})
        raise ProviderError("stopped at its budget cap", completion=stopped)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_replies_stopped_at_their_cap_are_billed_but_are_not_a_dead_backend(tmp_path):
    repo = make_repo(tmp_path)
    raw = _raw(repo, head(repo), generations=3)
    raw["budget"] = {"max_usd": 10.0, "max_consecutive_provider_errors": 2}
    provider = AlwaysCapped()
    summary = Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run",
                     providers={"small": provider, "strong": provider}, log=lambda _line: None).run()
    try:
        rows = read_jsonl(tmp_path / "run" / "usage.jsonl")
        rows = list(rows)
        assert len(rows) > 2 and sum(r["usd"] for r in rows) > 0, "the capped replies are billed"
        assert not summary.aborted and not summary.stop_reason.startswith("provider failing"), summary.stop_reason
    finally:
        tree.remove_all(tmp_path / "run")
