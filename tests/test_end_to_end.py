"""The whole loop against the scripted `fake` provider: offline and deterministic.

The fake script is written so that one of its responses is a genuinely better
heuristic than the best-fit seed on the proxy set, the full set *and* the
private hold-out. If this test starts failing on the improvement assertion,
either the instance generator or `fake_responses.yaml` changed.
"""

from __future__ import annotations

import json

import pytest

from evolvekit.config import load_config
from evolvekit.leaderboard import novelty_counts, render_html, render_markdown
from evolvekit.ledger import Ledger
from evolvekit.prompts import build_messages, system_prompt
from evolvekit.search.driver import Driver
from evolvekit.search.lhs import latin_hypercube, sweep_params

# Driver-level: almost every test here shares the module-scoped `demo_run`
# fixture (one real search loop) or builds its own in isolation. Marking only
# the individual tests whose own duration shows up in `--durations` would not
# actually save anything -- whichever test runs first still pays for building
# the fixture -- so the whole file is `slow` and excluded from the fast
# `python tasks.py test` default; `python tasks.py full` and `check` still run
# it.
pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def demo_run(tmp_path_factory, request):
    from tests.conftest import EXAMPLE_CONFIG

    config = load_config(EXAMPLE_CONFIG)
    run_dir = tmp_path_factory.mktemp("demo")
    driver = Driver(config, run_dir=run_dir)
    summary = driver.run()
    return driver, summary


def test_the_loop_improves_on_the_seed(demo_run):
    _driver, summary = demo_run
    assert summary.seed_score is not None
    assert summary.best is not None and summary.best.score is not None
    assert summary.best.score > summary.seed_score
    assert summary.improvement > 0.1
    # Best fit scores -5.871. The script improves twice: a usable-residual
    # bonus reaches about -5.62, and a second, structurally new rule with two
    # bonus terms reaches about -5.37.
    assert summary.seed_score == pytest.approx(-5.871, abs=0.01)
    assert summary.best.score == pytest.approx(-5.370, abs=0.01)


def test_the_winner_is_a_descendant_of_the_seed(demo_run):
    _driver, summary = demo_run
    assert summary.best.parent_id is not None
    assert summary.best.generation >= 1
    assert summary.best.operator in {"diff", "rewrite", "big_step/diff", "big_step/rewrite"}


def test_the_winner_generalises_to_the_private_holdout(demo_run):
    _driver, summary = demo_run
    best = summary.best
    assert best.private_score is not None
    assert best.generalization_gap is not None
    assert best.generalization_gap == pytest.approx(best.public_score - best.private_score)


def test_no_candidate_ever_has_a_none_score(demo_run):
    driver, _summary = demo_run
    rows = driver.ledger.runs()
    assert rows
    assert all(row["score"] is not None for row in rows)
    assert all(isinstance(row["score"], (int, float)) for row in rows)


def test_the_run_exercises_rejection_penalty_and_both_apply_modes(demo_run):
    driver, _summary = demo_run
    rows = driver.ledger.runs()
    operators = {row["operator"] for row in rows}
    assert "human-seed" in operators
    assert "diff" in operators, "the SEARCH/REPLACE path never fired"
    assert "crossover" in operators, "the crossover operator never fired"
    assert any(op.startswith("big_step") for op in operators), "no big step was taken"
    assert any(row["rejected"] for row in rows), "nothing was hard-rejected"
    assert any(
        row["penalty_total"] > 0 for row in rows
    ), "no candidate was penalised rather than voided"


# -- Phase B: the archive, the novelty filter and the scratchpad -----------


def test_the_novelty_filter_catches_a_no_op_and_a_duplicate(demo_run):
    driver, summary = demo_run
    rows = driver.ledger.runs()
    no_ops = [r for r in rows if r["novelty"] == "no_op"]
    duplicates = [r for r in rows if r["novelty"] == "duplicate"]
    assert no_ops and duplicates, "the scripted twins were not caught"
    assert summary.no_ops == len(no_ops)
    assert summary.duplicates == len(duplicates)
    assert summary.wasted_calls == len(no_ops) + len(duplicates)

    for row in no_ops + duplicates:
        assert row["rejected"] is True
        assert row["twin_id"] and row["twin_id"] in row["reject_reason"]
        # Zero evaluation cost: no stage ever ran for them.
        assert row["stages_reached"] == []
        assert row["kpis"] == {}
        assert row["cell"] is None

    for row in no_ops:
        assert row["twin_id"] == row["parent_id"], "a no-op twins with its parent"
    for row in duplicates:
        assert row["twin_id"] != row["id"]


def test_the_signature_filter_catches_a_monotone_re_expression(demo_run):
    """`[-(b - item) * 2 for b in bins]`: a different AST, the same packing.

    Exactly the shape the first real Phase B run paid for five times over. The
    structural filter passes it; the behaviour signature does not.
    """
    driver, summary = demo_run
    rows = driver.ledger.runs()
    twins = [r for r in rows if r["novelty"] == "behavioural"]
    assert twins, "the scripted monotone re-expression was not caught"
    assert summary.behavioural == len(twins)

    for row in twins:
        assert row["rejected"] is True
        assert row["behaviour_twin_id"]
        assert row["behaviour_twin_id"] != row["id"]
        assert row["reject_reason"] == (
            f"behavioural duplicate of {row['behaviour_twin_id']} at stage proxy"
        )
        # Caught at the proxy stage: it never bought the full one.
        assert row["stages_reached"] == ["static", "proxy"]
        # ...but its KPIs and its score are kept for the record.
        assert row["score"] is not None and row["score"] != -1000.0
        assert row["kpis"]
        assert row["behaviour_signatures"]["proxy"]

    # The seed is a valid twin target, and here it is the one that was matched:
    # same proxy fingerprint, different block.
    seed = next(r for r in rows if r["operator"] == "human-seed")
    matched = [r for r in twins if r["behaviour_twin_id"] == seed["id"]]
    assert matched, "the seed was not usable as a twin target"
    for row in matched:
        assert row["behaviour_signatures"]["proxy"] == seed["behaviour_signatures"]["proxy"]
        assert row["block_hash"] != seed["block_hash"]


def test_a_behavioural_twin_never_enters_the_archive_or_breeds(demo_run):
    driver, _summary = demo_run
    twins = {c.id for c in driver.archive if c.novelty == "behavioural"}
    assert twins
    assert twins.isdisjoint(driver.grid.members)
    assert twins.isdisjoint({c.id for c in driver.grid.elites()})
    parents = {r["parent_id"] for r in driver.ledger.runs() if r["parent_id"]}
    assert twins.isdisjoint(parents), "a twin was sampled as a parent"
    assert all(c.cell is None for c in driver.archive if c.id in twins)


def test_the_twins_parent_is_told_why_the_child_bought_nothing(demo_run):
    """The feedback loop: the artefact is problem-agnostic and lands on the
    parent's next prompt, not on some unrelated candidate's."""
    driver, _summary = demo_run
    twins = [c for c in driver.archive if c.novelty == "behavioural"]
    parents = {c.parent_id for c in twins}
    assert parents and None not in parents

    carriers = []
    for path in driver.ledger.traces_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        body = payload["messages"][1]["content"] if len(payload["messages"]) > 1 else ""
        if "produced identical behaviour" in body:
            carriers.append(payload)
    assert carriers, "no later prompt carried the artefact"
    for payload in carriers:
        assert payload["meta"]["parent_id"] in parents
        body = payload["messages"][1]["content"]
        assert "Change the decisions the program makes" in body
        # Generic wording: nothing about bins, items or packing.
        line = next(
            l for l in body.splitlines() if "produced identical behaviour" in l
        )
        assert not any(word in line for word in ("bin", "pack", "item"))


def test_the_run_summary_and_leaderboard_split_the_three_rejection_kinds(demo_run):
    driver, summary = demo_run
    rows = driver.ledger.runs()
    counts = novelty_counts(rows)
    assert (counts["no_op"], counts["duplicate"], counts["behavioural"]) == (
        summary.no_ops,
        summary.duplicates,
        summary.behavioural,
    )
    assert summary.behavioural >= 1
    assert summary.rejection_breakdown == (
        f"{summary.rejected} ({summary.no_ops} no-op, "
        f"{summary.duplicates} duplicate, {summary.behavioural} behavioural)"
    )
    markdown = render_markdown(rows)
    assert (
        f"{counts['rejected']} rejected ({counts['no_op']} no-op, "
        f"{counts['duplicate']} duplicate, {counts['behavioural']} behavioural)"
    ) in markdown


def test_the_example_evaluator_reports_per_instance_bins(demo_run):
    """The list KPI makes the proxy signature per-instance, not per-mean; it
    must not leak into the score or the archive descriptors."""
    driver, _summary = demo_run
    outputs = sorted((driver.cascade.work_dir / "stage_out").glob("*.proxy.json"))
    assert outputs
    payload = json.loads(outputs[0].read_text(encoding="utf-8"))
    per_instance = payload["kpis"]["bins_per_instance"]
    assert isinstance(per_instance, list) and len(per_instance) == 5
    for row in driver.ledger.runs():
        assert "bins_per_instance" not in (row["kpis"] or {})


def test_twins_never_enter_the_archive_or_the_parent_pool(demo_run):
    """Every novelty verdict *except* `near`, which is a flag and not a twin."""
    driver, _summary = demo_run
    twins = {
        c.id for c in driver.archive if c.novelty and c.novelty != "near"
    }
    assert twins
    assert twins.isdisjoint(driver.grid.members)
    assert twins.isdisjoint({c.id for c in driver.grid.elites()})
    parents = {r["parent_id"] for r in driver.ledger.runs() if r["parent_id"]}
    assert twins.isdisjoint(parents)


def test_the_archive_spreads_across_cells(demo_run):
    driver, summary = demo_run
    assert summary.cells >= 2, "the whole run collapsed into one cell"
    assert len(driver.grid.axes) == 2
    assert {a.kpi for a in driver.grid.axes} == {"complexity", "mean_openness"}
    # Every scored candidate carries the coordinate it was placed at.
    for row in driver.ledger.runs():
        if not row["rejected"]:
            assert row["cell"] is not None and len(row["cell"]) == 2
    # One elite per occupied cell, and each is the best in its cell.
    for cell in driver.grid.cells.values():
        occupants = [
            c
            for c in driver.grid.members.values()
            if tuple(c.cell or ()) == cell.coord
        ]
        assert cell.fitness == max(c.fitness for c in occupants)


def test_parents_and_inspirations_come_from_the_archive(demo_run):
    driver, _summary = demo_run
    known = {c.id for c in driver.archive}
    for row in driver.ledger.runs():
        if row["parent_id"]:
            assert row["parent_id"] in known
        for inspiration in row["inspiration_ids"]:
            assert inspiration in known
            assert inspiration != row["parent_id"], "an inspiration was the parent"
    assert any(row["inspiration_ids"] for row in driver.ledger.runs())
    # Children counts are what makes a much-mutated parent less attractive.
    assert driver.grid.children
    assert sum(driver.grid.children.values()) >= len(driver.archive) - 1


def test_the_scratchpad_is_cached_and_reaches_later_prompts(demo_run):
    driver, _summary = demo_run
    assert driver.scratchpad.path.is_file()
    cached = driver.scratchpad.path.read_text(encoding="utf-8")
    assert len(cached.splitlines()) <= 41  # 40 lines plus the refresh marker
    assert "Best fit (the seed) is the reference" in cached

    bodies = []
    for path in driver.ledger.traces_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if len(payload["messages"]) > 1:
            bodies.append(payload["messages"][1]["content"])
    assert any("Notes from earlier generations" in b for b in bodies)

    meta = list(driver.ledger.traces_dir.glob("meta/*.json"))
    assert meta, "the scratchpad call was not traced"
    assert all(p.stem.startswith("scratchpad-g") for p in meta)


def test_the_ranking_score_is_what_everything_ranks_by(demo_run):
    driver, summary = demo_run
    assert summary.best is not None
    assert summary.best.ranking_score is not None
    # No hold-out regression in this demo, so ranking == public everywhere; the
    # penalty itself is exercised in tests/test_ranking.py.
    for row in driver.ledger.runs():
        if row["private_score"] is None or row["public_score"] is None:
            continue
        gap = row["public_score"] - row["private_score"]
        expected = row["public_score"] - 1.0 * max(0.0, gap)
        assert row["ranking_score"] == pytest.approx(expected)
    assert driver.grid.best is not None
    assert driver.grid.best.id == summary.best.id


def test_lineage_fields_are_present_on_every_record(demo_run):
    """Phase B reads exactly these; they must never be back-filled."""
    driver, _summary = demo_run
    for row in driver.ledger.runs():
        for field in (
            "parent_id",
            "inspiration_ids",
            "operator",
            "model",
            "complexity",
            "block_hash",
            "generation",
        ):
            assert field in row, field


def test_every_llm_call_is_traced_and_billed(demo_run):
    """Including the framework's own calls, which live under traces/meta/."""
    driver, _summary = demo_run
    usage = driver.ledger.usage()
    assert usage
    traced = {p.stem for p in driver.ledger.traces_dir.glob("**/*.json")}
    for row in usage:
        # A novelty re-prompt bills a second call against the same candidate.
        assert row["candidate_id"] in traced
        assert row["usd"] >= 0.0
        assert row["input_tokens"] > 0 and row["output_tokens"] > 0
    totals = driver.ledger.totals()
    assert totals["usd"] == pytest.approx(sum(r["usd"] for r in usage))


def test_traces_hold_the_exact_messages_sent(demo_run):
    driver, _summary = demo_run
    trace = json.loads(
        next(iter(sorted(driver.ledger.traces_dir.glob("*.json")))).read_text(
            encoding="utf-8"
        )
    )
    roles = [m["role"] for m in trace["messages"]]
    assert roles == ["system", "user"]
    assert "Online bin packing" in trace["messages"][0]["content"]


def test_spend_stays_inside_the_budget(demo_run):
    driver, summary = demo_run
    assert driver.budget.state.usd < driver.config.budget.max_usd
    assert driver.budget.state.tokens < driver.config.budget.max_tokens
    assert summary.totals["usd"] == pytest.approx(driver.budget.state.usd)


def test_the_archive_snapshot_is_written(demo_run):
    """The snapshot is the grid now, not a flat top-20 list."""
    driver, _summary = demo_run
    payload = json.loads(driver.ledger.archive_path.read_text(encoding="utf-8"))
    assert payload["top_k"]
    assert payload["top_k"][0]["ranking_score"] >= payload["top_k"][-1]["ranking_score"]
    assert payload["cells"] and payload["elites"]
    assert payload["descriptors"][0]["kpi"]
    assert payload["counts"]["cells"] == len(payload["cells"])
    # One elite per occupied cell, and every elite is a member of some cell.
    assert {c["elite"] for c in payload["cells"]} == {e["id"] for e in payload["elites"]}


def test_the_leaderboard_renders(demo_run):
    driver, summary = demo_run
    rows = driver.ledger.runs()
    markdown = render_markdown(rows)
    assert summary.best.id in markdown
    # Phase B footer: rejections are broken out by why they were rejected.
    assert "no-op" in markdown and "duplicate" in markdown
    assert "cell" in markdown and "lineage" in markdown
    # The winner's parent chain is on its row.
    assert summary.best.parent_id in markdown

    archive = json.loads(driver.ledger.archive_path.read_text(encoding="utf-8"))
    page = render_html(rows, archive=archive)
    assert page.startswith("<!doctype html>")
    assert "evolvekit leaderboard" in page
    assert "Archive grid" in page
    assert archive["cells"][0]["elite"] in page
    assert "http://" not in page and "https://" not in page  # self-contained


def test_a_second_run_on_the_same_directory_resumes_the_archive(tmp_path):
    """archive.json is a snapshot; runs.jsonl is what the grid is rebuilt from."""
    from dataclasses import replace

    from tests.conftest import EXAMPLE_CONFIG

    config = load_config(EXAMPLE_CONFIG)
    config = replace(
        config,
        search=replace(config.search, generations=1, scratchpad_every=0),
    )
    run_dir = tmp_path / "resumed"
    first = Driver(config, run_dir=run_dir)
    first.run()
    first_ids = {c.id for c in first.archive}
    assert first.grid.members

    second = Driver(config, run_dir=run_dir)
    summary = second.run()

    # No second seed, and the grid came back with everything that survived.
    seeds = [r for r in second.ledger.runs() if r["operator"] == "human-seed"]
    assert len(seeds) == 1
    assert first_ids <= {c.id for c in second.archive}
    assert set(first.grid.members) <= set(second.grid.members)
    assert second.grid.cells.keys() >= first.grid.cells.keys()

    # Generation numbering and candidate ids continue rather than colliding.
    new_ids = {c.id for c in second.archive} - first_ids
    assert new_ids and not (new_ids & first_ids)
    assert max(int(r["generation"]) for r in second.ledger.runs()) == 2
    assert summary.seed_score == pytest.approx(-5.871, abs=0.01)

    # The novelty index came back too: what was scored before is a known twin.
    known = next(iter(first.grid.members.values()))
    assert second.novelty.lookup(known.block) is not None

    # ...and so did the behaviour index, so a program already paid for once
    # cannot be paid for again after a restart.
    assert first.behaviour.by_key
    assert set(first.behaviour.by_key) <= set(second.behaviour.by_key)
    for key, cid in first.behaviour.by_key.items():
        assert second.behaviour.by_key[key] == cid


def test_a_budget_of_almost_nothing_stops_the_run_immediately(tmp_path):
    from dataclasses import replace

    from tests.conftest import EXAMPLE_CONFIG

    config = load_config(EXAMPLE_CONFIG)
    config = replace(config, budget=replace(config.budget, max_tokens=1))
    driver = Driver(config, run_dir=tmp_path / "broke")
    summary = driver.run()
    # The cap is checked before each call, so exactly one child gets through
    # before the guard trips; the run then stops instead of finishing its 3
    # configured generations.
    assert summary.generations <= 1
    assert summary.candidates <= 2
    assert int(summary.totals["calls"]) == 1
    assert "max_tokens" in summary.stop_reason


def test_patience_stops_a_flat_run(tmp_path):
    from dataclasses import replace

    from tests.conftest import EXAMPLE_CONFIG

    config = load_config(EXAMPLE_CONFIG)
    # One operator that always returns the same block: nothing can improve.
    stuck = "```python\ndef priority(item, bins):\n    return [float(-(b - item)) for b in bins]\n```"
    config = replace(
        config,
        stop=replace(config.stop, patience=1, min_big_steps=0),
        search=replace(config.search, generations=6),
    )
    from evolvekit.providers.fake import FakeProvider

    driver = Driver(
        config,
        run_dir=tmp_path / "flat",
        providers={"small": FakeProvider([stuck]), "strong": FakeProvider([stuck])},
    )
    summary = driver.run()
    assert summary.generations < 6
    assert "patience" in summary.stop_reason


# -- prompt shape ----------------------------------------------------------


def test_the_system_prompt_is_stable_across_a_run(demo_run):
    driver, _summary = demo_run
    prompts = set()
    for path in driver.ledger.traces_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["messages"]:
            prompts.add(payload["messages"][0]["content"])
    assert len(prompts) == 1, "the cacheable prefix changed mid-run"


def test_the_prompt_body_stays_bounded(demo_run):
    driver, _summary = demo_run
    for path in driver.ledger.traces_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for message in payload["messages"]:
            assert len(message["content"]) < 12000


def test_the_prompt_never_dumps_other_candidates_verbatim(demo_run):
    """The post-mortem's single most expensive habit.

    Phase B raises the ceiling from one block to two, and only for crossover:
    that operator cannot combine two candidates without seeing both. Every
    other operator still gets its inspirations as one-line delta summaries.
    """
    driver, _summary = demo_run
    blocks = {c.block.strip() for c in driver.archive}
    for path in driver.ledger.traces_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        body = payload["messages"][1]["content"] if len(payload["messages"]) > 1 else ""
        present = [b for b in blocks if b and b in body]
        allowed = 2 if payload["meta"].get("operator") == "crossover" else 1
        assert len(present) <= allowed, (
            f"{path.stem}: {len(present)} candidate block(s) inlined for "
            f"operator {payload['meta'].get('operator')!r}"
        )


def test_the_operator_instruction_reaches_the_model(demo_run):
    driver, _summary = demo_run
    bodies = []
    for path in driver.ledger.traces_dir.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if len(payload["messages"]) > 1:
            bodies.append(payload["messages"][1]["content"])
    assert any("SEARCH/REPLACE" in b for b in bodies)
    assert any("scheduled big step" in b for b in bodies)


# -- the LHS sampler, shipped for Phase B ---------------------------------


def test_latin_hypercube_is_seeded_and_stratified():
    a = latin_hypercube({"x": [0.0, 1.0]}, 5, seed=3)
    b = latin_hypercube({"x": [0.0, 1.0]}, 5, seed=3)
    assert a == b
    strata = sorted(int(p["x"] * 5) for p in a)
    assert strata == [0, 1, 2, 3, 4]  # exactly one sample per stratum


def test_sweep_params_keeps_the_base_point_first_and_preserves_types():
    base = {"k": 3, "alpha": 0.5, "fixed": "keep"}
    variants = sweep_params(base, {"k": [1, 9], "alpha": [0.0, 1.0]}, 4, seed=1)
    assert len(variants) == 4
    assert variants[0] == base
    assert all(isinstance(v["k"], int) for v in variants)
    assert all(v["fixed"] == "keep" for v in variants)


def test_sweep_params_with_no_overlapping_keys_returns_independent_copies():
    variants = sweep_params({"a": 1}, {"b": [0, 1]}, 3, seed=1)
    variants[0]["a"] = 99
    assert variants[1]["a"] == 1


def test_lhs_rejects_a_degenerate_range():
    with pytest.raises(ValueError, match="min < max"):
        latin_hypercube({"x": [1.0, 1.0]}, 3, seed=0)
