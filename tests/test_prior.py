"""Warm starts and the decaying prior: `study new --warm`, `Parameter.prior`, `param_lhs_typed`, `memory.priors_for`."""

from __future__ import annotations

import json
import random
import shutil
from dataclasses import replace
from statistics import fmean

import pytest
import yaml
from test_app_results import finished_run
from test_memory import finished

from evolvekit.app.store import Home
from evolvekit.candidate import Candidate
from evolvekit.cli import main
from evolvekit.harness import HarnessError, memory
from evolvekit.harness.compile import compile_study, write_run
from evolvekit.harness.library import BUILT_IN
from evolvekit.harness.manifest import Setting, load_harness
from evolvekit.harness.plan import Plan
from evolvekit.harness.study import SettingChoice, Study, load_study, save_study
from evolvekit.search.operators import latin_rows, param_lhs_typed, param_local
from evolvekit.search.tuning import Observation, _Density, propose_tpe
from evolvekit.space import ParameterSpace, SpaceError


def warm_study(home, docs, name="Winter") -> Study:
    """A `tune` study of the demo tour, warm-started from the home's latest `use` study."""
    slug = home.create_study("demo-tour", "tune", name)
    root = home.study_root(slug)
    study, _ = memory.warm_start(home, load_harness(root / "harness"), load_study(root), document_of=docs.get)
    save_study(study, root)
    return study


def seed_candidate(space) -> Candidate:
    """The starting point of a search over `space`."""
    params = space.defaults()
    return Candidate(id="seed", generation=0, block=space.render_block(params), source="", operator="human-seed",
                     params=params)


# -- the warm start -----------------------------------------------------------


def test_warm_uses_latest_winner(tmp_path, capsys, monkeypatch):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
    finished(home, docs, "Summer", decision="use", best={"neighbours": 16}, finished_at="2026-10-08")
    finished(home, docs, "Autumn", decision="keep", best={"neighbours": 25}, finished_at="2026-10-15")
    monkeypatch.setattr(memory, "build_status", docs.get)   # what the CLI's warm start reads
    assert main(["study", "new", "Winter", "--harness", "demo-tour", "--template", "tune", "--warm", "--home", str(home.root)]) == 0
    study = load_study(home.study_root("winter"))
    assert study.settings["neighbours"].start == 16 and study.warm.from_slug == "summer"
    assert study.warm.run == "r1" and study.warm.at.startswith("2026-10-08") and study.warm.ranked == []
    err = capsys.readouterr().err
    assert "warm start from Summer (use): neighbours 16" in err
    assert "final check" in err, "the warm start moves the starting point the final check compares with"
    assert "ranked" not in (home.study_root("winter") / "study.yaml").read_text(encoding="utf-8")


def test_warm_with_no_use_study_is_a_plain_start(tmp_path, capsys, monkeypatch):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Autumn", decision="keep", best={"neighbours": 25}, finished_at="2026-10-15")
    monkeypatch.setattr(memory, "build_status", docs.get)
    assert main(["study", "new", "Winter", "--harness", "demo-tour", "--template", "tune", "--warm", "--home", str(home.root)]) == 0
    study = load_study(home.study_root("winter"))
    assert study.warm is None and study.settings["neighbours"].start is None
    assert "nothing to warm-start from: no finished study on demo-tour says use; a plain start" in capsys.readouterr().err


def test_warm_start_values_inside_the_study_range(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 16, "restart_after": 13000}, finished_at="2026-10-01")
    slug = home.create_study("demo-tour", "tune", "Winter")
    root = home.study_root(slug)
    pinned = yaml.safe_load((root / "harness" / "harness.yaml").read_text(encoding="utf-8"))
    pinned["inputs"] = {"start_settings": {"label": "Settings to start from", "formats": [".json"],
                                           "describe": "A JSON object of setting values.", "provides": "settings"}}
    (root / "harness" / "harness.yaml").write_text(yaml.safe_dump(pinned, sort_keys=False), encoding="utf-8")
    harness = load_harness(root / "harness")

    narrowed = load_study(root)
    narrowed.settings["neighbours"] = SettingChoice("tune", low=2, high=10)
    warmed, lines = memory.warm_start(home, harness, narrowed, document_of=docs.get)
    assert warmed.settings["neighbours"].start is None and warmed.settings["restart_after"].start == 13000
    assert lines[0] == "warm start from Spring (use): restart_after 13000"
    assert any(line.startswith("neighbours: ") and "16" in line and "outside" in line for line in lines[1:])
    config, _ = compile_study(warmed, harness, Plan.fixed(), root=root)
    assert config["problem"]["parameters"]["neighbours"]["default"] == 8, "the harness default"

    (root / "inputs" / "start.json").write_text(json.dumps({"neighbours": 5}), encoding="utf-8")
    plain = load_study(root)
    plain.inputs = {"start_settings": "inputs/start.json"}
    config, _ = compile_study(plain, harness, Plan.fixed(), root=root)
    assert config["problem"]["parameters"]["neighbours"]["default"] == 5, "without a warm start the file decides"
    warmed, _ = memory.warm_start(home, harness, plain, document_of=docs.get)
    config, _ = compile_study(warmed, harness, Plan.fixed(), root=root)
    assert config["problem"]["parameters"]["neighbours"]["default"] == 16, "the start wins over the file"


IMPORTANCE = {"accept_worse": (0.7, 20), "init": (0.6, 20), "neighbours": (0.5, 20), "restart_after": (0.1, 20)}


def test_warm_reranks_recommended_after_three(tmp_path, capsys, monkeypatch):
    home, docs = Home(tmp_path / "my home"), {}
    copy = home.harnesses_dir / "demo-tour-1.1.1"
    shutil.copytree(BUILT_IN / "demo-tour", copy, ignore=shutil.ignore_patterns("__pycache__", "samples"))
    raw = yaml.safe_load((copy / "harness.yaml").read_text(encoding="utf-8"))
    raw["version"] = "1.1.1"
    raw["settings"]["init"]["recommended"] = False
    (copy / "harness.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    assert home.harness_entry("demo-tour").harness.recommended == ["neighbours", "restart_after", "accept_worse"]
    monkeypatch.setattr(memory, "build_status", docs.get)
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, importance=IMPORTANCE, finished_at="2026-10-01")
    finished(home, docs, "Summer", decision="keep", importance=IMPORTANCE, finished_at="2026-10-08")

    assert main(["study", "new", "Two", "--harness", "demo-tour", "--template", "tune", "--warm", "--home", str(home.root)]) == 0
    two = load_study(home.study_root("two"))
    assert set(two.tuned_settings()) == {"neighbours", "restart_after", "accept_worse"} and two.warm.ranked == []
    assert "tuned:" not in capsys.readouterr().err

    finished(home, docs, "Autumn", decision="keep", importance=IMPORTANCE, finished_at="2026-10-15")
    assert main(["study", "new", "Three", "--harness", "demo-tour", "--template", "tune", "--warm", "--home", str(home.root)]) == 0
    three = load_study(home.study_root("three"))
    assert three.harness_version == "1.1.1"
    assert set(three.tuned_settings()) == {"accept_worse", "init", "neighbours"}
    assert three.warm.ranked == ["accept_worse", "init", "neighbours", "restart_after"]
    assert three.settings["neighbours"].start == 12
    err = capsys.readouterr().err
    assert ("tuned: the 3 settings that went most with the measure in 3 studies: accept_worse, init, neighbours"
            in err)


def test_warm_needs_the_home(tmp_path, capsys):
    assert main(["study", "new", "W", "--harness", "demo-tour", "--warm", "--dir", str(tmp_path / "w")]) == 2
    assert "--warm reads the library home's finished studies; leave out --dir" in capsys.readouterr().err
    assert not (tmp_path / "w").exists()


def test_warm_key_round_trips(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
    study = warm_study(home, docs)
    again = load_study(home.study_root("winter"))
    assert again.warm == study.warm and again.warm.from_slug == "spring"
    document = yaml.safe_load((home.study_root("winter") / "study.yaml").read_text(encoding="utf-8"))
    assert set(document["warm"]) == {"from", "run", "at"}


# -- the decaying prior -------------------------------------------------------


def test_prior_weight_decays_by_half(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
    study = warm_study(home, docs)
    assert memory.priors_for(home, study, document_of=docs.get) == {"neighbours": {"value": 12, "weight": 0.5}}
    finished(home, docs, "Summer", decision="keep", best={"neighbours": 20}, finished_at="2026-10-08")
    assert memory.priors_for(home, study, document_of=docs.get)["neighbours"]["weight"] == 0.25
    for day in ("15", "22", "29"):
        finished(home, docs, f"S{day}", decision="keep", best={"neighbours": 20}, finished_at=f"2026-10-{day}")
    assert memory.priors_for(home, study, document_of=docs.get) == {}, "0.5 ** 5 is below 0.05: dropped"


def test_prior_outside_the_study_range_is_dropped(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
    study = warm_study(home, docs)
    study.settings["neighbours"] = SettingChoice("tune", low=2, high=10)
    assert memory.priors_for(home, study, document_of=docs.get) == {}


def lhs_children(space, count: int, per_cube: int = 10):
    """`count` `param_lhs` children over `space`, as the driver hands them out:
    the rows of hypercubes of `per_cube` rows, each cube from its own seed."""
    rows = [row for s in range(count // per_cube) for row in latin_rows(space, per_cube, random.Random(s))]
    return [param_lhs_typed(space, seed_candidate(space), row=values, prior=names) for values, names in rows]


def test_prior_share_of_draws():
    space = ParameterSpace.parse({"n": {"type": "int", "low": 2, "high": 30, "default": 8, "prior": {"value": 12, "weight": 0.3}}})
    drawn = lhs_children(space, 1000)
    share = sum("n" in r.meta.get("prior", []) for r in drawn) / 1000
    assert abs(share - 0.3) <= 0.05
    assert all(abs(space.parameters[0].to_unit(r.meta["params"]["n"]) - space.parameters[0].to_unit(12)) <= 0.1 + 1e-9
               for r in drawn if "n" in r.meta.get("prior", []))


def test_prior_never_excludes_range_ends():
    space = ParameterSpace.parse({"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.5,
                                        "prior": {"value": 0.5, "weight": 0.5}}})
    values = [r.meta["params"]["x"] for r in lhs_children(space, 1000)]
    assert any(v < 0.05 for v in values) and any(v > 0.95 for v in values)


def test_prior_draws_are_seeded():
    space = ParameterSpace.parse({
        "n": {"type": "int", "low": 2, "high": 30, "default": 8, "prior": {"value": 12, "weight": 0.5}},
        "r": {"type": "int", "low": 100, "high": 50000, "default": 2000, "log": True, "prior": {"value": 13000, "weight": 0.5}},
        "init": {"type": "choice", "choices": ["random", "nearest"], "default": "random", "prior": {"value": "nearest", "weight": 0.5}},
    })
    for seed in range(30):
        first, second = (latin_rows(space, 6, random.Random(seed)) for _ in range(2))
        assert first == second
        for values, names in first:
            if "init" in names:
                assert values["init"] == "nearest", "a choice is drawn as the value itself"


NO_PRIOR = ParameterSpace.parse({
    "n": {"type": "int", "low": 2, "high": 30, "default": 8},
    "r": {"type": "int", "low": 100, "high": 50000, "default": 2000, "log": True},
    "a": {"type": "float", "low": 0.0, "high": 0.05, "default": 0.0},
    "init": {"type": "choice", "choices": ["random", "nearest"], "default": "random"},
    "or_opt": {"type": "bool", "default": False},
})


def test_no_prior_draws_as_before():
    """Without a prior a cube is the space's own hypercube: nothing more is drawn."""
    for seed in (0, 1, 7, 42):
        ours, plain = random.Random(seed), random.Random(seed)
        rows = latin_rows(NO_PRIOR, 6, ours)
        assert [values for values, _ in rows] == NO_PRIOR.latin_hypercube(6, plain)
        assert ours.getstate() == plain.getstate(), "not one number more drawn"
        assert all(names == [] for _, names in rows)
        child = param_lhs_typed(NO_PRIOR, seed_candidate(NO_PRIOR), row=rows[0][0], prior=rows[0][1])
        assert child.meta == {"params": rows[0][0]}


def test_a_prior_at_a_range_end_is_reflected_not_piled_on_the_end():
    space = ParameterSpace.parse({"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.5,
                                        "prior": {"value": 0.0, "weight": 0.5}}})
    near = [r.meta["params"]["x"] for r in lhs_children(space, 400) if "x" in r.meta.get("prior", [])]
    assert len(near) > 100 and all(0.0 <= v <= 0.1 + 1e-9 for v in near)
    assert sum(v == 0.0 for v in near) <= 2, "clamping would put about half of them on 0.0"


def test_prior_is_not_part_of_the_cache_key():
    plain = ParameterSpace.parse({"n": {"type": "int", "low": 2, "high": 30, "default": 8}})
    with_prior = ParameterSpace.parse({"n": {"type": "int", "low": 2, "high": 30, "default": 8, "prior": {"value": 12, "weight": 0.5}}})
    assert plain.render_block({"n": 9}) == with_prior.render_block({"n": 9})
    assert plain.describe() == with_prior.describe()


# -- priors beyond the sweep: the estimator and the first local steps (Q18) ----

UNIT = ParameterSpace.parse({"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.2}})


def _with_prior(space: ParameterSpace, value, weight: float) -> ParameterSpace:
    """`space` with a prior on its one parameter. Built rather than parsed, so a
    weight of one can be tried: a declaration refuses it, the density does not."""
    return ParameterSpace((replace(space.parameters[0], prior=(value, weight)),))


def test_a_numeric_prior_adds_four_weights_of_a_narrow_gaussian_to_the_uniform():
    """No observations: the density is the prior part alone, (U + 4w G) / (1 + 4w).
    At w 0.5 and the prior's own value 0.8: G(0.8) = phi(0) / (0.1 Z),
    Z = Phi(2) - Phi(-8) = 0.97725, so G = 4.0822 and the pdf (1 + 2 G) / 3 = 3.0548.
    It integrates to one, the narrow Gaussian renormalised inside [0, 1]."""
    density = _Density(_with_prior(UNIT, 0.8, 0.5).parameters[0], [])
    assert density.pdf(0.8) == pytest.approx(3.0548, abs=1e-3)
    assert density.pdf(0.0) == pytest.approx(1 / 3, abs=1e-6), "eight bandwidths from the prior: the uniform's share alone"
    edge = _Density(_with_prior(UNIT, 1.0, 0.5).parameters[0], [Observation({"x": 0.9}, 1.0)])
    assert sum(edge.pdf((i + 0.5) / 4000) for i in range(4000)) / 4000 == pytest.approx(1.0, abs=0.005)


def test_a_choice_prior_is_four_weights_of_pseudo_counts_on_its_value():
    """A bool with a prior on True at w 0.5 and no observations: 1/2 on each value from
    the uniform part, 4 x 0.5 = 2 more on True; total W = 3, so True has 5/6."""
    flag = ParameterSpace.parse({"on": {"type": "bool", "default": False, "prior": {"value": True, "weight": 0.5}}})
    density = _Density(flag.parameters[0], [])
    assert (density.pdf(False), density.pdf(True)) == pytest.approx((1 / 6, 5 / 6))
    plain = _Density(ParameterSpace.parse({"on": {"type": "bool", "default": False}}).parameters[0],
                     [Observation({"on": True}, 1.0)])
    assert (plain.pdf(False), plain.pdf(True)) == pytest.approx((1 / 4, 3 / 4)), "without a prior, as before"


def test_a_prior_pulls_tpe_towards_its_value():
    """Same observations, flat around the box; a prior at 0.8 with weight 1: over
    200 seeds the mean proposal moves towards 0.8 by at least 0.1 on the unit scale.
    Flat: each seed has its own history, 30 stratified points with scores drawn at
    random, so no side of the box is better on average. Measured: 0.481 without the
    prior, 0.698 with it, a paired shift of 0.217 (SE 0.021); at weight 0.5, 0.114."""
    pulled = _with_prior(UNIT, 0.8, 1.0)
    plain, near = [], []
    for s in range(200):
        rng = random.Random(s)
        history = [Observation({"x": (i + rng.random()) / 30}, rng.random()) for i in range(30)]
        plain.append(propose_tpe(UNIT, history, random.Random(10_000 + s))["x"])
        near.append(propose_tpe(pulled, history, random.Random(10_000 + s))["x"])
    print(f"mean proposal: {fmean(plain):.3f} without the prior, {fmean(near):.3f} with it at 0.8")
    assert fmean(near) >= fmean(plain) + 0.1


def test_a_local_step_from_the_seed_starts_at_the_prior_value_at_its_weight():
    """Decision 11: one parameter, default 0.2, prior value 0.8, weight w = 0.5, 4000 seeds;
    "near" = within one step (0.15) of the prior value. Expected share
    w * 0.68 + (1 - w) * plain: 0.68 = P(|N(0, 0.15)| <= 0.15) is a step from the prior
    value landing near it (a step past 1 is clamped onto 1, 0.2 away: not near), and
    plain = P(N(0, 0.15) >= 0.45) = 0.0013, the share of plain steps from 0.2 that land
    near 0.8. So about 34.2 %; measured 35.0 % (SE 0.75 points), plain 0.18 %;
    assert 0.30 <= share <= 0.40."""
    def near(space) -> float:
        start = seed_candidate(space)
        return sum(abs(param_local(space, start, seed=s).meta["params"]["x"] - 0.8) <= 0.15 for s in range(4000)) / 4000

    share, plain = near(_with_prior(UNIT, 0.8, 0.5)), near(UNIT)
    print(f"near the prior: {share:.1%} from the seed with the prior, {plain:.2%} plain")
    assert 0.30 <= share <= 0.40


def test_a_local_step_from_another_parent_ignores_the_prior():
    space = _with_prior(UNIT, 0.8, 0.5)
    parent = Candidate(id="g001-c0001", generation=1, block=space.render_block({"x": 0.2}), source="",
                       operator="param_lhs", params={"x": 0.2})
    for s in range(50):
        assert param_local(space, parent, seed=s).meta == {"params": space.perturb({"x": 0.2}, random.Random(s))}
        unpriored = param_local(UNIT, seed_candidate(UNIT), seed=s)
        assert unpriored.meta == {"params": UNIT.perturb({"x": 0.2}, random.Random(s))}, "no prior: nothing more drawn"


def test_a_local_step_that_starts_at_the_prior_says_so():
    space = _with_prior(UNIT, 0.8, 0.5)
    started = [param_local(space, seed_candidate(space), seed=s).meta for s in range(200)]
    assert 70 <= sum(m.get("prior") == ["x"] for m in started) <= 130, "about half, at w 0.5"


@pytest.mark.parametrize("prior, words", [
    ({"value": 40, "weight": 0.5}, "above its maximum"),
    ({"value": 12, "weight": 0}, "weight"),
    ({"value": 12, "weight": 1.5}, "prior.weight: below 1, so every value in range can still be drawn"),
    ({"value": 12, "weight": 1}, "prior.weight: below 1, so every value in range can still be drawn"),
    ({"value": 12}, "weight"),
    (12, "value"),
])
def test_a_bad_prior_is_refused_where_it_is_written(prior, words):
    with pytest.raises(SpaceError, match=r"problem\.parameters\.n\.prior") as caught:
        ParameterSpace.parse({"n": {"type": "int", "low": 2, "high": 30, "default": 8, "prior": prior}})
    assert words in str(caught.value)


def test_compile_writes_prior_only_for_warm_studies(tmp_path, monkeypatch):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
    monkeypatch.setattr(memory, "build_status", docs.get)   # what write_run's priors read
    study = warm_study(home, docs)
    root = home.study_root("winter")
    harness = load_harness(root / "harness")
    run_dir = write_run(study, harness, Plan.fixed(), root, "r1")
    config = yaml.safe_load((run_dir / "evolvekit.yaml").read_text(encoding="utf-8"))
    parameters = config["problem"]["parameters"]
    assert parameters["neighbours"]["prior"] == {"value": 12, "weight": 0.5}
    assert [name for name, spec in parameters.items() if "prior" in spec] == ["neighbours"]
    assert ParameterSpace.parse(parameters).parameters[0].prior == (12, 0.5), "the engine reads it"
    study.warm = None
    run_dir = write_run(study, harness, Plan.fixed(), root, "r2")
    assert "prior" not in (run_dir / "evolvekit.yaml").read_text(encoding="utf-8")


def test_a_warm_studys_second_run_keeps_its_weight(tmp_path, monkeypatch):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
    monkeypatch.setattr(memory, "build_status", docs.get)
    study = warm_study(home, docs)
    root = home.study_root("winter")
    harness = load_harness(root / "harness")
    write_run(study, harness, Plan.fixed(), root, "r1")
    finished_run(root / "runs" / "r1", decision="keep", started="2026-10-05T00:00:00+00:00")
    assert [f.slug for f in memory.finished_studies(home, "demo-tour")] == ["spring", "winter"]
    run_dir = write_run(study, harness, Plan.fixed(), root, "r2")
    config = yaml.safe_load((run_dir / "evolvekit.yaml").read_text(encoding="utf-8"))
    assert config["problem"]["parameters"]["neighbours"]["prior"] == {"value": 12, "weight": 0.5}, \
        "its own first run is not a study since the winner: the new study is counted once"


def test_priors_that_cannot_be_read_are_said_not_swallowed(tmp_path, monkeypatch):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
    study = warm_study(home, docs)
    root = home.study_root("winter")
    harness = load_harness(root / "harness")

    def locked(*_a, **_k):
        raise OSError("a run folder is locked")

    monkeypatch.setattr(memory, "priors_for", locked)
    said: list[str] = []
    run_dir = write_run(study, harness, Plan.fixed(), root, "r1", log=said.append)
    assert json.loads((run_dir / "study-run.json").read_text(encoding="utf-8"))["priors"] == \
        "not applied: a run folder is locked"
    assert said == ["the warm start's prior is not applied: a run folder is locked; "
                    "the search draws over the whole range alone"]
    assert "prior" not in yaml.safe_load((run_dir / "evolvekit.yaml").read_text(encoding="utf-8"))["problem"]["parameters"]["neighbours"]

    def broken(*_a, **_k):
        raise KeyError("winners")

    monkeypatch.setattr(memory, "priors_for", broken)
    with pytest.raises(KeyError):
        write_run(study, harness, Plan.fixed(), root, "r2")


def test_a_harness_declares_no_prior():
    with pytest.raises(HarnessError, match=r"settings\.neighbours: unknown key\(s\) \['prior'\]"):
        Setting.parse("neighbours", {"type": "int", "low": 2, "high": 30, "default": 8,
                                     "prior": {"value": 12, "weight": 0.5}})


def test_warm_with_a_template_that_tunes_nothing(tmp_path, capsys, monkeypatch):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
    monkeypatch.setattr(memory, "build_status", docs.get)
    assert main(["study", "new", "Weights", "--harness", "demo-tour", "--template", "stop-weights", "--warm",
                 "--home", str(home.root)]) == 0
    assert load_study(home.study_root("weights")).warm is None
    assert "this template tunes no setting, so there is nothing to warm-start" in capsys.readouterr().err


def test_a_failed_warm_start_leaves_no_study_behind(tmp_path, monkeypatch):
    home = Home(tmp_path / "my home")

    def locked(*_a, **_k):
        raise OSError("a run folder is locked")

    monkeypatch.setattr(memory, "warm_start", locked)
    with pytest.raises(OSError):
        home.create_study("demo-tour", "tune", "Winter", warm=True)
    assert not (home.studies_dir / "winter").exists()


def test_next_study_drops_the_warm_start(tmp_path, monkeypatch):
    from evolvekit import status
    from evolvekit.app import jobs

    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
    warm_study(home, docs)
    root = home.study_root("winter")
    finished_run(root / "runs" / "r1", decision="keep", started="2026-10-05T00:00:00+00:00")
    monkeypatch.setattr(status, "build_status", lambda _run_dir: {"progress": {"best": {"id": "c1"}}})
    slug = jobs.next_study(home, root, "r1", "Winter again")
    assert load_study(home.study_root(slug)).warm is None
