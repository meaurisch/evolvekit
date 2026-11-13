"""Budget caps, the stop policy and the USD arithmetic in the ledger."""

from __future__ import annotations

import json
import random

import pytest

from evolvekit.budget import BudgetGuard, StopPolicy
from evolvekit.config import BudgetConfig, ModelConfig, StopConfig
from evolvekit.ledger import Ledger, price_completion
from evolvekit.providers.base import Completion

BUDGET = BudgetConfig(max_usd=1.0, max_tokens=1000, max_full_evals_per_day=2)


def model(price_in=3.0, price_out=15.0) -> ModelConfig:
    return ModelConfig(
        role="small",
        provider="fake",
        model="m",
        price_in_per_mtok=price_in,
        price_out_per_mtok=price_out,
    )


# -- budget ----------------------------------------------------------------


def test_budget_allows_spending_until_the_usd_cap():
    guard = BudgetGuard(BUDGET)
    assert guard.check().allowed
    guard.spend(usd=0.99, tokens=10)
    assert guard.check().allowed
    guard.spend(usd=0.02, tokens=10)
    verdict = guard.check()
    assert verdict.allowed is False and "max_usd" in verdict.reason


def test_budget_stops_on_the_token_cap_too():
    guard = BudgetGuard(BUDGET)
    guard.spend(usd=0.0, tokens=1000)
    verdict = guard.check()
    assert verdict.allowed is False and "max_tokens" in verdict.reason


def test_full_evaluations_are_metered_per_day():
    guard = BudgetGuard(BUDGET)
    assert guard.check_full_eval("2026-08-22").allowed
    guard.record_full_eval("2026-08-22")
    guard.record_full_eval("2026-08-22")
    blocked = guard.check_full_eval("2026-08-22")
    assert blocked.allowed is False and "max_full_evals_per_day" in blocked.reason
    # A new day resets the meter.
    assert guard.check_full_eval("2026-08-23").allowed


# -- stop policy -----------------------------------------------------------


def test_patience_stops_after_flat_generations():
    policy = StopPolicy(StopConfig(patience=3, epsilon=0.01))
    assert policy.update(1.0).stop is False
    for _ in range(2):
        assert policy.update(1.0).stop is False
    decision = policy.update(1.0)
    assert decision.stop is True and "patience" in decision.reason


def test_an_improvement_below_epsilon_does_not_reset_patience():
    policy = StopPolicy(StopConfig(patience=2, epsilon=0.5))
    policy.update(1.0)
    policy.update(1.2)  # +0.2 < epsilon, so still stagnant
    assert policy.update(1.3).stop is True


def test_an_improvement_above_epsilon_resets_patience():
    policy = StopPolicy(StopConfig(patience=2, epsilon=0.01))
    policy.update(1.0)
    policy.update(1.0)
    assert policy.update(5.0).stop is False
    assert policy.stagnant == 0


def test_target_stops_immediately():
    policy = StopPolicy(StopConfig(patience=99, epsilon=0.0, target=2.0))
    assert policy.update(1.0).stop is False
    decision = policy.update(2.5)
    assert decision.stop is True and "target" in decision.reason


def test_plateau_is_flagged_at_half_patience_before_stopping():
    policy = StopPolicy(StopConfig(patience=4, epsilon=0.01))
    policy.update(1.0)
    assert policy.plateau is False
    policy.update(1.0)
    policy.update(1.0)
    assert policy.plateau is True  # driver's cue to spend a big step
    assert policy.update(1.0).stop is False


def test_min_big_steps_holds_the_plateau_open():
    policy = StopPolicy(StopConfig(patience=2, epsilon=0.01, min_big_steps=1))
    policy.update(1.0)
    policy.update(1.0)
    held = policy.update(1.0)
    assert held.stop is False and "required big step" in held.reason
    policy.note_big_step()
    assert policy.update(1.0).stop is True


def test_slow_steady_progress_is_progress_without_the_ratchet():
    policy = StopPolicy(StopConfig(patience=2, epsilon=0.5))
    for value in (1.0, 1.3, 1.6):
        decision = policy.update(value)
    assert decision.stagnant_generations == 0 and policy.best == 1.6, "1.6 > 1.0 + 0.5"


def test_a_sub_epsilon_step_does_not_move_the_best():
    policy = StopPolicy(StopConfig(patience=9, epsilon=0.5))
    policy.update(1.0)
    policy.update(1.3)
    assert policy.best == 1.0, "no ratchet: 1.3 is not 0.5 above 1.0"


def test_the_automatic_epsilon_follows_the_noise():
    policy = StopPolicy(StopConfig(patience=9, epsilon=None))
    assert policy.epsilon_now() == 1e-6, "no noise: today's epsilon"
    policy.noise_sd = 2.0
    policy.update(100.0)
    assert policy.epsilon_now() == pytest.approx(1.0)
    assert policy.update(100.9).stagnant_generations == 1
    assert policy.update(101.1).stagnant_generations == 0


def test_the_automatic_epsilon_has_a_relative_floor_and_an_explicit_one_ignores_the_noise():
    policy = StopPolicy(StopConfig(patience=9, epsilon=None))
    policy.update(1e7)
    assert policy.epsilon_now() == pytest.approx(1e-2), "1e-9 * |best|"
    fixed = StopPolicy(StopConfig(patience=9, epsilon=0.25))
    fixed.noise_sd = 10.0
    assert fixed.epsilon_now() == 0.25


def test_with_no_noise_the_automatic_epsilon_is_todays_default():
    automatic = StopPolicy(StopConfig(patience=3, epsilon=None))
    today = StopPolicy(StopConfig(patience=3, epsilon=1e-6))
    for value in (1.0, 1.0 + 5e-7, 1.0 + 2e-6, 1.0 + 2e-6, 1.0 + 2e-6, 1.0 + 2e-6):
        assert automatic.update(value) == today.update(value)


def test_the_patience_reason_says_the_epsilon_used():
    policy = StopPolicy(StopConfig(patience=1, epsilon=None))
    policy.noise_sd = 0.5
    policy.update(1.0)
    decision = policy.update(1.0)
    assert decision.stop and decision.reason == "stop.patience reached: no improvement > 0.25 for 1 generation(s)"


def test_the_target_is_read_from_the_generations_best_not_the_gated_one():
    policy = StopPolicy(StopConfig(patience=9, epsilon=0.5, target=1.2))
    policy.update(1.0)
    assert policy.update(1.3).stop is True, "1.3 >= 1.2, though 1.3 < 1.0 + 0.5"


def test_an_equal_candidate_reads_as_an_improvement_over_one_draw_in_about_36_percent():
    """x, b ~ N(0, 1), epsilon 0.5: P(N(0, 2) > 0.5) = P(Z > 0.354) = 36.2 %
    (50 % with today's 1e-6). 20 000 trials: SE 0.34 %; measured 36.6 % (band of Decision 12)."""
    rng, hits = random.Random(51), 0
    for _ in range(20_000):
        policy = StopPolicy(StopConfig(patience=9, epsilon=None))
        policy.noise_sd = 1.0
        policy.update(rng.gauss(0, 1))
        hits += policy.update(rng.gauss(0, 1)).stagnant_generations == 0
    print(f"equal candidate read as an improvement: {100 * hits / 20_000:.2f} %")
    assert 0.345 <= hits / 20_000 <= 0.385


def test_a_generation_with_no_score_counts_as_stagnant():
    policy = StopPolicy(StopConfig(patience=2, epsilon=0.0))
    policy.update(None)
    assert policy.update(None).stop is True


# -- ledger ----------------------------------------------------------------


def test_usd_is_computed_from_the_price_table():
    completion = Completion(
        text="x", input_tokens=1_000_000, output_tokens=200_000, model="m", provider="fake"
    )
    # 1M in at $3/M + 0.2M out at $15/M = 3.00 + 3.00
    assert price_completion(completion, model()) == pytest.approx(6.0)


def test_partial_millions_are_priced_proportionally():
    completion = Completion(
        text="x", input_tokens=1_500, output_tokens=500, model="m", provider="fake"
    )
    expected = 1_500 / 1e6 * 3.0 + 500 / 1e6 * 15.0
    assert price_completion(completion, model()) == pytest.approx(expected)


def test_a_provider_reported_cost_wins_over_the_table():
    completion = Completion(
        text="x",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        model="m",
        provider="claude-cli",
        usd=0.0514,
    )
    assert price_completion(completion, model()) == pytest.approx(0.0514)


def test_zero_prices_cost_nothing():
    completion = Completion(
        text="x", input_tokens=999_999, output_tokens=999_999, model="m", provider="fake"
    )
    assert price_completion(completion, model(0.0, 0.0)) == 0.0


def test_ledger_records_usage_and_totals(tmp_path):
    ledger = Ledger(tmp_path / "run")
    cfg = model()
    for _ in range(2):
        ledger.record_usage(
            Completion(text="x", input_tokens=1000, output_tokens=100, model="m", provider="fake"),
            cfg,
            candidate_id="c1",
            generation=1,
            operator="diff",
        )
    totals = ledger.totals()
    assert totals["calls"] == 2
    assert totals["input_tokens"] == 2000
    assert totals["total_tokens"] == 2200
    assert totals["usd"] == pytest.approx(2 * (1000 / 1e6 * 3.0 + 100 / 1e6 * 15.0))


def test_usage_rows_carry_the_pricing_provenance(tmp_path):
    ledger = Ledger(tmp_path / "run")
    ledger.record_usage(
        Completion(text="x", input_tokens=1, output_tokens=1, model="m", provider="claude-cli", usd=0.5),
        model(),
        candidate_id="c1",
        generation=0,
        operator="big_step",
    )
    row = ledger.usage()[0]
    assert row["priced_from"] == "provider" and row["usd"] == 0.5
    assert row["candidate_id"] == "c1" and row["operator"] == "big_step"


def test_runs_jsonl_is_append_only(tmp_path):
    ledger = Ledger(tmp_path / "run")
    ledger.record_run({"id": "a", "score": 1.0})
    ledger.record_run({"id": "b", "score": 2.0})
    assert [r["id"] for r in ledger.runs()] == ["a", "b"]
    assert ledger.runs_path.read_text(encoding="utf-8").count("\n") == 2


def test_a_torn_final_line_is_skipped_not_fatal(tmp_path):
    ledger = Ledger(tmp_path / "run")
    ledger.record_run({"id": "a"})
    with ledger.runs_path.open("a", encoding="utf-8") as handle:
        handle.write('{"id": "b", "sco')
    assert [r["id"] for r in ledger.runs()] == ["a"]


def test_traces_are_written_per_candidate(tmp_path):
    ledger = Ledger(tmp_path / "run")
    path = ledger.write_trace(
        "c1",
        messages=[{"role": "user", "content": "hi"}],
        response="there",
        meta={"operator": "diff"},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["response"] == "there"
    assert payload["messages"][0]["content"] == "hi"
    assert payload["meta"]["operator"] == "diff"


def test_usage_rows_carry_the_requests_a_plan_counts(tmp_path):
    """Persona round 2, D3: a provider that counts requests (copilot-cli) records them, so the verdict
    can say what the run's AI spent; a provider that bills money writes no `requests` at all."""
    ledger = Ledger(tmp_path / "run")
    for requests in (2.0, None):
        ledger.record_usage(
            Completion(text="x", input_tokens=1, output_tokens=1, model="m", provider="copilot-cli", usd=0.0,
                       requests=requests),
            model(), candidate_id="c1", generation=1, operator="diff",
        )
    first, second = ledger.usage()
    assert first["requests"] == 2.0 and "requests" not in second
