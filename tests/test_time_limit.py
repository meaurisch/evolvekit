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
    assert summary.stop_reason.startswith("time limit: another round would not finish within")
    assert summary.generations == 0 and [r["generation"] for r in driver.ledger.runs()] == [0]


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