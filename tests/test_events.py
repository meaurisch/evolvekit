"""The event log and the heartbeat: what a run says about itself while it runs.

`runs.jsonl` gets a row when a generation's whole cascade has returned. With an
evaluator that takes an hour that is one write every few hours, and between
writes nothing in the run directory changes: no way to tell a run that is
working from one that died, which candidate is being evaluated, or what failed.
These tests pin down the record that fills that gap.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from evolvekit import events as events_module
from evolvekit.config import load_config
from evolvekit.events import EventLog, Heartbeat, read_events, read_heartbeat
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.driver import Driver
from tests.conftest import EXAMPLE_CONFIG

# -- the log ---------------------------------------------------------------


def test_every_event_is_one_json_line_with_a_sequence_a_time_and_a_session(tmp_path):
    log = EventLog(tmp_path)
    first = log.emit("run_started", generations_planned=6)
    second = log.emit("generation_started", generation=1)

    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [first, second]
    assert (first["seq"], second["seq"]) == (1, 2)
    assert first["type"] == "run_started" and first["generations_planned"] == 6
    assert first["session"] == second["session"] == log.session
    assert first["pid"] == os.getpid()
    assert first["ts"].endswith("+00:00") and "." in first["ts"]  # UTC, sub-second


def test_a_second_session_continues_the_numbering_and_names_itself(tmp_path):
    first = EventLog(tmp_path)
    first.emit("run_started")
    first.emit("run_finished")
    second = EventLog(tmp_path)
    event = second.emit("run_started")
    assert event["seq"] == 3
    assert second.session != first.session
    assert [e["seq"] for e in read_events(tmp_path)] == [1, 2, 3]


def test_a_torn_last_line_is_skipped_and_does_not_break_the_numbering(tmp_path):
    log = EventLog(tmp_path)
    log.emit("run_started")
    with (tmp_path / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 2, "type": "eval_fin')  # the process died here
    resumed = EventLog(tmp_path)
    event = resumed.emit("run_started")
    events = read_events(tmp_path)
    assert [e["type"] for e in events] == ["run_started", "run_started"]
    assert event["seq"] == 2
    # The torn fragment must not swallow the line that follows it.
    assert events[-1]["session"] == resumed.session


def test_events_from_several_threads_never_interleave(tmp_path):
    log = EventLog(tmp_path)

    def worker(index: int) -> None:
        for step in range(50):
            log.emit("eval_finished", worker=index, step=step, note="x" * 200)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    events = read_events(tmp_path)
    assert len(events) == 300
    assert sorted(e["seq"] for e in events) == list(range(1, 301))


def test_values_json_cannot_hold_are_written_as_text_rather_than_lost(tmp_path):
    log = EventLog(tmp_path)
    log.emit("eval_finished", path=Path("work") / "out.json", bad=float("nan"))
    (event,) = read_events(tmp_path)
    assert event["path"] == str(Path("work") / "out.json")
    assert event["bad"] is None  # NaN is not JSON; a reader must never choke on it


def test_reading_a_directory_that_has_no_log_is_empty_not_an_error(tmp_path):
    assert read_events(tmp_path / "nowhere") == []
    assert read_heartbeat(tmp_path / "nowhere") is None


# -- the heartbeat ---------------------------------------------------------


def _wait_for(predicate, within: float = 5.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_the_heartbeat_keeps_beating_and_carries_the_current_state(tmp_path):
    beat = Heartbeat(tmp_path, session="s1", interval=0.05)
    beat.start(phase="starting")
    try:
        assert _wait_for(lambda: read_heartbeat(tmp_path) is not None)
        first = read_heartbeat(tmp_path)
        assert first["pid"] == os.getpid() and first["session"] == "s1"
        assert first["phase"] == "starting" and first["interval_s"] == 0.05

        beat.update(phase="evaluating", generation=3)
        assert _wait_for(lambda: read_heartbeat(tmp_path).get("generation") == 3)
        later = read_heartbeat(tmp_path)
        assert later["phase"] == "evaluating"
        assert later["beats"] > first["beats"]
    finally:
        beat.stop(phase="finished")
    final = read_heartbeat(tmp_path)
    assert final["phase"] == "finished"
    beats = final["beats"]
    time.sleep(0.2)
    assert read_heartbeat(tmp_path)["beats"] == beats, "it kept beating after stop()"


def test_a_beat_that_cannot_be_written_does_not_kill_the_heartbeat(tmp_path, monkeypatch):
    """On Windows `os.replace` fails while a reader holds the file open."""
    real = events_module._atomic_write
    failures = {"left": 3}

    def flaky(path, text):
        if failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError("sharing violation")
        real(path, text)

    monkeypatch.setattr(events_module, "_atomic_write", flaky)
    beat = Heartbeat(tmp_path, session="s1", interval=0.02)
    beat.start(phase="starting")
    try:
        assert _wait_for(lambda: read_heartbeat(tmp_path) is not None)
        assert failures["left"] == 0
    finally:
        beat.stop()


# -- the driver writes them ------------------------------------------------


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(config, search=replace(config.search, scratchpad_every=0))
    run_dir = tmp_path_factory.mktemp("events-demo")
    driver = Driver(config, run_dir=run_dir)
    summary = driver.run(generations=2)
    return driver, summary, read_events(run_dir)


@pytest.mark.slow
def test_a_run_opens_with_what_it_is_and_closes_with_why_it_stopped(demo):
    driver, summary, events = demo
    assert events[0]["type"] == "run_started"
    started = events[0]
    assert started["objective"] == "excess_pct" and started["direction"] == "minimize"
    assert started["generations_planned"] == 2 and started["first_generation"] == 1
    assert started["resumed"] is False
    assert [s["id"] for s in started["stages"]] == ["static", "proxy", "full"]
    assert started["stages"][-1]["final"] is True
    assert started["stages"][1]["timeout_s"] == 120
    assert started["budget"]["max_usd"] == 1.0
    assert started["config_path"].endswith("evolvekit.yaml")

    assert events[-1]["type"] == "run_finished"
    assert events[-1]["stop_reason"] == summary.stop_reason == "generations exhausted"
    assert events[-1]["best_id"] == summary.best.id


@pytest.mark.slow
def test_generations_and_children_are_announced_as_they_happen(demo):
    _driver, _summary, events = demo
    kinds = [e["type"] for e in events]
    assert kinds.count("generation_started") == 3  # the seed generation and two more
    assert kinds.count("generation_finished") == 3
    for generation in (0, 1, 2):
        start = next(
            i for i, e in enumerate(events)
            if e["type"] == "generation_started" and e["generation"] == generation
        )
        finish = next(
            i for i, e in enumerate(events)
            if e["type"] == "generation_finished" and e["generation"] == generation
        )
        assert start < finish
        assert events[finish]["duration_s"] >= 0
    bred = [e for e in events if e["type"] == "candidate_bred"]
    assert bred and all(e["generation"] >= 1 for e in bred)
    assert {"candidate_id", "operator", "parent_id", "ok"} <= set(bred[0])


@pytest.mark.slow
def test_every_evaluator_run_is_bracketed_by_a_start_and_a_finish(demo):
    driver, _summary, events = demo
    started = [e for e in events if e["type"] == "eval_started"]
    finished = [e for e in events if e["type"] == "eval_finished"]
    assert started and len(started) == len(finished)
    key = lambda e: (e["candidate_id"], e["stage"], e["seed"], e["private"])  # noqa: E731
    assert sorted(map(key, started)) == sorted(map(key, finished))

    seed_full = next(
        e for e in finished
        if e["candidate_id"] == "g000-c0001" and e["stage"] == "full" and not e["private"]
    )
    assert seed_full["ok"] is True and seed_full["duration_s"] > 0
    assert seed_full["kpis"]["excess_pct"] == pytest.approx(5.871, abs=0.001)
    assert any(e["private"] for e in finished), "the hold-out run left no trace"
    # The logs PR #15 keeps are addressable from the event, relative to the run dir.
    assert (driver.ledger.run_dir / seed_full["stderr_log"]).is_file()


@pytest.mark.slow
def test_the_console_lines_are_in_the_log_too(demo):
    _driver, _summary, events = demo
    messages = [e["message"] for e in events if e["type"] == "log"]
    assert any(m.startswith("gen 0  seed") for m in messages)
    assert any(m.startswith("gen 2 ") for m in messages)


@pytest.mark.slow
def test_the_heartbeat_ends_in_the_state_the_run_ended_in(demo):
    driver, _summary, _events = demo
    beat = read_heartbeat(driver.ledger.run_dir)
    assert beat["phase"] == "finished" and beat["pid"] == os.getpid()


def _crashing_project(tmp_path: Path):
    from evolvekit.config import build_config

    (tmp_path / "skeleton.py").write_text(
        "# EVOLVE-BLOCK-START\nCOST = 40\n\n\ndef f():\n    return COST\n# EVOLVE-BLOCK-END\n",
        encoding="utf-8",
    )
    (tmp_path / "evaluate.py").write_text(
        "import json, sys\n"
        "src = open(sys.argv[1], encoding='utf-8').read()\n"
        "if 'BOOM' in src:\n"
        "    sys.exit('solver: segmentation fault in neighbourhood 7')\n"
        "open(sys.argv[2], 'w').write(json.dumps({'kpis': {'cost': 40.0}}))\n",
        encoding="utf-8",
    )
    reply = "```python\n# BOOM\nCOST = 39\n\n\ndef f():\n    return COST\n```"
    model = {"provider": "fake", "options": {"responses": [reply]}}
    raw = {
        "problem": {"skeleton": "skeleton.py", "required_functions": ["f"]},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static"},
                {
                    "id": "full",
                    "kind": "command",
                    "command": f'"{sys.executable}" evaluate.py {{candidate}} {{out}}',
                    "timeout": 60,
                },
            ],
            "score": {"objective": "cost", "direction": "minimize"},
        },
        "models": {"small": {**model, "model": "s"}, "strong": {**model, "model": "b"}},
        "search": {
            "children_per_generation": 1,
            "generations": 1,
            "big_step_every": 99,
            "scratchpad_every": 0,
            "operators": {"rewrite": 1.0},
            "novelty": {"near": {"method": "off"}},
        },
    }
    return build_config(raw, base_dir=tmp_path)


@pytest.mark.slow
def test_a_failed_evaluation_says_what_ran_and_what_it_printed(tmp_path):
    config = _crashing_project(tmp_path)
    Driver(config, run_dir=tmp_path / "run").run()
    failed = [
        e for e in read_events(tmp_path / "run")
        if e["type"] == "eval_finished" and not e["ok"]
    ]
    assert len(failed) == 1
    event = failed[0]
    assert event["failure"] == "exit code 1"
    assert "segmentation fault in neighbourhood 7" in event["stderr_tail"]
    assert event["argv"][1] == "evaluate.py"
    assert event["candidate_id"] == "g001-c0002" and event["stage"] == "full"


@pytest.mark.slow
def test_an_interrupted_run_says_so_and_releases_its_lock(tmp_path):
    config = load_config(EXAMPLE_CONFIG)

    class Interrupting(FakeProvider):
        def complete(self, *args, **kwargs):
            raise KeyboardInterrupt

    provider = Interrupting(["never reached"])
    driver = Driver(
        config, run_dir=tmp_path / "run", providers={"small": provider, "strong": provider}
    )
    with pytest.raises(KeyboardInterrupt):
        driver.run()
    events = read_events(tmp_path / "run")
    assert events[-1]["type"] == "run_interrupted"
    assert read_heartbeat(tmp_path / "run")["phase"] == "interrupted"
    assert not (tmp_path / "run" / ".lock").exists()


@pytest.mark.slow
def test_a_crash_inside_the_loop_is_recorded_before_it_propagates(tmp_path):
    config = load_config(EXAMPLE_CONFIG)

    class Exploding(FakeProvider):
        def complete(self, *args, **kwargs):
            raise RuntimeError("the unexpected")

    provider = Exploding(["never reached"])
    driver = Driver(
        config, run_dir=tmp_path / "run", providers={"small": provider, "strong": provider}
    )
    with pytest.raises(RuntimeError):
        driver.run()
    last = read_events(tmp_path / "run")[-1]
    assert last["type"] == "run_crashed"
    assert "RuntimeError: the unexpected" in last["error"]
    assert read_heartbeat(tmp_path / "run")["phase"] == "crashed"
