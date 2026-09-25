"""A stage timeout has to be a wall-clock bound on the whole process tree.

An evaluator is rarely one process. It is a wrapper that starts a solver, or a
script that re-launches itself in another interpreter (the PyVRP example does
exactly that). `subprocess.run(timeout=...)` kills the direct child only, and
with pipes attached it then waits for *every* process holding the other end:

* a hung solver outlived its timeout as an orphan, still eating a core during
  the next candidate's time-limited evaluation;
* a finished evaluation whose grandchild kept stdout open was held until the
  timeout fired and was then reported as a timeout.

The tests that wait on the clock are `slow`; the rest are not.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from evolvekit.config import StageConfig
from evolvekit.evaluate import run_command_stage
from evolvekit.evaluate.process import read_tail, run_bounded
from evolvekit.lock import pid_alive

GRANDCHILD = "import time; time.sleep(60)"


def _stage(script: Path, timeout: float) -> StageConfig:
    return StageConfig(
        id="full",
        kind="command",
        command=f'"{sys.executable}" "{script}" {{candidate}} {{out}}',
        timeout=timeout,
    )


def _wait_until_dead(pid: int, within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.1)
    return not pid_alive(pid)


def _run(tmp_path: Path, body: str, timeout: float):
    script = tmp_path / "evaluator.py"
    script.write_text(body, encoding="utf-8")
    started = time.monotonic()
    outcome = run_command_stage(
        tmp_path / "cand.py",
        _stage(script, timeout),
        inputs=[],
        out_path=tmp_path / "out.json",
        cwd=tmp_path,
    )
    return outcome, time.monotonic() - started


def _grandchild_pid(tmp_path: Path) -> int:
    return int((tmp_path / "grandchild.pid").read_text(encoding="utf-8"))


@pytest.mark.slow
def test_a_timeout_kills_the_solver_the_evaluator_started(tmp_path):
    outcome, elapsed = _run(
        tmp_path,
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-c', {GRANDCHILD!r}])\n"
        "open('grandchild.pid', 'w').write(str(child.pid))\n"
        "time.sleep(60)\n",
        timeout=2.0,
    )
    assert outcome.ok is False and "timeout after 2s" in (outcome.failure or "")
    assert elapsed < 15, f"the timeout was 2 s; the stage took {elapsed:.1f} s"
    assert _wait_until_dead(_grandchild_pid(tmp_path), within=5), (
        "the process the evaluator started outlived the stage timeout"
    )


@pytest.mark.slow
def test_a_grandchild_holding_stdout_does_not_hold_up_a_finished_evaluation(tmp_path):
    outcome, elapsed = _run(
        tmp_path,
        "import json, subprocess, sys\n"
        # Inherits this process's stdout and stderr, and outlives it.
        f"child = subprocess.Popen([sys.executable, '-c', {GRANDCHILD!r}])\n"
        "open('grandchild.pid', 'w').write(str(child.pid))\n"
        "print('evaluated')\n"
        "open(sys.argv[2], 'w').write(json.dumps({'kpis': {'cost': 12.5}}))\n",
        timeout=20.0,
    )
    assert outcome.ok is True, outcome.failure
    assert outcome.kpis == {"cost": 12.5}
    assert elapsed < 10, f"the evaluator exited at once; the stage took {elapsed:.1f} s"
    # An evaluation is over when its command returns. What it left running
    # would share a core with the next candidate's time-limited run.
    assert _wait_until_dead(_grandchild_pid(tmp_path), within=5)


def test_the_full_output_of_an_evaluation_is_kept_beside_its_result(tmp_path):
    outcome, _ = _run(
        tmp_path,
        "import sys\n"
        "print('x' * 50_000)\n"
        "print('the last line of stdout')\n"
        "sys.stderr.write('warning: ' + 'y' * 50_000 + '\\n')\n"
        "sys.exit('solver: infeasible after 3 restarts')\n",
        timeout=20.0,
    )
    assert outcome.ok is False and "exit code 1" in (outcome.failure or "")
    # The prompt artefact stays bounded...
    assert len(outcome.stderr) <= 2000 and "infeasible after 3 restarts" in outcome.stderr
    assert len(outcome.stdout) <= 2000 and "the last line of stdout" in outcome.stdout
    # ...and the whole thing is on disk for whoever has to debug the solver.
    stderr_log = (tmp_path / "out.stderr.log").read_text(encoding="utf-8")
    stdout_log = (tmp_path / "out.stdout.log").read_text(encoding="utf-8")
    assert len(stderr_log) > 50_000 and "infeasible after 3 restarts" in stderr_log
    assert len(stdout_log) > 50_000


def test_a_timed_out_evaluation_keeps_what_it_printed_before_it_hung(tmp_path):
    outcome, _ = _run(
        tmp_path,
        "import sys, time\n"
        "print('loaded instance', flush=True)\n"
        "sys.stderr.write('restart 1: no feasible solution' + chr(10))\n"
        "sys.stderr.flush()\n"
        "time.sleep(60)\n",
        timeout=1.0,
    )
    assert outcome.ok is False and "timeout after 1s" in (outcome.failure or "")
    assert "loaded instance" in outcome.stdout
    assert "no feasible solution" in outcome.stderr


def test_a_command_that_cannot_be_started_is_an_error_not_an_exception(tmp_path):
    run = run_bounded(
        [str(tmp_path / "no-such-solver.exe")],
        timeout=5.0,
        cwd=tmp_path,
        stdout_path=tmp_path / "o.log",
        stderr_path=tmp_path / "e.log",
    )
    assert run.returncode is None and run.timed_out is False
    assert run.error


def test_read_tail_is_bounded_and_survives_bytes_that_are_not_utf8(tmp_path):
    log = tmp_path / "solver.log"
    log.write_bytes(b"\xff\xfe" + b"a" * 200_000 + b"\r\nlast line\r\n")
    tail = read_tail(log, 40)
    assert len(tail) == 40 and tail.endswith("last line\n")
    assert read_tail(tmp_path / "missing.log", 40) == ""
