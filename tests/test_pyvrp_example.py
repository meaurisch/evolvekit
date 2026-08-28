"""The PyVRP configuration-search example: the instance, the referee, the loop.

Every test here needs PyVRP, which the core suite deliberately does not depend
on -- `python tasks.py check` in the base interpreter skips this whole file.
Run it with the example's own interpreter instead:

    .venv-pyvrp/Scripts/python -m pytest tests/test_pyvrp_example.py -q

What is asserted: that the generator is deterministic and actually exercises
the PyVRP features the README claims; that the 900-second cap lives above the
fence and cannot be argued with; that the evaluator emits every KPI the config
names plus its `text_feedback`; and that two offline generations against the
`fake` provider produce and record a *different* configuration. Beating the
defaults is not asserted -- that is what a paid run is for.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("pyvrp", reason="the pyvrp example needs the pyvrp extra")

MIN_PYVRP = (0, 14)
"""The example builds `ProblemData` with reload depots, overtime and shipments,
and configures `PerturbationParams`. All of those arrived in 0.14, so an older
PyVRP on the interpreter is a skip rather than a failure -- the version, not
merely the package, is the dependency."""

_installed = importlib.metadata.version("pyvrp")
if tuple(int(part) for part in _installed.split(".")[:2]) < MIN_PYVRP:
    pytest.skip(
        f"the pyvrp example needs pyvrp >= {'.'.join(map(str, MIN_PYVRP))}; "
        f"this interpreter has {_installed}",
        allow_module_level=True,
    )

from evolvekit.config import load_config  # noqa: E402
from evolvekit.search.driver import Driver  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "examples" / "pyvrp"
EXAMPLE_CONFIG = EXAMPLE_DIR / "evolvekit.yaml"
REAL_CONFIG = EXAMPLE_DIR / "evolvekit.real.yaml"
INSTANCE_300 = EXAMPLE_DIR / "instance-300-s7.json"


def _load(path: Path, name: str):
    if str(EXAMPLE_DIR) not in sys.path:
        sys.path.insert(0, str(EXAMPLE_DIR))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def maker():
    return _load(EXAMPLE_DIR / "make_instance.py", "vrp_make_instance")


@pytest.fixture(scope="module")
def skeleton():
    return _load(EXAMPLE_DIR / "skeleton.py", "vrp_skeleton")


@pytest.fixture(scope="module")
def instance(skeleton):
    return skeleton.load_instance(INSTANCE_300)


# -- the generator ---------------------------------------------------------


def test_the_generator_is_deterministic(maker):
    assert maker.generate(300, 7) == maker.generate(300, 7)


def test_a_different_seed_is_a_different_instance(maker):
    assert maker.generate(300, 7) != maker.generate(300, 23)


def test_the_committed_instance_matches_the_generator(maker, instance):
    """The file in the repo is exactly what `--orders 300 --seed 7` produces."""
    assert maker.generate(300, 7) == instance


def test_the_instance_uses_every_feature_the_readme_claims(instance):
    counts = instance["meta"]["counts"]
    # Optional prize-collecting orders. Every optional client that is not a
    # group member carries a prize; the group members are optional only
    # because PyVRP refuses a required client inside a mutually exclusive
    # group, and their prize is deliberately zero -- the *group* is required,
    # so there is nothing to bribe the solver with.
    assert counts["optional_clients"] > 0
    prize_collecting = [
        c for c in instance["clients"] if not c["required"] and c["group"] is None
    ]
    assert prize_collecting
    assert all(c["prize"] > 0 for c in prize_collecting)
    assert all(
        c["prize"] == 0 for c in instance["clients"] if c["group"] is not None
    )
    # Simultaneous pickup-and-delivery: returns collected at the delivery address.
    assert counts["pickup_clients"] > 0
    # Release times: a second inbound wave that cannot leave the depot at 06:00.
    assert counts["released_clients"] > 0
    # Tight one-hour delivery slots.
    assert counts["tight_window_clients"] > 0
    # Mutually exclusive client groups: two addresses, exactly one visited.
    assert counts["groups"] > 0
    for group in instance["groups"]:
        assert len(group["clients"]) == 2
        assert group["required"] is True
        for index in group["clients"]:
            # PyVRP refuses a required client inside a mutually exclusive group.
            assert instance["clients"][index]["required"] is False
            assert instance["clients"][index]["group"] is not None
    # Same-day courier shipments: pick up at A, deliver at B.
    assert counts["shipments"] > 0
    # Two load dimensions, everywhere.
    assert all(len(c["delivery"]) == 2 and len(c["pickup"]) == 2 for c in instance["clients"])


def test_the_fleet_is_heterogeneous_and_multi_depot(instance):
    types = instance["vehicle_types"]
    depots = instance["depots"]
    assert len(depots) == 3
    assert len(types) == 12  # four classes at each of the three depots
    assert len({tuple(v["capacity"]) for v in types}) > 1
    assert len({v["fixed_cost"] for v in types}) > 1
    assert len({v["unit_distance_cost"] for v in types}) > 1
    assert all(v["unit_duration_cost"] > 0 for v in types)
    assert all(v["max_distance"] < 10**9 for v in types)
    assert all(v["shift_duration"] < 10**9 for v in types)
    # Two routing profiles, both in use.
    assert len(instance["meta"]["profiles"]) == 2
    assert {v["profile"] for v in types} == {0, 1}
    # Multi-trip: some vehicles may return to a depot to reload.
    assert any(v["reload_depots"] and v["max_reloads"] > 0 for v in types)
    # Overtime beyond the nominal shift, at a premium.
    assert any(v["max_overtime"] > 0 and v["unit_overtime_cost"] > 0 for v in types)
    # Shift time windows differ per depot, and every type has a latest start.
    assert len({v["tw_early"] for v in types}) > 1
    assert all(v["start_late"] >= v["tw_early"] for v in types)


def test_every_vehicle_type_starts_and_ends_at_a_real_depot(instance):
    depots = range(len(instance["depots"]))
    for vehicle in instance["vehicle_types"]:
        assert vehicle["start_depot"] in depots
        assert vehicle["end_depot"] in depots
        assert all(d in depots for d in vehicle["reload_depots"])


def test_scaling_down_keeps_the_features(maker):
    small = maker.generate(120, 3)
    counts = small["meta"]["counts"]
    assert counts["groups"] > 0 and counts["shipments"] > 0
    assert counts["vehicle_types"] == 12


# -- the referee -----------------------------------------------------------


def test_the_time_limit_is_capped_at_fifteen_minutes(skeleton):
    assert skeleton.MAX_TIME_LIMIT_S == 900.0
    assert skeleton.enforce_time_limit(20) == 20.0
    assert skeleton.enforce_time_limit(900) == 900.0
    assert skeleton.enforce_time_limit(901) == 900.0
    assert skeleton.enforce_time_limit(86_400) == 900.0
    assert skeleton.enforce_time_limit(float("inf")) == 900.0


def test_a_nonsense_time_limit_falls_back_to_the_cap(skeleton):
    assert skeleton.enforce_time_limit(-5) == 900.0
    assert skeleton.enforce_time_limit(0) == 900.0
    assert skeleton.enforce_time_limit("an hour") == 900.0
    assert skeleton.enforce_time_limit(None) == 900.0
    assert skeleton.enforce_time_limit(0.001) == skeleton.MIN_SOLVE_S


def test_the_cap_binds_the_actual_solve_not_only_the_arithmetic(
    skeleton, instance, monkeypatch
):
    """Ask for ten minutes with the cap lowered to two seconds; get two seconds."""
    monkeypatch.setattr(skeleton, "MAX_TIME_LIMIT_S", 2.0)
    result = skeleton.run_solve(instance, time_limit=600, seed=1)
    assert result["budget_s"] == 2.0
    assert result["time_used_s"] < 20.0


def test_configure_time_comes_out_of_the_candidates_own_budget(
    skeleton, instance, monkeypatch
):
    import time as _time

    def slow_configure(data, time_limit, seed):
        _time.sleep(1.0)
        return skeleton.SolveParams()

    monkeypatch.setattr(skeleton, "MAX_TIME_LIMIT_S", 4.0)
    monkeypatch.setattr(skeleton, "configure", slow_configure)
    result = skeleton.run_solve(instance, time_limit=600, seed=1)
    assert result["configure_s"] >= 1.0
    assert result["solve_s"] < result["budget_s"]
    assert result["time_used_s"] < 4.0 + 5.0  # the overshoot is bounded, not zero


def test_a_crashing_block_is_counted_and_replaced_by_the_defaults(
    skeleton, instance, monkeypatch
):
    def boom(data, time_limit, seed):
        raise RuntimeError("no")

    monkeypatch.setattr(skeleton, "MAX_TIME_LIMIT_S", 2.0)
    monkeypatch.setattr(skeleton, "configure", boom)
    result = skeleton.run_solve(instance, time_limit=600, seed=1)
    assert result["config_errors"] == 1.0
    assert "RuntimeError" in result["config_note"]
    assert result["objective"] > 0.0  # it still scored


def test_the_wrong_return_type_is_counted_not_fatal(skeleton, instance, monkeypatch):
    monkeypatch.setattr(skeleton, "MAX_TIME_LIMIT_S", 2.0)
    monkeypatch.setattr(skeleton, "configure", lambda d, t, s: {"neighbours": 20})
    result = skeleton.run_solve(instance, time_limit=600, seed=1)
    assert result["config_errors"] == 1.0
    assert "expected SolveParams" in result["config_note"]


def test_a_configuration_that_only_fails_inside_pyvrp_is_counted_too(
    skeleton, instance, monkeypatch
):
    """An operator list holding something that is not an operator only blows up
    once PyVRP looks at it. Count it, fall back to the defaults, still score."""
    monkeypatch.setattr(skeleton, "MAX_TIME_LIMIT_S", 3.0)
    monkeypatch.setattr(
        skeleton,
        "configure",
        lambda d, t, s: skeleton.SolveParams(operators=[object]),
    )
    result = skeleton.run_solve(instance, time_limit=600, seed=1)
    assert result["config_errors"] == 1.0
    assert "pyvrp.solve()" in result["config_note"]
    assert result["objective"] > 0.0


def test_the_tuple_contract_is_accepted(skeleton, instance, monkeypatch):
    monkeypatch.setattr(skeleton, "MAX_TIME_LIMIT_S", 2.0)
    monkeypatch.setattr(skeleton, "configure", lambda d, t, s: (skeleton.SolveParams(), None))
    result = skeleton.run_solve(instance, time_limit=600, seed=1)
    assert result["config_errors"] == 0.0
    assert result["warm_started"] is False


# -- the model ------------------------------------------------------------


def test_the_problem_data_carries_the_features_into_pyvrp(skeleton, instance):
    data = skeleton.build_data(instance)
    assert data.num_clients == instance["meta"]["counts"]["clients"]
    assert data.num_depots == 3
    assert data.num_vehicle_types == 12
    assert data.num_load_dimensions == 2
    assert data.num_profiles == 2
    assert data.num_groups == instance["meta"]["counts"]["groups"]
    assert data.num_shipments == instance["meta"]["counts"]["shipments"]


def test_travel_times_come_from_distance_at_the_profile_speed(skeleton, instance):
    data = skeleton.build_data(instance)
    light, heavy = 0, 1
    speeds = [p["speed_mps"] for p in instance["meta"]["profiles"]]
    for profile in (light, heavy):
        distance = data.distance_matrix(profile)
        duration = data.duration_matrix(profile)
        i, j = 3, 40
        assert duration[i, j] == round(distance[i, j] / speeds[profile])
    # The heavy profile is longer on inner-city edges and never shorter.
    assert (data.distance_matrix(heavy) >= data.distance_matrix(light)).all()
    assert data.distance_matrix(heavy).sum() > data.distance_matrix(light).sum()


# -- the evaluator ---------------------------------------------------------


@pytest.fixture(scope="module")
def evaluator_output(tmp_path_factory):
    out = tmp_path_factory.mktemp("pyvrp-kpis") / "kpis.json"
    proc = subprocess.run(
        [
            sys.executable,
            "evaluate.py",
            "--candidate",
            "skeleton.py",
            "--inputs",
            "metro300",
            "--out",
            str(out),
            "--seed",
            "42",
            "--time-limit",
            "5",
        ],
        cwd=str(EXAMPLE_DIR),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_evaluator_emits_every_kpi_the_config_names(evaluator_output):
    config = load_config(EXAMPLE_CONFIG)
    kpis = evaluator_output["kpis"]
    named = {config.evaluate.score.objective}
    named |= set(config.evaluate.score.weights)
    named |= {p.kpi for p in config.evaluate.penalties}
    named |= {
        d.kpi
        for d in config.search.archive.descriptors
        if d.kpi not in ("complexity", "static_problems")
    }
    missing = sorted(named - set(kpis))
    assert not missing, f"the evaluator never emits {missing}"


def test_the_evaluator_emits_the_kpis_the_brief_asks_for(evaluator_output):
    kpis = evaluator_output["kpis"]
    for name in (
        "objective",
        "infeasible",
        "distance",
        "duration",
        "num_routes",
        "fixed_cost",
        "unassigned_clients",
        "time_used_s",
        "iterations",
        "mean_route_length",
        "fleet_mix_entropy",
    ):
        assert name in kpis, name
        assert isinstance(kpis[name], (int, float))
    # The convergence trace is a list KPI: it never reaches the score, only the
    # behaviour signature.
    assert isinstance(kpis["convergence"], list) and len(kpis["convergence"]) == 4


def test_the_evaluator_emits_text_feedback(evaluator_output):
    feedback = evaluator_output["text_feedback"]
    assert isinstance(feedback, str) and feedback.strip()
    assert "budget" in feedback
    assert "route(s)" in feedback
    assert "metro-300-s7" in feedback


def test_the_defaults_solve_the_small_instance_feasibly(evaluator_output):
    kpis = evaluator_output["kpis"]
    assert kpis["infeasible"] == 0.0
    assert kpis["missing_required_clients"] == 0.0
    assert kpis["missing_groups"] == 0.0
    assert kpis["unassigned_shipments"] == 0.0
    assert kpis["config_errors"] == 0.0
    assert 0.0 < kpis["objective"] < 100_000.0
    assert kpis["num_routes"] >= 1.0


def test_the_evaluator_checks_its_own_interpreter(monkeypatch):
    """The stage command says `python`; on Windows that is the *system*
    interpreter even inside an activated venv, so the evaluator hands the job
    over rather than failing on an import three stages into a run."""
    evaluator = _load(EXAMPLE_DIR / "evaluate.py", "vrp_evaluate")
    assert evaluator.ensure_pyvrp() is None  # this interpreter is fine

    monkeypatch.setattr(evaluator, "_pyvrp_is_usable", lambda: False)
    monkeypatch.setenv("EVOLVEKIT_PYVRP_RELAUNCHED", "1")
    with pytest.raises(SystemExit) as excinfo:
        evaluator.ensure_pyvrp()
    assert "pyvrp" in str(excinfo.value)


def test_the_stage_command_rejects_an_unknown_input_set(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            "evaluate.py",
            "--candidate",
            "skeleton.py",
            "--inputs",
            "not-a-set",
            "--out",
            str(tmp_path / "unused.json"),
        ],
        cwd=str(EXAMPLE_DIR),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "unknown input set" in proc.stderr


# -- the config ------------------------------------------------------------


def test_the_offline_example_is_one_command_stage_and_no_holdout():
    config = load_config(EXAMPLE_CONFIG)
    assert [s.id for s in config.evaluate.stages] == ["static", "full"]
    assert config.evaluate.score.direction == "minimize"
    assert config.final_stage.private_inputs == ()


def test_the_real_variant_inherits_the_problem_and_adds_a_holdout():
    base = load_config(EXAMPLE_CONFIG)
    real = load_config(REAL_CONFIG)
    assert real.problem == base.problem
    assert [s.id for s in real.evaluate.stages] == ["static", "proxy", "full"]
    assert real.final_stage.inputs == ("metro3000",)
    assert real.final_stage.private_inputs == ("metro3000b",)
    assert real.models.small.provider == "claude-cli"
    assert real.budget.max_usd == 10.0
    assert real.budget.max_full_evals_per_day == 6
    assert real.stop.patience == 8
    assert real.stop.max_usd_since_improvement == 1.50
    assert real.search.adaptive_children is not None
    assert (
        real.search.adaptive_children.min,
        real.search.adaptive_children.max,
        real.search.adaptive_children.grow_after,
        real.search.adaptive_children.shrink_after,
    ) == (3, 6, 2, 1)


def test_every_stage_timeout_leaves_room_for_the_solve_budget(skeleton):
    """A stage must not kill a solve that is obeying the cap: a killed stage
    reports no KPIs at all, which is a worse signal than a capped one."""
    for config_path in (EXAMPLE_CONFIG, REAL_CONFIG):
        config = load_config(config_path)
        for stage in config.evaluate.stages:
            if stage.kind != "command":
                continue
            requested = float(stage.command.split("--time-limit")[1].split()[0])
            budget = skeleton.enforce_time_limit(requested)
            assert stage.timeout >= 1.5 * budget, f"{config_path.name}:{stage.id}"


# -- two generations, offline ---------------------------------------------


@pytest.fixture(scope="module")
def offline_run(tmp_path_factory):
    """Two generations against the `fake` provider, on a three-second budget.

    The stage command is rewritten to this interpreter (the config says
    `python`, which only resolves to a pyvrp-carrying interpreter when the
    example's venv is activated) and to a much shorter solve budget, so the
    fixture costs seconds rather than minutes.
    """
    config = load_config(EXAMPLE_CONFIG)
    stages = tuple(
        stage
        if stage.kind != "command"
        else replace(
            stage,
            command=stage.command.replace("python ", f'"{sys.executable}" ', 1).replace(
                "--time-limit 20", "--time-limit 3"
            ),
            timeout=180,
        )
        for stage in config.evaluate.stages
    )
    config = replace(
        config,
        evaluate=replace(config.evaluate, stages=stages),
        search=replace(
            config.search,
            generations=2,
            children_per_generation=1,
            big_step_every=99,
            scratchpad_every=0,
            inspirations=0,
            adaptive_children=None,
            operators={"rewrite": 1.0},
        ),
    )
    driver = Driver(config, run_dir=tmp_path_factory.mktemp("pyvrp-run"))
    return driver, driver.run()


def test_the_offline_run_scores_the_seed_and_its_children(offline_run):
    driver, summary = offline_run
    assert summary.seed_score is not None
    rows = [r for r in driver.ledger.runs() if not r["rejected"] and r["kpis"]]
    assert len(rows) >= 2, "the run evaluated nothing but the seed"
    for row in rows:
        assert row["score"] is not None
        assert row["score"] > driver.config.evaluate.failure_score


def test_the_loop_records_a_different_configuration(offline_run):
    """The scripted answers are different *configurations*, not nudged numbers."""
    driver, _summary = offline_run
    blocks = [
        row["block"]
        for row in driver.ledger.runs()
        if row.get("block") and row["parent_id"] is not None
    ]
    assert blocks, "no child block was recorded"
    seed_block = next(
        row["block"] for row in driver.ledger.runs() if row["parent_id"] is None
    )
    assert any(block != seed_block for block in blocks)
    # And what changed is structure: the scripted children name operator
    # classes the seed does not, or turn the exhaustive pass off.
    assert any(
        "exhaustive_on_best=False" in block or "Swap33" in block for block in blocks
    )


def test_every_evaluated_candidate_reports_the_behaviour_descriptor(offline_run):
    driver, _summary = offline_run
    for row in driver.ledger.runs():
        if row["rejected"] or not row["kpis"]:
            continue
        assert "fleet_mix_entropy" in row["kpis"]
        assert 0.0 <= row["kpis"]["fleet_mix_entropy"] <= 4.0


def test_the_offline_run_never_exceeded_the_cap(offline_run):
    driver, _summary = offline_run
    for row in driver.ledger.runs():
        if row["rejected"] or not row["kpis"]:
            continue
        assert row["kpis"]["budget_s"] <= 900.0
        assert row["kpis"]["time_used_s"] <= 900.0
