"""Every evaluator run records how busy the machine was while it ran, and the
status document says when that was more than the run itself explains.

Learnt the hard way: a test suite pinned to the one free core of a laptop cost
three pinned, time-limited solver runs 15-30 % of their iterations, and nothing
anywhere said so.
"""

from __future__ import annotations

import sys
import time

import pytest

from evolvekit.config import StageConfig
from evolvekit.evaluate.hostload import HostLoad, cpu_times
from evolvekit.evaluate.stages import run_command_stage
from evolvekit.status import build_status, render_text
from tests.test_status import NOW, RunDir

SUPPORTED = sys.platform == "win32" or sys.platform.startswith("linux")


@pytest.mark.skipif(not SUPPORTED, reason="no cheap system-wide CPU counter on this platform")
def test_the_counters_only_ever_go_up_and_busy_is_part_of_total():
    first = cpu_times()
    deadline = time.perf_counter() + 0.3
    while time.perf_counter() < deadline:  # be busy, so that there is something to count
        sum(range(1000))
    second = cpu_times()
    assert first is not None and second is not None
    assert second[1] > first[1] and second[0] >= first[0]
    assert 0 <= second[0] - first[0] <= second[1] - first[1]


@pytest.mark.skipif(not SUPPORTED, reason="no cheap system-wide CPU counter on this platform")
def test_a_busy_interval_reads_as_a_share_between_nothing_and_everything():
    load = HostLoad().start()
    deadline = time.perf_counter() + 0.5
    while time.perf_counter() < deadline:
        sum(range(1000))
    share = load.stop()
    assert share is not None and 0.0 < share <= 1.0


def test_a_platform_that_cannot_say_says_none(monkeypatch):
    from evolvekit.evaluate import hostload

    monkeypatch.setattr(hostload, "cpu_times", lambda: None)
    assert HostLoad().start().stop() is None


def test_every_evaluator_run_reports_it(tmp_path):
    (tmp_path / "solver.py").write_text("import json\nprint(json.dumps({'cost': 1.0}))\n", encoding="utf-8")
    stage = StageConfig.parse(
        {"id": "full", "kind": "command", "kpis_from": "stdout", "command": "{python} solver.py {candidate}"}, 1
    )
    candidate = tmp_path / "candidate.py"
    candidate.write_text("", encoding="utf-8")
    seen: list[dict] = []
    outcome = run_command_stage(
        candidate, stage, inputs=(), out_path=tmp_path / "out" / "o.json", cwd=tmp_path,
        observer=lambda type, **fields: seen.append({"type": type, **fields}),
    )
    finished = [e for e in seen if e["type"] == "eval_finished"][0]
    assert "host_busy" in finished and finished["host_busy"] == outcome.host_busy
    if SUPPORTED:
        assert outcome.host_busy is not None and 0.0 <= outcome.host_busy <= 1.0


# -- what the status document makes of it ----------------------------------


def _run_with_loads(tmp_path, loads: list[float], *, workers: int = 3, cpus: int | None = 8) -> RunDir:
    run = RunDir(tmp_path)
    extra = {} if cpus is None else {"cpus": cpus}
    run.event(
        "run_started", 1000, objective="cost", direction="minimize", first_generation=1, generations_planned=3,
        stages=[{"id": "full", "kind": "command", "timeout_s": 900, "seeds": 1, "final": True, "workers": workers,
                 "instances": ["a", "b"], "normalize": "baseline"}],
        **extra,
    )
    for index, load in enumerate(loads):
        run.event("eval_finished", 900 - index, candidate_id=f"g001-c{index + 2:04d}", stage="full", seed=0,
                  private=False, instance="a", attempt=0, ok=True, duration_s=600.0, kpis={"cost": 1.0},
                  host_busy=load)
    return run


def test_runs_that_shared_the_machine_are_counted_and_named(tmp_path):
    # Three single-threaded workers on eight logical CPUs explain 0.375.
    _run_with_loads(tmp_path, [0.38, 0.39, 0.37, 0.40, 0.55, 0.62, 0.38])
    document = build_status(tmp_path, now=NOW)
    host = document["health"]["host"]
    assert host["available"] and host["cpus"] == 8 and host["flagged"] == 2 and host["measured"] == 7
    stage = host["stages"][0]
    assert stage["explained"] == pytest.approx(0.375) and stage["flagged"] == 2 and not stage["busier_throughout"]
    assert [e["candidate_id"] for e in host["examples"]] == ["g001-c0006", "g001-c0007"]
    assert "2 of 7 run(s) shared the machine with something else" in render_text(document)


def test_a_quiet_machine_is_not_mentioned(tmp_path):
    _run_with_loads(tmp_path, [0.38, 0.39, 0.37, 0.41])
    document = build_status(tmp_path, now=NOW)
    assert document["health"]["host"]["flagged"] == 0
    assert "host load" not in render_text(document)


def test_a_machine_that_is_busier_than_explained_throughout_is_said_so_once(tmp_path):
    # A solver with four threads of its own, or a build running the whole time:
    # no single run stands out, but the level does.
    _run_with_loads(tmp_path, [0.81, 0.80, 0.82, 0.79])
    document = build_status(tmp_path, now=NOW)
    stage = document["health"]["host"]["stages"][0]
    assert stage["flagged"] == 0 and stage["busier_throughout"] is True
    assert "busier than explained throughout" in render_text(document)


def test_a_cached_result_and_an_old_run_directory_have_no_load_to_speak_of(tmp_path):
    run = _run_with_loads(tmp_path, [0.9], cpus=None)  # written before `cpus` was recorded
    run.event("eval_finished", 10, candidate_id="g001-c0009", stage="full", seed=0, private=False,
              instance="b", attempt=0, ok=True, duration_s=0.0, kpis={"cost": 1.0}, cached=True, host_busy=None)
    assert build_status(tmp_path, now=NOW)["health"]["host"] == {"available": False, "flagged": 0, "stages": []}
