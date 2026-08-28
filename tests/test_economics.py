"""USD per unit of improvement: the series, the two stop rules, and the views.

The question this answers is the one neither real run could answer while it was
running: is the search still buying anything? Run 1 paid $0.83 for +0.345; run 2
paid $0.83 for nothing at all. The difference is a ratio, and a ratio needs a
denominator per generation.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from evolvekit.budget import StopPolicy
from evolvekit.config import (
    ConfigError,
    GainPerUsdRule,
    StopConfig,
    build_config,
    load_config,
)
from evolvekit.economics import DEFAULT_WINDOW, format_series, series
from evolvekit.leaderboard import economics_svg, render_economics, render_html, render_markdown
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.driver import Driver
from tests.conftest import EXAMPLE_CONFIG


def run_row(cid, generation, score, **overrides):
    row = {
        "id": cid,
        "generation": generation,
        "score": score,
        "ranking_score": score,
        "rejected": False,
    }
    row.update(overrides)
    return row


def usage_row(generation, usd):
    return {"generation": generation, "usd": usd}


# -- the series ------------------------------------------------------------


def test_an_empty_log_has_no_series():
    assert series([], []) == []
    assert format_series([]) == "no generations recorded yet"


def test_best_is_the_running_maximum_over_generations():
    points = series(
        [
            run_row("a", 0, -10.0),
            run_row("b", 1, -8.0),
            run_row("c", 1, -12.0),
            run_row("d", 2, -9.0),  # worse than gen 1: the running best holds
        ],
        [],
    )
    assert [p.best for p in points] == [-10.0, -8.0, -8.0]
    assert [p.generation for p in points] == [0, 1, 2]


def test_rejected_candidates_never_set_the_best():
    points = series(
        [
            run_row("a", 0, -10.0),
            run_row("b", 1, 99.0, rejected=True, novelty="duplicate"),
        ],
        [],
    )
    assert points[-1].best == -10.0


def test_a_near_duplicate_still_counts_because_it_was_evaluated():
    points = series(
        [run_row("a", 0, -10.0), run_row("b", 1, -5.0, novelty="near")],
        [],
    )
    assert points[-1].best == -5.0


def test_spend_is_cumulative_and_bucketed_by_generation():
    points = series(
        [run_row("a", 0, -10.0), run_row("b", 1, -9.0), run_row("c", 2, -9.0)],
        [usage_row(1, 0.10), usage_row(1, 0.05), usage_row(2, 0.20)],
    )
    assert [round(p.cumulative_usd, 4) for p in points] == [0.0, 0.15, 0.35]
    assert [p.calls for p in points] == [0, 2, 1]
    assert [p.cumulative_calls for p in points] == [0, 2, 3]


def test_usd_since_improvement_resets_when_the_best_moves():
    points = series(
        [
            run_row("a", 0, -10.0),
            run_row("b", 1, -9.0),  # improvement
            run_row("c", 2, -9.0),
            run_row("d", 3, -9.0),
        ],
        [usage_row(1, 0.10), usage_row(2, 0.20), usage_row(3, 0.30)],
    )
    assert [round(p.usd_since_improvement, 4) for p in points] == [0.0, 0.0, 0.20, 0.50]
    assert [p.improved for p in points] == [True, True, False, False]


def test_an_improvement_below_epsilon_does_not_reset_the_clock():
    points = series(
        [run_row("a", 0, -10.0), run_row("b", 1, -9.9999)],
        [usage_row(1, 0.10)],
        epsilon=0.01,
    )
    assert points[-1].improved is False
    assert points[-1].usd_since_improvement == pytest.approx(0.10)


def test_the_window_measures_gain_against_the_generation_w_back():
    points = series(
        [run_row(str(g), g, -10.0 + g) for g in range(5)],
        [usage_row(g, 0.10) for g in range(1, 5)],
        window=2,
    )
    last = points[-1]
    assert last.window == 2
    assert last.window_gain == pytest.approx(2.0)  # best[4] - best[2]
    assert last.window_usd == pytest.approx(0.20)
    assert last.gain_per_usd == pytest.approx(10.0)
    assert last.usd_per_gain == pytest.approx(0.10)
    assert last.gain_line == "$0.1000/unit"


def test_a_window_that_gained_nothing_reports_no_rate():
    points = series(
        [run_row("a", 0, -10.0), run_row("b", 1, -10.0), run_row("c", 2, -10.0)],
        [usage_row(1, 0.10), usage_row(2, 0.10)],
        window=2,
    )
    last = points[-1]
    assert last.usd_per_gain is None
    assert last.gain_per_usd == pytest.approx(0.0)
    assert last.gain_line == "no gain"


def test_a_window_that_cost_nothing_has_no_exchange_rate():
    points = series(
        [run_row("a", 0, -10.0), run_row("b", 1, -9.0)],
        [],
        window=2,
    )
    assert points[-1].gain_per_usd is None
    assert points[-1].gain_line == "free"


def test_a_short_history_anchors_on_the_first_generation():
    points = series(
        [run_row("a", 0, -10.0), run_row("b", 1, -9.0)],
        [usage_row(1, 0.50)],
        window=9,
    )
    assert points[-1].window_gain == pytest.approx(1.0)
    assert points[-1].window_usd == pytest.approx(0.50)


def test_generations_with_only_spend_still_appear():
    points = series([run_row("a", 0, -10.0)], [usage_row(1, 0.10)])
    assert [p.generation for p in points] == [0, 1]
    assert points[-1].best == -10.0


def test_a_non_finite_score_is_ignored_rather_than_poisoning_the_series():
    points = series(
        [run_row("a", 0, -10.0), run_row("b", 1, float("nan"))], []
    )
    assert points[-1].best == -10.0


def test_format_series_shows_the_tail_only():
    points = series([run_row(str(g), g, float(g)) for g in range(12)], [])
    text = format_series(points, tail=3)
    assert "gen" in text.splitlines()[0]
    assert len(text.splitlines()) == 5  # header, rule, three rows
    assert " 11 " in text and " 4 " not in text


# -- the stop rules --------------------------------------------------------


RULE_WINDOW = 2


def _points(spend_after_improvement: float, window_gain: float = 0.0):
    """A four-generation series: one improvement, then two paid generations.

    `window_gain` is what the two generations after the improvement bought
    between them; `spend_after_improvement` is what they cost. With a window of
    two, the last point's exchange rate is exactly that ratio.
    """
    rows = [run_row("a", 0, 0.0), run_row("b", 1, 1.0)]
    usage = [usage_row(1, 0.01)]
    for index, generation in enumerate((2, 3), start=1):
        rows.append(run_row(str(generation), generation, 1.0 + window_gain * index / 2))
        usage.append(usage_row(generation, spend_after_improvement / 2))
    return series(rows, usage, window=RULE_WINDOW)


def _plateaued_policy(config) -> StopPolicy:
    """A policy that has seen its improvement and spent a big step since.

    The order matters and is the driver's: an improvement clears the big-step
    credit, so the credit that counts is the one spent *after* it.
    """
    policy = StopPolicy(config)
    policy.update(1.0)          # the improvement
    policy.note_big_step()      # the plateau's first answer: the strong model
    return policy


def test_the_usd_since_improvement_rule_stops_a_paid_plateau():
    policy = _plateaued_policy(
        StopConfig(patience=99, max_usd_since_improvement=0.20)
    )
    decision = policy.update(1.0, _points(0.50)[-1])
    assert decision.stop
    assert "stop.max_usd_since_improvement reached" in decision.reason


def test_the_usd_rule_holds_until_a_big_step_has_been_tried():
    policy = StopPolicy(StopConfig(patience=99, max_usd_since_improvement=0.20))
    assert policy.update(1.0, _points(0.50)[-1]).stop is False


def test_the_usd_rule_does_not_fire_below_its_cap():
    policy = _plateaued_policy(
        StopConfig(patience=99, max_usd_since_improvement=5.0)
    )
    assert policy.update(1.0, _points(0.50)[-1]).stop is False


def test_the_gain_per_usd_rule_stops_a_bad_exchange_rate():
    policy = _plateaued_policy(
        StopConfig(patience=99, min_gain_per_usd=GainPerUsdRule(window=RULE_WINDOW, threshold=1.0))
    )
    decision = policy.update(1.0, _points(0.50, window_gain=0.01)[-1])
    assert decision.stop
    assert "stop.min_gain_per_usd reached" in decision.reason


def test_a_healthy_exchange_rate_keeps_the_run_going():
    policy = _plateaued_policy(
        StopConfig(patience=99, min_gain_per_usd=GainPerUsdRule(window=RULE_WINDOW, threshold=1.0))
    )
    assert policy.update(1.0, _points(0.02, window_gain=1.0)[-1]).stop is False


def test_both_rules_are_off_by_default():
    policy = _plateaued_policy(StopConfig(patience=99))
    assert policy.update(1.0, _points(99.0)[-1]).stop is False
    assert policy.economics_reason(_points(99.0)[-1]) is None


def test_an_improvement_clears_the_big_step_credit_so_the_rules_hold_again():
    policy = StopPolicy(StopConfig(patience=99, max_usd_since_improvement=0.01))
    policy.note_big_step()
    policy.update(1.0)          # first score: baseline
    policy.update(5.0)          # an improvement resets big_steps_in_window
    assert policy.big_steps_in_window == 0
    assert policy.update(5.0, _points(0.50)[-1]).stop is False


def test_patience_still_wins_when_both_would_fire():
    policy = _plateaued_policy(
        StopConfig(patience=1, max_usd_since_improvement=0.01)
    )
    decision = policy.update(1.0, _points(0.50)[-1])
    assert decision.stop and "stop.patience" in decision.reason


# -- config ----------------------------------------------------------------


def test_the_economics_stops_default_to_off(minimal_raw, tmp_path):
    stop = build_config(minimal_raw, base_dir=tmp_path).stop
    assert stop.max_usd_since_improvement is None
    assert stop.min_gain_per_usd is None
    assert stop.economics_window == 3


def test_the_window_comes_from_the_gain_rule_when_there_is_one(minimal_raw, tmp_path):
    minimal_raw["stop"] = {"min_gain_per_usd": {"window": 6, "threshold": 0.5}}
    stop = build_config(minimal_raw, base_dir=tmp_path).stop
    assert stop.economics_window == 6
    assert stop.min_gain_per_usd.threshold == 0.5


def test_a_gain_rule_without_a_threshold_is_an_error(minimal_raw, tmp_path):
    minimal_raw["stop"] = {"min_gain_per_usd": {"window": 3}}
    with pytest.raises(ConfigError, match="stop.min_gain_per_usd.threshold"):
        build_config(minimal_raw, base_dir=tmp_path)


def test_a_non_positive_usd_cap_is_an_error(minimal_raw, tmp_path):
    minimal_raw["stop"] = {"max_usd_since_improvement": 0}
    with pytest.raises(ConfigError, match="stop.max_usd_since_improvement"):
        build_config(minimal_raw, base_dir=tmp_path)


def test_an_unknown_stop_key_is_still_rejected(minimal_raw, tmp_path):
    minimal_raw["stop"] = {"max_usd_since_improve": 1.0}
    with pytest.raises(ConfigError, match="stop: unknown key"):
        build_config(minimal_raw, base_dir=tmp_path)


# -- the views -------------------------------------------------------------


def _demo_points():
    return series(
        [run_row(str(g), g, -10.0 + g) for g in range(4)],
        [usage_row(g, 0.10) for g in range(1, 4)],
    )


def test_the_markdown_table_has_one_row_per_generation():
    text = render_economics(_demo_points())
    assert "### Economics" in text
    assert "USD/unit (window 3)" in text
    body = [ln for ln in text.splitlines() if ln.startswith("| ") and ln[2].isdigit()]
    assert len(body) == 4, "one row per generation, header excluded"


def test_the_board_only_shows_economics_when_it_is_given_usage():
    rows = [run_row("a", 0, -10.0), run_row("b", 1, -9.0)]
    assert "### Economics" not in render_markdown(rows)
    assert "### Economics" in render_markdown(rows, usage=[usage_row(1, 0.1)])


def test_the_svg_chart_is_inline_and_dependency_free():
    svg = economics_svg(_demo_points())
    assert svg.startswith("<h2>Economics</h2>")
    assert "<svg" in svg and "viewBox" in svg
    assert "http" not in svg and "<script" not in svg
    assert svg.count("<circle") == 4
    assert 'class="dot up"' in svg, "improving generations are marked"


def test_the_chart_needs_two_points_before_it_draws_anything():
    assert "Not enough generations" in economics_svg(_demo_points()[:1])
    assert "Not enough generations" in economics_svg([])


def test_a_flat_run_does_not_divide_by_zero():
    points = series(
        [run_row(str(g), g, -10.0) for g in range(3)],
        [usage_row(g, 0.0) for g in range(1, 3)],
    )
    svg = economics_svg(points)
    assert "<svg" in svg


def test_the_html_dashboard_embeds_the_chart_when_usage_is_supplied():
    rows = [run_row(str(g), g, -10.0 + g) for g in range(4)]
    usage = [usage_row(g, 0.10) for g in range(1, 4)]
    assert "<h2>Economics</h2>" not in render_html(rows)
    page = render_html(rows, usage=usage)
    assert "<h2>Economics</h2>" in page
    assert ".chart .line" in page, "the chart's styles ship with the page"


# -- inside the loop -------------------------------------------------------


def test_the_driver_stops_when_the_plateau_gets_too_expensive(tmp_path):
    """One improvement, then nothing, against a cap of a fraction of a cent."""
    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        evaluate=replace(config.evaluate, stages=config.evaluate.stages[:2]),
        models=replace(
            config.models,
            small=replace(config.models.small, price_in_per_mtok=1000.0),
            strong=replace(config.models.strong, price_in_per_mtok=1000.0),
        ),
        stop=replace(
            config.stop,
            patience=99,
            min_big_steps=0,
            max_usd_since_improvement=0.001,
        ),
        search=replace(
            config.search,
            generations=6,
            children_per_generation=1,
            big_step_every=1,  # every generation is a big step: the guard is met
            scratchpad_every=0,
            inspirations=0,
            operators={"rewrite": 1.0},
        ),
    )
    flat = (
        "```python\n"
        "def priority(item, bins):\n"
        '    """Worst fit -- worse than the seed, and never improving."""\n'
        "    return [b - item for b in bins]\n"
        "```"
    )
    provider = FakeProvider([flat], cycle=True)
    driver = Driver(
        config,
        run_dir=tmp_path / "econ",
        providers={"small": provider, "strong": provider},
    )
    summary = driver.run()
    assert "stop.max_usd_since_improvement reached" in summary.stop_reason
    assert summary.generations < 6
    assert summary.economics, "the summary carries the series"
    assert summary.economics[-1].usd_since_improvement >= 0.001


def test_the_summary_series_matches_the_ledger(tmp_path):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        evaluate=replace(config.evaluate, stages=config.evaluate.stages[:2]),
        search=replace(
            config.search,
            generations=2,
            children_per_generation=1,
            big_step_every=99,
            scratchpad_every=0,
            inspirations=0,
            operators={"rewrite": 1.0},
        ),
        stop=replace(config.stop, patience=99),
    )
    provider = FakeProvider(
        [
            "```python\ndef priority(item, bins):\n"
            '    """A."""\n    return [b - item for b in bins]\n```',
            "```python\ndef priority(item, bins):\n"
            '    """B."""\n    return [-(b - item) * 3 - 1 for b in bins]\n```',
        ],
        cycle=True,
    )
    driver = Driver(
        config,
        run_dir=tmp_path / "econ2",
        providers={"small": provider, "strong": provider},
    )
    summary = driver.run()
    totals = driver.ledger.totals()
    assert summary.economics[-1].cumulative_usd == pytest.approx(totals["usd"])
    assert summary.economics[-1].cumulative_calls == int(totals["calls"])


def test_the_archive_snapshot_carries_the_configured_economics_window(tmp_path, capsys):
    """`status` reads this back instead of assuming DEFAULT_WINDOW."""
    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        evaluate=replace(config.evaluate, stages=config.evaluate.stages[:2]),
        search=replace(
            config.search,
            generations=1,
            children_per_generation=1,
            scratchpad_every=0,
            inspirations=0,
            operators={"rewrite": 1.0},
        ),
        stop=replace(
            config.stop,
            patience=99,
            min_gain_per_usd=GainPerUsdRule(window=7, threshold=0.001),
        ),
    )
    assert config.stop.economics_window == 7
    provider = FakeProvider(["not valid python"], cycle=True)
    driver = Driver(
        config,
        run_dir=tmp_path / "econ3",
        providers={"small": provider, "strong": provider},
    )
    driver.run()
    assert driver.ledger.read_archive()["economics_window"] == 7

    from evolvekit.cli import main

    code = main(["status", "--run-dir", str(driver.ledger.run_dir)])
    assert code == 0
    assert "(w7)" in capsys.readouterr().out
