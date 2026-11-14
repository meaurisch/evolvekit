"""The MAP-Elites grid: placement, elites, sampling pressure and the rebuild."""

from __future__ import annotations

import random

import pytest

from evolvekit.candidate import Candidate
from evolvekit.config import ArchiveConfig, ConfigError, DescriptorConfig, SearchConfig
from evolvekit.search.archive import Archive, cut_points, descriptor_value, sigmoid


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
    assert archive.axes[0].edges == [2.5, 4.5, 6.5], "halfway between neighbouring values"
    assert len(archive.cells) == 4, "equal counts; equal width put seven in bin 0"
    assert archive.axes[0].label(0) == "1..2.5" and archive.axes[0].label(3) == "6.5..1000"
    assert archive.to_dict()["descriptors"][0]["edges"] == [2.5, 4.5, 6.5]


def test_an_auto_axis_rebins_when_its_members_double():
    archive = Archive.from_config(ArchiveConfig(descriptors=(DescriptorConfig("complexity", 4),)))
    seen = []
    for i in range(9):
        archive.add(make(f"c{i}", score=float(i), complexity=float(i + 1)))
        seen.append(list(archive.axes[0].edges))
    assert seen[0] == [], "one member: everything is bin 0"
    assert seen[1] == [1.5]          # 2 members: one gap, one cut
    assert seen[2] == seen[1], "not at 3"
    assert seen[3] == [1.5, 2.5, 3.5]            # 4 members
    assert seen[4] == seen[5] == seen[6] == seen[3], "not at 5, 6 or 7"
    assert seen[7] == [2.5, 4.5, 6.5]            # 8 members
    assert seen[8] == seen[7]


def test_an_auto_axis_with_one_distinct_value_puts_everyone_in_bin_0():
    archive = Archive.from_config(ArchiveConfig(descriptors=(DescriptorConfig("complexity", 4),)))
    for i in range(4):
        archive.add(make(f"c{i}", score=float(i), complexity=5.0))
    assert archive.axes[0].edges == []
    assert set(archive.cells) == {(0,)}


@pytest.mark.parametrize(
    "values, edges",
    [
        ([1] * 7 + [5], [3.0]),                       # quantiles 1, 1, 1: one cut, not three at 1
        ([3] * 6 + [1, 9], [2.0, 6.0]),
        ([1] * 10 + [2, 3, 4, 5], [1.5, 2.5, 3.5]),   # tied quantiles: the fullest bin is cut
        ([10] * 5 + [11] * 5 + [12] * 5 + [13] * 5 + [14] * 4 + [20] * 8, [10.5, 12.5, 17.0]),
    ],
)
def test_tied_quantile_edges_never_merge_distinct_values(values, edges):
    assert cut_points(values, 4) == edges
    assert len(set(edges)) == len(edges) and not set(edges) & set(values), "every cut lies between two values"
    archive = Archive.from_config(ArchiveConfig(descriptors=(DescriptorConfig("complexity", 4),)))
    for i, value in enumerate(values):
        archive.add(make(f"c{i:02d}", score=float(i), complexity=float(value)))
    archive._refit()
    archive._rebin()
    by_value: dict[float, set] = {}
    for member in archive.members.values():
        by_value.setdefault(member.kpis["complexity"], set()).add(tuple(member.cell))
    cells = [cell for found in by_value.values() for cell in found]
    assert all(len(found) == 1 for found in by_value.values())
    if len(by_value) <= 4:
        assert len(set(cells)) == len(by_value), "no two distinct values share a cell"
    else:
        assert len(set(cells)) == 4


def test_two_distinct_values_take_two_cells_as_soon_as_they_are_seen():
    archive = Archive.from_config(ArchiveConfig(descriptors=(DescriptorConfig("complexity", 4),)))
    for i, value in enumerate([1] * 7 + [5]):
        archive.add(make(f"c{i}", score=float(i), complexity=float(value)))
    assert archive.members["c0"].cell != archive.members["c7"].cell


def test_an_update_that_moves_a_value_cuts_the_edges_again_as_a_resume_would():
    config = ArchiveConfig(descriptors=(DescriptorConfig("complexity", 2),))
    live = Archive.from_config(config)
    for cid, size in (("a", 1.0), ("b", 2.0), ("c", 3.0), ("d", 4.0)):
        live.add(make(cid, score=0.0, complexity=size))
    assert live.axes[0].edges == [2.5]
    # As the driver's re-evaluation does it: the member the grid holds is changed
    # in place, and `update` is handed that same object.
    member = live.members["b"]
    member.kpis = {**member.kpis, "complexity": 10.0}
    live.update(member)
    resumed = Archive.from_records([m.to_record() for m in sorted(live.members.values(), key=lambda m: m.id)], config)
    assert live.axes[0].edges == resumed.axes[0].edges == [3.5]
    assert live.cells.keys() == resumed.cells.keys()
    assert {m.id: m.cell for m in live.members.values()} == {m.id: m.cell for m in resumed.members.values()}


def test_an_update_that_keeps_the_values_keeps_the_edges():
    archive = Archive.from_config(ArchiveConfig(descriptors=(DescriptorConfig("complexity", 2),)))
    for cid, size in (("a", 1.0), ("b", 2.0), ("c", 3.0), ("d", 40.0)):
        archive.add(make(cid, score=0.0, complexity=size))
    before = list(archive.axes[0].edges)
    archive.update(make("b", score=5.0, complexity=2.0))
    assert archive.axes[0].edges == before


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


def test_a_parameter_axis_is_labelled_in_the_parameters_own_values():
    from evolvekit.space import ParameterSpace

    space = ParameterSpace.parse({
        "a": FLOAT01,
        "rate": {"type": "float", "low": 0.01, "high": 100.0, "log": True, "default": 1.0},
        "mode": {"type": "choice", "choices": ["x", "y", "z"], "default": "x"},
    })
    config = ArchiveConfig(descriptors=tuple(DescriptorConfig(bins=4, lo=0.0, hi=1.0, param=n) for n in space.names))
    archive = Archive.from_config(config, space=space)
    a, rate, mode = archive.axes
    assert [a.label(i) for i in range(4)] == ["0..0.25", "0.25..0.5", "0.5..0.75", "0.75..1"]
    assert rate.label(0) == "0.01..0.1" and rate.label(3) == "10..100", "log scale: decades"
    assert [mode.label(i) for i in range(4)] == ["x", "-", "y", "z"]


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


def test_a_resumed_run_rebuilds_the_pressure_the_live_run_had(tmp_path):
    from dataclasses import replace

    from evolvekit.search.driver import Driver

    config = _tuning_config(tmp_path)
    config = replace(config, search=replace(config.search, children_per_generation=3, generations=2,
                                                         selection_temperature=0.5))
    live = Driver(config, run_dir=tmp_path / "run", log=lambda m: None)
    live.run()
    assert live.grid.pressure, "the children were handed to the cascade"
    resumed = Driver(config, run_dir=tmp_path / "run", log=lambda m: None)
    resumed.resume()
    assert resumed.grid.pressure == pytest.approx(live.grid.pressure)
    assert resumed.grid.temperature == live.grid.temperature == 0.5


def test_a_run_stopped_in_generation_2_ends_it_with_the_pressure_of_an_uninterrupted_run(tmp_path):
    """Killed after generation 2's children were bred and written down: the
    resumed run adopts them from `pending.json`, and once it has evaluated
    them its pressure is the uninterrupted run's after generation 2."""
    from dataclasses import replace

    from evolvekit.events import read_events
    from evolvekit.search.driver import Driver

    def config_in(directory):
        directory.mkdir()
        config = _tuning_config(directory)
        return replace(config, search=replace(config.search, children_per_generation=3, generations=2))

    whole = Driver(config_in(tmp_path / "whole"), run_dir=tmp_path / "whole" / "run", log=lambda m: None)
    whole.run()

    config = config_in(tmp_path / "cut")
    cut = Driver(config, run_dir=tmp_path / "cut" / "run", log=lambda m: None)
    real = cut.cascade.evaluate_generation

    def dying(candidates, *args, **kwargs):
        if any(int(c.generation) == 2 for c in candidates):
            raise KeyboardInterrupt
        return real(candidates, *args, **kwargs)

    cut.cascade.evaluate_generation = dying
    with pytest.raises(KeyboardInterrupt):
        cut.run()
    assert (tmp_path / "cut" / "run" / "pending.json").exists()

    resumed = Driver(config, run_dir=tmp_path / "cut" / "run", log=lambda m: None)
    resumed.run()
    adopted = [e for e in read_events(tmp_path / "cut" / "run") if e["type"] == "generation_adopted"]
    assert adopted and adopted[0]["generation"] == 2
    assert whole.grid.pressure, "the children were handed to the cascade"
    assert resumed.grid.pressure == pytest.approx(whole.grid.pressure)


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
        archive.note_evaluated("fit")
    after = dict(zip((c.id for c in pool), archive.weights(pool)))
    assert after["fit"] == pytest.approx(before["fit"] / 10.0)
    assert after["fit"] < after["unfit"], "10 children did not cool the hot parent"


def _two_elites(temperature: float = 1.0) -> Archive:
    """Two elites in two cells, `a` the fitter (z = +1, `b` z = -1)."""
    archive = grid()
    archive.temperature = temperature
    archive.add(make("a", score=-1.0, behaviour=0.1))
    archive.add(make("b", score=-9.0, behaviour=0.9))
    return archive


def ratio(weights: list[float]) -> float:
    return weights[0] / weights[1]


def test_a_failed_apply_or_a_duplicate_does_not_make_a_parent_less_attractive():
    archive = _two_elites()
    before = archive.weights(archive.elites())
    archive.note_child("a")                       # counted for the record only
    assert archive.weights(archive.elites()) == before
    assert archive.children == {"a": 1}
    archive.note_evaluated("a")
    assert archive.weights(archive.elites())[0] < before[0]


def test_the_pressure_halves_every_generation():
    archive = _two_elites()
    archive.note_evaluated("a")
    archive.note_evaluated("a")
    archive.decay()
    assert archive.pressure["a"] == pytest.approx(1.0)
    archive.decay()
    assert archive.weights(archive.elites())[0] == pytest.approx(sigmoid(1.0) / 1.5)


def test_at_temperature_one_the_weight_is_todays_sigmoid():
    archive = _two_elites()
    assert archive.weights(archive.elites()) == [pytest.approx(sigmoid(1.0)), pytest.approx(sigmoid(-1.0))]


def test_a_lower_temperature_selects_harder():
    hot, cold = _two_elites(temperature=1.0), _two_elites(temperature=0.25)
    assert ratio(cold.weights(cold.elites())) > ratio(hot.weights(hot.elites()))
    assert ratio(cold.weights(cold.elites())) == pytest.approx(sigmoid(4.0) / sigmoid(-4.0))


def test_inspirations_are_drawn_without_the_pressure():
    archive = grid()
    for i in range(4):
        archive.add(make(f"c{i}", score=-float(i), behaviour=i / 4.0))
    parent = archive.members["c3"]
    draws = [[p.id for p in archive.sample_inspirations(parent, 2, random.Random(s))] for s in range(40)]
    pool = archive.elites()
    plain = archive.weights(pool)
    for _ in range(50):
        archive.note_evaluated("c0")
    assert archive.weights(pool, pressure=False) == plain
    assert archive.weights(pool)[0] < plain[0], "parents still feel it"
    again = [[p.id for p in archive.sample_inspirations(parent, 2, random.Random(s))] for s in range(40)]
    assert again == draws


def _row(cid: str, generation: int, *, parent: str | None = None, rejected: bool = False,
         novelty: str | None = None, block: str = "def f():\n    return 2\n") -> dict:
    candidate = make(cid, score=-1.0, parent=parent, rejected=rejected)
    candidate.generation = generation
    candidate.novelty = novelty
    candidate.block = block
    return candidate.to_record()


def test_the_pressure_rebuilds_from_the_records_as_it_was_live():
    seed_block = "def f():\n    return 1\n"
    rows = [
        _row("s", 0, block=seed_block),
        _row("c1", 1, parent="s"),                                         # evaluated
        _row("c2", 1, parent="s", rejected=True, novelty="duplicate"),     # never evaluated
        _row("c3", 1, parent="s", rejected=True, novelty="no_op", block=seed_block),
        _row("c4", 1, parent="s", rejected=True, block=seed_block),        # a failed apply
        _row("c5", 1, parent="s", rejected=True, novelty="behavioural"),   # caught in the cascade
        _row("c6", 1, parent="s", rejected=True),                          # a static-stage reject
        _row("c7", 3, parent="s"),                                         # generation 2 bred nothing
    ]
    config = ArchiveConfig(descriptors=(
        DescriptorConfig("complexity", 4, 0.0, 40.0), DescriptorConfig("behaviour", 4, 0.0, 1.0)))

    live = Archive.from_config(config)
    for generation, evaluated in ((1, 3), (2, 0), (3, 1)):
        live.decay()
        for _ in range(evaluated):
            live.note_evaluated("s")

    rebuilt = Archive.from_records(rows, config)
    assert rebuilt.pressure == pytest.approx(live.pressure)
    assert rebuilt.pressure["s"] == pytest.approx(3 / 4 + 1)
    assert rebuilt.children == {"s": 7}, "the views still count every attempt"


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
