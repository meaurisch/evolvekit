"""`budget.max_hours` and stop requests.

A time limit is the active run time across sessions. Before each generation
the run checks whether another one would still finish in time; when the limit
is reached in the middle of one, what is running is stopped and counted as
*abandoned* -- not failed -- and the cut generation is finished first when the
run is resumed. A `stop-request` file in the run directory has the same effect:
it is how a program that started the run as a detached process stops it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from evolvekit import cli
from evolvekit.config import ConfigError, build_config
from evolvekit.events import read_events
from evolvekit.search.driver import Driver
from evolvekit.stopping import STOP_REQUEST, RunStop, active_seconds


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# -- RunStop -------------------------------------------------------------------


def test_the_time_limit_counts_what_earlier_sessions_used(tmp_path):
    clock = Clock()
    stop = RunStop(tmp_path, max_hours=1.0, clock=clock)
    stop.start(used_s=3000.0, watch=False)
    clock.now += 599.0
    assert stop.poll() is None and stop.elapsed_s() == pytest.approx(3599.0)
    clock.now += 1.0
    assert stop.poll().startswith("time limit reached: 1.00 h of active run time")
    assert stop.triggered and stop.event.is_set()


def test_a_round_that_would_not_finish_in_time_is_not_started(tmp_path):
    clock = Clock()
    stop = RunStop(tmp_path, max_hours=1.0, clock=clock)
    stop.start(used_s=0.0, watch=False)
    clock.now += 3000.0
    assert stop.before_generation(projected_s=500.0) is None
    reason = stop.before_generation(projected_s=700.0)
    assert reason.startswith("time limit: another round would not finish within 1 h (0.83 h used")
    assert not stop.triggered, "nothing is running: the run simply does not start another round"


def test_a_first_round_that_would_not_finish_says_it_is_a_projection(tmp_path):
    # Persona p5 fix round 1, M1: with only the starting point measured no round "has taken" anything.
    clock = Clock()
    stop = RunStop(tmp_path, max_hours=0.124, clock=clock)
    stop.start(used_s=36.0, watch=False)
    reason = stop.before_generation(projected_s=432.0, first=True)
    assert reason == ("time limit: its first round would not finish within 0.124 h "
                      "(0.01 h used; its first round would take about 0.12 h)")
    assert "has taken" not in reason


def test_without_a_limit_nothing_is_projected(tmp_path):
    stop = RunStop(tmp_path, max_hours=None, clock=Clock())
    stop.start(used_s=1e9, watch=False)
    assert stop.before_generation(projected_s=1e9) is None


def test_a_stop_request_stops_the_run_and_is_then_removed(tmp_path):
    stop = RunStop(tmp_path, max_hours=None, clock=Clock())
    stop.start(used_s=0.0, watch=False)
    (tmp_path / STOP_REQUEST).write_text("please", encoding="utf-8")
    assert stop.before_generation(projected_s=None) == "stopped on request"
    stop.close()
    assert not (tmp_path / STOP_REQUEST).exists(), "consumed: the next session must not stop at once"


def test_a_request_that_did_not_stop_the_run_is_left_for_the_next_session(tmp_path):
    clock = Clock()
    stop = RunStop(tmp_path, max_hours=1.0, clock=clock)
    stop.start(used_s=3600.0, watch=False)
    assert stop.poll().startswith("time limit reached")
    (tmp_path / STOP_REQUEST).write_text("please", encoding="utf-8")
    stop.close()
    assert (tmp_path / STOP_REQUEST).exists()


def test_the_watcher_notices_a_request_on_its_own(tmp_path):
    stop = RunStop(tmp_path, max_hours=None, interval=0.02)
    stop.start(used_s=0.0)
    (tmp_path / STOP_REQUEST).write_text("please", encoding="utf-8")
    assert stop.event.wait(5.0)
    assert stop.reason == "stopped on request"
    stop.close()


def test_active_seconds_add_up_the_sessions_and_skip_the_nights():
    events = [
        {"session": "a", "ts": "2026-09-26T10:00:00.000+00:00"},
        {"session": "a", "ts": "2026-09-26T10:30:00.000+00:00"},
        {"session": "b", "ts": "2026-09-27T09:00:00.000+00:00"},
        {"session": "b", "ts": "2026-09-27T09:10:00.000+00:00"},
        {"session": "b", "ts": "not a time"},
    ]
    assert active_seconds(events) == 2400.0
    assert active_seconds(events, exclude_session="b") == 1800.0


def test_max_hours_is_a_positive_number(tmp_path):
    raw = _raw(max_hours=0)
    with pytest.raises(ConfigError, match=r"budget\.max_hours: must be > 0"):
        build_config(raw, base_dir=tmp_path)


# -- a run ---------------------------------------------------------------------

# The solver is steered by `control.json` in its working directory, so that
# the command -- part of what a run directory is a run of -- never changes
# between the sessions of one test.
SOLVER = """\
import argparse, json, pathlib, time
p = argparse.ArgumentParser(); p.add_argument('--x', type=float); a = p.parse_args()
control = json.loads(pathlib.Path('control.json').read_text()) if pathlib.Path('control.json').exists() else {}
calls = pathlib.Path('calls.txt')
n = int(calls.read_text()) + 1 if calls.exists() else 1
calls.write_text(str(n))
if n == control.get('request_at'):
    pathlib.Path(control['request']).write_text('stop')
if n >= control.get('hang_from', 10 ** 9):
    time.sleep(30)
time.sleep(control.get('sleep', 0.0))
print(json.dumps({'cost': 100 + abs(a.x - 0.3)}))
"""


def _raw(*, max_hours=None, children=2, generations=2):
    raw = {
        "problem": {"parameters": {"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.9}}},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static"},
                {"id": "full", "kind": "command", "kpis_from": "stdout", "command": "{python} solver.py {params}",
                 "timeout": 60},
            ],
            "score": {"objective": "cost", "direction": "minimize"},
        },
        "search": {"operators": {"param_lhs": 1.0}, "children_per_generation": children, "generations": generations,
                   "seed": 1},
        "stop": {"patience": 20},
    }
    if max_hours is not None:
        raw["budget"] = {"max_hours": max_hours}
    return raw


def _driver(tmp_path: Path, control: dict, **raw) -> Driver:
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    (tmp_path / "control.json").write_text(json.dumps(control), encoding="utf-8")
    return Driver(build_config(_raw(**raw), base_dir=tmp_path), run_dir=tmp_path / "run")


@pytest.mark.slow
def test_a_stop_request_ends_the_run_within_seconds_and_the_cut_generation_is_finished_on_resume(tmp_path):
    run = tmp_path / "run"
    # Call 1 is the seed; call 3 is generation 1's second child: it asks the
    # run to stop and then hangs, so only the stop can end it in time.
    driver = _driver(tmp_path, {"request_at": 3, "hang_from": 3, "request": (run / STOP_REQUEST).as_posix()})
    began = time.monotonic()
    summary = driver.run()
    assert time.monotonic() - began < 20, "the hanging evaluation was stopped, not waited for"
    assert summary.stop_reason == "stopped on request" and not summary.aborted
    assert [r["generation"] for r in driver.ledger.runs()] == [0], "nothing of the cut generation is recorded"
    pending = json.loads((run / "pending.json").read_text(encoding="utf-8"))
    assert pending["generation"] == 1
    finished = [e for e in read_events(run) if e["type"] == "eval_finished"]
    assert any(e.get("abandoned") for e in finished)
    assert not [e for e in finished if not e["ok"] and not e.get("abandoned")], "abandoned is not failed"
    assert not [e for e in read_events(run) if e["type"] == "generation_finished" and e["generation"] == 1]
    assert not (run / STOP_REQUEST).exists()

    resumed = _driver(tmp_path, {})
    summary = resumed.run()
    assert summary.stop_reason == "generations exhausted"
    assert any(e["type"] == "generation_adopted" and e["generation"] == 1 for e in read_events(run))
    rows = resumed.ledger.runs()
    assert sorted({r["generation"] for r in rows}) == [0, 1, 2]
    assert {r["id"] for r in rows if r["generation"] == 1} == {c["id"] for c in pending["children"]}


@pytest.mark.slow
def test_a_round_that_would_not_finish_within_the_limit_is_not_started(tmp_path):
    # The seed takes over a second; four children at that pace cannot fit in
    # the four seconds the run is given.
    driver = _driver(tmp_path, {"sleep": 1.0}, max_hours=4 / 3600, children=4)
    summary = driver.run()
    assert summary.stop_reason.startswith("time limit: its first round would not finish within")
    assert "its first round would take about" in summary.stop_reason
    assert summary.generations == 0 and [r["generation"] for r in driver.ledger.runs()] == [0]


# -- the first round, projected from the seed (persona p5, J1) ------------------

STAGED_SOLVER = """\
import argparse, json, pathlib, time
p = argparse.ArgumentParser(); p.add_argument('--x', type=float); p.add_argument('--case'); p.add_argument('--stage')
a = p.parse_args()
control = json.loads(pathlib.Path('control.json').read_text()) if pathlib.Path('control.json').exists() else {}
time.sleep(control.get('sleep_' + a.stage, 0.0))
print(json.dumps({'cost': 100 + abs(a.x - 0.3) + len(a.case)}))
"""


def _staged_raw(*, children: int, max_hours: float | None, screen_cases: int, full_cases: int, workers: int,
                top_k: int = 2, operators: dict | None = None) -> dict:
    """A screening study as the plan compiles one: every child on a few cases first, the best `top_k` on all."""
    def stage(name: str, cases: int, **more) -> dict:
        return {"id": name, "kind": "command", "kpis_from": "stdout", "workers": workers, "timeout": 60,
                "command": "{python} solver.py {params} --case {instance} --stage " + name,
                "instances": [f"case-{i}" for i in range(cases)], **more}

    raw = _raw(max_hours=max_hours, children=children, generations=6)
    raw["evaluate"]["stages"] = [{"id": "static", "kind": "builtin-static"},
                                 stage("screen", screen_cases, promote={"top_k_per_generation": top_k}),
                                 stage("full", full_cases)]
    if operators:
        raw["search"]["operators"] = operators
    return raw


def test_the_first_round_is_projected_stage_by_stage_from_the_seed(tmp_path):
    # The intern's run (persona p5, J1): 12 combinations, each screened on 2 cases, the best 2 on all 6, on 3
    # workers. The seed took 36 s (4.4 s screening, 31.3 s on the 6 cases); 12 times that is 0.12 h, and the
    # round was refused with 0.11 h left. A round of 12 runs about 8 screening waves and 4 full ones: 101 s,
    # as the four rounds that ran later each took (96 to 101 s).
    driver = Driver(build_config(_staged_raw(children=12, max_hours=0.124, screen_cases=2, full_cases=6, workers=3),
                                 base_dir=tmp_path), run_dir=tmp_path / "run")
    driver._durations = {0: 35.963}
    driver._seed_stages = {"screen": 4.365, "full": 31.343}
    projected = driver._projected_s()
    assert projected == pytest.approx(8 * 4.365 + 4 * 31.343 / 2 + 12 * (35.963 - 4.365 - 31.343), abs=0.01)
    assert 95 < projected < 110
    driver.run_stop.start(used_s=36.0, watch=False)
    assert driver.run_stop.before_generation(projected) is None, "a round of 101 s fits in the 410 s left"


def test_without_the_seeds_stages_the_round_is_projected_as_the_seed_times_the_breadth(tmp_path):
    driver = Driver(build_config(_staged_raw(children=12, max_hours=0.124, screen_cases=2, full_cases=6, workers=3),
                                 base_dir=tmp_path), run_dir=tmp_path / "run")
    driver._durations = {0: 35.963}
    assert driver._projected_s() == pytest.approx(12 * 35.963), "an upper bound, as before"
    driver._durations = {0: 35.963, 1: 100.75, 2: 101.0}
    assert driver._projected_s() == pytest.approx(101.0), "once rounds ran, the longest of the last two"


def test_a_search_with_ai_adds_the_time_its_changes_take_to_the_first_round(tmp_path):
    from evolvekit.stopping import AI_CHANGE_S, ai_seconds

    from dataclasses import replace

    raw = _staged_raw(children=4, max_hours=1.0, screen_cases=2, full_cases=2, workers=2)
    driver = Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run")
    # Only the shares matter here: the projection reads them, as the plan does (`plan.ai_seconds`).
    driver.config = replace(driver.config, search=replace(driver.config.search,
                                                          operators={"param_local": 0.5, "diff": 0.5}))
    driver._durations, driver._seed_stages = {0: 3.0}, {"screen": 1.0, "full": 2.0}
    assert ai_seconds({"param_local": 0.5, "diff": 0.5})[0] == pytest.approx(AI_CHANGE_S / 2)
    # 4 children x 2 cases on 2 workers: 4 screening waves; the best 2 x 2 cases: 2 full waves; and the AI.
    assert driver._projected_s() == pytest.approx(4 * 1.0 + 2 * 2.0 + 4 * AI_CHANGE_S / 2)


def test_the_seeds_stages_are_read_back_from_the_events_of_earlier_sessions():
    from evolvekit.search.driver import _seed_stage_durations

    events = [
        {"type": "generation_started", "generation": 0},
        {"type": "stage_finished", "stage": "screen", "private": False, "duration_s": 4.365},
        {"type": "stage_finished", "stage": "full", "private": False, "duration_s": 31.343},
        {"type": "generation_finished", "generation": 0, "duration_s": 35.963},
        {"type": "generation_started", "generation": 1},
        {"type": "stage_finished", "stage": "screen", "private": False, "duration_s": 35.2},
        {"type": "stage_finished", "stage": "full", "private": True, "duration_s": 9.0},
    ]
    assert _seed_stage_durations(events) == {"screen": 4.365, "full": 31.343}


@pytest.mark.slow
def test_a_screening_run_with_time_for_a_round_searches_it(tmp_path):
    # The seed takes about 2 s (0.1 s screening, 1.5 s on both cases); six times that (12 s) would not fit in
    # the 8 s the run has, but a round of six screened on 2 workers and one promoted takes about 3.5 s.
    (tmp_path / "solver.py").write_text(STAGED_SOLVER, encoding="utf-8")
    (tmp_path / "control.json").write_text(json.dumps({"sleep_screen": 0.1, "sleep_full": 1.5}), encoding="utf-8")
    raw = _staged_raw(children=6, max_hours=8 / 3600, screen_cases=2, full_cases=2, workers=2, top_k=1)
    driver = Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run")
    summary = driver.run()
    assert summary.generations >= 1, summary.stop_reason
    assert 1 in {r["generation"] for r in driver.ledger.runs()}


@pytest.mark.slow
def test_the_time_limit_stops_a_generation_in_the_middle_and_a_resumed_run_stays_stopped(tmp_path):
    run = tmp_path / "run"
    driver = _driver(tmp_path, {"hang_from": 2}, max_hours=6 / 3600, children=1)
    began = time.monotonic()
    summary = driver.run()
    assert 5 < time.monotonic() - began < 20
    assert summary.stop_reason.startswith("time limit reached")
    assert [r["generation"] for r in driver.ledger.runs()] == [0]
    assert any(e.get("abandoned") for e in read_events(run) if e["type"] == "eval_finished")

    again = _driver(tmp_path, {}, max_hours=6 / 3600, children=1)
    assert again.run().stop_reason.startswith("time limit"), "the hours are the run's, not the session's"
    assert [r["generation"] for r in again.ledger.runs()] == [0]

    more = _driver(tmp_path, {}, max_hours=1.0, children=1)
    assert more.run().stop_reason == "generations exhausted"
    assert sorted({r["generation"] for r in more.ledger.runs()}) == [0, 1, 2]


def test_evolvekit_stop_writes_the_request(tmp_path):
    (tmp_path / "run").mkdir()
    assert cli.main(["stop", "--run-dir", str(tmp_path / "run")]) == 0
    assert (tmp_path / "run" / STOP_REQUEST).is_file()
    assert cli.main(["stop", "--run-dir", str(tmp_path / "missing")]) == 1


# -- status ----------------------------------------------------------------------

from evolvekit.status import build_status, render_text  # noqa: E402


@pytest.mark.slow
def test_status_counts_abandoned_evaluations_apart_and_shows_the_time_limit(tmp_path):
    run = tmp_path / "run"
    driver = _driver(tmp_path, {"request_at": 3, "hang_from": 3, "request": (run / STOP_REQUEST).as_posix()},
                     max_hours=1.0)
    driver.run()
    document = build_status(run)
    health = document["health"]
    assert health["state"] == "finished" and health["stop_reason"] == "stopped on request"
    evaluations = health["evaluations"]
    assert evaluations["abandoned"] >= 1 and evaluations["failed"] == 0
    assert evaluations["failure_reasons"] == [] and document["failures"] == []
    limit = next(item for item in health["limits"] if item["name"] == "budget.max_hours")
    assert limit["cap"] == 1.0 and limit["unit"] == "hours" and 0 < limit["used"] < 0.1
    assert all(entry["failed"] == 0 for entry in document["progress"]["generations"])
    text = render_text(document)
    assert "abandoned (the run was stopped" in text and "failed" in text