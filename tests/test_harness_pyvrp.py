"""The PyVRP reference harness (`harnesses/pyvrp/`).

Most tests read requests and tables in plain Python. The ones that solve need
a Python with PyVRP 0.14 -- `.venv-pyvrp` in the checkout, or the interpreter
EVOLVEKIT_PYVRP_PYTHON names -- because the runner runs under the
application's Python, never evolvekit's. Without one they are skipped.
"""

from __future__ import annotations

import copy
import importlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from evolvekit.harness.check import _study_run, check_harness, exit_code
from evolvekit.harness.execute import run_study
from evolvekit.harness.library import create_study
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.study import load_study, save_study

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "harnesses" / "pyvrp"
BENCHMARK = ROOT / "benchmarks" / "pyvrp_hard"
SAMPLES = sorted((HARNESS / "samples").glob("*.json"))
CITY = HARNESS / "samples" / "city-60.json"
RECOMMENDED = {
    "solutions_between_updates", "max_penalty", "target_feasible", "penalty_decrease",
    "exhaustive_on_best", "use_swap21", "num_neighbours", "min_perturbations",
}
TINY_VRPLIB = """NAME : tiny
TYPE : CVRP
DIMENSION : 6
CAPACITY : 10
EDGE_WEIGHT_TYPE : EUC_2D
NODE_COORD_SECTION
1 0 0
2 10 0
3 0 10
4 -10 0
5 0 -10
6 7 7
DEMAND_SECTION
1 0
2 3
3 4
4 5
5 2
6 6
DEPOT_SECTION
1
-1
EOF
"""


def _pyvrp_python() -> str | None:
    named = os.environ.get("EVOLVEKIT_PYVRP_PYTHON")
    candidates = [named] if named else []
    candidates += [sys.executable, ROOT / ".venv-pyvrp" / "Scripts" / "python.exe", ROOT / ".venv-pyvrp" / "bin" / "python"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            found = subprocess.run([str(candidate), "-c", "import pyvrp"], capture_output=True, timeout=120)
            if found.returncode == 0:
                return str(Path(candidate).absolute())
    return None


PYVRP = _pyvrp_python()
needs_pyvrp = pytest.mark.skipif(
    PYVRP is None, reason="needs a Python with PyVRP 0.14 (.venv-pyvrp, or EVOLVEKIT_PYVRP_PYTHON)")


def _kit(name: str):
    """A module of the harness's kit, imported under a package name of its
    own (`kit` is a common name) and with the harness's SDK, as the runner has
    them."""
    package = "pyvrp_harness_kit"
    if package not in sys.modules:
        sys.path.insert(0, str(HARNESS))
        try:
            spec = importlib.util.spec_from_file_location(
                package, HARNESS / "kit" / "__init__.py", submodule_search_locations=[str(HARNESS / "kit")])
            module = importlib.util.module_from_spec(spec)
            sys.modules[package] = module
            spec.loader.exec_module(module)
            for sub in ("instance", "params", "request", "plan"):
                importlib.import_module(f"{package}.{sub}")
        finally:
            sys.path.remove(str(HARNESS))
    return sys.modules[f"{package}.{name}"]


def _by_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _read(path: Path = CITY):
    request = _kit("request")
    req = request.read(str(path))
    tables = request.tables(req)
    req.original = copy.deepcopy(tables)
    return req, tables


# ---------------------------------------------------------------------------
# the kit, the settings, the samples
# ---------------------------------------------------------------------------


def test_the_kit_builds_on_the_benchmark_unchanged():
    assert (HARNESS / "kit" / "instance.py").read_bytes() == (BENCHMARK / "instance.py").read_bytes(), \
        "kit/instance.py is a verbatim copy of benchmarks/pyvrp_hard/instance.py: copy it again"
    params = _kit("params")
    solve = _by_path("pyvrp_hard_solve_for_harness_test", BENCHMARK / "solve.py")
    assert params.PARAMETERS == solve.PARAMETERS
    assert params.OPERATOR_ORDER == solve.OPERATOR_ORDER
    assert params.MAX_PENALTY_CEILING == solve.MAX_PENALTY_CEILING


def test_the_settings_are_the_benchmarks_with_its_tuning_ranges():
    harness = load_harness(HARNESS)
    params = _kit("params")
    tuning = yaml.safe_load((BENCHMARK / "tuning.yaml").read_text(encoding="utf-8"))["problem"]["parameters"]
    assert list(harness.settings) == [name for name, *_ in params.PARAMETERS]
    for name, kind, default, _ in params.PARAMETERS:
        parameter = harness.settings[name].parameter
        assert parameter.type == kind.__name__ and parameter.default == default, name
        if kind is not bool:
            assert (parameter.low, parameter.high, parameter.log) == (
                tuning[name]["low"], tuning[name]["high"], tuning[name].get("log", False)), name
        setting = harness.settings[name]
        assert setting.label and setting.help and setting.explain and setting.group, name
    assert set(harness.recommended) == RECOMMENDED
    assert set(harness.templates) == {"tune", "route-costs"}
    assert harness.templates["tune"]["vary"] == {"settings": "recommended"}
    assert len(harness.kpis) == 12 and len(harness.kpi_templates) == 4
    assert set(harness.levers) == {"vehicle_costs", "fleet_size", "shifts", "time_windows", "prizes", "service_times"}
    assert harness.kpis["solver_cost"].changes_with_levers and not harness.kpis["real_cost"].changes_with_levers


def test_ten_tagged_samples_of_60_to_240_tasks():
    assert len(SAMPLES) == 10
    for path in SAMPLES:
        req, tables = _read(path)
        assert req.format == "pyvrp-request/1" and req.time_zero_s == 6 * 3600, path.name
        assert 60 <= len(tables["tasks"]) <= 240, path.name
        assert tables["task_tags"] and tables["vehicle_tags"], path.name
        assert {"van", "box_truck", "evening_van"} <= {vt["class"] for vt in tables["vehicle_types"]}, path.name


@pytest.mark.slow
def test_the_samples_regenerate_byte_for_byte(tmp_path):
    maker = _by_path("pyvrp_harness_make_samples", HARNESS / "samples" / "make_samples.py")
    maker.write(tmp_path)
    for path in SAMPLES:
        assert (tmp_path / path.name).read_bytes() == path.read_bytes().replace(b"\r\n", b"\n"), path.name


# ---------------------------------------------------------------------------
# tables and data changes, in plain Python
# ---------------------------------------------------------------------------


def test_the_tables_speak_metres_seconds_and_currency():
    raw = json.loads(CITY.read_text(encoding="utf-8"))
    req, tables = _read()
    assert tables["request"] == [{"name": "city-60", "format": "pyvrp-request/1", "time_zero_h": 6.0, "load_dims": 2}]
    van = next(vt for vt in raw["vehicle_types"] if vt["class"] == "van")
    row = next(vt for vt in tables["vehicle_types"] if vt["name"] == van["name"])
    assert row["unit_distance_cost"] == pytest.approx(van["unit_distance_cost"] * 1e-4 * 1000)  # per km
    assert row["unit_duration_cost"] == pytest.approx(van["unit_duration_cost"] * 1e-4 * 3600)  # per hour
    assert row["fixed_cost"] == pytest.approx(van["fixed_cost"] * 1e-4)
    assert row["unit_distance_cost"] == pytest.approx(0.3) and row["unit_duration_cost"] == pytest.approx(21.6)
    clients, shipments = raw["clients"], raw["shipments"]
    tasks = {t["task_id"]: t for t in tables["tasks"]}
    assert len(tasks) == len(clients) + 2 * len(shipments)
    first = len(clients)
    pickup, delivery = tasks[first], tasks[first + 1]
    assert (pickup["kind"], delivery["kind"]) == ("shipment_pickup", "shipment_delivery")
    assert pickup["pickup_kg"] == delivery["delivery_kg"] == shipments[0]["amount"][0]
    assert pickup["prize"] == 0 and delivery["prize"] == pytest.approx(shipments[0]["prize"] * 1e-4)
    assert tasks[0]["tw_early_s"] == clients[0]["tw_early"] and tasks[0]["delivery_boxes"] == 0
    tagged = {t["task_id"] for t in tables["task_tags"]}
    assert tagged == {i for i, c in enumerate(clients) if c.get("tags")} | {
        first + 2 * j + k for j, s in enumerate(shipments) if s.get("tags") for k in (0, 1)}


def test_a_changed_request_is_written_and_reads_back_the_same(tmp_path):
    request = _kit("request")
    req, tables = _read()
    changed = copy.deepcopy(tables)
    for vt in changed["vehicle_types"]:
        if vt["class"] == "van":
            vt["unit_distance_cost"] *= 1.37
        if vt["class"] == "evening_van":
            vt["num_available"] = 0
    request.change_windows([t for t in changed["tasks"] if t["kind"] == "delivery"], 1.5, "scale")
    inst, type_ids = request._changed(req, changed, internal=True)
    vans = [vt for vt in inst["vehicle_types"] if vt["class"] == "van"]
    assert vans and all(vt["unit_distance_cost"] == 41 for vt in vans), "3 x 1.37 at ten times the resolution"
    assert not any(vt["class"] == "evening_van" for vt in inst["vehicle_types"]), "no vehicles: out of PyVRP's fleet"
    assert len(type_ids) == len(inst["vehicle_types"]) < len(tables["vehicle_types"])

    request.write(req, changed, str(tmp_path / "changed.json"))
    again = request.read(str(tmp_path / "changed.json"))
    reread = request.tables(again)
    assert again.format == "pyvrp-request/1" and again.time_zero_s == req.time_zero_s
    for before, after in zip(changed["vehicle_types"], reread["vehicle_types"]):
        for column in ("fixed_cost", "unit_distance_cost", "unit_duration_cost", "unit_overtime_cost"):
            assert after[column] == pytest.approx(before[column], abs=0.01), column  # a tenth of a cost unit
        assert after["num_available"] == before["num_available"]
    assert [(t["tw_early_s"], t["tw_late_s"]) for t in reread["tasks"]] == [(t["tw_early_s"], t["tw_late_s"]) for t in changed["tasks"]]
    assert reread["task_tags"] == tables["task_tags"] and reread["vehicle_tags"] == tables["vehicle_tags"]


def test_time_windows_widen_around_their_middle_and_move():
    request = _kit("request")
    rows = [{"tw_early_s": 3600, "tw_late_s": 7200}, {"tw_early_s": 0, "tw_late_s": 1000},
            {"tw_early_s": 100, "tw_late_s": None}]
    request.change_windows(rows, 2.0, "scale")
    assert rows == [{"tw_early_s": 1800, "tw_late_s": 9000}, {"tw_early_s": 0, "tw_late_s": 1500},
                    {"tw_early_s": 100, "tw_late_s": None}]
    request.change_windows(rows, -2000, "add")
    assert rows[:2] == [{"tw_early_s": 0, "tw_late_s": 7000}, {"tw_early_s": 0, "tw_late_s": 0}]
    with pytest.raises(request.evk.InvalidValues, match="widen .scale. or move .add."):
        request.change_windows(rows, 1.0, "set")
    with pytest.raises(request.evk.InvalidValues, match="factor of -1.0"):
        request.change_windows(rows, -1.0, "scale")


@pytest.mark.parametrize("change, says", [
    (lambda t: t["vehicle_types"][0].update(unit_distance_cost=-0.1), "with a negative unit distance cost"),
    (lambda t: [vt.update(num_available=0) for vt in t["vehicle_types"]], "leave no vehicle at all"),
    (lambda t: t["tasks"][0].update(tw_early_s=5000, tw_late_s=4000), "task 0 with the time window 5000 s to 4000 s"),
    (lambda t: t["tasks"][1].update(service_s=-5), "task 1 with a negative service time"),
])
def test_data_changes_that_break_the_request_are_refused_before_solving(change, says):
    request = _kit("request")
    req, tables = _read()
    change(tables)
    with pytest.raises(request.evk.InvalidValues, match=says):
        request.problem(req, tables)


# ---------------------------------------------------------------------------
# with PyVRP
# ---------------------------------------------------------------------------


def _solve(tmp_path: Path, case: Path, *, levers=None, values=None, limit: str = "1"):
    harness = load_harness(HARNESS)
    (tmp_path / "study.json").write_text(json.dumps(_study_run(harness, levers=levers)), encoding="utf-8")
    (tmp_path / "values.json").write_text(json.dumps(values or {}), encoding="utf-8")
    return subprocess.run(
        [PYVRP, str(HARNESS / "runner.py"), "solve", "--case", str(case), "--seed", "1", "--values", "values.json",
         "--study", "study.json", "--time-limit", limit, "--tables-out", "tables.sqlite"],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", timeout=300,
    )


@needs_pyvrp
def test_the_settings_in_cost_units_follow_pyvrps_finer_cost_unit():
    script = (
        "import json, sys; sys.path.insert(0, sys.argv[1]); import pyvrp.search\n"
        "from kit import params\n"
        "p = params.solve_params(params.resolve({'max_penalty': 2e5}), 10)\n"
        "print(json.dumps([p.penalty.min_penalty, p.penalty.max_penalty, p.neighbourhood.weight_wait_time,\n"
        "                  [o.__name__ for o in p.operators] == [o.__name__ for o in pyvrp.search.OPERATORS]]))\n"
    )
    done = subprocess.run([PYVRP, "-c", script, str(HARNESS)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == [1.0, 2e6, 2.0, True], \
        "penalty clamps and the waiting weight x10; at the defaults, PyVRP's own operators"


@needs_pyvrp
def test_a_solved_plan_adds_up(tmp_path):
    done = _solve(tmp_path, CITY, limit="2")
    assert done.returncode == 0, done.stderr
    kpis = json.loads(done.stdout.strip().splitlines()[-1])["kpis"]
    assert kpis["real_cost"] == pytest.approx(kpis["solver_cost"]), "nothing changed: the solver saw the real rates"
    db = sqlite3.connect(tmp_path / "tables.sqlite")
    one = lambda sql: db.execute(sql).fetchone()  # noqa: E731
    try:
        assert one("SELECT (SELECT TOTAL(solver_cost) FROM routes) + (SELECT TOTAL(prize) FROM unassigned)")[0] == \
            pytest.approx(one("SELECT solver_objective FROM summary")[0]), "PyVRP's objective: routes plus prizes forgone"
        assert one("SELECT (SELECT COUNT(*) FROM visits) + (SELECT COUNT(*) FROM unassigned)") == one("SELECT COUNT(*) FROM tasks")
        assert one("SELECT COUNT(*) FROM visits v JOIN routes r USING (route_id) JOIN vehicle_types t ON t.type_id = r.type_id "
                   "WHERE v.load_kg < 0 OR v.load_kg > t.capacity_kg") == (0,)
        assert one("SELECT MIN(seq), COUNT(DISTINCT route_id) FROM visits") == (1, kpis["routes_used"])
    finally:
        db.close()


@needs_pyvrp
def test_a_data_change_moves_what_the_solver_sees_but_not_the_real_rates(tmp_path):
    levers = {"van_km": {"lever": "vehicle_costs", "table": "vehicle_types", "column": "unit_distance_cost",
                         "where": "class = 'van'", "mode": "scale", "code": False, "integer": False}}
    done = _solve(tmp_path, CITY, levers=levers, values={"van_km": 1.5}, limit="2")
    assert done.returncode == 0, done.stderr
    db = sqlite3.connect(tmp_path / "tables.sqlite")
    try:
        rates = db.execute("SELECT t.unit_distance_cost, o.unit_distance_cost FROM vehicle_types t "
                           "JOIN orig_vehicle_types o USING (type_id) WHERE t.class = 'van'").fetchall()
        assert rates and all(new == pytest.approx(1.5 * old) for new, old in rates)
        vans = db.execute("SELECT cost, solver_cost FROM routes WHERE class = 'van' AND distance_m > 0").fetchall()
        assert all(seen > real for real, seen in vans), "a van route costs the solver more than it really does"
    finally:
        db.close()


@needs_pyvrp
def test_a_vrplib_case_is_solved_but_takes_no_data_changes(tmp_path):
    case = tmp_path / "tiny.vrp"
    case.write_text(TINY_VRPLIB, encoding="utf-8")
    inspected = subprocess.run([PYVRP, str(HARNESS / "runner.py"), "inspect", "--case", str(case)],
                               capture_output=True, text=True, timeout=120)
    assert json.loads(inspected.stdout.strip().splitlines()[-1])["summary"] == "5 tasks, 5 vehicles in 1 type, 1 depot"
    solved = _solve(tmp_path, case)
    assert solved.returncode == 0, solved.stderr
    assert json.loads(solved.stdout.strip().splitlines()[-1])["kpis"]["missed_required"] == 0
    fleet = {"fleet": {"lever": "fleet_size", "table": "vehicle_types", "column": "num_available", "where": "1 = 1",
                       "mode": "set", "code": False, "integer": True}}
    refused = _solve(tmp_path, case, levers=fleet, values={"fleet": 3})
    assert refused.returncode == 2
    assert "a VRPLIB case takes no data changes" in refused.stderr.strip().splitlines()[-1]


@needs_pyvrp
@pytest.mark.slow
def test_the_harness_passes_its_own_check():
    checks = check_harness(HARNESS, PYVRP)
    assert exit_code(checks) == 0, [c for c in checks if not c.ok]
    assert any(c.id == "drift" and c.ok for c in checks)


def _study(tmp_path: Path, template: str, time_limit: float) -> Path:
    folder = tmp_path / "study"
    create_study(folder, HARNESS, f"PyVRP {template}", template)
    for name in ("city-60.json", "towns-80.json"):
        shutil.copy(HARNESS / "samples" / name, folder / "cases" / name)
    study = load_study(folder)
    study.application_path = PYVRP
    study.training = ["cases/city-60.json"]
    study.test = ["cases/towns-80.json"]
    study.limits.time_per_case_s = time_limit
    study.budget.hours = 0.1
    study.plan = {"auto": False, "children": 2, "generations": 2}
    save_study(study, folder)
    return folder


def _rows(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (run_dir / "runs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


@needs_pyvrp
@pytest.mark.slow
def test_a_settings_study_runs_end_to_end(tmp_path):
    folder = _study(tmp_path, "tune", 1.0)
    job = run_study(folder, "settings", log=lambda _m: None)
    assert job["phase"] == "done", job
    rows = _rows(folder / "runs" / "settings")
    assert len(rows) >= 3 and all("solver_cost" in row["kpis"] for row in rows if not row["rejected"])
    config = yaml.safe_load((folder / "runs" / "settings" / "evolvekit.yaml").read_text(encoding="utf-8"))
    assert set(config["problem"]["parameters"]) == RECOMMENDED


@needs_pyvrp
@pytest.mark.slow
def test_a_lever_study_runs_end_to_end(tmp_path):
    folder = _study(tmp_path, "route-costs", 2.0)
    job = run_study(folder, "costs", log=lambda _m: None)
    assert job["phase"] == "done", job
    config = yaml.safe_load((folder / "runs" / "costs" / "evolvekit.yaml").read_text(encoding="utf-8"))
    parameters = config["problem"]["parameters"]
    assert set(parameters) == {"van_per_km", "van_per_hour", "truck_per_km", "truck_per_hour",
                               "evening_per_km", "evening_per_hour"}
    assert all((p["low"], p["high"]) == (0.5, 2.0) for p in parameters.values())
    rows = _rows(folder / "runs" / "costs")
    assert rows and all("deliveries_per_hour" in row["kpis"] for row in rows if not row["rejected"])
