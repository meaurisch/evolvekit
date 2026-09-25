"""A run that never calls a model must be possible, and must not need one.

`param_lhs` costs no tokens, so a sweep over a block's declared constants is a
complete search that needs no provider, no key and no network. It could not be
run: `big_step_every: 0` was refused, a plateau escalated to the strong model
whatever the operator shares said, the scratchpad asked the small model for
notes every few generations, `stop.patience` waited for a big step that could
never happen, and a `models` section was mandatory even though nothing would
ever read it. Four of six new users ran into this within their first hour.
"""

from __future__ import annotations

import copy

import pytest

from evolvekit.config import ConfigError, build_config
from evolvekit.search.driver import Driver
from tests.test_params import BLOCK, sweepable_project  # noqa: F401  (a fixture)


@pytest.fixture
def raw(tmp_path):
    (tmp_path / "skeleton.py").write_text(
        "# EVOLVE-BLOCK-START\n" + BLOCK + "# EVOLVE-BLOCK-END\n", encoding="utf-8"
    )
    return {
        "problem": {"skeleton": "skeleton.py", "required_functions": ["value"]},
        "evaluate": {
            "stages": [{"id": "static", "kind": "builtin-static"}],
            "score": {"objective": "value"},
        },
        "search": {"operators": {"param_lhs": 1.0}},
    }


# -- configuration ---------------------------------------------------------


def test_models_may_be_left_out_when_no_operator_needs_one(raw, tmp_path):
    config = build_config(raw, base_dir=tmp_path)
    assert config.models is None
    assert config.search.uses_llm is False


def test_a_share_of_zero_switches_an_inherited_operator_off(raw, tmp_path):
    """`extends` merges `search.operators` key by key, so a child config drops
    an operator it inherited by giving it a share of 0."""
    raw["search"]["operators"] = {"diff": 0, "rewrite": 0.0, "param_lhs": 1.0}
    assert build_config(raw, base_dir=tmp_path).search.uses_llm is False


def test_leaving_models_out_while_an_llm_operator_has_a_share_is_explained(raw, tmp_path):
    raw["search"]["operators"] = {"diff": 0.5, "param_lhs": 0.5}
    with pytest.raises(ConfigError) as error:
        build_config(raw, base_dir=tmp_path)
    message = str(error.value)
    assert "models" in message and "diff" in message
    assert "param_lhs" in message, "the message has to say how to run without a model"



def test_an_embedding_gate_without_any_models_is_explained_before_the_run(raw, tmp_path):
    # With no `models` section the embedding check returned early, the config
    # was accepted, and the run died in Driver() with "'NoneType' object has no
    # attribute 'by_role'" -- a traceback instead of a sentence.
    raw["search"]["novelty"] = {"near": {"method": "embedding"}}
    with pytest.raises(ConfigError) as error:
        build_config(raw, base_dir=tmp_path)
    message = str(error.value)
    assert "search.novelty.near.method" in message and "models.embed" in message
    assert "'local'" in message and "'off'" in message, "the message has to say how to run without a model"

def test_big_step_every_zero_means_never(raw, tmp_path):
    raw["search"]["big_step_every"] = 0
    assert build_config(raw, base_dir=tmp_path).search.big_step_every == 0
    raw["search"]["big_step_every"] = -1
    with pytest.raises(ConfigError, match="big_step_every"):
        build_config(raw, base_dir=tmp_path)


def test_big_steps_can_be_switched_off_in_a_run_that_does_use_a_model(raw, tmp_path):
    raw = copy.deepcopy(raw)
    raw["search"] = {"operators": {"rewrite": 1.0}, "big_step_every": 0}
    model = {"provider": "fake", "model": "m", "options": {"responses": ["x"]}}
    raw["models"] = {"small": model, "strong": model}
    driver = Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run")
    assert all("big_step" not in driver._plan_operators(g, plateau=False) for g in range(1, 12))


# -- the loop --------------------------------------------------------------


def _without_models(project, tmp_path, **search):
    import yaml

    document = yaml.safe_load(project.read_text(encoding="utf-8"))
    del document["models"]
    document["search"].update(search)
    document["search"].pop("big_step_every", None)   # the default: every 5 generations
    document["search"].pop("scratchpad_every", None)  # the default: every 5 generations
    document["stop"] = {"patience": 3, "epsilon": 1e9}  # nothing ever counts as an improvement
    return build_config(document, base_dir=project.parent)


def test_no_generation_of_a_zero_llm_run_plans_a_big_step(sweepable_project, tmp_path):  # noqa: F811
    driver = Driver(_without_models(sweepable_project, tmp_path), run_dir=tmp_path / "run")
    for generation in range(1, 12):
        assert driver._plan_operators(generation, plateau=True) == ["param_lhs"] * 3


@pytest.mark.slow
def test_a_whole_run_without_models_runs_and_stops_on_patience(sweepable_project, tmp_path):  # noqa: F811
    """Default `big_step_every`, default `scratchpad_every`, default
    `min_big_steps`: every one of them used to reach for a model."""
    config = _without_models(sweepable_project, tmp_path, generations=9)
    summary = Driver(config, run_dir=tmp_path / "run").run()
    assert "stop.patience" in summary.stop_reason, summary.stop_reason
    assert summary.generations == 3
    assert summary.totals.get("calls", 0.0) == 0.0
    assert summary.candidates > 1


def test_preflight_with_a_provider_check_has_no_provider_to_check(sweepable_project, tmp_path):  # noqa: F811
    from evolvekit.preflight import preflight

    report = preflight(_without_models(sweepable_project, tmp_path), provider_check=True)
    assert report.exit_code == 0 and report.providers == []
