"""Running a study: the preview, the test run, and the study run itself.

- **The preview** solves the smallest training case once, at the starting
  values, and keeps what it saw in `preview/`: the tables
  (`tables.sqlite`, which the assistant summarises and the SQL box queries),
  every KPI the harness offers, and how long the run took -- the plan's
  measured time. It needs only the cases and the application, not a finished
  study.
- **The test run** is the engine's `preflight` on the compiled study,
  restricted to the smallest training case: every KPI, the guardrails, the
  duration, and whether the starting point already breaks a guardrail --
  before anything is spent.
- **A study run** compiles the study into `runs/<id>/`, runs the search, and
  then the final check: the best against the starting point on the held-back
  test cases, on seeds the search never used. `job.json` says which phase it
  is in (`search`, `check`, `done`, `stopped`, `failed`), so whoever started
  it can go away and come back; a `stop-request` file stops either phase.

Everything reads and writes inside the study folder.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from evolvekit.config import load_config
from evolvekit.confirm import ConfirmStopped, confirm, decide
from evolvekit.evaluate.process import read_tail, run_bounded
from evolvekit.harness import HarnessError
from evolvekit.harness.compile import (
    CONFIG,
    STUDY_RUN,
    UP,
    base_settings,
    compile_study,
    runner_input,
    runner_kpi,
    runner_settings,
    study_operators,
    write_run,
)
from evolvekit.harness.manifest import Harness, load_harness
from evolvekit.harness.plan import CHECK_SEEDS, DEFAULT_CHECK_SEEDS, Plan, make_plan
from evolvekit.harness.sql import schema_database, sql_problem
from evolvekit.harness.study import Study, load_study, load_study_file, require_valid, resolve_kpi
from evolvekit.leaderboard import rank
from evolvekit.ledger import _atomic_write, read_jsonl
from evolvekit.stopping import STOP_REQUEST, RunStop

__all__ = [
    "JOB",
    "PREVIEW",
    "PREVIEW_TIME_LIMIT_S",
    "preview_time_limit",
    "Job",
    "continue_study",
    "final_check",
    "plan_for",
    "preview",
    "read_job",
    "read_preview",
    "resume_study",
    "run_study",
    "run_bakeoff",
    "run_study_of",
    "runner_command",
    "smallest_case",
    "study_harness",
    "run_test",
    "write_exports",
    "ExportFailed",
]

PREVIEW = "preview"
PREVIEW_TIME_LIMIT_S = 10.0
"""The preview's time limit, at most: it shows what the application reports
and measures its overhead, and neither needs the study's full time per case."""
JOB = "job.json"
DOWNLOADS = "downloads"
"""`runs/<id>/downloads/`: the exports of a run's best, written by the final check."""
EXPORT_SUFFIX = {"settings_json": ".json", "settings_flags": ".txt", "data_changes": ".json", "requests": ".json"}
EXPORT_TIMEOUT_S = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def study_harness(root: Path) -> Harness:
    """The harness pinned in the study (`<study>/harness/`)."""
    return load_harness(Path(root) / "harness")


def smallest_case(root: Path, study: Study) -> str:
    if not study.training:
        raise HarnessError("cases.training: add at least one case first")
    return min(study.training, key=lambda c: ((Path(root) / c).stat().st_size if (Path(root) / c).is_file() else 0, c))


def runner_command(study: Study, harness: Harness, runner: str) -> list[str]:
    """How to start the harness's runner: under the application's Python, or
    (a program harness) under evolvekit's, told where the program is."""
    if not study.application_path:
        raise HarnessError("application: say where the application is first")
    if harness.application.kind == "python":
        return [study.application_path, runner]
    return [sys.executable, runner, "--app", study.application_path]


def _starting_values(study: Study, base: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name in study.tuned_settings():
        start = study.settings[name].start
        values[name] = base[name] if start is None else start
    for name, change in study.data.items():
        values[name] = change.start
    return values


def _preview_kpis(study: Study, harness: Harness) -> dict[str, dict[str, Any]]:
    """Every ready-made KPI of the harness, and each of the study's own that
    runs: the app shows a KPI's value before anybody picks it."""
    kpis = {name: runner_kpi({"sql": k.sql, "measure": k.measure, "direction": k.direction}) for name, k in harness.kpis.items()}
    db = schema_database(harness.declared(), harness.request_tables)
    try:
        for name, kpi in study.kpis.items():
            if name in kpis:
                continue
            try:
                resolved = resolve_kpi(study, harness, name)
            except KeyError:
                continue
            if resolved.get("sql") and sql_problem(db, resolved["sql"], resolved.get("params") or ()):
                continue
            if resolved.get("weighted") and not all(part in kpis or part in study.kpis for part in resolved["weighted"]):
                continue
            kpis[name] = runner_kpi(resolved)
    finally:
        db.close()
    # A weighted sum needs its parts measured first; keep sums last.
    return dict(sorted(kpis.items(), key=lambda item: "weighted" in item[1]))


def _solve_once(
    root: Path, study: Study, harness: Harness, folder: Path, *, case: str, values: dict[str, Any],
    study_run: dict[str, Any], time_limit: float, tables_out: bool, up: str,
) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / STUDY_RUN).write_text(json.dumps(study_run, indent=2), encoding="utf-8")
    (folder / "values.json").write_text(json.dumps(values, indent=2), encoding="utf-8")
    argv = runner_command(study, harness, f"{up}/harness/runner.py") + [
        "solve", "--case", f"{up}/{case}", "--seed", "0", "--values", "values.json", "--study", STUDY_RUN,
    ]
    if harness.time_limit.accepts:
        argv += ["--time-limit", f"{time_limit:g}"]
    if tables_out:
        (folder / "tables.sqlite").unlink(missing_ok=True)
        argv += ["--tables-out", "tables.sqlite"]
    timeout = time_limit * 1.5 + 30 if harness.time_limit.accepts else time_limit
    started = time.perf_counter()
    run = run_bounded(argv, timeout=timeout, cwd=folder, stdout_path=folder / "stdout.log", stderr_path=folder / "stderr.log")
    wall = time.perf_counter() - started
    result: dict[str, Any] = {
        "case": case, "time_limit_s": time_limit, "wall_s": round(wall, 3), "exit_code": run.returncode,
        "values": values, "finished_at": _now(),
    }
    if run.error is not None:
        return {**result, "ok": False, "error": f"could not start the application: {run.error}"}
    if run.timed_out:
        return {**result, "ok": False, "error": f"no result after {timeout:g} s: the run was stopped"}
    if run.returncode != 0:
        said = read_tail(folder / "stderr.log", 2000).strip().splitlines()
        return {**result, "ok": False, "error": said[-1] if said else f"exit code {run.returncode}"}
    lines = [line for line in (folder / "stdout.log").read_text(encoding="utf-8", errors="replace").splitlines() if line.startswith("{")]
    if not lines:
        return {**result, "ok": False, "error": "the harness printed no result"}
    printed = json.loads(lines[-1])
    return {**result, "ok": True, "error": None, "kpis": printed.get("kpis") or {},
            "text_feedback": printed.get("text_feedback") or ""}


def preview_time_limit(study: Study, harness: Harness) -> float:
    """The preview's time limit: the study's, at most PREVIEW_TIME_LIMIT_S
    when the application takes a time limit at all."""
    limit = float(study.limits.time_per_case_s)
    return min(limit, PREVIEW_TIME_LIMIT_S) if harness.time_limit.accepts else limit


def preview(root: str | Path) -> dict[str, Any]:
    """Solve the smallest training case at the starting values, within
    `preview_time_limit`; keep the tables and the timing in `preview/`.
    Returns what `preview/preview.json` holds."""
    root = Path(root)
    study = load_study(root)
    harness = study_harness(root)
    case = smallest_case(root, study)
    base = base_settings(study, harness, root)
    kept = replace(study, constraints=[])  # the preview measures; it does not judge
    study_run = runner_input(kept, harness, runner_settings(study, harness, root), kpis=_preview_kpis(study, harness),
                             guardrails=False, up="..")
    result = _solve_once(
        root, study, harness, root / PREVIEW, case=case, values=_starting_values(study, base),
        study_run=study_run, time_limit=preview_time_limit(study, harness), tables_out=True, up="..",
    )
    if result["ok"] and harness.time_limit.accepts:
        result["overhead_s"] = round(max(0.0, result["wall_s"] - result["time_limit_s"]), 3)
    _atomic_write(root / PREVIEW / "preview.json", json.dumps(result, indent=2) + "\n")
    return result


def read_preview(root: str | Path) -> dict[str, Any] | None:
    try:
        payload = json.loads((Path(root) / PREVIEW / "preview.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def plan_for(study: Study, harness: Harness, measured: dict[str, Any], *, hours: float | None = None,
             models: dict[str, Any] | None = None, operators: dict[str, float] | None = None,
             bakeoff: bool = False) -> Plan:
    """The automatic plan, from the preview's measured time; for the study's
    hours, or for `hours` (a run continued with more time). Each round pays
    the AI's time of the search's operators (`operators`, else
    `compile.study_operators` with `models`, the ones the run is compiled
    with); a bake-off searches nothing."""
    overrides = None if study.plan.get("auto", True) else {k: v for k, v in study.plan.items() if k != "auto"}
    if operators is None:
        operators = study_operators(study, models, bakeoff=bakeoff)
    return make_plan(
        training=len(study.training), test=len(study.test), runs_per_case=study.limits.runs_per_case,
        time_limit_s=float(study.limits.time_per_case_s), hours=float(study.budget.hours if hours is None else hours),
        overhead_s=float(measured.get("overhead_s") or 0.0), measured_s=float(measured.get("wall_s") or study.limits.time_per_case_s),
        accepts_time_limit=harness.time_limit.accepts, overrides=overrides, operators=operators,
    )


def measured(root: Path) -> dict[str, Any]:
    measured = read_preview(root)
    if measured is None or not measured.get("ok"):
        measured = preview(root)
    if not measured.get("ok"):
        raise HarnessError(f"the preview run failed, so nothing can be planned: {measured.get('error')}")
    return measured


def run_test(root: str | Path, *, models: dict[str, Any] | None = None) -> dict[str, Any]:
    """`preflight` on the compiled study, restricted to the smallest training
    case: does everything work together, and does the starting point already
    break a guardrail? Nothing is kept but `preview/test-run/`."""
    from evolvekit.preflight import preflight

    root = Path(root)
    study = load_study(root)
    harness = study_harness(root)
    require_valid(study, harness, root=root)
    single = replace(study, training=[smallest_case(root, study)], test=[])
    folder = root / PREVIEW / "test-run"  # two levels down, like a run: the compiled paths hold
    config, study_run = compile_study(single, harness, Plan.fixed(children=1, generations=1), root=root, models=models)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / CONFIG).write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    (folder / STUDY_RUN).write_text(json.dumps(study_run, indent=2), encoding="utf-8")
    loaded = load_config(folder / CONFIG)
    started = time.perf_counter()
    report = preflight(loaded, work_dir=folder / "work")
    stages = [s for s in report.stages if s.kind == "command"]
    kpis = dict(stages[-1].kpis) if stages else {}
    return {
        "ok": not report.failures,
        "case": single.training[0],
        "duration_s": round(time.perf_counter() - started, 3),
        "failures": list(report.failures),
        "warnings": list(report.warnings),
        "kpis": kpis,
        "guardrails_hold": kpis.get("guardrail_violation", 0.0) == 0.0,
    }


# ---------------------------------------------------------------------------
# a study run
# ---------------------------------------------------------------------------


class Job:
    """`runs/<id>/job.json`: the phase a study run is in, rewritten atomically."""

    def __init__(self, run_dir: Path, **fields: Any) -> None:
        self.path = Path(run_dir) / JOB
        self.state: dict[str, Any] = {"phase": "search", "pid": os.getpid(), "started_at": _now(), **fields}
        self._write()

    @classmethod
    def resume(cls, run_dir: Path, state: dict[str, Any], phase: str) -> "Job":
        """The job of `run_dir` again, in `phase`: a stopped final check resumed."""
        job = cls.__new__(cls)
        job.path = Path(run_dir) / JOB
        job.state = {**state, "phase": phase, "pid": os.getpid(), "resumed_at": _now()}
        job.state.pop("finished_at", None)
        job._write()
        return job

    def update(self, **fields: Any) -> None:
        self.state.update(fields)
        self._write()

    def finish(self, phase: str, **fields: Any) -> None:
        self.update(phase=phase, finished_at=_now(), **fields)

    def _write(self) -> None:
        self.state["updated_at"] = _now()
        _atomic_write(self.path, json.dumps(self.state, indent=2) + "\n")


def read_job(run_dir: str | Path) -> dict[str, Any] | None:
    try:
        payload = json.loads((Path(run_dir) / JOB).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def run_study(
    root: str | Path,
    run_id: str | None = None,
    *,
    models: dict[str, Any] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Compile the study, search, and check the best on the held-back cases.
    Returns the final `job.json`."""
    from evolvekit.search.driver import Driver

    root = Path(root)
    study = load_study(root)
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = root / "runs" / run_id
    if (run_dir / STOP_REQUEST).is_file():
        # Stopped while starting: before the preview, the compile and the seed.
        # The file stays: it is the run's record, and whoever starts a run on
        # this directory again removes it first.
        job = Job(run_dir, run_id=run_id, study=study.name, plan={})
        job.finish("stopped", search={"stop_reason": "stopped on request before the search began",
                                      "generations": 0, "aborted": False})
        return job.state
    harness = study_harness(root)
    require_valid(study, harness, root=root)
    plan = plan_for(study, harness, measured(root), models=models)
    write_run(study, harness, plan, root, run_id, models=models)  # compiles into `run_dir`
    job = Job(run_dir, run_id=run_id, study=study.name, plan=plan.to_json())
    try:
        config = load_config(run_dir / CONFIG)
        summary = Driver(config, run_dir=run_dir, log=log).run()
        job.update(search={
            "stop_reason": summary.stop_reason, "generations": summary.generations,
            "best_id": summary.best.id if summary.best else None, "aborted": summary.aborted,
        })
        if summary.aborted:
            job.finish("failed", error=summary.stop_reason)
            return job.state
        if summary.stop_reason == "stopped on request":
            job.finish("stopped")
            return job.state
        _final_check(study, plan, config, run_dir, job, log)
    except Exception as exc:  # noqa: BLE001 - job.json must say how the job ended
        job.finish("failed", error=f"{type(exc).__name__}: {exc}")
        raise
    return job.state


BAKEOFF = "bakeoff.json"
"""`{"refs": [...]}` in a bake-off's run directory, written by whoever starts it (`jobs.start`)."""
NO_SEARCH = "branches compared: no search"


def run_bakeoff(root: str | Path, run_id: str, *, seeds: int | None = None,
                log: Callable[[str], None] = print) -> dict[str, Any]:
    """The bake-off (design §5.5): a run with no search whose candidates are
    the branches in `runs/<id>/bakeoff.json`, each checked against the
    starting point (the pinned base) on the held-back cases by the final
    check's paired statistics. `runs.jsonl` holds the starting point and one
    row per branch (its own change, `tree.diff_between`, and its commit).
    `job.final.candidate` is the branch with the largest mean among those whose
    own decision is `use`, else the largest mean (decision 32); `final.decisions`
    holds every branch's. A run whose check was stopped is checked again on the
    same rows (the runs already done are kept); `seeds` asks for that many runs
    per case. Returns the final `job.json`."""
    from evolvekit import tree
    from evolvekit.app import AppError, jobs
    from evolvekit.candidate import SEED_OPERATOR, Candidate
    from evolvekit.ledger import Ledger

    root = Path(root)
    run_dir = root / "runs" / run_id
    state = read_job(run_dir) or {}
    again = bool(state.get("bakeoff")) and (run_dir / CONFIG).is_file() and (run_dir / "runs.jsonl").is_file()
    if again:
        study = run_study_of(root, run_dir)
        stored = state.get("plan") or _read_plan(run_dir)
        checks = max(1, min(int(seeds or stored.get("check_seeds") or DEFAULT_CHECK_SEEDS), len(CHECK_SEEDS)))
        (run_dir / STOP_REQUEST).unlink(missing_ok=True)
        job = Job.resume(run_dir, {**state, "plan": {**stored, "check_seeds": checks}}, "check")
        job.state.pop("final", None)
        entries = list(state["bakeoff"])
    else:
        study = load_study(root)
        if (run_dir / STOP_REQUEST).is_file():  # stopped while starting, as `run_study` says it
            job = Job(run_dir, run_id=run_id, study=study.name, plan={})
            job.finish("stopped", search={"stop_reason": "stopped on request before the check began",
                                          "generations": 0, "aborted": False})
            return job.state
        harness = study_harness(root)
        try:
            refs = json.loads((run_dir / BAKEOFF).read_text(encoding="utf-8"))["refs"]
        except (OSError, ValueError, KeyError, TypeError):
            raise HarnessError(f"runs/{run_id}/{BAKEOFF}: name the branches to compare as {{\"refs\": [...]}}") from None
        try:
            jobs.check_refs(root, study, harness, refs)  # again: a branch may have moved since the start
        except AppError as exc:
            raise HarnessError(str(exc)) from None
        # No rounds: the compiled config keeps the one generation it must have to load, and no driver runs it
        # (a blocked plan is not blocked here: it is the search's time that does not fit).
        plan = replace(plan_for(study, harness, measured(root), bakeoff=True), generations=1, rounds=0, children=len(refs),
                       blocked="", ways_out=())
        write_run(study, harness, plan, root, run_id, bakeoff=True)  # pins the base to a commit
        config = load_config(run_dir / CONFIG)
        base = config.problem.tree.base
        repo = tree.Repo(config.problem.tree.repo)
        try:
            checked = jobs.check_refs(root, study, harness, refs, base=base)  # against the pinned base
        except AppError as exc:
            raise HarnessError(str(exc)) from None
        ledger = Ledger(run_dir)
        seed = Candidate(id="g000-c0001", generation=0, block="", source="", operator=SEED_OPERATOR, model="human",
                         provider="human", role="human", commit=base)
        ledger.record_run(seed.to_record())
        entries = []
        for i, ref in enumerate(checked):
            cid = f"g000-c{i + 2:04d}"
            ledger.record_run(Candidate(id=cid, generation=0, block=tree.diff_between(repo, base, ref["commit"]), source="",
                                        operator="given", role="none", parent_id=seed.id, commit=ref["commit"]).to_record())
            entries.append({"ref": ref["ref"], "id": cid, "commit": ref["commit"]})
        checks = max(1, min(int(plan.check_seeds), len(CHECK_SEEDS)))
        job = Job(run_dir, run_id=run_id, study=study.name, plan=plan.to_json(), phase="check", bakeoff=entries)
    try:
        config = load_config(run_dir / CONFIG)
        ids = [e["id"] for e in entries]
        log(f"bake-off: {len(ids)} branch(es) against the starting point on {len(study.test)} held-back case(s), "
            f"{checks} run(s) per case")
        job.update(check={"seeds": checks})
        stop = RunStop(run_dir)
        stop.start(0.0)
        try:
            comparison = confirm(config, run_dir, seeds=list(CHECK_SEEDS[:checks]),
                                 candidates=",".join(ids), instances=[f"{UP}/{case}" for case in study.test],
                                 label="final", log=log, stop=stop.event)
        except ConfirmStopped:
            job.finish("stopped", search=_no_search(None), final={"stopped": True})
            return job.state
        finally:
            stop.close()
        decisions = {cid: _decision(result) for cid, result in comparison.per_candidate.items()}

        def mean(cid: str) -> float:
            value = (comparison.per_candidate[cid].get("summary") or {}).get("mean")
            return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else -math.inf

        using = [cid for cid in ids if decisions.get(cid) == "use"]
        chosen = max(using or ids, key=mean)  # `max` keeps the first of equals: the order the refs were given
        own = decisions.get(chosen, "keep")
        decision = "use" if own == "use" else "keep" if own in ("keep", "worse") else own
        _write_exports(run_dir, log)
        job.finish("done", search=_no_search(chosen),
                   final={"candidate": chosen, "confirmed": decision == "use", "decision": decision,
                          "decisions": decisions, "seeds": checks, "comparison": "confirm/final/comparison.json"})
    except Exception as exc:  # noqa: BLE001 - job.json must say how the job ended
        job.finish("failed", error=f"{type(exc).__name__}: {exc}")
        raise
    return job.state


def _no_search(best_id: str | None) -> dict[str, Any]:
    return {"stop_reason": NO_SEARCH, "generations": 0, "best_id": best_id, "aborted": False}


def run_study_of(root: str | Path, run_dir: str | Path) -> Study:
    """The study a run was started with: the copy of `study.yaml` the run
    keeps, so its cases, goal and choices stay the run's own when the study
    is edited afterwards. Runs made before runs kept that copy read the study
    folder's, as they always did."""
    pinned = Path(run_dir) / "study.yaml"
    return load_study_file(pinned) if pinned.is_file() else load_study(root)


def final_check(root: str | Path, run_id: str, *, seeds: int | None = None,
                log: Callable[[str], None] = print) -> dict[str, Any]:
    """The final check of a search that has finished, run again: after a stop
    during the check, or a crash in it, or with more `seeds` (runs per case)
    for a firmer answer. `confirm` keeps what it measured, so the runs
    already done are not repeated. Returns the final `job.json`."""
    root = Path(root)
    run_dir = root / "runs" / run_id
    state = read_job(run_dir)
    if state and state.get("bakeoff"):  # a bake-off checks every branch again, not the search's best
        return run_bakeoff(root, run_id, seeds=seeds, log=log)
    if not state or not state.get("search"):
        raise HarnessError(f"runs/{run_id}: the search has not finished, so there is nothing to check yet")
    if (state.get("search") or {}).get("aborted"):
        raise HarnessError(f"runs/{run_id}: the search was aborted: {state['search'].get('stop_reason')}")
    study = run_study_of(root, run_dir)
    stored = state.get("plan") or {}
    checks = int(seeds or stored.get("check_seeds") or DEFAULT_CHECK_SEEDS)
    if not 1 <= checks <= len(CHECK_SEEDS):
        raise HarnessError(f"the final check takes 1 to {len(CHECK_SEEDS)} runs per case, not {checks}")
    state = {**state, "plan": {**stored, "check_seeds": checks}}
    (run_dir / STOP_REQUEST).unlink(missing_ok=True)
    job = Job.resume(run_dir, state, "check")
    # The run's own numbers, so a resumed check adds runs by the same measure
    # the run did; with explicit `seeds` the check does exactly that many.
    plan = _check_plan(stored, study, check_seeds=checks)
    try:
        config = load_config(run_dir / CONFIG)
        _final_check(study, plan, config, run_dir, job, log, extend=seeds is None)
    except Exception as exc:  # noqa: BLE001 - job.json must say how the job ended
        job.finish("failed", error=f"{type(exc).__name__}: {exc}")
        raise
    return job.state


def _check_plan(stored: dict[str, Any], study: Study, *, check_seeds: int | None = None) -> Plan:
    """The plan a run was started with, as far as the final check reads it
    (`plan.json`, or the copy in `job.json`): its runs per case, workers,
    time per run and hours. The rest of the plan is the search's, and the
    compiled `evolvekit.yaml` already holds that."""
    seeds = int(check_seeds or stored.get("check_seeds") or DEFAULT_CHECK_SEEDS)
    return replace(
        Plan.fixed(), check_seeds=seeds, workers=max(1, int(stored.get("workers") or 1)),
        run_s=float(stored.get("run_s") or 1.0), hours=float(stored.get("hours") or 0.0), test_cases=len(study.test),
    )


def resume_study(
    root: str | Path,
    run_id: str,
    *,
    models: dict[str, Any] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """A run that stopped or died during its search, continued on the same
    directory: the engine rebuilds its archive from `runs.jsonl`, charges
    what the run already spent, and finishes the generations the plan holds
    -- the ones it did are not done again; then the final check, exactly as
    `run_study` does it. `job.json` keeps the run's first `started_at` (the
    study's hours count from there) and gains `resumed_at`.

    `models` is accepted as `run_study` takes it, and is not needed: the run
    was compiled with the models it was started with, and the compiled
    `evolvekit.yaml` in the run directory is what resumes. Returns the final
    `job.json`."""
    from evolvekit.search.driver import Driver

    root = Path(root)
    run_dir = root / "runs" / run_id
    state = read_job(run_dir) or {}
    # Not the phase in `job.json`: a run that died still says `search` there;
    # whether its process lives is for whoever starts the resume to judge.
    if not (run_dir / CONFIG).is_file() or not (run_dir / "runs.jsonl").is_file():
        raise HarnessError(f"runs/{run_id}: the search never began, so there is nothing to continue; start the study again")
    study = run_study_of(root, run_dir)
    stored = state.get("plan") or _read_plan(run_dir)
    # The generations on record: the engine reports the ones *this* session
    # finishes, and the run's count is the plan's in all.
    before = max((int(r.get("generation") or 0) for r in read_jsonl(run_dir / "runs.jsonl")), default=0)
    (run_dir / STOP_REQUEST).unlink(missing_ok=True)
    job = Job.resume(run_dir, {**state, "plan": stored, "run_id": run_id, "study": study.name}, "search")
    for old in ("error", "final", "check"):  # how the last session ended; this one says anew
        job.state.pop(old, None)
    try:
        config = load_config(run_dir / CONFIG)
        summary = Driver(config, run_dir=run_dir, log=log).run()
        job.update(search={
            "stop_reason": summary.stop_reason, "generations": before + summary.generations,
            "best_id": summary.best.id if summary.best else None, "aborted": summary.aborted,
        })
        if summary.aborted:
            job.finish("failed", error=summary.stop_reason)
            return job.state
        if summary.stop_reason == "stopped on request":
            job.finish("stopped")
            return job.state
        _final_check(study, _check_plan(stored, study), config, run_dir, job, log)
    except Exception as exc:  # noqa: BLE001 - job.json must say how the job ended
        job.finish("failed", error=f"{type(exc).__name__}: {exc}")
        raise
    return job.state


def continue_study(
    root: str | Path,
    run_id: str,
    *,
    hours: float,
    models: dict[str, Any] | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """A run that finished -- `done`, or `stopped` with its search on record
    -- searched on for `hours` more on the same directory, then checked
    again. The extra time buys as many rounds as the plan would give a
    study of `hours` with this run's measured numbers (at least one), and the
    engine runs exactly those, bounded by the time: `budget.max_hours` is
    rewritten for this session to what the run has used plus `hours`, so
    the clock the engine keeps across sessions ends `hours` from now, and its
    patience grows by the rounds so a plateau the first session reached does
    not end this one at once. The final check runs again in the same
    `confirm/final/` directory: a candidate it has measured already costs
    nothing more. `job.json` keeps the run's first `started_at` and gains
    `continued_at` and `hours_added`.

    `models` is accepted as `run_study` takes it, and is not needed: the
    compiled `evolvekit.yaml` in the run directory holds the models the run
    was started with. Returns the final `job.json`."""
    from evolvekit.events import read_events
    from evolvekit.search.driver import Driver
    from evolvekit.stopping import active_seconds

    root = Path(root)
    run_dir = root / "runs" / run_id
    state = read_job(run_dir) or {}
    search = state.get("search") or {}
    if (state.get("phase") not in ("done", "stopped") or not search or search.get("aborted")
            or not (run_dir / CONFIG).is_file() or not (run_dir / "runs.jsonl").is_file()):
        raise HarnessError(f"runs/{run_id}: the search has not finished, so there is nothing to continue from")
    hours = float(hours)
    if not hours > 0:
        raise HarnessError("hours: more than 0")
    study = run_study_of(root, run_dir)
    harness = study_harness(root)
    stored = state.get("plan") or _read_plan(run_dir)
    rounds = max(1, _more_plan(study, harness, stored, hours, _run_operators(run_dir)).rounds)
    before = max((int(r.get("generation") or 0) for r in read_jsonl(run_dir / "runs.jsonl")), default=0)
    (run_dir / STOP_REQUEST).unlink(missing_ok=True)
    job = Job.resume(run_dir, {**state, "plan": stored, "run_id": run_id, "study": study.name}, "search")
    for old in ("error", "final", "check"):  # how the last session ended; this one says anew
        job.state.pop(old, None)
    # The same moment as `resumed_at`: a resume after this one is younger.
    job.update(continued_at=job.state["resumed_at"], hours_added=hours)
    log(f"continuing runs/{run_id} for {hours:g} h more: {rounds} round(s), then the final check again")
    try:
        config = load_config(run_dir / CONFIG)
        used_h = active_seconds(read_events(run_dir)) / 3600.0
        config = replace(
            config,
            budget=replace(config.budget, max_hours=used_h + hours,
                           max_full_evals_per_day=config.budget.max_full_evals_per_day
                           + rounds * config.search.children_per_generation),
            stop=replace(config.stop, patience=config.stop.patience + rounds),
        )
        summary = Driver(config, run_dir=run_dir, log=log).run(generations=rounds)
        job.update(search={
            "stop_reason": summary.stop_reason, "generations": before + summary.generations,
            "best_id": summary.best.id if summary.best else None, "aborted": summary.aborted,
        })
        if summary.aborted:
            job.finish("failed", error=summary.stop_reason)
            return job.state
        if summary.stop_reason == "stopped on request":
            job.finish("stopped")
            return job.state
        _final_check(study, _check_plan(stored, study), config, run_dir, job, log)
    except Exception as exc:  # noqa: BLE001 - job.json must say how the job ended
        job.finish("failed", error=f"{type(exc).__name__}: {exc}")
        raise
    return job.state


def _more_plan(study: Study, harness: Harness, stored: dict[str, Any], hours: float,
               operators: dict[str, float] | None = None) -> Plan:
    """The plan `hours` would buy this run: `plan_for` on the run's own
    measured numbers (`run_s` from `plan.json`, which is the time limit plus
    the overhead the preview measured, or the whole measured run when the
    application does not stop itself), not today's preview, and on the run's
    own operators (`_run_operators`)."""
    limit = float(study.limits.time_per_case_s)
    run_s = float(stored.get("run_s") or limit)
    numbers = {"overhead_s": max(0.0, run_s - limit) if harness.time_limit.accepts else 0.0, "wall_s": run_s}
    return plan_for(study, harness, numbers, hours=hours, operators=operators)


def _run_operators(run_dir: Path) -> dict[str, float] | None:
    """The operator shares a run was compiled with (`search.operators` of its
    `evolvekit.yaml`); None when it cannot be read."""
    try:
        loaded = yaml.safe_load((run_dir / CONFIG).read_text(encoding="utf-8"))
        operators = loaded["search"]["operators"]
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return None
    return {str(k): float(v) for k, v in operators.items()} if isinstance(operators, dict) else None


def _read_plan(run_dir: Path) -> dict[str, Any]:
    try:
        loaded = json.loads((run_dir / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _decision(result: dict[str, Any]) -> str:
    """What one candidate's comparison says: by the levels when the study
    scores by levels, else by `decide` on the paired summary. A candidate
    whose every run gave exactly the starting point's result is `keep`: its
    change was not exercised, so there is nothing to decide on."""
    if result.get("identical"):
        return "keep"
    if "levels" in result:
        return "use" if result["levels"]["confirmed"] else "keep"
    return decide(result["summary"])


def _extend_check(
    study: Study, plan: Plan, config: Any, run_dir: Path, job: Job, log: Callable[[str], None], *,
    stop: threading.Event, time_left_s: Callable[[], float], confirm: Callable[..., Any] = confirm,
) -> tuple[Any, int]:
    """The final check with `plan.check_seeds` runs per case, then one more
    run per case at a time while the answer is still open (`keep`), seeds
    remain, the time left pays for another and nothing asked for a stop.
    `confirm` keeps the runs it did, so every call names the whole seed list
    and only the new seed is run. Returns the last comparison and its runs
    per case; `job.json` says how many it is on (`check.seeds`)."""
    m = len(study.test)
    seed_cost_s = math.ceil(2 * m / max(1, plan.workers)) * float(plan.run_s)
    instances = [f"{UP}/{case}" for case in study.test]
    seeds = max(1, min(int(plan.check_seeds), len(CHECK_SEEDS)))
    while True:
        job.update(check={"seeds": seeds})
        comparison = confirm(config, run_dir, seeds=list(CHECK_SEEDS[:seeds]), instances=instances, label="final",
                             log=log, stop=stop)
        (_cid, result), = comparison.per_candidate.items()
        # A change every run of which gave exactly today's result: more runs cannot open the answer.
        if (_decision(result) != "keep" or result.get("identical") or seeds >= len(CHECK_SEEDS)
                or time_left_s() < seed_cost_s or stop.is_set()):
            return comparison, seeds
        seeds += 1
        log(f"final check: the answer is still open, adding a run per case ({seeds} so far)")


def _time_left(job: Job, plan: Plan) -> Callable[[], float]:
    """Seconds of the study's hours left, counted from the run's start: the
    extension of the check stays inside the time the study was given. A run
    continued with more time counts the hours added from the continuation."""
    moment, hours = job.state.get("started_at"), float(plan.hours)
    if job.state.get("continued_at") and job.state.get("hours_added"):
        moment, hours = job.state["continued_at"], float(job.state["hours_added"])
    try:
        started = datetime.fromisoformat(str(moment))
    except (ValueError, TypeError):
        return lambda: 0.0
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return lambda: hours * 3600.0 - (datetime.now(timezone.utc) - started).total_seconds()


class ExportFailed(HarnessError):
    """The harness's runner did not write an export."""


def export_path(run_dir: str | Path, name: str, case: str | None = None) -> Path:
    """Where the export `name` of a run is written: `downloads/<name>.<suffix>`,
    or `downloads/<name>-<case>.<suffix>` for a per-case export."""
    return Path(run_dir) / DOWNLOADS / f"{name}{'-' + Path(case).stem if case else ''}{EXPORT_SUFFIX.get(name, '.out')}"


def _best_values(run_dir: Path) -> dict[str, Any]:
    best = rank(list(read_jsonl(run_dir / "runs.jsonl")), 1)
    if not best:
        raise HarnessError(f"runs/{run_dir.name}: the run has no result to export yet")
    return dict(best[0].get("params") or {})


def write_exports(root: str | Path, run_dir: str | Path, values: dict[str, Any] | None = None, *,
                  names: list[str] | None = None, case: str | None = None) -> list[Path]:
    """The harness's exports of `values` (the run's best when None) into
    `runs/<id>/downloads/`, by the harness's runner: every export that applies
    to what the study tunes (settings, data), a per-case export (`requests`)
    once per case -- or only `names`, for `case` alone when given. Exports
    written from other values are removed first, so what is in the folder is
    always of one set of values. Returns the files written; raises
    `ExportFailed` when the runner does not write one."""
    root, run_dir = Path(root), Path(run_dir)
    study, harness = load_study(root), study_harness(root)
    if names is None:
        tuned = run_study_of(root, run_dir)
        kinds = ({"settings"} if tuned.tuned_settings() else set()) | ({"data"} if tuned.data else set())
        names = [name for name, export in harness.exports.items() if export.applies_to in kinds]
        cases = tuned.training + tuned.test
        if not names:  # a code study tunes nothing an export writes
            return []
    else:
        cases = [case] if case else []
    if values is None:
        values = _best_values(run_dir)
    scratch = run_dir / DOWNLOADS
    scratch.mkdir(exist_ok=True)
    stored = scratch / "values.json"
    try:
        same = json.loads(stored.read_text(encoding="utf-8")) == values
    except (OSError, ValueError):
        same = False
    if not same:
        for old in scratch.iterdir():
            if old.is_file() and old != stored:
                old.unlink()
    stored.write_text(json.dumps(values, indent=2), encoding="utf-8")
    runner = runner_command(study, harness, str(root / "harness" / "runner.py"))
    written = []
    for name in names:
        for one in (cases if name == "requests" else [case] if case else [None]):
            out = export_path(run_dir, name, one)
            argv = runner + ["export", "--format", name, "--values", str(stored), "--study", str(run_dir / STUDY_RUN),
                             "--out", str(out)]
            if one:
                argv += ["--case", str((root / one).resolve())]
            done = subprocess.run(argv, cwd=run_dir, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=EXPORT_TIMEOUT_S)
            if done.returncode != 0 or not out.is_file():
                said = (done.stderr.strip().splitlines() or ["no message"])[-1]
                raise ExportFailed(f"the export {name} failed: {said}")
            written.append(out)
    return written


def _write_exports(run_dir: Path, log: Callable[[str], None]) -> None:
    """The exports of the run's best, so a finished run always has its files;
    the answer stands without them when the runner cannot write one."""
    try:
        write_exports(run_dir.parent.parent, run_dir)
    except Exception as exc:  # noqa: BLE001 - an export is a convenience, the verdict is the result
        log(f"final check: the exports could not be written: {exc}")


def _final_check(study: Study, plan: Plan, config: Any, run_dir: Path, job: Job, log: Callable[[str], None], *,
                 extend: bool = True) -> None:
    """The best against the starting point on the held-back cases; with
    `extend`, more runs per case while time remains and the answer is open."""
    if not study.test:
        _write_exports(run_dir, log)
        job.finish("done", final={"skipped": "no cases were held back, so there is no final check"})
        return
    rows = list(read_jsonl(run_dir / "runs.jsonl"))
    best = rank(rows, 1)
    if not best or best[0].get("operator") == "human-seed":
        if best:
            _write_exports(run_dir, log)
        job.finish("done", final={"skipped": "the starting point stayed the best: there is nothing to check"})
        return
    job.update(phase="check")
    stop = RunStop(run_dir)
    stop.start(0.0)
    try:
        comparison, seeds = _extend_check(
            study, plan, config, run_dir, job, log, stop=stop.event,
            time_left_s=_time_left(job, plan) if extend else (lambda: 0.0),
        )
    except ConfirmStopped:
        job.finish("stopped", final={"stopped": True})
        return
    finally:
        stop.close()
    (cid, result), = comparison.per_candidate.items()
    decision = _decision(result)
    _write_exports(run_dir, log)
    job.finish("done", final={"candidate": cid, "confirmed": decision == "use", "decision": decision,
                              "seeds": seeds, "comparison": "confirm/final/comparison.json"})
