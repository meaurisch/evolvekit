"""Runs of a study: started as detached processes, stopped with a file, and
read back for the page -- the status in plain words, the results, the
downloads, and `report.html`.

A run is `python -m evolvekit.app.job STUDY RUN` (`job.py`), started so that
it outlives the app: on Windows `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP |
CREATE_NO_WINDOW`, elsewhere a session of its own. Its output goes to
`runs/<id>/job.log`, the API keys it may need go into its environment
explicitly, and `job.json` says which phase it is in. The app never keeps a
handle on it: everything here is read from the run directory.
"""

from __future__ import annotations

import html
import io
import json
import math
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Callable

from evolvekit.app import AppError
from evolvekit.confirm import BASELINE
from evolvekit.harness.compile import STUDY_RUN
from evolvekit.harness.execute import read_job, runner_command
from evolvekit.harness.manifest import Harness, load_harness
from evolvekit.harness.study import Study, load_study, resolve_kpi, save_study, study_problems
from evolvekit.ledger import read_jsonl
from evolvekit.lock import pid_alive
from evolvekit.stopping import STOP_REQUEST

__all__ = ["download", "latest_run", "next_study", "phase", "report_html", "results", "resume_final_check",
           "running", "runs", "start", "state_line", "status", "stop"]

LAUNCH = "launch.json"
MODELS = "models.json"
LOG = "job.log"
EXPORT_TIMEOUT_S = 300
PRICES = {"anthropic/claude-sonnet-5": (2.0, 10.0)}
"""USD per million input and output tokens, for the models the app suggests."""
_RUN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
DocumentOf = Callable[[Path], Any]


def _read(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _alive(pid: Any) -> bool:
    try:
        return bool(pid) and pid_alive(int(pid))
    except (TypeError, ValueError):
        return False


def _log_tail(run_dir: Path, lines: int = 3) -> str:
    try:
        text = (run_dir / LOG).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "there is no job.log"
    said = [line for line in text.strip().splitlines() if line.strip()]
    return " / ".join(said[-lines:]) if said else "job.log is empty"


def _run_dirs(root: Path) -> list[Path]:
    folder = root / "runs"
    if not folder.is_dir():
        return []
    found = [p for p in folder.iterdir() if p.is_dir() and _RUN.match(p.name)]

    def started(path: Path) -> str:
        launch, job = _read(path / LAUNCH) or {}, read_job(path) or {}
        return str(launch.get("started_at") or job.get("started_at") or "")

    return sorted(found, key=lambda p: (started(p), p.name))


def _run_dir(root: Path, run: str) -> Path:
    if not isinstance(run, str) or not _RUN.match(run) or not (root / "runs" / run).is_dir():
        raise AppError(f"there is no run {run!r} in this study", 404)
    return root / "runs" / run


def phase(run_dir: Path) -> dict[str, Any]:
    """`job.json`, with the phase corrected for a process that is gone."""
    job = read_job(run_dir)
    launch = _read(run_dir / LAUNCH) or {}
    if job is None:
        if launch and _alive(launch.get("pid")):
            return {"phase": "starting", "started_at": launch.get("started_at")}
        if launch:
            return {"phase": "failed", "started_at": launch.get("started_at"),
                    "error": f"the run did not start: {_log_tail(run_dir)}"}
        return {"phase": "unknown"}
    if job.get("phase") in ("search", "check") and not _alive(job.get("pid")):
        return {**job, "phase": "failed", "error": f"the run's process ended without finishing: {_log_tail(run_dir)}"}
    return job


def runs(root: Path) -> list[dict[str, Any]]:
    listed = []
    for run_dir in _run_dirs(root):
        job = phase(run_dir)
        listed.append({"id": run_dir.name, "phase": job.get("phase"), "started_at": job.get("started_at"),
                       "finished_at": job.get("finished_at")})
    return listed


def latest_run(root: Path) -> str | None:
    dirs = _run_dirs(root)
    return dirs[-1].name if dirs else None


def running(root: Path) -> bool:
    return any(phase(d).get("phase") in ("starting", "search", "check") for d in _run_dirs(root))


# ---------------------------------------------------------------------------
# starting and stopping
# ---------------------------------------------------------------------------


def ai_models(home: Any) -> dict[str, Any] | None:
    """The engine's `models` section for AI search help, from the app's
    settings; `None` when there is no key for the chosen provider."""
    settings = home.settings()
    provider = settings["provider"]
    keys = home.keys()
    needed = {"openrouter": ["OPENROUTER_API_KEY"], "azure": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"]}.get(provider, [])
    if any(not keys.get(name) for name in needed):
        return None
    model = settings["models"]["search"]
    price_in, price_out = PRICES.get(model, (0.0, 0.0))
    slot = {"provider": provider, "model": model, "price_in_per_mtok": price_in, "price_out_per_mtok": price_out,
            "max_tokens": 4096}
    return {"small": dict(slot), "strong": dict(slot)}


def _launch(home: Any, root: Path, run_dir: Path, extra: list[str]) -> int:
    env = {**os.environ, **home.job_environment(), "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    argv = [sys.executable, "-m", "evolvekit.app.job", str(root), run_dir.name, *extra]
    with open(run_dir / LOG, "ab") as log:
        options: dict[str, Any] = {"cwd": str(root), "stdin": subprocess.DEVNULL, "stdout": log,
                                   "stderr": subprocess.STDOUT, "env": env, "close_fds": True}
        if os.name == "nt":
            options["creationflags"] = _DETACHED
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(argv, **options)
    (run_dir / LAUNCH).write_text(json.dumps({"pid": process.pid, "started_at": _now().isoformat(timespec="seconds"),
                                              "argv": argv[1:]}, indent=2), encoding="utf-8")
    return process.pid


def start(home: Any, root: Path) -> dict[str, Any]:
    if running(root):
        raise AppError("the study is already running", 409)
    study, harness = load_study(root), load_harness(root / "harness")
    problems = study_problems(study, harness, root=root)
    if problems:
        raise AppError(f"the study is not ready yet: {problems[0]}", 409)
    models = None
    if study.budget.ai_enabled:
        models = ai_models(home)
        if models is None:
            raise AppError("AI search help is on, and there is no key for it: add one in Settings, or turn AI help off", 409)
    run_id = _now().astimezone().strftime("%Y%m%d-%H%M%S")
    number = 2
    while (root / "runs" / run_id).exists():
        run_id, number = f"{run_id.split('~')[0]}~{number}", number + 1
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True)
    if models:
        (run_dir / MODELS).write_text(json.dumps(models, indent=2), encoding="utf-8")
    _launch(home, root, run_dir, [])
    return {"run": run_id}


def stop(root: Path, run: str) -> dict[str, Any]:
    run_dir = _run_dir(root, run)
    if phase(run_dir).get("phase") not in ("starting", "search", "check"):
        raise AppError("the run is not running", 409)
    (run_dir / STOP_REQUEST).write_text("stop\n", encoding="utf-8")
    return {"stopping": True, "run": run}


def resume_final_check(home: Any, root: Path, run: str) -> dict[str, Any]:
    run_dir = _run_dir(root, run)
    if running(root):
        raise AppError("the study is running", 409)
    job = phase(run_dir)
    if not job.get("search") or (job.get("search") or {}).get("aborted"):
        raise AppError("the search of this run did not finish, so there is nothing to check", 409)
    if not load_study(root).test:
        raise AppError("the study holds no cases back, so there is no final check", 409)
    _launch(home, root, run_dir, ["--check"])
    return {"run": run}


# ---------------------------------------------------------------------------
# reading a run
# ---------------------------------------------------------------------------


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{round(seconds)} s"
    if seconds < 5400:
        return f"{round(seconds / 60)} min"
    hours, minutes = divmod(round(seconds / 60), 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def _pct(value: float | None, digits: int = 1) -> str:
    return "" if value is None else f"{abs(value):.{digits}f} %"


def _goal(study: Study, harness: Harness) -> dict[str, Any]:
    level = study.goal[0] if study.goal else None
    if level is None:
        return {"kpi": "", "says": "the goal", "direction": "lower"}
    try:
        says = resolve_kpi(study, harness, level.kpi).get("says") or level.kpi
    except KeyError:
        says = level.kpi
    return {"kpi": level.kpi, "says": says, "direction": level.direction}


def _better_word(direction: str) -> str:
    return "lower" if direction == "lower" else "higher"


def _what(study: Study) -> str:
    settings, data = bool(study.tuned_settings()), bool(study.data)
    return "combination of settings and data changes" if settings and data else "data changes" if data else "settings"


def _rows(run_dir: Path) -> list[dict[str, Any]]:
    return list(read_jsonl(run_dir / "runs.jsonl"))


def _plan(run_dir: Path, job: dict[str, Any]) -> dict[str, Any]:
    return job.get("plan") or _read(run_dir / "plan.json") or {}


def _started(job: dict[str, Any], run_dir: Path) -> datetime | None:
    raw = job.get("started_at") or (_read(run_dir / LAUNCH) or {}).get("started_at")
    try:
        return datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def _failures_in_words(reasons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = []
    for reason in reasons:
        text, count = str(reason.get("failure") or "failed"), int(reason.get("count") or 0)
        low = text.lower()
        if "timed out" in low or "timeout" in low or "no result after" in low:
            todo = "These runs took longer than allowed. If it happens often, give each case more time, or check the application."
        elif "exit code 2" in low or "constraint" in low or "invalid" in low:
            todo = "These combinations broke a rule of the study before solving; the search simply skips them."
        elif "exit code 3" in low or "cannot read" in low:
            todo = "A case could not be read. Check the cases in step 3."
        else:
            todo = "Runs failed with an error. Open the detailed dashboard to see the message; if every run fails, run the test run again."
        grouped.append({"count": count, "stage": reason.get("stage"), "message": text, "todo": todo})
    return grouped


def status(root: Path, run: str, document_of: DocumentOf) -> dict[str, Any]:
    """Where a run is, in the words the Running screen uses."""
    run_dir = _run_dir(root, run)
    study, harness = load_study(root), load_harness(root / "harness")
    job = phase(run_dir)
    plan = _plan(run_dir, job)
    document = document_of(run_dir) if (run_dir / "events.jsonl").exists() else {}
    health, progress = document.get("health") or {}, document.get("progress") or {}
    goal = _goal(study, harness)
    started = _started(job, run_dir)
    hours = float(plan.get("hours") or study.budget.hours)
    used = (_now() - started).total_seconds() if started else None
    finished = job.get("phase") in ("done", "stopped", "failed")
    left = None if used is None or finished else max(0.0, hours * 3600 - used)
    finish_at = (started + timedelta(hours=hours)).astimezone().strftime("%H:%M") if started and not finished else None

    generation = (health.get("generation") or {})
    done_rounds = generation.get("last_finished")
    rounds = {"done": max(0, int(done_rounds)) if isinstance(done_rounds, int) else 0,
              "planned": int(plan.get("rounds") or 0)}
    improvement = (progress.get("improvement") or {})
    pct, verdict = improvement.get("pct"), improvement.get("verdict")
    better = _better_word(goal["direction"])
    if pct is None or verdict in ("none", None) or (pct or 0) <= 0:
        best_line = "No combination has beaten the starting point yet."
    else:
        noise = {"clear": "clearly better", "within noise": "within the noise so far", "worse": "worse",
                 "unknown": "too early to tell"}.get(str(verdict), str(verdict))
        best_line = f"{_pct(pct)} {better} {goal['says']} than the starting point ({noise})."
    levels = (progress.get("levels") or {})
    series = [[s.get("elapsed_s"), s.get("improvement_pct")] for s in progress.get("series") or []
              if s.get("elapsed_s") is not None]

    stage = health.get("stage") or {}
    current = generation.get("current")
    if job.get("phase") == "check":
        now_line = "The final check: the best against the starting point on the held-back cases."
    elif stage and isinstance(current, int):
        what = "measuring the starting point" if current == 0 else f"round {current}"
        cases = len(study.training)
        now_line = (f"{what[0].upper() + what[1:]}: {stage.get('candidates')} combination{'s' if stage.get('candidates') != 1 else ''} "
                    f"on {cases} case{'s' if cases != 1 else ''}, {stage.get('runs_done')} of {stage.get('runs_planned')} runs done.")
    elif job.get("phase") in ("starting", "search"):
        now_line = "Getting ready: preparing the runs."
    else:
        now_line = ""

    sentences = {
        "starting": "Starting.",
        "search": "Searching for better " + _what(study) + ".",
        "check": "Checking the best on the cases the search never saw.",
        "done": "Finished.",
        "stopped": "Stopped." + (f" {job['search'].get('stop_reason')}." if (job.get("search") or {}).get("stop_reason") else ""),
        "failed": "It failed: " + str(job.get("error") or "no reason recorded") + ".",
    }
    gated = sum(1 for r in _rows(run_dir) if r.get("gated"))
    return {
        "run": run,
        "phase": job.get("phase"),
        "sentence": sentences.get(str(job.get("phase")), ""),
        "error": job.get("error"),
        "time": {"used_s": used, "left_s": left, "finish_at": finish_at, "used": _duration(used), "left": _duration(left),
                 "hours": hours},
        "rounds": rounds,
        "goal": goal,
        "best": {"pct": pct, "verdict": verdict, "line": best_line,
                 "levels": levels.get("levels") if levels.get("available") else None,
                 "levels_verdict": levels.get("verdict") if levels.get("available") else None},
        "series": series,
        "now": now_line,
        "failures": _failures_in_words((health.get("evaluations") or {}).get("failure_reasons") or []),
        "gated": gated,
        "gated_line": (f"{gated} combination{'s' if gated != 1 else ''} broke a guardrail and {'do' if gated != 1 else 'does'} not count."
                       if gated else ""),
        "final": job.get("final"),
        "can_check": bool(job.get("search")) and job.get("phase") in ("stopped", "failed") and bool(study.test),
        "dashboard": f"/studies/{root.name}/runs/{run}/dashboard/",
    }


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


def _seed_and_best(run_dir: Path, document: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    rows = _rows(run_dir)
    seed = next((r for r in rows if r.get("operator") == "human-seed"), None)
    best_id = ((document.get("progress") or {}).get("best") or {}).get("id")
    best = next((r for r in rows if r.get("id") == best_id), None) if best_id else None
    return seed, best


def _test_kpis(comparison: dict[str, Any], candidate: str) -> dict[str, dict[str, float]]:
    """The mean of every KPI over the held-back cases and seeds, per side."""
    sums: dict[str, dict[str, list[float]]] = {BASELINE: {}, candidate: {}}
    for run in comparison.get("runs") or []:
        side = run.get("configuration")
        if side not in sums or not run.get("ok"):
            continue
        for name, value in (run.get("kpis") or {}).items():
            if isinstance(value, (int, float)) and math.isfinite(value):
                sums[side].setdefault(name, []).append(float(value))
    return {side: {k: fmean(v) for k, v in kpis.items() if v} for side, kpis in sums.items()}


def _final_words(job: dict[str, Any], comparison: dict[str, Any] | None, goal: dict[str, Any]) -> dict[str, Any]:
    final = job.get("final") or {}
    if final.get("skipped"):
        return {"state": "skipped", "line": final["skipped"][0].upper() + final["skipped"][1:] + "."}
    if final.get("stopped"):
        return {"state": "stopped", "line": "The final check was stopped before it finished; it can be run again."}
    if comparison is None:
        return {"state": "missing", "line": "There is no final check for this run."}
    candidate = final.get("candidate") or next(iter(comparison.get("per_candidate") or {}), None)
    result = (comparison.get("per_candidate") or {}).get(candidate) or {}
    summary = result.get("summary") or {}
    n, mean, ci = summary.get("n") or 0, summary.get("mean"), summary.get("ci95")
    better = _better_word(goal["direction"])
    worse = "higher" if better == "lower" else "lower"
    cases = f"{n} case{'s' if n != 1 else ''} the search never saw"
    if "levels" in result:
        levels = result["levels"]
        state = "confirmed" if levels.get("confirmed") else "unclear"
        return {"state": state, "line": ("Confirmed on " if levels.get("confirmed") else "Not clear on ") + cases + ": "
                + str(levels.get("verdict")) + ".", "levels": levels.get("levels"), "n": n}
    if mean is None:
        return {"state": "unclear", "line": "The final check has no comparable results.", "n": n}
    word = better if mean >= 0 else worse
    if ci is None:
        return {"state": "single", "n": n, "mean": mean,
                "line": f"On the one held-back case: {_pct(mean)} {word} {goal['says']}. One case gives no interval: "
                        "hold back more cases for an answer to rely on."}
    low, high = ci
    span = f"likely between {low:+.1f} % and {high:+.1f} % better"
    if low > 0:
        return {"state": "confirmed", "n": n, "mean": mean, "ci95": ci,
                "line": f"Confirmed: {_pct(mean)} {better} {goal['says']} on {cases} ({span})."}
    if high < 0:
        return {"state": "worse", "n": n, "mean": mean, "ci95": ci,
                "line": f"Worse on {cases}: {_pct(mean)} {worse} {goal['says']} ({span})."}
    return {"state": "unclear", "n": n, "mean": mean, "ci95": ci,
            "line": f"Not distinguishable from your starting point on {cases} ({span})."}


def _describe_value(value: Any) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def results(root: Path, run: str, document_of: DocumentOf) -> dict[str, Any]:
    run_dir = _run_dir(root, run)
    study, harness = load_study(root), load_harness(root / "harness")
    job = phase(run_dir)
    document = document_of(run_dir) if (run_dir / "events.jsonl").exists() else {}
    goal = _goal(study, harness)
    seed, best = _seed_and_best(run_dir, document)
    improvement = ((document.get("progress") or {}).get("improvement") or {})
    pct = improvement.get("pct")
    what = _what(study)
    if best is None or seed is None:
        headline = "The search has no finished result yet."
    elif best.get("id") == seed.get("id") or not pct or pct <= 0:
        headline = f"No {what} found beat your starting point on the training cases."
    else:
        headline = (f"The best {what} found give{'s' if what.startswith('combination') else ''} {_pct(pct)} "
                    f"{_better_word(goal['direction'])} {goal['says']} than your starting point on the training cases.")
    comparison = None
    if (job.get("final") or {}).get("comparison"):
        comparison = _read(run_dir / job["final"]["comparison"])
    final = _final_words(job, comparison, goal)
    test = _test_kpis(comparison, (job.get("final") or {}).get("candidate") or "") if comparison else {}

    kpis = []
    for name in study.kpis:
        try:
            resolved = resolve_kpi(study, harness, name)
        except KeyError:
            continue
        kpis.append({
            "name": name, "says": resolved.get("says") or name, "unit": resolved.get("unit") or "",
            "direction": resolved.get("direction", "lower"),
            "training": {"start": (seed or {}).get("kpis", {}).get(name), "best": (best or {}).get("kpis", {}).get(name)},
            "test": {"start": test.get(BASELINE, {}).get(name), "best": test.get((job.get("final") or {}).get("candidate") or "", {}).get(name)},
        })
    guardrails = [{"kpi": g.kpi, "max": g.max, "min": g.min} for g in study.guardrails]

    changed = []
    starting = (seed or {}).get("params") or {}
    found = (best or {}).get("params") or {}
    for name in study.tunables():
        old, new = starting.get(name), found.get(name, starting.get(name))
        if name in harness.settings:
            setting = harness.settings[name]
            label, help_text = setting.label, setting.help
        else:
            change = study.data[name]
            lever = harness.levers.get(change.lever)
            label = change.says or f"{lever.label if lever else change.lever}: {change.column or 'in code'} where {change.where or 'all rows'} ({change.mode})"
            help_text = lever.help if lever else ""
        changed.append({"name": name, "label": label, "help": help_text, "old": old, "new": new,
                        "old_text": _describe_value(old), "new_text": _describe_value(new),
                        "changed": old != new})
    kinds = {"settings"} if study.tuned_settings() else set()
    if study.data:
        kinds.add("data")
    downloads = [{"name": name, "label": export.label} for name, export in harness.exports.items() if export.applies_to in kinds]
    downloads.append({"name": "report", "label": "The report (report.html), for sharing"})
    return {
        "run": run, "phase": job.get("phase"), "study": study.name, "harness": harness.title,
        "headline": headline, "improvement_pct": pct, "goal": goal, "final": final, "kpis": kpis,
        "guardrails": guardrails, "changed": changed, "downloads": downloads,
        "best_id": (best or {}).get("id"), "is_seed": bool(best and seed and best.get("id") == seed.get("id")),
        "cases": {"training": list(study.training), "test": list(study.test)},
    }


# ---------------------------------------------------------------------------
# downloads
# ---------------------------------------------------------------------------


def _best_values(run_dir: Path, document_of: DocumentOf) -> dict[str, Any]:
    document = document_of(run_dir) if (run_dir / "events.jsonl").exists() else {}
    _, best = _seed_and_best(run_dir, document)
    if best is None:
        raise AppError("the run has no result to download yet", 409)
    return dict(best.get("params") or {})


def _export(root: Path, run_dir: Path, name: str, values: dict[str, Any], case: str | None = None) -> bytes:
    study, harness = load_study(root), load_harness(root / "harness")
    scratch = run_dir / "downloads"
    scratch.mkdir(exist_ok=True)
    (scratch / "values.json").write_text(json.dumps(values, indent=2), encoding="utf-8")
    suffix = {"settings_json": ".json", "settings_flags": ".txt", "data_changes": ".json", "requests": ".json"}.get(name, ".out")
    out = scratch / f"{name}{'-' + Path(case).stem if case else ''}{suffix}"
    argv = runner_command(study, harness, str(root / "harness" / "runner.py")) + [
        "export", "--format", name, "--values", str(scratch / "values.json"), "--study", str(run_dir / STUDY_RUN),
        "--out", str(out)]
    if case:
        argv += ["--case", str(root / case)]
    done = subprocess.run(argv, cwd=run_dir, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=EXPORT_TIMEOUT_S)
    if done.returncode != 0 or not out.is_file():
        said = (done.stderr.strip().splitlines() or ["no message"])[-1]
        raise AppError(f"the export {name} failed: {said}", 500)
    return out.read_bytes()


def download(root: Path, run: str, export: str, document_of: DocumentOf) -> tuple[bytes, str, str]:
    """`(bytes, content type, file name)` of a download."""
    run_dir = _run_dir(root, run)
    slug = root.name
    if export == "report":
        data = results(root, run, document_of)
        return report_html(data).encode("utf-8"), "text/html; charset=utf-8", f"{slug}-report.html"
    harness = load_harness(root / "harness")
    if export not in harness.exports:
        raise AppError(f"the harness offers no download {export!r}", 404)
    values = _best_values(run_dir, document_of)
    if export == "requests":
        study = load_study(root)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for case in study.training + study.test:
                archive.writestr(f"{Path(case).stem}-changed.json", _export(root, run_dir, export, values, case))
        return buffer.getvalue(), "application/zip", f"{slug}-changed-requests.zip"
    body = _export(root, run_dir, export, values)
    if export == "data_changes":
        csv = (run_dir / "downloads" / "data_changes.csv")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("data-changes.json", body)
            if csv.is_file():
                archive.writestr("data-changes.csv", csv.read_bytes())
        return buffer.getvalue(), "application/zip", f"{slug}-data-changes.zip"
    names = {"settings_json": (f"{slug}-settings.json", "application/json"),
             "settings_flags": (f"{slug}-settings-flags.txt", "text/plain; charset=utf-8")}
    filename, content_type = names.get(export, (f"{slug}-{export}.out", "application/octet-stream"))
    return body, content_type, filename


def report_html(data: dict[str, Any]) -> str:
    """The results as one self-contained page, for sharing."""
    esc = html.escape

    def number(value: Any) -> str:
        if value is None:
            return "–"
        if isinstance(value, float):
            return f"{value:,.4g}" if abs(value) < 1e4 else f"{value:,.0f}"
        return esc(str(value))

    kpi_rows = "".join(
        f"<tr><td>{esc(k['says'])}{' (' + esc(k['unit']) + ')' if k['unit'] else ''}</td>"
        f"<td>{number(k['training']['start'])}</td><td>{number(k['training']['best'])}</td>"
        f"<td>{number(k['test']['start'])}</td><td>{number(k['test']['best'])}</td></tr>"
        for k in data["kpis"]
    )
    changed_rows = "".join(
        f"<tr><td>{esc(c['label'])}<div class='help'>{esc(c['help'])}</div></td><td>{esc(c['old_text'])}</td>"
        f"<td>{'<b>' if c['changed'] else ''}{esc(c['new_text'])}{'</b>' if c['changed'] else ''}</td></tr>"
        for c in data["changed"]
    )
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(data['study'])}: results</title>
<style>
:root {{ color-scheme: light dark; --ink:#0b0b0b; --ink-2:#52514e; --page:#f6f6f3; --surface:#fff; --hair:rgba(11,11,11,.12); --good:#006300; --bad:#b42525; }}
@media (prefers-color-scheme: dark) {{ :root {{ --ink:#fff; --ink-2:#c3c2b7; --page:#0d0d0d; --surface:#1a1a19; --hair:rgba(255,255,255,.14); --good:#4fd14f; --bad:#ef6a6a; }} }}
body {{ margin:0; background:var(--page); color:var(--ink); font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
main {{ max-width:880px; margin:0 auto; padding:32px 20px 64px; }}
h1 {{ font-size:24px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:32px 0 8px; }}
.meta {{ color:var(--ink-2); font-size:13px; }}
.card {{ background:var(--surface); border:1px solid var(--hair); border-radius:12px; padding:16px 18px; margin-top:12px; }}
.headline {{ font-size:18px; font-weight:600; }}
.final[data-state=confirmed] {{ border-left:4px solid var(--good); }} .final[data-state=worse] {{ border-left:4px solid var(--bad); }}
table {{ width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }}
th, td {{ text-align:left; padding:8px 6px; border-bottom:1px solid var(--hair); vertical-align:top; }}
th {{ font-size:12px; color:var(--ink-2); font-weight:600; }}
.help {{ color:var(--ink-2); font-size:12px; }}
.wrap {{ overflow-x:auto; }}
</style></head><body><main>
<h1>{esc(data['study'])}</h1>
<div class="meta">{esc(data['harness'])} · run {esc(data['run'])} · report made {generated}</div>
<div class="card"><div class="headline">{esc(data['headline'])}</div></div>
<h2>The final check</h2>
<div class="card final" data-state="{esc(str(data['final'].get('state')))}">{esc(data['final']['line'])}</div>
<h2>Every measure</h2>
<div class="card wrap"><table><thead><tr><th>Measure</th><th>Start (training)</th><th>Best (training)</th><th>Start (test)</th><th>Best (test)</th></tr></thead>
<tbody>{kpi_rows}</tbody></table></div>
<h2>What changed</h2>
<div class="card wrap"><table><thead><tr><th>What</th><th>Before</th><th>Now</th></tr></thead><tbody>{changed_rows}</tbody></table></div>
<p class="meta">Training cases: {esc(', '.join(Path(c).name for c in data['cases']['training']))}.
Held back for the final check: {esc(', '.join(Path(c).name for c in data['cases']['test']) or 'none')}.</p>
</main></body></html>
"""


# ---------------------------------------------------------------------------
# the home card, and a study from a result
# ---------------------------------------------------------------------------


def state_line(root: Path, study: Study, document_of: DocumentOf | None = None) -> dict[str, Any]:
    """The home card's state in a few words: "draft · step 4 of 7", "running ·
    1 h 10 min left · 3.2 % better so far", "finished · confirmed 3.1 % lower
    real cost"."""
    dirs = _run_dirs(root)
    if not dirs:
        return {"kind": "draft", "line": f"draft · step {max(1, min(study.step, 7))} of 7"}
    run_dir = dirs[-1]
    job = phase(run_dir)
    state = job.get("phase")
    try:
        goal = _goal(study, load_harness(root / "harness"))
    except Exception:  # noqa: BLE001 - a card must render even for a damaged study
        goal = {"says": "the goal", "direction": "lower"}
    if state in ("starting", "search", "check"):
        started = _started(job, run_dir)
        hours = float((_plan(run_dir, job) or {}).get("hours") or study.budget.hours)
        left = max(0.0, hours * 3600 - (_now() - started).total_seconds()) if started else None
        line = "running" + (f" · {_duration(left)} left" if left is not None else "")
        if document_of is not None and (run_dir / "events.jsonl").exists():
            pct = (((document_of(run_dir) or {}).get("progress") or {}).get("improvement") or {}).get("pct")
            if pct is not None and pct > 0:
                line += f" · {_pct(pct)} better so far"
        return {"kind": "running", "run": run_dir.name, "line": line}
    if state == "done":
        final = job.get("final") or {}
        if final.get("skipped"):
            return {"kind": "finished", "run": run_dir.name, "line": "finished · no final check"}
        comparison = _read(run_dir / final["comparison"]) if final.get("comparison") else None
        summary = (((comparison or {}).get("per_candidate") or {}).get(final.get("candidate") or "") or {}).get("summary") or {}
        mean = summary.get("mean")
        if final.get("confirmed"):
            amount = f" {_pct(mean)} {_better_word(goal['direction'])} {goal['says']}" if isinstance(mean, (int, float)) else ""
            return {"kind": "finished", "run": run_dir.name, "line": "finished · confirmed" + amount}
        return {"kind": "finished", "run": run_dir.name, "line": "finished · not confirmed on the held-back cases"}
    if state == "stopped":
        return {"kind": "stopped", "run": run_dir.name, "line": "stopped"}
    return {"kind": "failed", "run": run_dir.name, "line": "failed · " + str(job.get("error") or "see the run")[:80]}


def next_study(home: Any, root: Path, run: str, name: str = "") -> str:
    """A new study that starts where this result ends: the same harness,
    cases and choices, with the best settings as its starting settings."""
    import copy

    from evolvekit.status import build_status

    run_dir = _run_dir(root, run)
    study = load_study(root)
    _, best = _seed_and_best(run_dir, build_status(run_dir))
    if best is None:
        raise AppError("the run has no result to start from", 409)
    slug = home.create_study(study.harness_id, study.template or None, name or f"{study.name} (next)")
    new_root = home.study_root(slug)
    fresh = copy.deepcopy(study)
    fresh.name = load_study(new_root).name
    for sub in ("cases", "inputs"):
        source = root / sub
        if source.is_dir():
            for path in source.iterdir():
                if path.is_file():
                    (new_root / sub / path.name).write_bytes(path.read_bytes())
    tuned = {k: v for k, v in (best.get("params") or {}).items() if k in study.tuned_settings()}
    if tuned:
        harness = load_harness(new_root / "harness")
        settings_input = next((n for n, s in harness.inputs.items() if s.provides == "settings"), None)
        if settings_input:
            base: dict[str, Any] = {}
            if study.inputs.get(settings_input):
                try:
                    base = json.loads((root / study.inputs[settings_input]).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    base = {}
            (new_root / "inputs" / "start-settings.json").write_text(json.dumps({**base, **tuned}, indent=2), encoding="utf-8")
            fresh.inputs[settings_input] = "inputs/start-settings.json"
            for key in tuned:  # the tuned settings start from the file, that is from the result
                if key in fresh.settings and fresh.settings[key].mode == "tune":
                    fresh.settings[key].start = None
        else:
            for key, value in tuned.items():
                if key in fresh.settings and fresh.settings[key].mode == "tune":
                    fresh.settings[key].start = value
    for key, change in fresh.data.items():  # a data change starts where the result left it
        value = (best.get("params") or {}).get(key)
        if isinstance(value, (int, float)) and change.low <= value <= change.high:
            change.start = float(value)
    fresh.step = 4
    save_study(fresh, new_root)
    return slug
