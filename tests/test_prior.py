"""Warm starts and the decaying prior: `study new --warm`, `Parameter.prior`, `param_lhs_typed`, `memory.priors_for`."""

from __future__ import annotations

import json
import random
import shutil

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
from evolvekit.search.operators import latin_rows, param_lhs_typed
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
        rows = latin_rows(NO_PRIOR, 6, random.Random(seed))
        assert [values for values, _ in rows] == NO_PRIOR.latin_hypercube(6, random.Random(seed))
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
