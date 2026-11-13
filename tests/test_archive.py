"""The MAP-Elites grid: placement, elites, sampling pressure and the rebuild."""

from __future__ import annotations

import random

import pytest

from evolvekit.candidate import Candidate
from evolvekit.config import ArchiveConfig, ConfigError, DescriptorConfig, SearchConfig
from evolvekit.search.archive import Archive, descriptor_value, sigmoid


def make(
    cid: str,
    *,
    score: float,
    complexity: float = 10.0,
    behaviour: float = 0.0,
    parent: str | None = None,
    rejected: bool = False,
    ranking: float | None = None,
    replicates: int = 0,
    params: dict | None = None,
) -> Candidate:
    return Candidate(
        id=cid,
        generation=1,
        block="def f():\n    return 1\n",
        source="",
        operator="rewrite",
        parent_id=parent,
        score=score,
        ranking_score=score if ranking is None else ranking,
        rejected=rejected,
        kpis={"complexity": complexity, "behaviour": behaviour},
        replicates=replicates,
        params=params,
    )


def grid(bins: int = 4) -> Archive:
    return Archive.from_config(
        ArchiveConfig(
            descriptors=(
                DescriptorConfig("complexity", bins, 0.0, 40.0),
                DescriptorConfig("behaviour", bins, 0.0, 1.0),
            )
        )
    )


# -- placement -------------------------------------------------------------


def test_one_elite_per_cell_and_the_better_candidate_wins():
    archive = grid()
    first = archive.add(make("a", score=-5.0, complexity=10, behaviour=0.1))
    second = archive.add(make("b", score=-4.0, complexity=11, behaviour=0.12))
    assert first.coord == second.coord
    assert second.became_elite and second.displaced == "a"
    assert len(archive.cells) == 1
    assert archive.cells[second.coord].occupants == 2
    assert [c.id for c in archive.elites()] == ["b"]


def test_a_worse_candidate_in_the_same_cell_is_kept_but_not_elite():
    archive = grid()
    archive.add(make("a", score=-4.0, complexity=10, behaviour=0.1))
    placement = archive.add(make("b", score=-9.0, complexity=10, behaviour=0.1))
    assert placement.inserted and not placement.became_elite
    assert archive.cells[placement.coord].elite_id == "a"
    assert "b" in archive.members  # still rankable in the global top-k


def test_different_behaviour_opens_a_second_cell():
    archive = grid()
    archive.add(make("a", score=-4.0, complexity=10, behaviour=0.1))
    archive.add(make("b", score=-9.0, complexity=10, behaviour=0.9))
    assert len(archive.cells) == 2
    assert {c.id for c in archive.elites()} == {"a", "b"}


def test_an_equal_challenger_takes_the_cell_in_about_24_percent():
    """Elite and challenger both N(0, 1) as fitness, noise_sd 1: P(N(0, 2) > 1) =
    P(Z > 0.707) = 24.0 % (50 % with today's rule). 20 000 trials: SE 0.30 %; measured 24.0 %
    (band of Decision 12)."""
    rng, taken = random.Random(52), 0
    for i in range(20_000):
        archive = grid()
        archive.noise_sd = 1.0
        archive.add(make(f"e{i}", score=rng.gauss(0, 1), complexity=10))
        taken += archive.add(make(f"c{i}", score=rng.gauss(0, 1), complexity=10)).displaced is not None
    print(f"equal challenger took the cell: {100 * taken / 20_000:.2f} %")
    assert 0.228 <= taken / 20_000 <= 0.252


def test_a_challenger_within_the_noise_does_not_take_the_cell():
    archive = grid()
    archive.noise_sd = 1.0
    archive.add(make("e", score=1.0))
    assert archive.add(make("c", score=1.9)).displaced is None
    assert archive.add(make("d", score=2.1)).displaced == "e"


def test_more_replicates_take_the_cell_on_any_gain():
    archive = grid()
    archive.noise_sd = 1.0
    archive.add(make("e", score=1.0, replicates=3))
    assert archive.add(make("c", score=1.1, replicates=4)).displaced == "e"


def test_unknown_replicates_read_as_equal():
    archive = grid()
    archive.noise_sd = 1.0
    archive.add(make("e", score=1.0, replicates=0))
    assert archive.add(make("c", score=1.1, replicates=4)).displaced is None
    archive.add(make("f", score=2.5, replicates=3))
    assert archive.add(make("g", score=2.6, replicates=0)).displaced is None


def test_with_no_noise_the_elite_rule_is_strictly_greater():
    archive = grid()
    archive.add(make("e", score=1.0))
    assert archive.add(make("tie", score=1.0)).displaced is None
    assert archive.add(make("c", score=1.0 + 1e-12)).displaced == "e"


def test_a_rebin_applies_the_noise_rule_in_id_order():
    archive = Archive.from_config(ArchiveConfig(descriptors=(DescriptorConfig("complexity", 2),)))
    archive.noise_sd = 1.0
    archive.add(make("a", score=1.0, complexity=10.0))
    archive.add(make("b", score=1.5, complexity=11.0))    # re-bin at 2: a and b apart
    archive.add(make("c", score=0.0, complexity=100.0))
    archive.add(make("d", score=0.0, complexity=200.0))   # re-bin at 4: a and b share the bottom bin
    assert archive.members["a"].cell == archive.members["b"].cell
    assert archive.cells[tuple(archive.members["a"].cell)].elite_id == "a", "1.5 is within 1.0 + 1"


def test_rejected_candidates_are_never_placed():
    archive = grid()
    placement = archive.add(make("bad", score=-1000.0, rejected=True))
    assert not placement.inserted and placement.coord is None
    assert not archive.members and not archive.cells


def test_the_grid_ranks_by_the_ranking_score_not_the_raw_score():
    archive = grid()
    archive.add(make("public-winner", score=-4.0, ranking=-6.0, behaviour=0.1))
    archive.add(make("honest", score=-5.0, ranking=-5.0, behaviour=0.9))
    assert archive.best is not None and archive.best.id == "honest"


def test_values_out_of_range_clamp_into_the_end_bins():
    archive = grid(bins=4)
    low = archive.add(make("low", score=-1.0, complexity=-99.0, behaviour=-1.0))
    high = archive.add(make("high", score=-1.0, complexity=999.0, behaviour=99.0))
    assert low.coord == (0, 0)
    assert high.coord == (3, 3)


def test_a_missing_kpi_falls_back_to_zero_and_complexity_to_the_block():
    candidate = Candidate(
        id="x", generation=0, block="def f():\n    return 1\n", source="", operator="s"
    )
    assert descriptor_value(candidate, "nope") == 0.0
    assert descriptor_value(candidate, "complexity") == float(candidate.complexity) > 0


# -- auto ranges -----------------------------------------------------------


def test_an_auto_axis_rebins_what_it_already_holds():
    archive = Archive.from_config(
        ArchiveConfig(descriptors=(DescriptorConfig("complexity", 2),))
    )
    archive.add(make("a", score=-1.0, complexity=10.0))
    archive.add(make("b", score=-2.0, complexity=20.0))
    assert archive.axes[0].lo == 10.0 and archive.axes[0].hi == 20.0
    assert archive.members["a"].cell != archive.members["b"].cell
    archive.add(make("c", score=-3.0, complexity=100.0))
    archive.add(make("d", score=-4.0, complexity=110.0))
    assert archive.axes[0].hi == 110.0
    # a and b now share the bottom bin; the grid was re-placed, not appended to
    assert archive.members["a"].cell == archive.members["b"].cell
    assert archive.members["c"].cell == archive.members["d"].cell != archive.members["a"].cell
    assert len(archive.cells) == 2
    assert sum(c.occupants for c in archive.cells.values()) == 4


def test_one_outlier_no_longer_squashes_an_auto_axis():
    archive = Archive.from_config(ArchiveConfig(descriptors=(DescriptorConfig("complexity", 4),)))
    for i, value in enumerate([1, 2, 3, 4, 5, 6, 7, 1000]):
        archive.add(make(f"c{i}", score=float(i), complexity=value))
    assert archive.axes[0].edges == [2.75, 4.5, 6.25]
    assert len(archive.cells) == 4, "equal counts; equal width put seven in bin 0"
    assert archive.axes[0].label(0) == "1..2.75" and archive.axes[0].label(3) == "6.25..1000"
    assert archive.to_dict()["descriptors"][0]["edges"] == [2.75, 4.5, 6.25]


def test_an_auto_axis_rebins_when_its_members_double():
    archive = Archive.from_config(ArchiveConfig(descriptors=(DescriptorConfig("complexity", 4),)))
    seen = []
    for i in range(9):
        archive.add(make(f"c{i}", score=float(i), complexity=float(i + 1)))
        seen.append(list(archive.axes[0].edges))
    assert seen[0] == [], "one member: everything is bin 0"
    assert seen[1] == [1.25, 1.5, 1.75]            # 2 members
    assert seen[2] == seen[1], "not at 3"
    assert seen[3] == [1.75, 2.5, 3.25]            # 4 members
    assert seen[4] == seen[5] == seen[6] == seen[3], "not at 5, 6 or 7"
    assert seen[7] == [2.75, 4.5, 6.25]            # 8 members
    assert seen[8] == seen[7]


def test_an_auto_axis_with_one_distinct_value_puts_everyone_in_bin_0():
    archive = Archive.from_config(ArchiveConfig(descriptors=(DescriptorConfig("complexity", 4),)))
    for i in range(4):
        archive.add(make(f"c{i}", score=float(i), complexity=5.0))
    assert archive.axes[0].edges == []
    assert set(archive.cells) == {(0,)}


def test_a_rebin_keeps_the_elite_rule_and_an_update():
    archive = Archive.from_config(ArchiveConfig(descriptors=(DescriptorConfig("complexity", 2),)))
    archive.noise_sd = 1.0
    for cid, score, size in (("a", 1.0, 1.0), ("b", 1.5, 2.0), ("c", 0.0, 3.0), ("d", 5.0, 4.0)):
        archive.add(make(cid, score=score, complexity=size))
    assert archive.cells[tuple(archive.members["a"].cell)].elite_id == "a", "1.5 is within 1.0 + 1"
    archive.update(make("b", score=3.0, complexity=2.0))
    assert archive.cells[tuple(archive.members["a"].cell)].elite_id == "b"


# -- parameter axes --------------------------------------------------------

FLOAT01 = {"type": "float", "low": 0.0, "high": 1.0, "default": 0.5}


def _tuning_config(tmp_path):
    from evolvekit.config import build_config

    (tmp_path / "solver.py").write_text("print('{\"cost\": 1}')\n", encoding="utf-8")
    raw = {
        "problem": {"parameters": {"a": FLOAT01, "b": FLOAT01, "c": FLOAT01}},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static"},
                {"id": "full", "kind": "command", "kpis_from": "stdout", "command": "{python} solver.py {params}"},
            ],
            "score": {"objective": "cost", "direction": "minimize"},
        },
        "search": {"operators": {"param_local": 1.0}},
    }
    return build_config(raw, base_dir=tmp_path)


def test_a_tuning_run_spreads_over_its_first_two_parameters(tmp_path):
    config = _tuning_config(tmp_path)
    assert [d.param for d in config.search.archive.descriptors] == ["a", "b"]
    archive = Archive.from_config(config.search.archive, space=config.problem.parameters)
    for i, (a, b) in enumerate([(0.1, 0.1), (0.9, 0.1), (0.1, 0.9), (0.9, 0.9)] * 2):
        archive.add(make(f"c{i}", score=float(i), params={"a": a, "b": b, "c": 0.5}))
    assert len(archive.cells) == 4
    assert archive.coord_for(make("q", score=0.0, params={"a": 0.3, "b": 0.6, "c": 0.5})) == (1, 2), \
        "fixed quarters of the unit scale: 0.3 -> bin 1, 0.6 -> bin 2"
    assert archive.occupancy() == "4/16 cells occupied (a[4] x b[4])"
    assert archive.to_dict()["descriptors"][0]["param"] == "a"


def test_a_parameter_axis_is_never_rebinned(tmp_path):
    config = _tuning_config(tmp_path)
    archive = Archive.from_config(config.search.archive, space=config.problem.parameters)
    for i in range(8):
        archive.add(make(f"c{i}", score=float(i), params={"a": 0.2 * i / 7, "b": 0.5, "c": 0.5}))
    assert {tuple(c.cell)[0] for c in archive.members.values()} == {0}, \
        "quantile bins would have spread them over a"
    assert archive.axes[0].edges == []


def test_a_parameter_axis_reads_the_parameters_own_scale():
    from evolvekit.space import ParameterSpace

    space = ParameterSpace.parse({
        "rate": {"type": "float", "low": 0.001, "high": 1000.0, "log": True, "default": 1.0},
        "mode": {"type": "choice", "choices": ["x", "y", "z"], "default": "x"},
    })
    rate = make("r", score=0.0, params={"rate": 1.0, "mode": "z"})
    assert descriptor_value(rate, "rate", param="rate", space=space) == pytest.approx(0.5)
    assert descriptor_value(rate, "mode", param="mode", space=space) == 1.0
    assert descriptor_value(make("none", score=0.0), "rate", param="rate", space=space) == 0.0


def test_the_grid_with_parameter_axes_rebuilds_from_run_records(tmp_path):
    config = _tuning_config(tmp_path)
    space = config.problem.parameters
    original = Archive.from_config(config.search.archive, space=space)
    rows = []
    for i, (a, b) in enumerate([(0.1, 0.1), (0.9, 0.1), (0.6, 0.9)]):
        candidate = make(f"c{i}", score=float(i), params={"a": a, "b": b, "c": 0.5})
        original.add(candidate)
        rows.append(candidate.to_record())
    rebuilt = Archive.from_records(rows, config.search.archive, space=space)
    assert set(rebuilt.cells) == set(original.cells) == {(0, 0), (3, 0), (2, 3)}


def test_occupancy_reports_the_grid_shape():
    archive = grid(bins=4)
    archive.add(make("a", score=-1.0, complexity=10.0, behaviour=0.1))
    assert archive.occupancy() == "1/16 cells occupied (complexity[4] x behaviour[4])"


# -- sampling --------------------------------------------------------------


def test_sigmoid_saturates_without_overflowing():
    assert sigmoid(0.0) == 0.5
    assert sigmoid(-1000.0) == 0.0
    assert sigmoid(1000.0) == 1.0


def test_weights_prefer_fitter_parents_and_punish_used_ones():
    archive = grid()
    archive.add(make("fit", score=-1.0, behaviour=0.1))
    archive.add(make("unfit", score=-9.0, behaviour=0.9))
    pool = archive.elites()
    before = dict(zip((c.id for c in pool), archive.weights(pool)))
    assert before["fit"] > before["unfit"]

    for _ in range(9):
        archive.note_child("fit")
    after = dict(zip((c.id for c in pool), archive.weights(pool)))
    assert after["fit"] == pytest.approx(before["fit"] / 10.0)
    assert after["fit"] < after["unfit"], "10 children did not cool the hot parent"


def test_sampling_is_seeded_and_stays_inside_the_elites():
    archive = grid()
    for i in range(4):
        archive.add(make(f"c{i}", score=-float(i), behaviour=i / 4.0))
    elite_ids = {c.id for c in archive.elites()}
    first = [c.id for c in archive.sample_parents(6, random.Random(7))]
    second = [c.id for c in archive.sample_parents(6, random.Random(7))]
    assert first == second
    assert set(first) <= elite_ids


def test_inspirations_come_from_other_cells_only():
    archive = grid()
    for i in range(4):
        archive.add(make(f"c{i}", score=-float(i), behaviour=i / 4.0))
    parent = archive.members["c0"]
    picks = archive.sample_inspirations(parent, 2, random.Random(1))
    assert len(picks) == 2
    assert all(p.id != parent.id for p in picks)
    assert all(tuple(p.cell or ()) != tuple(parent.cell or ()) for p in picks)


def test_a_single_cell_offers_no_inspiration():
    archive = grid()
    archive.add(make("a", score=-1.0, behaviour=0.1))
    archive.add(make("b", score=-2.0, behaviour=0.11))
    assert len(archive.cells) == 1
    assert archive.sample_inspirations(archive.members["a"], 2, random.Random(0)) == []


def test_sampling_an_empty_archive_yields_nothing():
    archive = grid()
    assert archive.sample_parents(3, random.Random(0)) == []
    assert archive.weights([]) == []
    assert archive.best is None


# -- lineage and persistence ----------------------------------------------


def test_lineage_walks_the_parent_chain_and_survives_a_cycle():
    archive = grid()
    archive.add(make("a", score=-3.0, behaviour=0.1))
    archive.add(make("b", score=-2.0, behaviour=0.4, parent="a"))
    archive.add(make("c", score=-1.0, behaviour=0.9, parent="b"))
    assert archive.lineage("c") == ["b", "a"]

    archive.members["a"].parent_id = "c"  # a cycle must not hang the render
    assert len(archive.lineage("c", limit=5)) <= 5


def test_the_snapshot_carries_cells_elites_children_and_lineage():
    archive = grid()
    archive.add(make("a", score=-3.0, behaviour=0.1))
    archive.add(make("b", score=-1.0, behaviour=0.9, parent="a"))
    archive.note_child("a")
    payload = archive.to_dict()
    assert payload["counts"] == {
        "members": 2,
        "cells": 2,
        "no_ops": 0,
        "duplicates": 0,
        "near_duplicates": 0,
    }
    assert {c["elite"] for c in payload["cells"]} == {"a", "b"}
    assert payload["children"] == {"a": 1}
    assert payload["lineage"]["b"] == ["a"]
    assert payload["descriptors"][0]["kpi"] == "complexity"
    assert [e["id"] for e in payload["elites"]] == ["b", "a"]  # best first


def test_the_grid_rebuilds_from_run_records():
    config = ArchiveConfig(
        descriptors=(
            DescriptorConfig("complexity", 4, 0.0, 40.0),
            DescriptorConfig("behaviour", 4, 0.0, 1.0),
        )
    )
    original = Archive.from_config(config)
    rows = []
    for candidate in (
        make("a", score=-3.0, behaviour=0.1),
        make("b", score=-1.0, behaviour=0.9, parent="a"),
        make("c", score=-1000.0, parent="a", rejected=True),
    ):
        original.add(candidate)
        if candidate.parent_id:
            original.note_child(candidate.parent_id)
        rows.append(candidate.to_record())
    rows[2]["novelty"] = "duplicate"

    rebuilt = Archive.from_records(rows, config)
    assert set(rebuilt.members) == {"a", "b"}  # the twin stays out
    assert rebuilt.cells.keys() == original.cells.keys()
    assert rebuilt.best is not None and rebuilt.best.id == "b"
    assert rebuilt.children == {"a": 2}
    assert rebuilt.duplicates == 1


# -- config ---------------------------------------------------------------


def test_descriptors_reject_a_duplicate_axis():
    with pytest.raises(ConfigError, match="duplicate kpi"):
        SearchConfig.parse(
            {
                "archive": {
                    "descriptors": [
                        {"kpi": "complexity", "bins": 2},
                        {"kpi": "complexity", "bins": 3},
                    ]
                }
            }
        )


def test_a_descriptor_range_must_be_ordered_or_auto():
    with pytest.raises(ConfigError, match="lo < hi"):
        SearchConfig.parse(
            {"archive": {"descriptors": [{"kpi": "x", "range": [2.0, 1.0]}]}}
        )
    parsed = SearchConfig.parse(
        {"archive": {"descriptors": [{"kpi": "x", "range": "auto"}]}}
    )
    assert parsed.archive.descriptors[0].auto


def test_the_default_archive_is_one_auto_complexity_axis():
    default = SearchConfig.parse({})
    assert [d.kpi for d in default.archive.descriptors] == ["complexity"]
    assert default.archive.descriptors[0].auto
    assert default.archive.top_k == 20
