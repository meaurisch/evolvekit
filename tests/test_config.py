"""Config validation: every failure must name the key that caused it."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from evolvekit.config import ConfigError, build_config, load_config


def build(raw, tmp_path: Path):
    return build_config(raw, base_dir=tmp_path)


def test_minimal_config_is_valid(minimal_raw, tmp_path):
    config = build(minimal_raw, tmp_path)
    assert config.problem.skeleton.name == "skeleton.py"
    assert config.evaluate.score.weights == {"value": 1.0}
    assert config.models.small.provider == "fake"
    # Defaults fill in the optional sections.
    assert config.budget.max_usd == 1.0
    assert config.search.children_per_generation == 4


def test_example_config_loads(example_config_path):
    config = load_config(example_config_path)
    assert [s.id for s in config.evaluate.stages] == ["static", "proxy", "full"]
    assert config.evaluate.stages[1].promote.top_k_per_generation == 2
    assert config.final_stage.private_inputs == ("holdout",)
    assert config.evaluate.score.direction == "minimize"
    assert {p.kpi for p in config.evaluate.penalties} == {
        "priority_errors",
        "overfull_bins",
    }


def test_missing_section_names_the_key(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    del raw["models"]
    with pytest.raises(ConfigError, match=r"<root>\.models"):
        build(raw, tmp_path)


def test_unknown_key_is_rejected_with_suggestions(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["budget"] = {"max_usd": 1.0, "max_dollars": 5}
    with pytest.raises(ConfigError) as excinfo:
        build(raw, tmp_path)
    assert "budget" in str(excinfo.value)
    assert "max_dollars" in str(excinfo.value)
    assert "max_tokens" in str(excinfo.value)  # known keys are listed


def test_missing_skeleton_file(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["problem"]["skeleton"] = "nope.py"
    with pytest.raises(ConfigError, match="problem.skeleton: no such file"):
        build(raw, tmp_path)


def test_unknown_provider(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["models"]["small"]["provider"] = "ollama"
    with pytest.raises(ConfigError, match=r"models\.small\.provider"):
        build(raw, tmp_path)


def test_negative_price_is_rejected(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["models"]["small"]["price_in_per_mtok"] = -1
    with pytest.raises(ConfigError, match="price_in_per_mtok: must be >= 0"):
        build(raw, tmp_path)


def test_first_stage_must_be_static(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["stages"] = [
        {"id": "proxy", "kind": "command", "command": "x {candidate} {out}"}
    ]
    with pytest.raises(ConfigError, match=r"stages\[0\]\.kind"):
        build(raw, tmp_path)


def test_static_stage_may_not_declare_a_promote_rule(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["stages"] = [
        {"id": "static", "kind": "builtin-static", "promote": {"top_k_per_generation": 1}},
        {"id": "proxy", "kind": "command", "command": "x {candidate} {out}"},
    ]
    with pytest.raises(ConfigError, match=r"stages\[0\]\.promote"):
        build(raw, tmp_path)


def test_command_stage_requires_placeholders(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["stages"] = [
        {"id": "static", "kind": "builtin-static"},
        {"id": "proxy", "kind": "command", "command": "python run.py {candidate}"},
    ]
    with pytest.raises(ConfigError, match=r"must contain the \{out\} placeholder"):
        build(raw, tmp_path)


def test_duplicate_stage_ids(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["stages"] = [
        {"id": "static", "kind": "builtin-static"},
        {"id": "static", "kind": "command", "command": "x {candidate} {out}"},
    ]
    with pytest.raises(ConfigError, match="duplicate stage id"):
        build(raw, tmp_path)


def test_private_inputs_only_on_the_final_stage(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["stages"] = [
        {"id": "static", "kind": "builtin-static"},
        {
            "id": "proxy",
            "kind": "command",
            "command": "x {candidate} {out}",
            "private_inputs": ["hold"],
        },
        {"id": "full", "kind": "command", "command": "x {candidate} {out}"},
    ]
    with pytest.raises(ConfigError, match="only the final stage"):
        build(raw, tmp_path)


def test_objective_must_be_weighted(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["score"] = {"objective": "value", "weights": {"other": 1.0}}
    with pytest.raises(ConfigError, match="must include the objective KPI"):
        build(raw, tmp_path)


def test_bad_direction(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["score"] = {"objective": "value", "direction": "sideways"}
    with pytest.raises(ConfigError, match="maximize"):
        build(raw, tmp_path)


def test_penalty_scale_must_be_known(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["penalties"] = [{"kpi": "bad", "scale": "sqrt"}]
    with pytest.raises(ConfigError, match=r"penalties\[0\]\.scale"):
        build(raw, tmp_path)


def test_penalty_weight_must_be_non_negative(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["penalties"] = [{"kpi": "bad", "weight": -1.0}]
    with pytest.raises(ConfigError, match="penalties always subtract"):
        build(raw, tmp_path)


def test_unknown_operator_share(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["search"] = {"operators": {"telepathy": 1.0}}
    with pytest.raises(ConfigError, match="unknown operator"):
        build(raw, tmp_path)


def test_promote_rule_needs_a_sub_rule(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["stages"] = [
        {"id": "static", "kind": "builtin-static"},
        {
            "id": "proxy",
            "kind": "command",
            "command": "x {candidate} {out}",
            "promote": {"top_k_per_generation": None, "archive_percentile": None},
        },
    ]
    with pytest.raises(ConfigError, match="top_k_per_generation or archive_percentile"):
        build(raw, tmp_path)


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="config file not found"):
        load_config(tmp_path / "absent.yaml")


def test_invalid_yaml(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("problem: [\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config(path)


def test_non_python_language_rejected(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["problem"]["language"] = "rust"
    with pytest.raises(ConfigError, match="only 'python' is supported"):
        build(raw, tmp_path)


# -- the behaviour signature -----------------------------------------------


def test_signature_defaults_are_the_documented_ones(minimal_raw, tmp_path):
    config = build(minimal_raw, tmp_path)
    assert config.evaluate.signature_digits == 9
    assert config.evaluate.signature_ignore == (
        "runtime_s",
        "complexity",
        "static_problems",
    )


def test_signature_keys_are_configurable(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["signature_digits"] = 4
    raw["evaluate"]["signature_ignore"] = ["seconds"]
    config = build(raw, tmp_path)
    assert config.evaluate.signature_digits == 4
    assert config.evaluate.signature_ignore == ("seconds",)


def test_an_empty_signature_ignore_list_is_not_the_default(minimal_raw, tmp_path):
    """`[]` means "ignore nothing"; only an omitted key means "use the default"."""
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["signature_ignore"] = []
    assert build(raw, tmp_path).evaluate.signature_ignore == ()


def test_signature_digits_must_be_positive(minimal_raw, tmp_path):
    raw = copy.deepcopy(minimal_raw)
    raw["evaluate"]["signature_digits"] = 0
    with pytest.raises(ConfigError, match="evaluate.signature_digits: must be >= 1"):
        build(raw, tmp_path)


# -- extends ----------------------------------------------------------------


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_deep_merge_merges_mappings_and_replaces_everything_else():
    from evolvekit.config import deep_merge

    base = {"a": {"x": 1, "y": 2}, "list": [1, 2], "keep": "yes"}
    override = {"a": {"y": 3, "z": 4}, "list": [9]}
    assert deep_merge(base, override) == {
        "a": {"x": 1, "y": 3, "z": 4},
        "list": [9],
        "keep": "yes",
    }


def test_a_null_in_the_child_clears_an_inherited_mapping():
    from evolvekit.config import deep_merge

    assert deep_merge({"options": {"responses_path": "r.yaml"}}, {"options": None}) == {
        "options": None
    }


def test_extends_folds_a_child_onto_its_base(tmp_path, minimal_raw):
    import yaml

    write(tmp_path / "base.yaml", yaml.safe_dump(minimal_raw))
    write(
        tmp_path / "child.yaml",
        yaml.safe_dump(
            {
                "extends": "base.yaml",
                "budget": {"max_usd": 7.0},
                "models": {"small": {"provider": "claude-cli", "model": "haiku",
                                     "options": None}},
            }
        ),
    )
    base = load_config(tmp_path / "base.yaml")
    child = load_config(tmp_path / "child.yaml")
    # Inherited wholesale...
    assert child.problem == base.problem
    assert child.evaluate == base.evaluate
    assert child.search == base.search
    # ...overridden where the child says so, `null` clearing the fake options.
    assert child.budget.max_usd == 7.0
    assert child.models.small.provider == "claude-cli"
    assert child.models.small.options == {}
    assert child.models.strong == base.models.strong


def test_extends_reports_a_missing_base(tmp_path):
    write(tmp_path / "child.yaml", "extends: nope.yaml\n")
    with pytest.raises(ConfigError, match="extends: no such file"):
        load_config(tmp_path / "child.yaml")


def test_extends_refuses_a_cycle(tmp_path):
    write(tmp_path / "a.yaml", "extends: b.yaml\n")
    write(tmp_path / "b.yaml", "extends: a.yaml\n")
    with pytest.raises(ConfigError, match="circular chain"):
        load_config(tmp_path / "a.yaml")


# -- the two shipped example configs ---------------------------------------


def test_the_claude_example_cannot_drift_from_the_base_example(example_dir):
    """The Phase B evidence run used a stale copy of this file: no hold-out
    penalty, no crossover, no archive descriptors. `extends` makes that
    impossible, and this test says so out loud."""
    base = load_config(example_dir / "evolvekit.yaml")
    claude = load_config(example_dir / "evolvekit.claude.yaml")
    assert claude.problem == base.problem
    assert claude.evaluate == base.evaluate
    assert claude.search == base.search
    assert claude.stop == base.stop
    # ...and differ in exactly the two sections a paid run is about.
    assert (claude.models.small.provider, claude.models.strong.provider) == (
        "claude-cli",
        "claude-cli",
    )
    assert (claude.models.small.model, claude.models.strong.model) == (
        "haiku",
        "sonnet",
    )
    assert base.models.small.provider == "fake"


def test_every_provider_variant_of_both_examples_loads(example_root):
    """A config that only parses when you run it is a config that fails at 3am."""
    variants = sorted(example_root.glob("*/evolvekit.*.yaml"))
    # binpacking + circlepacking: claude + openrouter each; pyvrp: real
    assert len(variants) >= 5, [p.name for p in variants]
    for path in variants:
        config = load_config(path)
        assert config.models.small.provider != "fake", path


@pytest.mark.parametrize("example", ["binpacking", "circlepacking"])
def test_the_openrouter_variant_shares_the_problem_and_the_evaluator(
    example_root, example
):
    """Cheapest breadth: the key for the many small calls, the subscription for
    the few big ones. It must still be the *same* problem."""
    directory = example_root / example
    base = load_config(directory / "evolvekit.yaml")
    variant = load_config(directory / "evolvekit.openrouter.yaml")
    assert variant.problem == base.problem
    assert variant.evaluate == base.evaluate
    assert variant.models.small.provider == "openrouter"
    assert variant.models.strong.provider == "claude-cli"
    assert variant.models.strong.model == "sonnet"
    # An embed slot exists so `preflight --provider-check` exercises the key,
    # but the novelty gate stays on the free local method.
    assert variant.models.embed is not None
    assert variant.models.embed.provider == "openrouter"
    assert variant.search.novelty.near.method == "local"
    # The fake provider's scripted-response options must not survive.
    assert variant.models.small.options == {}


@pytest.mark.parametrize("example", ["binpacking", "circlepacking"])
def test_the_openrouter_variant_leaves_the_model_ids_to_be_filled_in(
    example_root, example
):
    """A placeholder that looks like a placeholder, not a plausible wrong id."""
    variant = load_config(example_root / example / "evolvekit.openrouter.yaml")
    assert variant.models.small.model.startswith("<")
    assert variant.models.embed.model.startswith("<")
    text = (example_root / example / "evolvekit.openrouter.yaml").read_text(
        encoding="utf-8"
    )
    assert "FILL THIS IN" in text
    # The one operational rule that costs money to learn the hard way.
    assert "NEVER run two claude-cli jobs at once" in text


def test_the_base_example_declares_the_phase_b_search(example_config_path):
    config = load_config(example_config_path)
    assert config.evaluate.holdout_penalty == 1.0
    assert "crossover" in config.search.operators
    assert config.search.inspirations >= 1
    assert config.search.scratchpad_every >= 1
    assert config.search.novelty_retry is True
    assert [d.kpi for d in config.search.archive.descriptors] == [
        "complexity",
        "mean_openness",
    ]
