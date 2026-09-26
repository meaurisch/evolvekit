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
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from evolvekit.config import load_config
from evolvekit.confirm import ConfirmStopped, confirm
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
    write_run,
)
from evolvekit.harness.manifest import Harness, load_harness
from evolvekit.harness.plan import CHECK_SEEDS, Plan, make_plan
from evolvekit.harness.sql import schema_database, sql_problem
from evolvekit.harness.study import Study, load_study, require_valid, resolve_kpi
from evolvekit.leaderboard import rank
from evolvekit.ledger import _atomic_write, read_jsonl
from evolvekit.stopping import STOP_REQUEST, RunStop

__all__ = [
    "JOB",
    "PREVIEW",
    "Job",
    "plan_for",
    "preview",
    "read_job",
    "read_preview",
    "run_study",
    "runner_command",
    "smallest_case",
    "study_harness",
    "run_test",
]

PREVIEW = "preview"
JOB = "job.json"


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


def preview(root: str | Path) -> dict[str, Any]:
    """Solve the smallest training case at the starting values; keep the
    tables and the timing in `preview/`. Returns what `preview/preview.json` holds."""
    root = Path(root)
    study = load_study(root)
    harness = study_harness(root)
    case = smallest_case(root, study)
    base = base_settings(study, harness, root)
    kept = replace(study, constraints=[])  # the preview measures; it does not judge
    study_run = runner_input(kept, harness, base, kpis=_preview_kpis(study, harness), guardrails=False, up="..")
    result = _solve_once(
        root, study, harness, root / PREVIEW, case=case, values=_starting_values(study, base),
        study_run=study_run, time_limit=float(study.limits.time_per_case_s), tables_out=True, up="..",
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


def plan_for(study: Study, harness: Harness, measured: dict[str, Any]) -> Plan:
    """The automatic plan, from the preview's measured time."""
    overrides = None if study.plan.get("auto", True) else {k: v for k, v in study.plan.items() if k != "auto"}
    return make_plan(
        training=len(study.training), test=len(study.test), runs_per_case=study.limits.runs_per_case,
        time_limit_s=float(study.limits.time_per_case_s), hours=float(study.budget.hours),
        overhead_s=float(measured.get("overhead_s") or 0.0), measured_s=float(measured.get("wall_s") or study.limits.time_per_case_s),
        accepts_time_limit=harness.time_limit.accepts, overrides=overrides,
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
    harness = study_harness(root)
    require_valid(study, harness, root=root)
    plan = plan_for(study, harness, measured(root))
    run_id = run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = write_run(study, harness, plan, root, run_id, models=models)
    (run_dir / STOP_REQUEST).unlink(missing_ok=True)
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


def _final_check(study: Study, plan: Plan, config: Any, run_dir: Path, job: Job, log: Callable[[str], None]) -> None:
    if not study.test:
        job.finish("done", final={"skipped": "no test cases were held back, so there is no final check"})
        return
    rows = list(read_jsonl(run_dir / "runs.jsonl"))
    best = rank(rows, 1)
    if not best or best[0].get("operator") == "human-seed":
        job.finish("done", final={"skipped": "the starting point stayed the best: there is nothing to check"})
        return
    job.update(phase="check")
    stop = RunStop(run_dir)
    stop.start(0.0)
    try:
        comparison = confirm(
            config, run_dir, seeds=list(CHECK_SEEDS[: plan.check_seeds]),
            instances=[f"{UP}/{case}" for case in study.test], label="final", log=log, stop=stop.event,
        )
    except ConfirmStopped:
        job.finish("stopped", final={"stopped": True})
        return
    finally:
        stop.close()
    (cid, result), = comparison.per_candidate.items()
    confirmed = result["levels"]["confirmed"] if "levels" in result else bool((result["summary"].get("ci95") or [0.0])[0] > 0)
    job.finish("done", final={"candidate": cid, "confirmed": confirmed,
                              "comparison": "confirm/final/comparison.json"})
