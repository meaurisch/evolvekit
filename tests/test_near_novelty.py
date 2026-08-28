"""The near-duplicate gate: similarity instead of equality, and what it costs.

The case this exists for is the one the second real run kept paying for: a
child that is an earlier candidate with one constant moved. That is a different
AST dump, a different hash, and a full evaluation nobody wanted to buy.

Everything here is offline. The embedding path runs against the `fake` backend
or a hand-rolled stand-in for the OpenAI client; no test ever opens a socket.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from evolvekit.config import (
    ConfigError,
    ModelConfig,
    NearDuplicateConfig,
    build_config,
    load_config,
)
from evolvekit.ledger import Ledger
from evolvekit.providers.azure import AzureProvider
from evolvekit.providers.base import ProviderError, embedding_tokens
from evolvekit.providers.claude_cli import ClaudeCliProvider
from evolvekit.providers.fake import FakeProvider
from evolvekit.providers.openrouter import OpenRouterProvider
from evolvekit.search.driver import Driver
from evolvekit.search.novelty import (
    EmbeddingNearIndex,
    LocalNearIndex,
    NoveltyIndex,
    build_near_backend,
    cosine,
    local_similarity,
    node_ngrams,
    node_type_sequence,
)
from tests.conftest import EXAMPLE_CONFIG

BONUS_20 = '''\
def priority(item, bins):
    """Best fit with a bonus for a usable residual."""
    scores = []
    for b in bins:
        residual = b - item
        scores.append(-residual + 20.0 * math.exp(-((residual - 30.0) ** 2) / 128.0))
    return scores
'''

BONUS_18 = '''\
def priority(item, bins):
    """The same rule, three constants moved. A different hash; the same idea."""
    scores = []
    for b in bins:
        residual = b - item
        scores.append(-residual + 18.0 * math.exp(-((residual - 32.0) ** 2) / 120.0))
    return scores
'''

BEST_FIT = '''\
def priority(item, bins):
    """Best fit."""
    return [-(b - item) for b in bins]
'''

FIRST_FIT = '''\
def priority(item, bins):
    """First fit: the oldest bin with room wins."""
    return [float(len(bins) - i) for i in range(len(bins))]
'''


# -- the local method ------------------------------------------------------


def test_node_types_ignore_the_values_of_constants():
    assert node_type_sequence(BONUS_20) == node_type_sequence(BONUS_18)


def test_a_constant_only_change_is_a_perfect_local_match():
    assert local_similarity(BONUS_20, BONUS_18) == pytest.approx(1.0)


def test_a_different_rule_is_not_a_near_duplicate():
    assert local_similarity(BONUS_20, FIRST_FIT) < 0.97


def test_similarity_is_symmetric_and_self_similarity_is_one():
    assert local_similarity(BEST_FIT, BEST_FIT) == pytest.approx(1.0)
    assert local_similarity(BEST_FIT, FIRST_FIT) == pytest.approx(
        local_similarity(FIRST_FIT, BEST_FIT)
    )


def test_a_block_that_will_not_parse_scores_zero_rather_than_raising():
    assert node_ngrams("def priority(:::") == {}
    assert local_similarity("def priority(:::", BEST_FIT) == 0.0


def test_a_block_shorter_than_the_ngram_still_compares():
    grams = node_ngrams("x", n=99)
    assert len(grams) == 1


def test_cosine_of_disjoint_or_empty_vectors_is_zero():
    assert cosine({}, {("a",): 1}) == 0.0
    assert cosine({("a",): 1}, {("b",): 1}) == 0.0
    assert cosine({("a",): 2}, {("a",): 4}) == pytest.approx(1.0)


def test_the_local_index_names_the_closest_thing_it_has_seen():
    index = LocalNearIndex()
    index.add("g000-c0001", BEST_FIT)
    index.add("g001-c0002", BONUS_20)
    twin, score = index.closest(BONUS_18)
    assert twin == "g001-c0002"
    assert score == pytest.approx(1.0)


def test_an_empty_local_index_has_no_closest():
    assert LocalNearIndex().closest(BEST_FIT) == (None, 0.0)


# -- the verdict -----------------------------------------------------------


def _index(**kwargs) -> NoveltyIndex:
    return NoveltyIndex(near=LocalNearIndex(), threshold=0.97, **kwargs)


def test_a_near_duplicate_is_flagged_but_not_rejected():
    index = _index()
    index.add("g001-c0002", BONUS_20)
    verdict = index.check(BONUS_18, BEST_FIT, "g000-c0001")
    assert verdict.kind == "near"
    assert verdict.near and not verdict.rejects and not verdict.novel
    assert verdict.twin_id == "g001-c0002"
    assert verdict.similarity == pytest.approx(1.0)
    assert "near-duplicate: closest to g001-c0002" in verdict.reason


def test_the_exact_hash_still_wins_over_the_similarity_gate():
    index = _index()
    index.add("g001-c0002", BONUS_20)
    verdict = index.check(BONUS_20, BEST_FIT, "g000-c0001")
    assert verdict.kind == "duplicate" and verdict.rejects


def test_a_no_op_is_still_a_no_op_when_the_gate_is_on():
    index = _index()
    verdict = index.check(BEST_FIT, BEST_FIT, "g000-c0001")
    assert verdict.kind == "no_op"


def test_the_near_retry_hint_asks_for_a_new_rule_not_new_constants():
    index = _index()
    index.add("g001-c0002", BONUS_20)
    verdict = index.check(BONUS_18, BEST_FIT, "g000-c0001")
    hint = verdict.retry_hint
    assert "closest to g001-c0002" in hint
    assert "similarity 1.00" in hint
    assert "change the decision rule, not the constants" in hint


def test_near_false_skips_the_gate_entirely():
    index = _index()
    index.add("g001-c0002", BONUS_20)
    assert index.check(BONUS_18, BEST_FIT, "g000-c0001", near=False).novel


def test_a_gate_that_is_off_never_reports_a_near_duplicate():
    index = NoveltyIndex()
    index.add("g001-c0002", BONUS_20)
    assert index.closest(BONUS_18) == (None, 0.0)
    assert index.check(BONUS_18, BEST_FIT, "g000-c0001").novel


# -- the embedding method --------------------------------------------------


def test_the_fake_backend_embeds_deterministically():
    provider = FakeProvider(["x"])
    first = provider.embed(["hello"], model="fake-embed")
    second = provider.embed(["hello"], model="fake-embed")
    assert first == second
    assert first != provider.embed(["goodbye"], model="fake-embed")
    assert provider.last_embed_tokens > 0


def test_the_embedding_index_bills_once_per_distinct_block():
    provider = FakeProvider(["x"])
    calls: list[tuple[int, int]] = []
    index = EmbeddingNearIndex(
        provider=provider,
        model="fake-embed",
        on_call=lambda tokens, texts: calls.append((tokens, texts)),
    )
    index.add("g000-c0001", BEST_FIT)
    index.add("g001-c0002", BONUS_20)
    twin, score = index.closest(BONUS_20)  # cached: no third call
    assert twin == "g001-c0002" and score == pytest.approx(1.0)
    assert len(calls) == 2
    assert all(tokens > 0 and texts == 1 for tokens, texts in calls)


def test_the_embedding_index_finds_the_identical_block_before_any_other():
    provider = FakeProvider(["x"])
    index = EmbeddingNearIndex(provider=provider, model="fake-embed")
    index.add("a", BEST_FIT)
    index.add("b", FIRST_FIT)
    twin, score = index.closest(FIRST_FIT)
    assert twin == "b" and score == pytest.approx(1.0)


def test_an_empty_embedding_index_costs_nothing():
    provider = FakeProvider(["x"])
    index = EmbeddingNearIndex(provider=provider, model="fake-embed")
    assert index.closest(BEST_FIT) == (None, 0.0)
    assert provider.embed_calls == []


def test_an_empty_vector_from_a_backend_is_an_error():
    provider = SimpleNamespace(name="broken", embed=lambda texts, *, model: [[]])
    index = EmbeddingNearIndex(provider=provider, model="m")
    with pytest.raises(ProviderError, match="returned no vector"):
        index.add("a", BEST_FIT)


def test_build_near_backend_honours_the_method():
    assert build_near_backend(NearDuplicateConfig(method="off")) is None
    assert isinstance(build_near_backend(NearDuplicateConfig()), LocalNearIndex)


def test_the_embedding_method_needs_a_provider_that_can_embed():
    cfg = NearDuplicateConfig(method="embedding", model="m")
    with pytest.raises(ProviderError, match="no embedding provider"):
        build_near_backend(cfg)
    with pytest.raises(ProviderError, match="does not implement"):
        build_near_backend(cfg, provider=SimpleNamespace(name="nope"))


# -- provider embed() ------------------------------------------------------


def _embedding_client(vectors, prompt_tokens=42):
    calls: list[dict] = []

    class Embeddings:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=v) for v in vectors],
                usage=SimpleNamespace(prompt_tokens=prompt_tokens),
            )

    return SimpleNamespace(embeddings=Embeddings()), calls


@pytest.mark.parametrize("cls", [AzureProvider, OpenRouterProvider])
def test_the_openai_shaped_backends_send_the_batch_and_record_the_tokens(cls):
    client, calls = _embedding_client([[0.1, 0.2], [0.3, 0.4]])
    provider = cls(client=client)
    vectors = provider.embed(["a", "b"], model="text-embedding-3-small")
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert calls == [{"model": "text-embedding-3-small", "input": ["a", "b"]}]
    assert provider.last_embed_tokens == 42
    assert embedding_tokens(provider, ["a", "b"]) == 42


def test_a_short_embedding_batch_is_an_error():
    client, _ = _embedding_client([[0.1]])
    with pytest.raises(ProviderError, match="asked for 2 embedding"):
        AzureProvider(client=client).embed(["a", "b"], model="m")


def test_a_backend_failure_is_reported_as_a_provider_error():
    """And it names the cause, and it says which of the two calls it was."""

    class Boom:
        def create(self, **kwargs):
            raise RuntimeError("Error code: 401 - invalid api key")

    client = SimpleNamespace(embeddings=Boom())
    with pytest.raises(ProviderError, match="authentication failed"):
        OpenRouterProvider(client=client).embed(["a"], model="m")


def test_a_rate_limited_embedding_is_retried_and_then_named():
    """Bounded: three tries, then a message that says what happened."""
    attempts = []
    slept: list[float] = []

    class Limited:
        def create(self, **kwargs):
            attempts.append(kwargs)
            raise RuntimeError("Error code: 429 - too many requests")

    client = SimpleNamespace(embeddings=Limited())
    provider = OpenRouterProvider(client=client, sleep=slept.append)
    with pytest.raises(ProviderError, match="embeddings request"):
        provider.embed(["a"], model="m")
    assert len(attempts) == 3
    assert slept == [2.0, 4.0]


def test_an_unreported_token_count_falls_back_to_an_estimate():
    client, _ = _embedding_client([[0.1]], prompt_tokens=0)
    provider = AzureProvider(client=client)
    provider.embed(["x" * 400], model="m")
    assert provider.last_embed_tokens == 0
    assert embedding_tokens(provider, ["x" * 400]) == 100


def test_the_claude_cli_backend_says_it_has_no_embeddings():
    provider = ClaudeCliProvider(runner=lambda *a, **k: None)
    with pytest.raises(ProviderError, match="no embeddings"):
        provider.embed(["a"], model="m")


# -- config ----------------------------------------------------------------


def test_the_near_gate_defaults_to_local_at_point_nine_seven(minimal_raw, tmp_path):
    config = build_config(minimal_raw, base_dir=tmp_path)
    near = config.search.novelty.near
    assert (near.method, near.threshold, near.model) == ("local", 0.97, "")
    assert near.enabled


def test_an_unknown_near_method_is_rejected(minimal_raw, tmp_path):
    minimal_raw["search"] = {"novelty": {"near": {"method": "vibes"}}}
    with pytest.raises(ConfigError, match="search.novelty.near.method"):
        build_config(minimal_raw, base_dir=tmp_path)


def test_a_threshold_outside_zero_to_one_is_rejected(minimal_raw, tmp_path):
    minimal_raw["search"] = {"novelty": {"near": {"threshold": 1.4}}}
    with pytest.raises(ConfigError, match="search.novelty.near.threshold"):
        build_config(minimal_raw, base_dir=tmp_path)


def test_the_behavioural_switch_defaults_to_on(minimal_raw, tmp_path):
    config = build_config(minimal_raw, base_dir=tmp_path)
    assert config.search.novelty.behavioural == "on"


@pytest.mark.parametrize("mode", ["on", "off", "auto"])
def test_the_behavioural_switch_accepts_its_three_modes(minimal_raw, tmp_path, mode):
    minimal_raw["search"] = {"novelty": {"behavioural": mode}}
    config = build_config(minimal_raw, base_dir=tmp_path)
    assert config.search.novelty.behavioural == mode


def test_an_unknown_behavioural_mode_is_rejected(minimal_raw, tmp_path):
    minimal_raw["search"] = {"novelty": {"behavioural": "sometimes"}}
    with pytest.raises(ConfigError, match="search.novelty.behavioural"):
        build_config(minimal_raw, base_dir=tmp_path)


def test_an_unknown_key_under_novelty_is_rejected(minimal_raw, tmp_path):
    minimal_raw["search"] = {"novelty": {"far": {}}}
    with pytest.raises(ConfigError, match="search.novelty: unknown key"):
        build_config(minimal_raw, base_dir=tmp_path)


def test_the_embedding_method_requires_a_models_embed_slot(minimal_raw, tmp_path):
    minimal_raw["search"] = {"novelty": {"near": {"method": "embedding"}}}
    with pytest.raises(ConfigError, match="requires a models.embed slot"):
        build_config(minimal_raw, base_dir=tmp_path)


def test_claude_cli_cannot_be_the_embedding_backend(minimal_raw, tmp_path):
    minimal_raw["search"] = {"novelty": {"near": {"method": "embedding"}}}
    minimal_raw["models"]["embed"] = {"provider": "claude-cli", "model": "haiku"}
    with pytest.raises(ConfigError, match="has no embeddings API"):
        build_config(minimal_raw, base_dir=tmp_path)


def test_a_valid_embedding_route_parses(minimal_raw, tmp_path):
    minimal_raw["search"] = {
        "novelty": {"near": {"method": "embedding", "threshold": 0.95}}
    }
    minimal_raw["models"]["embed"] = {
        "provider": "openrouter",
        "model": "text-embedding-3-small",
        "price_in_per_mtok": 0.02,
    }
    config = build_config(minimal_raw, base_dir=tmp_path)
    assert config.models.by_role("embed").model == "text-embedding-3-small"
    assert config.search.novelty.near.threshold == 0.95


def test_asking_for_an_embed_slot_that_is_absent_is_an_error(minimal_raw, tmp_path):
    config = build_config(minimal_raw, base_dir=tmp_path)
    with pytest.raises(ConfigError, match="models.embed"):
        config.models.by_role("embed")


# -- the ledger ------------------------------------------------------------


def test_an_embedding_call_is_priced_from_the_table(tmp_path):
    ledger = Ledger(tmp_path / "run")
    model = ModelConfig(
        role="embed",
        provider="openrouter",
        model="text-embedding-3-small",
        price_in_per_mtok=0.02,
    )
    usage = ledger.record_embedding(
        model, tokens=500_000, texts=1, candidate_id="g001-c0002", generation=1
    )
    assert usage.usd == pytest.approx(0.01)
    assert usage.output_tokens == 0
    assert usage.operator == "embed"
    row = ledger.usage()[-1]
    assert row["candidate_id"] == "g001-c0002"
    assert row["priced_from"] == "table"
    assert ledger.totals()["usd"] == pytest.approx(0.01)
    assert ledger.embedding_calls == 1


# -- inside the loop -------------------------------------------------------


BONUS_16 = '''\
def priority(item, bins):
    """A third set of constants. Still nothing but constants."""
    scores = []
    for b in bins:
        residual = b - item
        scores.append(-residual + 16.0 * math.exp(-((residual - 28.0) ** 2) / 100.0))
    return scores
'''

RETRY_KEY = "change the decision rule, not the constants"


def _driver(
    tmp_path,
    responses,
    *,
    near_method="local",
    threshold=0.99,
    generations=2,
    providers=None,
):
    """Two generations by default: the gate needs something in the index first.

    A near-duplicate can only be recognised against a candidate that is already
    in the archive, and children are indexed when they are *recorded*, not when
    they are bred -- so the twin has to come from an earlier generation.
    """
    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        # Static + proxy only. The gate runs before any stage, so the full
        # stage and its hold-out would add seconds and tell us nothing.
        evaluate=replace(config.evaluate, stages=config.evaluate.stages[:2]),
        search=replace(
            config.search,
            generations=generations,
            children_per_generation=1,
            adaptive_children=None,
            big_step_every=99,
            scratchpad_every=0,
            inspirations=0,
            operators={"rewrite": 1.0},
            novelty=replace(
                config.search.novelty,
                near=NearDuplicateConfig(method=near_method, threshold=threshold),
            ),
        ),
        stop=replace(config.stop, patience=99),
    )
    provider = FakeProvider(responses, cycle=True)
    return (
        Driver(
            config,
            run_dir=tmp_path / "near",
            providers=providers or {"small": provider, "strong": provider},
        ),
        provider,
    )


def _still_near_script():
    """Generation 1 proposes a rule; generation 2 proposes the same rule with
    different constants, and so does the re-prompt."""
    return [
        f"```python\n{BONUS_20}```",
        f"```python\n{BONUS_18}```",
        {"when": RETRY_KEY, "response": f"```python\n{BONUS_16}```"},
    ]


@pytest.fixture(scope="module")
def stubborn_run(tmp_path_factory):
    """One run in which the model will not stop moving constants around."""
    driver, provider = _driver(
        tmp_path_factory.mktemp("stubborn"), _still_near_script()
    )
    summary = driver.run()
    return driver, provider, summary


def test_a_near_duplicate_is_re_prompted_once_then_evaluated_anyway(stubborn_run):
    driver, provider, summary = stubborn_run
    child = driver.archive[-1]

    assert len(provider.calls) == 3, "two children, exactly one re-prompt"
    assert RETRY_KEY in provider.calls[2]["messages"][1]["content"]
    assert child.novelty == "near"
    assert child.rejected is False
    assert child.near_twin_id == driver.archive[1].id
    assert child.similarity == pytest.approx(1.0)
    assert child.stages_reached, "a near-duplicate is still evaluated"
    assert child.score is not None
    assert summary.near == 1
    assert summary.rejected == 0


def test_a_flagged_near_duplicate_still_enters_the_archive(stubborn_run):
    driver, _provider, _summary = stubborn_run
    child = driver.archive[-1]
    assert child.id in driver.grid.members
    assert driver.grid.near_duplicates == 1


def test_the_near_verdict_reaches_runs_jsonl(stubborn_run):
    driver, _provider, _summary = stubborn_run
    row = driver.ledger.runs()[-1]
    assert row["novelty"] == "near"
    assert row["rejected"] is False
    assert row["near_twin_id"]
    assert row["similarity"] >= 0.99


def test_a_genuinely_new_retry_clears_the_near_flag(tmp_path):
    driver, provider = _driver(
        tmp_path,
        [
            f"```python\n{BONUS_20}```",
            f"```python\n{BONUS_18}```",
            {"when": RETRY_KEY, "response": f"```python\n{FIRST_FIT}```"},
        ],
    )
    summary = driver.run()
    child = driver.archive[-1]
    assert child.novelty is None
    assert "First fit" in child.block
    assert summary.near == 0
    assert len(provider.calls) == 3


def test_turning_the_gate_off_restores_the_phase_b_behaviour(tmp_path):
    driver, provider = _driver(tmp_path, _still_near_script(), near_method="off")
    summary = driver.run()
    assert summary.near == 0
    assert driver.archive[-1].novelty is None
    assert len(provider.calls) == 2, "two children, nothing to re-prompt about"


def test_the_embedding_gate_bills_every_vector_it_buys(tmp_path):
    config = load_config(EXAMPLE_CONFIG)
    embed_slot = ModelConfig(
        role="embed",
        provider="fake",
        model="fake-embed",
        price_in_per_mtok=1000.0,  # absurd on purpose: the spend must be visible
    )
    config = replace(
        config,
        models=replace(config.models, embed=embed_slot),
        evaluate=replace(config.evaluate, stages=config.evaluate.stages[:2]),
        search=replace(
            config.search,
            generations=1,
            children_per_generation=1,
            # The example turns adaptive breadth on; these tests are
            # about one child at a time.
            adaptive_children=None,
            big_step_every=99,
            scratchpad_every=0,
            operators={"rewrite": 1.0},
            novelty=replace(
                config.search.novelty,
                near=NearDuplicateConfig(method="embedding", threshold=0.999),
            ),
        ),
    )
    provider = FakeProvider([f"```python\n{FIRST_FIT}```"], cycle=True)
    driver = Driver(
        config,
        run_dir=tmp_path / "embed",
        providers={"small": provider, "strong": provider, "embed": provider},
    )
    summary = driver.run()

    embed_rows = [r for r in driver.ledger.usage() if r["operator"] == "embed"]
    assert embed_rows, "every embedding call is a usage row"
    assert all(r["output_tokens"] == 0 for r in embed_rows)
    assert all(r["model"] == "fake-embed" for r in embed_rows)
    assert summary.embedding_calls == len(embed_rows)
    assert "embedding call(s)" in summary.near_breakdown
    assert driver.budget.state.usd > 0.0


def test_a_parameter_sweep_is_never_called_a_near_duplicate(tmp_path, example_dir):
    """`param_lhs` changes constants and only constants -- by design."""
    skeleton = tmp_path / "skeleton.py"
    skeleton.write_text(
        "# EVOLVE-BLOCK-START\n"
        '# PARAMS: {"BONUS": [0.0, 40.0]}\n'
        "BONUS = 20.0\n\n\n"
        "def priority(item, bins):\n"
        "    return [-(b - item) + BONUS for b in bins]\n"
        "# EVOLVE-BLOCK-END\n",
        encoding="utf-8",
    )
    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        problem=replace(config.problem, skeleton=skeleton),
        evaluate=replace(config.evaluate, stages=config.evaluate.stages[:1]),
        search=replace(
            config.search,
            generations=1,
            children_per_generation=2,
            big_step_every=99,
            scratchpad_every=0,
            inspirations=0,
            operators={"param_lhs": 1.0},
            novelty=replace(
                config.search.novelty,
                near=NearDuplicateConfig(method="local", threshold=0.5),
            ),
        ),
    )
    provider = FakeProvider(["unused"], cycle=True)
    driver = Driver(
        config,
        run_dir=tmp_path / "sweep",
        providers={"small": provider, "strong": provider},
    )
    summary = driver.run()
    assert summary.near == 0
    assert provider.calls == [], "param_lhs costs no LLM call"
    assert all(c.novelty != "near" for c in driver.archive)
