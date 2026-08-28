"""`preflight`: everything checkable before a run costs anything.

The questions this answers are the ones that sank the real runs *after* money
had been spent -- a timeout guessed rather than measured, a daily cap that a
24-hour day cannot deliver, a provider that was never going to answer. Every
test here is offline; the provider check runs against the `fake` backend.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from evolvekit.cli import main
from evolvekit.config import build_config, load_config
from evolvekit.preflight import (
    BASELINE_QUIET_MACHINE_S,
    PROVIDER_CHECK_PROMPT,
    TIMEOUT_HEADROOM,
    format_report,
    preflight,
)
from evolvekit.providers.fake import FakeProvider

REPLY = "```python\ndef f():\n    return 2\n```"


def _sleeper(tmp_path: Path, seconds: float, *, value: float = 1.0) -> str:
    script = tmp_path / f"sleep_{int(seconds * 1000)}.py"
    script.write_text(
        "import json, sys, time, pathlib\n"
        f"time.sleep({seconds})\n"
        "pathlib.Path(sys.argv[1]).write_text(\n"
        f"    json.dumps({{'kpis': {{'value': {value}}}, "
        "'text_feedback': 'slept'}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}" {{out}} {{candidate}}'


def _content_timed_command(tmp_path: Path) -> str:
    """A command whose duration is driven by the candidate file's content --
    the seed and a swapped-in `--candidate` can be made to take different
    amounts of time, so a test can prove which one preflight actually ran."""
    script = tmp_path / "content_timed.py"
    script.write_text(
        "import json, sys, time, pathlib\n"
        "text = pathlib.Path(sys.argv[2]).read_text(encoding='utf-8')\n"
        "time.sleep(0.02 * text.count('X'))\n"
        "pathlib.Path(sys.argv[1]).write_text(\n"
        "    json.dumps({'kpis': {'value': 1.0}, 'text_feedback': 'ok'}),"
        " encoding='utf-8')\n",
        encoding="utf-8",
    )
    return f'"{sys.executable}" "{script}" {{out}} {{candidate}}'


def _config(minimal_raw, tmp_path, *, stages, **sections):
    minimal_raw["evaluate"]["stages"] = stages
    minimal_raw["evaluate"]["score"] = {"objective": "value"}
    minimal_raw.update(sections)
    return build_config(minimal_raw, base_dir=tmp_path)


# -- the happy path on both worked examples --------------------------------


def test_the_bin_packing_example_preflights_clean(example_config_path):
    report = preflight(load_config(example_config_path))
    assert report.exit_code == 0
    assert [s.label for s in report.stages] == [
        "static",
        "proxy",
        "full",
        "full (hold-out)",
    ]
    assert not report.failures and not report.warnings


def test_the_private_hold_out_is_run_too(example_config_path):
    """A hold-out that only runs once the search is under way is a hold-out
    whose first failure costs a generation."""
    report = preflight(load_config(example_config_path))
    private = [s for s in report.stages if s.private]
    assert len(private) == 1
    assert private[0].ok and private[0].kpis["excess_pct"] > 0


def test_the_circle_packing_example_preflights_clean(example_root):
    report = preflight(load_config(example_root / "circlepacking" / "evolvekit.yaml"))
    assert report.exit_code == 0
    assert [s.label for s in report.stages] == ["static", "full"]


def test_kpis_and_feedback_and_duration_are_all_reported(example_config_path):
    report = preflight(load_config(example_config_path))
    full = next(s for s in report.stages if s.label == "full")
    assert full.kpis["excess_pct"] == pytest.approx(5.871, abs=0.001)
    assert "worst single instance" in full.feedback
    assert full.duration_s > 0


# -- timeout headroom ------------------------------------------------------


@pytest.mark.slow
def test_a_timeout_with_no_headroom_warns(minimal_raw, tmp_path):
    config = _config(
        minimal_raw,
        tmp_path,
        stages=[
            {"id": "static", "kind": "builtin-static", "import_check": False},
            {
                "id": "full",
                "kind": "command",
                # 1.0s of sleep plus interpreter start-up, under a timeout that
                # is comfortably above the duration but below 1.5x it.
                "command": _sleeper(tmp_path, 1.0),
                "timeout": 1.4,
            },
        ],
        budget={"max_full_evals_per_day": 200},
        search={"generations": 2, "children_per_generation": 2},
    )
    report = preflight(config)
    assert report.exit_code == 1
    assert any("timeout" in w and "full" in w for w in report.warnings)
    assert not report.failures


def test_a_generous_timeout_is_a_note_not_a_warning(minimal_raw, tmp_path):
    config = _config(
        minimal_raw,
        tmp_path,
        stages=[
            {"id": "static", "kind": "builtin-static", "import_check": False},
            {
                "id": "full",
                "kind": "command",
                "command": _sleeper(tmp_path, 0.05),
                "timeout": 120,
            },
        ],
        budget={"max_full_evals_per_day": 200},
        search={"generations": 2, "children_per_generation": 2},
    )
    report = preflight(config)
    assert not report.warnings
    assert any("Generous is fine" in n for n in report.notes)


def test_the_headroom_multiple_is_the_documented_one(minimal_raw, tmp_path):
    """1.5x, because a child that does more work than the seed is normal."""
    assert TIMEOUT_HEADROOM == 1.5


# -- baseline discipline: a long timeout hints at a wall-clock-bounded solver


def test_a_stage_timeout_at_the_quiet_machine_threshold_notes_it(
    minimal_raw, tmp_path
):
    assert BASELINE_QUIET_MACHINE_S == 60.0
    config = _config(
        minimal_raw,
        tmp_path,
        stages=[
            {"id": "static", "kind": "builtin-static", "import_check": False},
            {
                "id": "full",
                "kind": "command",
                "command": _sleeper(tmp_path, 0.01),
                "timeout": BASELINE_QUIET_MACHINE_S,
            },
        ],
        budget={"max_full_evals_per_day": 200},
        search={"generations": 2, "children_per_generation": 2},
    )
    report = preflight(config)
    assert any(
        "quiet machine" in n and "full" in n for n in report.notes
    )


def test_a_short_stage_timeout_gets_no_quiet_machine_note(minimal_raw, tmp_path):
    config = _config(
        minimal_raw,
        tmp_path,
        stages=[
            {"id": "static", "kind": "builtin-static", "import_check": False},
            {
                "id": "full",
                "kind": "command",
                "command": _sleeper(tmp_path, 0.01),
                "timeout": BASELINE_QUIET_MACHINE_S - 1,
            },
        ],
        budget={"max_full_evals_per_day": 200},
        search={"generations": 2, "children_per_generation": 2},
    )
    report = preflight(config)
    assert not any("quiet machine" in n for n in report.notes)


# -- --candidate: preflight a real candidate instead of the seed -----------


def test_candidate_swaps_in_a_real_candidates_source(minimal_raw, tmp_path):
    """The seed is trivial (0 X's, near-instant); a saved candidate can be
    much heavier. `candidate=` should time the candidate, not the seed."""
    config = _config(
        minimal_raw,
        tmp_path,
        stages=[
            {"id": "static", "kind": "builtin-static", "import_check": False},
            {
                "id": "full",
                "kind": "command",
                "command": _content_timed_command(tmp_path),
                "timeout": 0.8,
            },
        ],
        budget={"max_full_evals_per_day": 200},
        search={"generations": 2, "children_per_generation": 2},
    )

    seed_report = preflight(config)
    assert not seed_report.warnings

    heavy = tmp_path / "heavy_candidate.py"
    heavy.write_text("# " + "X" * 30 + "\ndef f():\n    return 1\n", encoding="utf-8")
    candidate_report = preflight(config, candidate=heavy)
    assert candidate_report.exit_code == 1
    assert any("timeout" in w and "full" in w for w in candidate_report.warnings)
    full = next(s for s in candidate_report.stages if s.label == "full")
    assert full.duration_s > 0.5


def test_format_report_notes_the_candidate_path(minimal_raw, tmp_path):
    config = _config(
        minimal_raw,
        tmp_path,
        stages=[{"id": "static", "kind": "builtin-static", "import_check": False}],
        budget={"max_full_evals_per_day": 200},
        search={"generations": 2, "children_per_generation": 2},
    )
    candidate = tmp_path / "c.py"
    candidate.write_text("def f():\n    return 1\n", encoding="utf-8")
    report = preflight(config, candidate=candidate)
    text = format_report(report, config, candidate=candidate)
    assert f"candidate: {candidate}" in text
    assert f"candidate: {candidate}" not in format_report(report, config)


def test_the_cli_preflight_accepts_a_candidate_path(
    minimal_raw, tmp_path, capsys, monkeypatch
):
    minimal_raw["evaluate"]["stages"] = [
        {"id": "static", "kind": "builtin-static", "import_check": False}
    ]
    minimal_raw["evaluate"]["score"] = {"objective": "value"}
    minimal_raw["budget"] = {"max_full_evals_per_day": 200}
    import yaml

    config_path = tmp_path / "evolvekit.yaml"
    config_path.write_text(yaml.safe_dump(minimal_raw), encoding="utf-8")
    candidate = tmp_path / "c.py"
    candidate.write_text("def f():\n    return 1\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    code = main(
        ["preflight", "--config", str(config_path), "--candidate", str(candidate)]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert f"candidate: {candidate}" in out


# -- budget and stop against the observed durations ------------------------


def test_a_daily_cap_that_a_day_cannot_deliver_warns(minimal_raw, tmp_path):
    """20 full evaluations a day, at 2.5 hours each, is 50 hours."""
    config = _config(
        minimal_raw,
        tmp_path,
        stages=[
            {"id": "static", "kind": "builtin-static", "import_check": False},
            {
                "id": "full",
                "kind": "command",
                "command": _sleeper(tmp_path, 0.5),
                "timeout": 60,
            },
        ],
        budget={"max_full_evals_per_day": 400_000},
    )
    report = preflight(config)
    assert report.exit_code == 1
    warning = next(w for w in report.warnings if "max_full_evals_per_day" in w)
    assert "wall clock" in warning
    # It names the number that would fit instead of just complaining.
    assert "set it to about" in warning


def test_a_run_that_needs_more_full_evals_than_the_cap_says_how_many_days(
    minimal_raw, tmp_path
):
    config = _config(
        minimal_raw,
        tmp_path,
        stages=[
            {"id": "static", "kind": "builtin-static", "import_check": False},
            {
                "id": "proxy",
                "kind": "command",
                "command": _sleeper(tmp_path, 0.01),
                "timeout": 60,
                "promote": {"top_k_per_generation": 2},
            },
            {
                "id": "full",
                "kind": "command",
                "command": _sleeper(tmp_path, 0.01),
                "timeout": 60,
            },
        ],
        budget={"max_full_evals_per_day": 3},
        search={"generations": 10, "children_per_generation": 4},
    )
    report = preflight(config)
    warning = next(w for w in report.warnings if "day(s) of calendar time" in w)
    # top_k_per_generation caps promotions at 2, not at the 4 children bred.
    assert "10 generation(s) x 2 promoted" in warning or "x 2 promoted" in warning
    assert "20 full evaluation(s)" in warning


def test_a_short_run_gets_a_projection_note_rather_than_a_warning(
    example_config_path,
):
    report = preflight(load_config(example_config_path))
    assert any("projected evaluator wall clock" in n for n in report.notes)
    assert not report.warnings


# -- failures --------------------------------------------------------------


def test_a_seed_that_fails_a_stage_is_a_failure_and_stops_the_cascade(
    minimal_raw, tmp_path
):
    broken = tmp_path / "broken.py"
    broken.write_text("import sys\nsys.exit(4)\n", encoding="utf-8")
    config = _config(
        minimal_raw,
        tmp_path,
        stages=[
            {"id": "static", "kind": "builtin-static", "import_check": False},
            {
                "id": "proxy",
                "kind": "command",
                "command": f'"{sys.executable}" "{broken}" {{out}} {{candidate}}',
                "timeout": 60,
            },
            {
                "id": "full",
                "kind": "command",
                "command": _sleeper(tmp_path, 0.01),
                "timeout": 60,
            },
        ],
    )
    report = preflight(config)
    assert report.exit_code == 2
    assert any("exit code 4" in f for f in report.failures)
    # The full stage was never reached: a broken proxy makes it meaningless.
    assert [s.stage_id for s in report.stages] == ["static", "proxy"]


def test_a_seed_that_fails_the_static_stage_is_named_as_such(minimal_raw, tmp_path):
    skeleton = tmp_path / "skeleton.py"
    skeleton.write_text(
        "# EVOLVE-BLOCK-START\nimport os\n# EVOLVE-BLOCK-END\n", encoding="utf-8"
    )
    minimal_raw["problem"]["skeleton"] = "skeleton.py"
    config = _config(
        minimal_raw,
        tmp_path,
        stages=[{"id": "static", "kind": "builtin-static", "import_check": False}],
    )
    report = preflight(config)
    assert report.exit_code == 2
    assert any("static stage" in f and "os" in f for f in report.failures)


# -- the optional provider check -------------------------------------------


def test_no_provider_check_by_default(example_config_path):
    report = preflight(load_config(example_config_path))
    assert report.providers == []


def test_each_configured_role_gets_exactly_one_call(example_config_path):
    small, strong = FakeProvider([REPLY]), FakeProvider([REPLY])
    report = preflight(
        load_config(example_config_path),
        provider_check=True,
        providers={"small": small, "strong": strong},
    )
    assert len(small.calls) == 1 and len(strong.calls) == 1
    assert [r.role for r in report.providers] == ["small", "strong"]
    assert all(r.ok for r in report.providers)


def test_the_check_prompt_is_the_documented_one(example_config_path):
    small, strong = FakeProvider([REPLY]), FakeProvider([REPLY])
    preflight(
        load_config(example_config_path),
        provider_check=True,
        providers={"small": small, "strong": strong},
    )
    assert small.calls[0]["messages"] == [
        {"role": "user", "content": PROVIDER_CHECK_PROMPT}
    ]
    # A minimal call, not the configured ceiling: this is a round trip, not a
    # generation.
    assert small.calls[0]["max_tokens"] <= 64


def test_tokens_usd_and_latency_are_all_reported(example_config_path):
    report = preflight(
        load_config(example_config_path),
        provider_check=True,
        providers={"small": FakeProvider([REPLY]), "strong": FakeProvider([REPLY])},
    )
    check = report.providers[0]
    assert check.input_tokens > 0 and check.output_tokens > 0
    assert check.usd > 0  # the example prices the fake models
    assert check.latency_s >= 0
    assert "def f" in check.reply


def test_an_embed_slot_is_checked_too(example_config_path, tmp_path):
    config = load_config(example_config_path)
    raw = {
        "provider": "fake",
        "model": "fake-embed",
        "price_in_per_mtok": 0.02,
        "options": {"responses": ["x"]},
    }
    from dataclasses import replace

    from evolvekit.config import ModelConfig

    config = replace(
        config,
        models=replace(config.models, embed=ModelConfig.parse(raw, "embed")),
    )
    embed = FakeProvider([REPLY])
    report = preflight(
        config,
        provider_check=True,
        providers={
            "small": FakeProvider([REPLY]),
            "strong": FakeProvider([REPLY]),
            "embed": embed,
        },
    )
    assert [r.role for r in report.providers] == ["small", "strong", "embed"]
    assert len(embed.embed_calls) == 1
    assert embed.embed_calls[0]["texts"] == [PROVIDER_CHECK_PROMPT]
    assert report.providers[-1].ok and "dimensional vector" in report.providers[-1].reply


def test_a_backend_with_no_embeddings_api_is_a_failure_not_a_crash(
    example_config_path,
):
    from evolvekit.config import ModelConfig
    from evolvekit.providers.claude_cli import ClaudeCliProvider
    from dataclasses import replace

    config = load_config(example_config_path)
    config = replace(
        config,
        models=replace(
            config.models,
            embed=ModelConfig.parse(
                {"provider": "openrouter", "model": "e"}, "embed"
            ),
        ),
    )
    report = preflight(
        config,
        provider_check=True,
        providers={
            "small": FakeProvider([REPLY]),
            "strong": FakeProvider([REPLY]),
            "embed": ClaudeCliProvider(runner=lambda *a, **k: None),
        },
    )
    assert report.exit_code == 2
    assert any("no embeddings" in f for f in report.failures)


def test_a_dead_provider_is_a_failure_naming_the_role(example_config_path):
    class Dead:
        name = "dead"

        def complete(self, *args, **kwargs):
            from evolvekit.providers.base import ProviderError

            raise ProviderError("session limit reached -- resets 12:30pm")

    report = preflight(
        load_config(example_config_path),
        provider_check=True,
        providers={"small": Dead(), "strong": FakeProvider([REPLY])},
    )
    assert report.exit_code == 2
    assert any("models.small" in f and "session limit" in f for f in report.failures)


# -- rendering and the CLI -------------------------------------------------


def test_the_report_renders_stages_notes_and_a_verdict(example_config_path):
    config = load_config(example_config_path)
    text = format_report(preflight(config), config)
    assert "stage static" in text
    assert "stage full (hold-out)" in text
    assert "kpis    :" in text
    assert "verdict : clean" in text


def test_the_cli_exits_zero_on_a_clean_config(example_config_path, capsys):
    code = main(["preflight", "--config", str(example_config_path)])
    assert code == 0
    assert "verdict : clean" in capsys.readouterr().out


def test_the_cli_exits_two_on_a_bad_config_path(capsys):
    code = main(["preflight", "--config", "no/such/file.yaml"])
    assert code == 2
    assert "config error" in capsys.readouterr().err
