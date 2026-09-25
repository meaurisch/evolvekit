"""The status document: one answer for the terminal, for `--json` and for the
dashboard.

Most tests here build a run directory by hand -- a few events, a lock, a
heartbeat -- because the states that matter most (running, stalled, died
without a word) are exactly the ones a finished test run is never in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evolvekit.cli import main
from evolvekit.config import load_config
from evolvekit.search.driver import Driver
from evolvekit.status import SCHEMA, build_status, render_text
from tests.conftest import EXAMPLE_CONFIG

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)


def _ts(seconds_ago: float) -> str:
    return (NOW - timedelta(seconds=seconds_ago)).isoformat(timespec="milliseconds")


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


STAGES = [
    {"id": "static", "kind": "builtin-static", "timeout_s": 30, "seeds": 1, "final": False},
    {"id": "full", "kind": "command", "timeout_s": 600, "seeds": 1, "final": True},
]


class RunDir:
    """A run directory written by hand, one event at a time."""

    def __init__(self, path: Path, *, pid: int | None = None, session: str = "s1") -> None:
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self.pid = os.getpid() if pid is None else pid
        self.session = session
        self.seq = 0

    def event(self, type: str, seconds_ago: float, **fields) -> None:
        self.seq += 1
        record = {
            "seq": self.seq, "ts": _ts(seconds_ago), "session": self.session,
            "pid": self.pid, "type": type, **fields,
        }
        with (self.path / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def started(self, seconds_ago: float, *, planned: int = 5, first: int = 1, **extra) -> None:
        self.event(
            "run_started", seconds_ago, objective="cost", direction="minimize",
            stages=STAGES, first_generation=first, generations_planned=planned,
            resumed=first > 1,
            budget={"max_usd": 2.0, "max_tokens": 1000, "max_full_evals_per_day": 8},
            stop={"patience": 4, "epsilon": 0.0}, **extra,
        )

    def lock(self) -> None:
        (self.path / ".lock").write_text(json.dumps({"pid": self.pid}), encoding="utf-8")

    def heartbeat(self, seconds_ago: float, **state) -> None:
        payload = {"ts": _ts(seconds_ago), "pid": self.pid, "session": self.session,
                   "interval_s": 5.0, "beats": 3, **state}
        (self.path / "heartbeat.json").write_text(json.dumps(payload), encoding="utf-8")

    def row(self, cid: str, generation: int, cost: float, **extra) -> None:
        record = {
            "id": cid, "generation": generation, "block": f"COST = {cost}\n",
            "operator": "human-seed" if generation == 0 else "rewrite",
            "score": -cost, "kpis": {"cost": cost}, "rejected": False, "competes": True,
            "stages_reached": ["static", "full"], **extra,
        }
        with (self.path / "runs.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def evaluation(self, cid: str, seconds_ago: float, cost: float, *, seed: int = 0,
                   duration: float = 10.0, **extra) -> None:
        common = {"candidate_id": cid, "stage": "full", "seed": seed, "private": False}
        self.event("eval_started", seconds_ago + duration, timeout_s=600, **common)
        self.event("eval_finished", seconds_ago, ok=True, duration_s=duration,
                   kpis={"cost": cost}, **common, **extra)


# -- is it alive? ----------------------------------------------------------


def test_a_directory_that_does_not_exist_is_reported_and_not_created(tmp_path):
    document = build_status(tmp_path / "typo")
    assert document["health"]["state"] == "missing"
    assert not (tmp_path / "typo").exists()


def test_an_empty_directory_is_empty(tmp_path):
    assert build_status(tmp_path)["health"]["state"] == "empty"


def test_a_run_with_a_live_owner_and_a_fresh_heartbeat_is_running(tmp_path):
    run = RunDir(tmp_path)
    run.started(100)
    run.event("generation_started", 90, generation=1, children_planned=3)
    run.event("eval_started", 40, candidate_id="g001-c0002", stage="full", seed=0,
              private=False, timeout_s=600)
    run.lock()
    run.heartbeat(2, phase="evaluating", generation=1)

    health = build_status(tmp_path, now=NOW)["health"]
    assert health["state"] == "running" and health["pid_alive"] is True
    assert health["generation"] == {"current": 1, "last_finished": None, "last_planned": 5}
    assert health["evaluations"]["in_flight"] == 1
    (flight,) = health["in_flight"]
    assert flight["candidate_id"] == "g001-c0002" and flight["stage"] == "full"
    assert flight["running_s"] == pytest.approx(40.0) and flight["timeout_s"] == 600
    assert health["elapsed_s"] == pytest.approx(100.0)


def test_a_live_process_whose_heartbeat_went_quiet_is_stalled(tmp_path):
    run = RunDir(tmp_path)
    run.started(4000)
    run.lock()
    run.heartbeat(1800, phase="evaluating")
    health = build_status(tmp_path, now=NOW)["health"]
    assert health["state"] == "stalled"
    assert "1800 s old" in health["detail"] and "slept" in health["detail"]


def test_an_evaluation_far_past_its_timeout_is_a_stall_too(tmp_path):
    run = RunDir(tmp_path)
    run.started(2000)
    run.event("eval_started", 900, candidate_id="g001-c0002", stage="full", seed=0,
              private=False, timeout_s=600)
    run.lock()
    run.heartbeat(1, phase="evaluating")
    health = build_status(tmp_path, now=NOW)["health"]
    assert health["state"] == "stalled" and "timeout of 600 s" in health["detail"]


def test_a_run_whose_process_vanished_without_a_closing_event_crashed(tmp_path):
    run = RunDir(tmp_path, pid=_dead_pid())
    run.started(500)
    run.event("eval_started", 300, candidate_id="g001-c0002", stage="full", seed=0,
              private=False, timeout_s=600)
    run.lock()  # a killed process leaves its lock behind
    run.heartbeat(290, phase="evaluating")

    health = build_status(tmp_path, now=NOW)["health"]
    assert health["state"] == "crashed" and "left no closing event" in health["detail"]
    assert health["in_flight"] == [], "a dead process has nothing in flight"
    assert health["evaluations"]["abandoned"] == 1
    # Counted up to the last thing it wrote, not up to now.
    assert health["elapsed_s"] == pytest.approx(200.0)


def test_a_reused_pid_cannot_make_a_finished_run_look_alive(tmp_path):
    """No lock file means not running, whoever owns the pid today."""
    run = RunDir(tmp_path)  # this very process: certainly alive
    run.started(500)
    run.heartbeat(1, phase="evaluating")
    assert build_status(tmp_path, now=NOW)["health"]["state"] == "crashed"


@pytest.mark.parametrize(
    "closing, fields, state, fragment",
    [
        ("run_finished", {"stop_reason": "stop.patience reached"}, "finished", "stop.patience reached"),
        ("run_interrupted", {}, "interrupted", "Ctrl+C"),
        ("run_crashed", {"error": "KeyError: 'x'"}, "crashed", "KeyError: 'x'"),
    ],
)
def test_a_session_that_said_how_it_ended_is_believed(tmp_path, closing, fields, state, fragment):
    run = RunDir(tmp_path)
    run.started(100)
    run.event(closing, 10, **fields)
    health = build_status(tmp_path, now=NOW)["health"]
    assert health["state"] == state and fragment in health["detail"]
    assert health["elapsed_s"] == pytest.approx(90.0)


def test_a_directory_from_before_the_event_log_is_unknown_not_wrong(tmp_path):
    RunDir(tmp_path).row("g000-c0001", 0, 100.0)
    document = build_status(tmp_path, now=NOW)
    assert document["health"]["state"] == "unknown"
    assert "older evolvekit" in document["health"]["detail"]
    assert document["progress"]["baseline"]["id"] == "g000-c0001"


# -- how long will it take? ------------------------------------------------


def test_the_eta_extrapolates_the_median_generation_and_says_so(tmp_path):
    run = RunDir(tmp_path)
    run.started(100, planned=5)
    run.event("generation_finished", 95, generation=0, duration_s=5.0)  # the seed: not typical
    run.event("generation_finished", 70, generation=1, duration_s=10.0)
    run.event("generation_finished", 40, generation=2, duration_s=30.0)
    run.lock()
    run.heartbeat(1, phase="breeding", generation=3)
    eta = build_status(tmp_path, now=NOW)["health"]["eta"]
    assert eta["seconds"] == pytest.approx(60.0)  # 3 to go x median(10, 30)
    assert "3 generation(s) to go" in eta["basis"] and "stop rule" in eta["basis"]


def test_no_eta_is_invented_before_a_generation_has_finished(tmp_path):
    run = RunDir(tmp_path)
    run.started(100, planned=5)
    run.lock()
    run.heartbeat(1, phase="evaluating", generation=1)
    eta = build_status(tmp_path, now=NOW)["health"]["eta"]
    assert eta["seconds"] is None and "nothing to extrapolate" in eta["basis"]


def test_every_stopping_criterion_reports_how_far_along_it_is(tmp_path):
    run = RunDir(tmp_path)
    run.started(100, planned=5)
    run.row("g000-c0001", 0, 100.0)
    run.row("g001-c0002", 1, 100.0)  # flat
    run.row("g002-c0003", 2, 100.0)  # flat
    run.event("generation_finished", 10, generation=2, duration_s=5.0)
    (tmp_path / "usage.jsonl").write_text(
        json.dumps({"generation": 1, "usd": 0.5, "input_tokens": 300, "output_tokens": 50}) + "\n",
        encoding="utf-8",
    )
    limits = {l["name"]: l for l in build_status(tmp_path, now=NOW)["health"]["limits"]}
    assert (limits["generations"]["used"], limits["generations"]["cap"]) == (2, 5)
    assert limits["budget.max_usd"]["used"] == pytest.approx(0.5)
    assert limits["budget.max_tokens"]["used"] == 350
    assert (limits["stop.patience"]["used"], limits["stop.patience"]["cap"]) == (2, 4)


# -- is it improving, and is that real? ------------------------------------


def _two_candidates(tmp_path, baseline_costs, best_costs):
    run = RunDir(tmp_path)
    run.started(1000)
    run.row("g000-c0001", 0, sum(baseline_costs) / len(baseline_costs))
    run.row("g001-c0002", 1, sum(best_costs) / len(best_costs), parent_id="g000-c0001")
    for seed, cost in enumerate(baseline_costs):
        run.evaluation("g000-c0001", 900 - seed, cost, seed=seed)
    for seed, cost in enumerate(best_costs):
        run.evaluation("g001-c0002", 500 - seed, cost, seed=seed)
    return build_status(tmp_path, now=NOW)["progress"]


def test_improvement_is_a_percentage_of_the_baseline_in_the_objectives_own_units(tmp_path):
    progress = _two_candidates(tmp_path, [200.0], [190.0])
    assert progress["baseline"]["objective"] == 200.0 and progress["best"]["objective"] == 190.0
    assert progress["improvement"]["pct"] == pytest.approx(5.0)
    assert progress["improvement"]["objective_abs"] == pytest.approx(10.0)
    assert [p["improvement_pct"] for p in progress["series"]] == [pytest.approx(0.0), pytest.approx(5.0)]


def test_a_single_sample_is_never_called_an_improvement_or_a_failure_to_improve(tmp_path):
    improvement = _two_candidates(tmp_path, [200.0], [190.0])["improvement"]
    assert improvement["verdict"] == "unknown" and "seeds: N" in improvement["why"]


def test_a_gain_that_holds_on_every_shared_seed_is_clear(tmp_path):
    progress = _two_candidates(tmp_path, [200.0, 210.0, 190.0], [190.0, 199.0, 181.0])
    assert progress["best"]["n"] == 3 and progress["best"]["sd"] is not None
    improvement = progress["improvement"]
    assert improvement["verdict"] == "clear" and improvement["n"] == 3
    assert improvement["t"] > 2
    assert "never saw" in improvement["why"], "selection bias has to be named"


def test_a_gain_smaller_than_the_seed_to_seed_swing_is_within_noise(tmp_path):
    improvement = _two_candidates(
        tmp_path, [200.0, 210.0, 190.0], [230.0, 170.0, 197.0]
    )["improvement"]
    assert improvement["verdict"] == "within noise"
    assert improvement["pct"] == pytest.approx(0.5)  # a number that looks like progress


def test_a_seed_evaluated_again_after_an_abort_is_the_baseline(tmp_path):
    """A run whose seed failed aborts; run again, it evaluates the seed again
    and records a second seed row. The baseline is the attempt that worked,
    not the one that failed."""
    from evolvekit.status import candidate_detail

    run = RunDir(tmp_path)
    run.started(1000)
    run.row("g000-c0001", 0, 1000.0, competes=False, last_failure="stage full: exit code 1")
    run.row("g000-c0002", 0, 200.0)
    run.row("g001-c0003", 1, 190.0, parent_id="g000-c0002")
    progress = build_status(tmp_path, now=NOW)["progress"]
    assert progress["baseline"]["id"] == "g000-c0002"
    assert progress["improvement"]["pct"] == pytest.approx(5.0)
    assert "seed (g000-c0002)" in candidate_detail(tmp_path, "g001-c0003")["diff_vs_seed"]


def test_gains_that_do_not_vary_are_judged_in_the_direction_of_their_mean(tmp_path):
    """The best losing by exactly 1 on every shared seed has no spread, and the
    verdict used to read "mean gain -1, t = inf ... within noise"."""
    run = RunDir(tmp_path)
    run.started(1000)
    # Ranked best (its recorded score says so, e.g. through the hold-out-aware
    # ranking), yet behind the baseline on every seed both were run on.
    run.row("g000-c0001", 0, 200.0)
    run.row("g001-c0002", 1, 190.0, parent_id="g000-c0001")
    for seed, (base, best) in enumerate([(200.0, 201.0), (210.0, 211.0), (190.0, 191.0)]):
        run.evaluation("g000-c0001", 900 - seed, base, seed=seed)
        run.evaluation("g001-c0002", 500 - seed, best, seed=seed)
    improvement = build_status(tmp_path, now=NOW)["progress"]["improvement"]
    assert improvement["verdict"] == "worse"
    assert "t = -inf" in improvement["why"] and "within noise" not in improvement["why"]


def test_gains_that_do_not_vary_and_help_are_clear(tmp_path):
    improvement = _two_candidates(tmp_path, [200.0, 210.0, 190.0], [199.0, 209.0, 189.0])["improvement"]
    assert improvement["verdict"] == "clear" and "t = inf" in improvement["why"]


# -- what is the best, and how does it differ? ------------------------------

PARAM_BLOCK = '# PARAMS: {{"ALPHA": [0.0, 10.0], "STEPS": [1, 9]}}\nALPHA = {alpha}\nSTEPS = {steps}\n'


def _sweep(tmp_path):
    run = RunDir(tmp_path)
    run.started(1000)
    run.row("g000-c0001", 0, 50.0, block=PARAM_BLOCK.format(alpha=5.0, steps=3))
    for i, (alpha, steps, cost) in enumerate(
        [(1.0, 3, 90.0), (3.0, 8, 70.0), (6.0, 2, 40.0), (8.0, 5, 20.0), (9.5, 7, 10.0)], start=2
    ):
        run.row(f"g001-c{i:04d}", 1, cost, parent_id="g000-c0001",
                block=PARAM_BLOCK.format(alpha=alpha, steps=steps))
    return build_status(tmp_path, now=NOW)


def test_the_best_candidate_comes_with_its_diff_and_its_parameters(tmp_path):
    best = _sweep(tmp_path)["best"]
    assert best["id"] == "g001-c0006" and best["lineage"] == ["g000-c0001"]
    assert "-ALPHA = 5.0" in best["diff_vs_seed"] and "+ALPHA = 9.5" in best["diff_vs_seed"]
    alpha = next(p for p in best["parameters"] if p["name"] == "ALPHA")
    assert (alpha["default"], alpha["value"], alpha["changed"]) == (5.0, 9.5, True)
    assert (alpha["low"], alpha["high"]) == (0.0, 10.0)


def test_parameters_are_ranked_by_how_strongly_they_go_with_the_score(tmp_path):
    parameters = _sweep(tmp_path)["parameters"]
    assert parameters["available"] is True
    names = [item["name"] for item in parameters["items"]]
    assert names == ["ALPHA", "STEPS"], "ALPHA orders the scores perfectly; STEPS does not"
    alpha = parameters["items"][0]
    assert alpha["importance"]["value"] == pytest.approx(1.0) and alpha["importance"]["n"] == 6
    assert alpha["importance"]["sign"] == 1  # a higher ALPHA goes with a higher score
    assert sum(alpha["coverage"]) == 6 and alpha["coverage"][9] == 1
    assert alpha["distinct_values"] == 6
    assert "interactions" in parameters["why"]


def test_a_problem_without_declared_parameters_says_so(tmp_path):
    run = RunDir(tmp_path)
    run.started(10)
    run.row("g000-c0001", 0, 50.0)
    parameters = build_status(tmp_path, now=NOW)["parameters"]
    assert parameters["available"] is False and "# PARAMS" in parameters["why"]


# -- where does it win and lose? -------------------------------------------


def test_a_per_instance_list_kpi_becomes_a_win_loss_breakdown(tmp_path):
    run = RunDir(tmp_path)
    run.started(1000)
    run.row("g000-c0001", 0, 100.0)
    run.row("g001-c0002", 1, 95.0, parent_id="g000-c0001")
    run.evaluation("g000-c0001", 900, 100.0, vector_kpis={"cost_per_instance": [100.0, 200.0, 50.0]})
    run.evaluation("g001-c0002", 500, 95.0, vector_kpis={"cost_per_instance": [90.0, 210.0, 50.0]})
    instances = build_status(tmp_path, now=NOW)["instances"]
    assert instances["available"] is True and instances["kpi"] == "cost_per_instance"
    assert (instances["wins"], instances["losses"], instances["ties"]) == (1, 1, 1)
    assert [r["improvement_pct"] for r in instances["rows"]] == [
        pytest.approx(10.0), pytest.approx(-5.0), pytest.approx(0.0)
    ]


def test_an_instance_without_a_value_keeps_every_other_instance_in_its_place(tmp_path):
    """An evaluator reports `null` for an instance it found nothing feasible
    on. Dropping it moved every later value up one place: the card compared
    instance 1's baseline with instance 2's best and showed a win that never
    happened."""
    run = RunDir(tmp_path)
    run.started(1000)
    run.row("g000-c0001", 0, 100.0)
    run.row("g001-c0002", 1, 95.0, parent_id="g000-c0001")
    run.evaluation("g000-c0001", 900, 100.0, vector_kpis={"cost_per_instance": [5.0, None, 7.0, 8.0]})
    run.evaluation("g001-c0002", 500, 95.0, vector_kpis={"cost_per_instance": [5.0, 4.0, None, 8.0]})
    instances = build_status(tmp_path, now=NOW)["instances"]
    assert instances["available"] is True
    rows = {r["instance"]: r for r in instances["rows"]}
    assert (rows[1]["baseline"], rows[1]["best"], rows[1]["improvement_pct"]) == (None, 4.0, None)
    assert (rows[2]["baseline"], rows[2]["best"], rows[2]["improvement_pct"]) == (7.0, None, None)
    assert (instances["wins"], instances["losses"], instances["ties"]) == (0, 0, 2)


class _CountingEvents(list):
    """The event log, counting how often it is read from end to end."""

    scans = 0

    def __iter__(self):
        _CountingEvents.scans += 1
        return super().__iter__()


def _per_instance_run(path: Path, candidates: int) -> None:
    run = RunDir(path)
    stages = [STAGES[0], {**STAGES[1], "instances": ["a", "b", "c"], "normalize": "baseline"}]
    run.event("run_started", 1000, objective="cost", direction="minimize", stages=stages,
              first_generation=1, generations_planned=5, budget={}, stop={})
    for index in range(candidates):
        cid = f"g{min(index, 1):03d}-c{index + 1:04d}"
        run.row(cid, min(index, 1), 100.0 - index * 0.01)
        for instance in ("a", "b", "c"):
            run.event("eval_finished", 900, candidate_id=cid, stage="full", seed=0, private=False,
                      instance=instance, ok=True, duration_s=1.0, kpis={"cost": 100.0 - index * 0.01})


def test_the_status_document_reads_the_event_log_a_fixed_number_of_times(tmp_path, monkeypatch):
    """Views that look at one candidate at a time used to scan the whole log
    per candidate: 33 s per document at 2,900 candidates, rebuilt about once a
    second inside the run's own process while a dashboard was open."""
    from evolvekit import status as status_module

    real = status_module.read_events
    monkeypatch.setattr(status_module, "read_events", lambda d: _CountingEvents(real(d)))
    scans = {}
    for candidates in (10, 40):
        _per_instance_run(tmp_path / str(candidates), candidates)
        _CountingEvents.scans = 0
        build_status(tmp_path / str(candidates), now=NOW)
        scans[candidates] = _CountingEvents.scans
    assert scans[40] == scans[10], f"scans grow with the number of candidates: {scans}"


def test_the_export_reads_the_run_once_not_once_per_candidate(tmp_path, monkeypatch):
    from evolvekit import status as status_module
    from evolvekit.dashboard import export_html

    _per_instance_run(tmp_path / "run", 30)
    real, calls = status_module.read_events, []
    monkeypatch.setattr(status_module, "read_events", lambda d: calls.append(d) or real(d))
    export_html(tmp_path / "run", tmp_path / "report.html")
    assert len(calls) <= 2, f"{len(calls)} reads of the event log for 30 candidates"


def test_without_a_per_instance_kpi_the_breakdown_explains_how_to_get_one(tmp_path):
    run = RunDir(tmp_path)
    run.started(10)
    run.row("g000-c0001", 0, 50.0)
    instances = build_status(tmp_path, now=NOW)["instances"]
    assert instances["available"] is False and "per-instance list KPI" in instances["why"]


# -- what went wrong? -------------------------------------------------------


def test_failures_carry_everything_needed_to_reproduce_them(tmp_path):
    run = RunDir(tmp_path)
    run.started(1000)
    run.row("g001-c0002", 1, 0.0, competes=False, last_failure="stage full: exit code 139")
    run.event("eval_started", 610, candidate_id="g001-c0002", stage="full", seed=2,
              private=True, timeout_s=600)
    run.event("eval_finished", 600, candidate_id="g001-c0002", stage="full", seed=2,
              private=True, ok=False, duration_s=10.0, failure="exit code 139",
              stderr_tail="Segmentation fault", argv=["./solver", "--instance", "c101.txt"],
              stderr_log="work/stage_out/g001-c0002.full.private.seed2.stderr.log")
    run.event("candidate_bred", 300, candidate_id="g002-c0003", generation=2, operator="diff",
              ok=False, novelty=None, reason="could not apply response: no SEARCH block matched")
    run.event("candidate_bred", 200, candidate_id="g002-c0004", generation=2, operator="diff",
              ok=False, novelty="duplicate", reason="duplicate: structurally identical")

    failures = build_status(tmp_path, now=NOW)["failures"]
    assert [f["kind"] for f in failures] == ["operator", "evaluation"], "most recent first; a duplicate is not a failure"
    evaluation = failures[1]
    assert evaluation["candidate_id"] == "g001-c0002" and evaluation["generation"] == 1
    assert (evaluation["stage"], evaluation["seed"], evaluation["holdout"]) == ("full", 2, True)
    assert evaluation["failure"] == "exit code 139" and "Segmentation" in evaluation["stderr_tail"]
    assert evaluation["argv"] == ["./solver", "--instance", "c101.txt"]
    assert evaluation["stderr_log"].endswith(".stderr.log")


# -- one broken view must not blank the rest -------------------------------


def test_a_bug_in_one_section_is_reported_and_the_others_survive(tmp_path, monkeypatch):
    from evolvekit import status as status_module

    run = RunDir(tmp_path)
    run.started(10)
    run.row("g000-c0001", 0, 50.0)
    monkeypatch.setattr(
        status_module._Run, "parameters", lambda self: (_ for _ in ()).throw(KeyError("boom"))
    )
    document = build_status(tmp_path, now=NOW)
    assert document["parameters"] is None
    assert any("parameters: KeyError" in error for error in document["errors"])
    assert document["progress"]["baseline"]["objective"] == 50.0


def test_the_document_is_strict_json_even_when_the_run_recorded_nonsense(tmp_path):
    run = RunDir(tmp_path)
    run.started(10)
    with (tmp_path / "runs.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"id": "g000-c0001", "generation": 0, "block": "", "operator": "human-seed", '
                     '"score": NaN, "kpis": {"cost": Infinity}}\n')
    document = build_status(tmp_path, now=NOW)
    text = json.dumps(document, allow_nan=False)  # raises if a NaN got through
    assert json.loads(text)["schema"] == SCHEMA


# -- a real run, and the command line --------------------------------------


@pytest.fixture(scope="module")
def finished_run(tmp_path_factory):
    config = load_config(EXAMPLE_CONFIG)
    config = replace(config, search=replace(config.search, scratchpad_every=0))
    run_dir = tmp_path_factory.mktemp("status-demo")
    summary = Driver(config, run_dir=run_dir).run(generations=2)
    return run_dir, summary


@pytest.mark.slow
def test_a_finished_run_reports_what_the_run_itself_reported(finished_run):
    run_dir, summary = finished_run
    document = build_status(run_dir)
    assert document["errors"] == []
    health = document["health"]
    assert health["state"] == "finished" and health["stop_reason"] == summary.stop_reason
    assert health["generation"]["last_finished"] == 2 == health["generation"]["last_planned"]
    assert health["evaluations"]["in_flight"] == 0 and health["evaluations"]["done"] > 0
    assert document["objective"] == {"name": "excess_pct", "direction": "minimize", "known": True, "unit": None}
    progress = document["progress"]
    assert progress["baseline"]["objective"] == pytest.approx(5.871, abs=0.001)
    assert progress["best"]["id"] == summary.best.id
    assert progress["improvement"]["pct"] > 0
    assert document["best"]["diff_vs_seed"].startswith("--- seed")
    assert len(document["candidates"]) == summary.candidates
    assert sum(c["is_best"] for c in document["candidates"]) == 1
    assert document["spend"]["calls"] == int(summary.totals["calls"])
    assert document["spend"]["evaluator_s"] > 0
    assert any(line["message"].startswith("gen 2") for line in document["log_tail"])
    assert document["instances"]["available"] is True  # bins_per_instance


@pytest.mark.slow
def test_status_json_is_the_document_and_the_text_view_is_made_from_it(finished_run, capsys):
    run_dir, _summary = finished_run
    assert main(["status", "--run-dir", str(run_dir), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["schema"] == SCHEMA and document["health"]["state"] == "finished"

    assert main(["status", "--run-dir", str(run_dir)]) == 0
    text = capsys.readouterr().out
    assert "state        : FINISHED -- stopped: generations exhausted" in text
    assert "improvement  : +" in text
    assert render_text(build_status(run_dir)).splitlines()[1] in text


def test_status_on_a_mistyped_directory_fails_and_leaves_no_trace(tmp_path, capsys):
    assert main(["status", "--run-dir", str(tmp_path / "typo")]) == 1
    assert "MISSING" in capsys.readouterr().out
    assert main(["status", "--run-dir", str(tmp_path / "typo"), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["health"]["state"] == "missing"
    assert not (tmp_path / "typo").exists()


# -- the rhythm of the run, and what is failing most -----------------------


def test_the_generation_ribbon_covers_the_plan_and_marks_what_moved_the_best(tmp_path):
    run = RunDir(tmp_path)
    run.started(1000, planned=5)
    run.row("g000-c0001", 0, 100.0)
    run.row("g001-c0002", 1, 90.0, parent_id="g000-c0001")   # a new best
    run.row("g002-c0003", 2, 95.0, parent_id="g001-c0002")   # not one
    for generation, ago in ((0, 900), (1, 700), (2, 500)):
        run.event("generation_started", ago + 50, generation=generation, children_planned=1)
        run.event("generation_finished", ago, generation=generation, duration_s=50.0, children=1)
    run.event("generation_started", 100, generation=3, children_planned=1)
    run.event("eval_finished", 60, candidate_id="g003-c0004", stage="full", seed=0, private=False,
              ok=False, failure="exit code 134", duration_s=2.0)
    ribbon = build_status(tmp_path, now=NOW)["progress"]["generations"]
    assert [g["generation"] for g in ribbon] == [0, 1, 2, 3, 4, 5]
    assert [g["state"] for g in ribbon] == ["done", "done", "done", "current", "planned", "planned"]
    assert [g["improved"] for g in ribbon] == [False, True, False, False, False, False]
    assert ribbon[1]["duration_s"] == 50.0 and ribbon[1]["best_objective"] == 90.0
    # The failed child has no row yet -- its generation is still running -- but
    # its id says where it belongs.
    assert ribbon[3]["failed"] == 1


def test_failures_are_counted_by_reason_because_ten_crashes_are_one_problem(tmp_path):
    run = RunDir(tmp_path)
    run.started(100)
    for i, (stage, failure) in enumerate(
        [("full", "exit code 134"), ("proxy", "timeout after 60s"), ("full", "exit code 134")]
    ):
        run.event("eval_finished", 50 - i, candidate_id=f"g001-c000{i + 2}", stage=stage, seed=0,
                  private=False, ok=False, failure=failure, duration_s=1.0)
    reasons = build_status(tmp_path, now=NOW)["health"]["evaluations"]["failure_reasons"]
    assert reasons == [
        {"stage": "full", "failure": "exit code 134", "count": 2},
        {"stage": "proxy", "failure": "timeout after 60s", "count": 1},
    ]


def test_the_run_directory_is_reported_as_an_absolute_path(tmp_path, monkeypatch):
    RunDir(tmp_path / "run").started(10)
    monkeypatch.chdir(tmp_path)
    assert build_status("run", now=NOW)["run_dir"] == str((tmp_path / "run").resolve())


# -- what watching a real, slow run asked for ------------------------------


def test_before_anything_is_recorded_the_per_instance_card_says_wait_not_cannot(tmp_path):
    run = RunDir(tmp_path)
    run.started(100)
    run.lock()
    run.heartbeat(1)
    instances = build_status(tmp_path, now=NOW)["instances"]
    assert instances["available"] is False
    assert instances["why"].startswith("nothing has finished the final stage yet")


def test_the_first_generation_has_a_time_left_too_the_stage_knows(tmp_path):
    run = RunDir(tmp_path)
    run.started(2000, planned=3)
    run.lock()
    run.heartbeat(1)
    run.event("generation_started", 1500, generation=1)
    run.event("stage_started", 1500, stage="full", private=False, workers=1,
              candidates=["g001-c0002"], runs_per_candidate=3)
    common = {"candidate_id": "g001-c0002", "stage": "full", "seed": 0, "private": False, "attempt": 0}
    run.event("eval_started", 1500, instance="a", timeout_s=900, **common)
    run.event("eval_finished", 900, instance="a", ok=True, duration_s=600.0, kpis={"cost": 95.0}, **common)
    run.event("eval_started", 300, instance="b", timeout_s=900, **common)
    eta = build_status(tmp_path, now=NOW)["health"]["eta"]
    assert eta["seconds"] is None, "no generation has finished: the run as a whole cannot be extrapolated"
    assert eta["stage_seconds"] == pytest.approx(300.0 + 600.0), "half of `b`, and all of `c`"
    assert "The stage in progress has about 900 s left" in eta["basis"]


def test_best_so_far_is_also_told_by_the_clock(tmp_path):
    run = RunDir(tmp_path)
    run.started(10_000)
    run.row("g000-c0001", 0, 100.0)
    run.event("generation_finished", 9000, generation=0, duration_s=1000.0)
    run.row("g001-c0002", 1, 98.0)
    run.event("generation_finished", 4000, generation=1, duration_s=5000.0)
    run.row("g002-c0003", 2, 99.0)  # generation 2 is still running
    series = build_status(tmp_path, now=NOW)["progress"]["series"]
    assert [(p["generation"], p["elapsed_s"]) for p in series] == [(0, 1000.0), (1, 6000.0), (2, None)]


def test_whether_the_cheap_stage_predicts_the_expensive_one_can_be_read_off(tmp_path):
    run = RunDir(tmp_path)
    run.event("run_started", 5000, objective="cost", direction="minimize", first_generation=1, generations_planned=3,
              stages=[{"id": "static", "kind": "builtin-static"}, {"id": "screen", "kind": "command"},
                      {"id": "full", "kind": "command", "final": True}])
    scores = {"g000-c0001": (-100.0, -100.0), "g001-c0002": (-99.0, -98.5), "g001-c0003": (-97.0, -97.2),
              "g002-c0004": (-98.0, -99.1), "g002-c0005": (-103.0, None)}
    for cid, (screen, full) in scores.items():
        stage_scores = {"screen": screen, **({"full": full} if full is not None else {})}
        run.row(cid, int(cid[1:4]), 100.0, stage_scores=stage_scores)
    screening = build_status(tmp_path, now=NOW)["progress"]["screening"]
    pair = screening["pairs"][0]
    assert (pair["earlier"], pair["later"], pair["n"]) == ("screen", "full", 4), "the unpromoted one has nothing to compare"
    assert pair["spearman"] == pytest.approx(0.8)
    assert pair["verdict"].startswith("the earlier stage ranks candidates much as")
    assert "range is restricted" in screening["why"]
