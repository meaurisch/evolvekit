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
import sqlite3
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
from evolvekit.harness.plan import CHECK_SEEDS
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
_BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
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
        return {**job, "phase": "failed",
                "error": "the run's process ended without finishing: it was ended from outside, or the computer "
                         f"restarted. The last thing it wrote: {_log_tail(run_dir, 1)}"}
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


_LAUNCHER = r"""
import json, os, subprocess, sys
argv, cwd, log = json.loads(sys.argv[1]), sys.argv[2], sys.argv[3]
with open(log, "ab") as out:
    options = dict(cwd=cwd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, close_fds=True)
    if os.name == "nt":
        try:
            process = subprocess.Popen(argv, creationflags=%d, **options)
        except OSError:
            process = subprocess.Popen(argv, creationflags=%d, **options)
    else:
        process = subprocess.Popen(argv, start_new_session=True, **options)
print(process.pid)
""" % (_DETACHED | _BREAKAWAY, _DETACHED)


def _launch(home: Any, root: Path, run_dir: Path, extra: list[str]) -> int:
    """Start the run so that nothing that ends the app ends the run: through a
    launcher that exits at once, so the run is nobody's child -- a host that
    kills the app's process tree (a terminal, an IDE) does not reach it --
    detached from any console, in a process group (or session) of its own,
    and outside the app's job object where Windows allows that."""
    env = {**os.environ, **home.job_environment(), "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    argv = [sys.executable, "-m", "evolvekit.app.job", str(root), run_dir.name, *extra]
    done = subprocess.run([sys.executable, "-c", _LAUNCHER, json.dumps(argv), str(root), str(run_dir / LOG)],
                          env=env, capture_output=True, text=True, timeout=60)
    try:
        pid = int(done.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        said = (done.stderr.strip().splitlines() or ["no message"])[-1]
        raise AppError(f"the run could not be started: {said}", 500) from None
    (run_dir / LAUNCH).write_text(json.dumps({"pid": pid, "started_at": _now().isoformat(timespec="seconds"),
                                              "argv": argv[1:]}, indent=2), encoding="utf-8")
    return pid


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


def resume_final_check(home: Any, root: Path, run: str, seeds: int | None = None) -> dict[str, Any]:
    run_dir = _run_dir(root, run)
    if running(root):
        raise AppError("the study is running", 409)
    job = phase(run_dir)
    if not job.get("search") or (job.get("search") or {}).get("aborted"):
        raise AppError("the search of this run did not finish, so there is nothing to check", 409)
    if not load_study(root).test:
        raise AppError("the study holds no cases back, so there is no final check", 409)
    if seeds is not None and not 1 <= int(seeds) <= len(CHECK_SEEDS):
        raise AppError(f"the final check takes 1 to {len(CHECK_SEEDS)} runs per case", 400)
    _launch(home, root, run_dir, ["--check"] + (["--seeds", str(int(seeds))] if seeds is not None else []))
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
    return {"kpi": level.kpi, "says": _in_sentence(says), "label": says, "direction": level.direction}


def _in_sentence(label: str) -> str:
    """A label inside a sentence: "Real cost" -> "real cost"; "PyVRP's objective" and "KPI" stay."""
    first = label.split(" ", 1)[0]
    if first[:1].isupper() and first[1:] == first[1:].lower():
        return label[0].lower() + label[1:]
    return label


def _better_word(direction: str) -> str:
    return "lower" if direction == "lower" else "higher"


def _what(study: Study) -> str:
    settings, data = bool(study.tuned_settings()), bool(study.data)
    return "combination of settings and data changes" if settings and data else "data changes" if data else "settings"


def _baseline(study: Study, harness: Harness, seed: dict[str, Any] | None) -> str:
    """What the starting point is, in words: "PyVRP's own defaults", "the
    settings in today.json", "the data as it is", or both."""
    parts = []
    settings_input = next((n for n, s in harness.inputs.items() if s.provides == "settings"), None)
    params = (seed or {}).get("params") or {}
    if study.tuned_settings() or not study.data:
        if settings_input and study.inputs.get(settings_input):
            parts.append(f"the settings in {Path(study.inputs[settings_input]).name}")
        elif any(c.mode == "fixed" for c in study.settings.values()) or any(
                name in harness.settings and value != harness.settings[name].parameter.default
                for name, value in params.items()):
            parts.append("your starting settings")
        else:
            parts.append(f"{harness.title}'s own defaults")
    if study.data:
        neutral = all(
            (change.mode == "scale" and params.get(name, change.start) == 1)
            or (change.mode == "add" and params.get(name, change.start) == 0)
            for name, change in study.data.items())
        parts.append("the data as it is" if neutral else "your starting data changes")
    return " and ".join(parts)


def _rows(run_dir: Path) -> list[dict[str, Any]]:
    return list(read_jsonl(run_dir / "runs.jsonl"))


def _plan(run_dir: Path, job: dict[str, Any]) -> dict[str, Any]:
    return job.get("plan") or _read(run_dir / "plan.json") or {}


def _moment(raw: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def _started(job: dict[str, Any], run_dir: Path) -> datetime | None:
    raw = job.get("started_at") or (_read(run_dir / LAUNCH) or {}).get("started_at")
    try:
        return datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def _failures_in_words(reasons: list[dict[str, Any]], limit_s: float) -> list[dict[str, Any]]:
    grouped = []
    stopped_at = limit_s * 1.5 + 30  # the stage timeout of a harness run (compile.py)
    for reason in reasons:
        text, count = str(reason.get("failure") or "failed"), int(reason.get("count") or 0)
        low = text.lower()
        if "timed out" in low or "timeout" in low or "no result after" in low:
            text = (f"Did not come back in time: each case gets {_duration(limit_s)}, and a run still going at "
                    f"{_duration(stopped_at)} is stopped")
            todo = ("Your time limit is applied; the application did not keep to it on these runs. A combination with "
                    "such a run counts as failed and the search moves on, so the best result is not affected. "
                    "If most runs do this, the application itself needs a look.")
        elif "exit code 2" in low or "constraint" in low or "invalid" in low:
            if low.strip(". ") == "exit code 2":
                text = "Broke a rule of the study"
            todo = "These combinations broke a rule of the study before solving; the search simply skips them. Nothing to do."
        elif "exit code 3" in low or "cannot read" in low:
            todo = "A case could not be read. Check the cases in step 3."
        else:
            todo = "Runs failed with an error. Open the detailed dashboard to see the message; if every run fails, run the test run again."
        grouped.append({"count": count, "stage": reason.get("stage"), "message": text, "todo": todo})
    return grouped


def _closest(run_dir: Path, finished: bool = False) -> str:
    """How close the tries came to the starting point when none beat it."""
    rows = [r for r in _rows(run_dir) if _finite(r.get("score"))]
    seed = next((r for r in rows if r.get("operator") == "human-seed"), None)
    tried = [r for r in rows if r.get("operator") != "human-seed" and not r.get("rejected") and not r.get("gated")]
    if seed is None or not tried or not seed["score"]:
        return ""
    best = max(float(r["score"]) for r in tried)  # a score is higher when better, whichever way the goal points
    gap = (float(seed["score"]) - best) / abs(float(seed["score"])) * 100.0
    so_far = "" if finished else " so far"
    return (f" {len(tried)} tried{so_far}; the closest came within {gap:.1f} % of it." if gap > 0.05
            else f" {len(tried)} tried{so_far}; the closest matched it.")


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
    finished = job.get("phase") in ("done", "stopped", "failed")
    ended = _moment(job.get("finished_at") or job.get("updated_at")) if finished else None
    used = ((ended or _now()) - started).total_seconds() if started else None
    left = None if used is None or finished else max(0.0, hours * 3600 - used)
    finish_at = (started + timedelta(hours=hours)).astimezone().strftime("%H:%M") if started and not finished else None

    generation = (health.get("generation") or {})
    done_rounds = generation.get("last_finished")
    rounds = {"done": max(0, int(done_rounds)) if isinstance(done_rounds, int) else 0,
              "planned": int(plan.get("rounds") or 0)}
    improvement = (progress.get("improvement") or {})
    pct, verdict = improvement.get("pct"), improvement.get("verdict")
    better = _better_word(goal["direction"])
    seed, _ = _seed_and_best(run_dir, document) if document else (None, None)
    start_words = f"the starting point ({_baseline(study, harness, seed)})"
    runs = study.limits.runs_per_case
    learned = (f"on the {len(study.training)} case{'s' if len(study.training) != 1 else ''} the search learns from "
               f"({'one run' if runs == 1 else f'{runs} runs'} each)")
    if pct is None or verdict in ("none", None) or (pct or 0) <= 0:
        best_line = (f"No combination beat {start_words}." if finished
                     else f"No combination has beaten {start_words} yet.") + _closest(run_dir, finished)
    else:
        # Only the final check says whether it is better: the search picks
        # what did best on its own cases, which flatters it.
        best_line = (f"So far {_pct(pct)} {better} {goal['says']} than {start_words}, {learned}. "
                     + ("Whether that holds on cases the search never saw, the final check tells."
                        if not finished else "The results say whether it held on the cases the search never saw."))
    levels = (progress.get("levels") or {})
    series = [[s.get("elapsed_s"), s.get("improvement_pct")] for s in progress.get("series") or []
              if s.get("elapsed_s") is not None]

    stage = health.get("stage") or {}
    # The round being run is the one after the last finished; round 0 is the
    # starting point alone.
    current = done_rounds + 1 if isinstance(done_rounds, int) else (0 if stage else None)
    if job.get("phase") == "check":
        now_line = "The final check: the best against the starting point on the held-back cases."
    elif stage and isinstance(current, int):
        cases = len(study.training)
        on = f"on {cases} case{'s' if cases != 1 else ''}"
        runs = f"{stage.get('runs_done')} of {stage.get('runs_planned')} runs done"
        count = stage.get("candidates") or 0
        now_line = (f"Measuring the starting point {on}: {runs}." if current == 0
                    else f"Round {current}: {count} combination{'s' if count != 1 else ''} {on}, {runs}.")
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
    host = health.get("host") or {}
    busy = host.get("available") and any(s.get("flagged") or s.get("busier_throughout") for s in host.get("stages") or [])
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
        "failures": _failures_in_words((health.get("evaluations") or {}).get("failure_reasons") or [],
                                       float((_read(run_dir / STUDY_RUN) or {}).get("time_limit_s") or study.limits.time_per_case_s)),
        "gated": gated,
        "gated_line": (f"{gated} combination{'s' if gated != 1 else ''} broke a guardrail and {'do' if gated != 1 else 'does'} not count."
                       if gated else ""),
        "busy_line": ("Other programs kept this computer busy during the study. A run with a time limit then finds a "
                      "little less than it would alone, so close heavy programs while a study runs." if busy else ""),
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


def _read_list(path: Path) -> list[dict[str, Any]]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in loaded if isinstance(item, dict)] if isinstance(loaded, list) else []


def _search_evals(run_dir: Path, ids: set[str]) -> list[dict[str, Any]]:
    """The final stage's runs of the candidates `ids` during the search, in
    the shape of the final check's `results.json`."""
    final_stage, evals = None, []
    for event in read_jsonl(run_dir / "events.jsonl"):
        kind = event.get("type")
        if kind == "run_started" and event.get("stages"):
            final_stage = (event["stages"][-1] or {}).get("id")
        elif kind == "eval_finished" and event.get("candidate_id") in ids and event.get("instance"):
            evals.append(event)
    return [{"configuration": e["candidate_id"], "instance": e["instance"], "seed": e.get("seed"), "ok": bool(e.get("ok")),
             "kpis": e.get("kpis") or {}} for e in evals if final_stage is None or e.get("stage") == final_stage]


def _paired(runs: list[dict[str, Any]], start: str, best: str) -> dict[tuple[Any, Any], tuple[dict[str, Any], dict[str, Any]]]:
    """(case, seed) -> the KPIs of the starting point and of the best, where both finished."""
    sides: dict[tuple[Any, Any], dict[str, dict[str, Any]]] = {}
    for run in runs:
        if run.get("ok") and run.get("configuration") in (start, best):
            sides.setdefault((run.get("instance"), run.get("seed")), {})[str(run["configuration"])] = run.get("kpis") or {}
    return {key: (both[start], both[best]) for key, both in sides.items() if start in both and best in both}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _means(pairs: dict[Any, tuple[dict[str, Any], dict[str, Any]]]) -> tuple[dict[str, float], dict[str, float]]:
    """Every KPI's mean over the pairs, for the starting point and the best."""
    start: dict[str, list[float]] = {}
    best: dict[str, list[float]] = {}
    for before, after in pairs.values():
        for name, value in before.items():
            if _finite(value) and _finite(after.get(name)):
                start.setdefault(name, []).append(float(value))
                best.setdefault(name, []).append(float(after[name]))
    return {k: fmean(v) for k, v in start.items()}, {k: fmean(v) for k, v in best.items()}


def _improvement(start: float | None, best: float | None, direction: str) -> float | None:
    """By how many percent `best` is better than `start`; negative when worse."""
    if not _finite(start) or not _finite(best) or start == 0:
        return None
    change = (best - start) / abs(start) * 100.0
    return -change if direction == "lower" else change


def _by_case(runs: list[dict[str, Any]], start: str, best: str, kpi: str, direction: str) -> list[dict[str, Any]]:
    """The goal per case: the starting point against the best, over the runs where both finished."""
    per_case: dict[str, list[tuple[float, float]]] = {}
    failed: dict[str, int] = {}
    for (case, _seed), (before, after) in _paired(runs, start, best).items():
        if _finite(before.get(kpi)) and _finite(after.get(kpi)):
            per_case.setdefault(str(case), []).append((float(before[kpi]), float(after[kpi])))
    for run in runs:
        if not run.get("ok") and run.get("configuration") in (start, best):
            failed[str(run.get("instance"))] = failed.get(str(run.get("instance")), 0) + 1
    rows = []
    for case in sorted(set(per_case) | set(failed)):
        pairs = per_case.get(case) or []
        before = fmean(p[0] for p in pairs) if pairs else None
        after = fmean(p[1] for p in pairs) if pairs else None
        rows.append({"case": case, "start": before, "best": after, "runs": len(pairs), "failed": failed.get(case, 0),
                     "change_pct": _improvement(before, after, direction)})
    return rows


def _span(low: float, high: float) -> str:
    """A 95 % interval of an improvement in percent, in words."""
    if low > 0:
        return f"likely between {low:.1f} % and {high:.1f} % better"
    if high < 0:
        return f"likely between {-high:.1f} % and {-low:.1f} % worse"
    return f"somewhere between {-low:.1f} % worse and {high:.1f} % better"


def _final_words(job: dict[str, Any], comparison: dict[str, Any] | None, goal: dict[str, Any], what: str,
                 baseline: str = "") -> dict[str, Any]:
    """The final check in a sentence, and what to do about it in another."""
    final = job.get("final") or {}
    new = f"the new {what}"
    they = "it is" if what.startswith("combination") else "they are"
    keep = {"settings": "Keep your current settings", "data changes": "Keep your data as it is"}.get(
        what, "Keep your current settings and data")
    if baseline and what != "data changes":
        keep += f" ({baseline})"
    if final.get("skipped"):
        reason = str(final["skipped"])
        advice = (f"{keep}: the search found nothing better." if "starting point stayed the best" in reason else
                  f"Not sure: nothing was held back to check {new} on. A study that holds back a few cases can tell.")
        return {"state": "skipped", "line": reason[0].upper() + reason[1:] + ".", "advice": advice}
    if final.get("stopped"):
        return {"state": "stopped", "line": "The final check was stopped before it finished.",
                "advice": "Run the final check to get an answer; the runs it already did are kept."}
    if comparison is None:
        return {"state": "missing", "line": "There is no final check for this run.", "advice": ""}
    candidate = final.get("candidate") or next(iter(comparison.get("per_candidate") or {}), None)
    result = (comparison.get("per_candidate") or {}).get(candidate) or {}
    summary = result.get("summary") or {}
    n, mean, ci = summary.get("n") or 0, summary.get("mean"), summary.get("ci95")
    seeds = len(comparison.get("seeds") or [])
    better = _better_word(goal["direction"])
    worse = "higher" if better == "lower" else "lower"
    cases = f"{n} held-back case{'s' if n != 1 else ''}"
    base = {"n": n, "seeds": seeds, "mean": mean, "ci95": ci}
    if "levels" in result:
        levels = result["levels"]
        if levels.get("confirmed"):
            return {**base, "state": "confirmed", "levels": levels.get("levels"),
                    "line": f"Confirmed on {cases}: {levels.get('verdict')}.",
                    "advice": f"Use {new}: {they} better on cases the search never saw."}
        return {**base, "state": "unclear", "levels": levels.get("levels"),
                "line": f"Not clear on {cases}: {levels.get('verdict')}.",
                "advice": f"{keep} for now: the gain is not proven."}
    if mean is None:
        return {**base, "state": "unclear", "line": "The final check has no comparable results.",
                "advice": f"{keep} for now: the gain is not proven."}
    word = better if mean >= 0 else worse
    if ci is None:
        return {**base, "state": "single",
                "line": f"On the one held-back case: {_pct(mean)} {word} {goal['says']}.",
                "advice": "Not sure: one held-back case is too few to tell. A study that holds back three or more can."}
    low, high = ci
    if low > 0:
        return {**base, "state": "confirmed",
                "line": f"Confirmed: {_pct(mean)} {better} {goal['says']} on {cases} ({_span(low, high)}).",
                "advice": f"Use {new}: {they} better on cases the search never saw."}
    if high < 0:
        return {**base, "state": "worse",
                "line": f"Worse on {cases}: {_pct(mean)} {worse} {goal['says']} ({_span(low, high)}).",
                "advice": f"{keep}: {new} did worse on cases the search never saw."}
    return {**base, "state": "unclear",
            "line": f"Not distinguishable from your starting point on {cases} ({_span(low, high)}).",
            "advice": f"{keep} for now: the gain is not proven."}


def _runs_word(count: int) -> str:
    return f"{count} run{'s' if count != 1 else ''}"


def _recheck(final: dict[str, Any], plan: dict[str, Any], comparison: dict[str, Any] | None,
             used_s: float | None = None) -> dict[str, Any] | None:
    """A final check with twice the runs per case, where that could still
    settle an open answer. The runs done are kept, so it only adds runs; what
    it adds is said against the study's total time."""
    if comparison is None or final.get("state") not in ("unclear", "worse"):
        return None
    have = len(comparison.get("seeds") or [])
    want = min(2 * have, len(CHECK_SEEDS))
    if want <= have:
        return None
    runs = 2 * len(comparison.get("instances") or []) * (want - have)
    seconds = math.ceil(runs / max(1, int(plan.get("workers") or 1))) * float(plan.get("run_s") or 60.0)
    budget = float(plan.get("hours") or 0) * 3600
    time = ""
    if used_s is not None and budget:
        after = used_s + seconds
        time = (f"The study then takes about {_duration(after)} of its {_duration(budget)}." if after <= budget
                else f"The study then takes about {_duration(after)}: more than its {_duration(budget)}.")
    return {"seeds": want, "seconds": seconds, "over": bool(time) and used_s + seconds > budget,
            "label": f"Check again with {_runs_word(want)} per case instead of {have} (about {_duration(seconds)})",
            "why": (time + " " if time else "") + "More runs per case make each case's number steadier, and the runs "
                   "already done are kept. The range can still widen: with a few cases, it is mostly how much they differ "
                   "that sets it."}


def _today(root: Path, harness: Harness, change: Any) -> list[float]:
    """The values a data change starts from: its column in the rows it
    selects, on the preview case (the smallest), untouched."""
    lever = harness.levers.get(change.lever)
    path = root / "preview" / "tables.sqlite"
    if lever is None or not change.column or not path.is_file():
        return []
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        table = f"orig_{lever.table}" if f"orig_{lever.table}" in tables else lever.table
        column = '"' + change.column.replace('"', '""') + '"'
        rows = db.execute(f'SELECT DISTINCT {column} FROM "{table}" WHERE {change.where or "1 = 1"} ORDER BY 1 LIMIT 50').fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()
    return [float(r[0]) for r in rows if _finite(r[0])]


def _changed_values(values: list[float], mode: str, value: Any) -> list[float]:
    if not _finite(value):
        return []
    return sorted({v * value if mode == "scale" else v + value if mode == "add" else float(value) for v in values})


def _span_text(values: list[float]) -> str:
    texts = [f"{v:,.4g}" for v in (min(values), max(values))]
    return texts[0] if texts[0] == texts[1] else f"{texts[0]}–{texts[1]}"


def _describe_value(value: Any) -> str:
    """A setting's value as people write it: on/off, 1,248,920, 0.923184."""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float) and math.isfinite(value):
        if value == int(value) and abs(value) < 1e15:
            return f"{int(value):,}"
        return f"{value:,.6g}" if abs(value) >= 1e-4 else f"{value:.3g}"
    return str(value)


def _number(value: Any) -> str:
    """A number as the page shows it: grouped thousands, a few digits."""
    if not _finite(value):
        return "–"
    value = float(value)
    if value == int(value) and abs(value) < 1e15:
        return f"{int(value):,}"
    if abs(value) >= 1000:
        return f"{value:,.1f}"
    return f"{value:.4g}"


def _kpi_help(study: Study, harness: Harness, name: str) -> str:
    """What a KPI means, in the harness's words; a study's own SQL KPI says it in its name."""
    kpi = study.kpis[name]
    if kpi.kind == "harness" and name in harness.kpis:
        return harness.kpis[name].help
    if kpi.kind == "template" and kpi.template in harness.kpi_templates:
        return harness.kpi_templates[kpi.template].help
    return ""


def _summary_text(data: dict[str, Any]) -> str:
    """The result as plain text to paste into a message."""
    context, final = data["context"], data["final"]
    learned, held = data["cases"]["training"], data["cases"]["test"]
    lines = [f"{data['study']} ({data['harness']}), run {data['run']}"]
    if final.get("advice"):
        lines.append(f"Answer: {final['advice']}")
    lines += [
        f"Set-up: {_number(context['time_per_case_s'])} s per case, {_duration(context['hours'] * 3600)} in total; "
        f"the search learned from {len(learned)} case{'s' if len(learned) != 1 else ''}"
        + (f" and was checked on {len(held)} held-back case{'s' if len(held) != 1 else ''}" if held else "") + "."
        + (f" Compared with {context['compared_with']}." if context.get("compared_with") else ""),
        f"Search: {data['headline']}",
        f"Final check: {final['line']}",
    ]
    side = []
    for k in data.get("kpis") or []:
        if k["name"] == data.get("goal", {}).get("kpi"):
            continue
        start, best = k["test"]["start"], k["test"]["best"]
        pct = _improvement(start, best, k["direction"])
        if pct is None:
            continue
        unit = f" {k['unit']}" if k.get("unit") else ""
        way = "the same" if abs(pct) < 0.05 else f"{abs((best - start) / abs(start) * 100):.1f} % {'higher' if best > start else 'lower'}, {'better' if pct > 0 else 'worse'}"
        side.append(f"{k['says']} {_number(start)}{unit} -> {_number(best)}{unit} ({way})")
    if side:
        lines.append("Other measures on the held-back cases: " + "; ".join(side) + ".")
    if data.get("rules"):
        lines.append("Rules every combination kept: " + "; ".join(data["rules"]) + ".")
    changed = [c for c in data["changed"] if c["changed"]]
    kept = [c for c in data["changed"] if not c["changed"]]
    if changed:
        lines.append("What changed:")
        lines += [f"- {c['label']} ({c['name']}): {c['old_text']} -> {c['new_text']}" for c in changed]
    if kept:
        lines.append("Tried, and kept as they were: " + ", ".join(c["name"] for c in kept) + ".")
    return "\n".join(lines) + "\n"


def results(root: Path, run: str, document_of: DocumentOf) -> dict[str, Any]:
    run_dir = _run_dir(root, run)
    study, harness = load_study(root), load_harness(root / "harness")
    job = phase(run_dir)
    plan = _plan(run_dir, job)
    document = document_of(run_dir) if (run_dir / "events.jsonl").exists() else {}
    goal = _goal(study, harness)
    seed, best = _seed_and_best(run_dir, document)
    improvement = ((document.get("progress") or {}).get("improvement") or {})
    pct = improvement.get("pct")
    what = _what(study)
    learned = "on the cases the search learned from"
    start_words = f"your starting point ({_baseline(study, harness, seed)})"
    if best is None or seed is None:
        headline = "The search has no finished result yet."
    elif best.get("id") == seed.get("id") or not pct or pct <= 0:
        headline = f"No {what} found beat {start_words} {learned}."
    else:
        headline = (f"The best {what} found give{'s' if what.startswith('combination') else ''} {_pct(pct)} "
                    f"{_better_word(goal['direction'])} {goal['says']} than {start_words} {learned}.")
    final_state = job.get("final") or {}
    comparison, check_runs = None, []
    if final_state.get("comparison"):
        comparison = _read(run_dir / final_state["comparison"])
        check_runs = _read_list((run_dir / final_state["comparison"]).parent / "results.json")
    baseline = _baseline(study, harness, seed)
    started, finished = _started(job, run_dir), job.get("finished_at")
    used_s = (datetime.fromisoformat(finished) - started).total_seconds() if started and finished else None
    final = _final_words(job, comparison, goal, what, baseline)
    final["recheck"] = _recheck(final, plan, comparison, used_s) if job.get("phase") == "done" else None
    if final.get("state") in ("unclear", "worse", "single"):
        final["next"] = ("For a firmer answer: bring more cases (other days or weeks) and start a new study from "
                         "this result that holds more of them back.")
    candidate = str(final_state.get("candidate") or "")
    held_start, held_best = _means(_paired(check_runs, BASELINE, candidate)) if candidate else ({}, {})
    seed_id, best_id = str((seed or {}).get("id") or ""), str((best or {}).get("id") or "")
    by_case: dict[str, list[dict[str, Any]]] = {"learned": [], "held": []}
    if goal["kpi"] and seed_id and best_id and seed_id != best_id:
        by_case["learned"] = _by_case(_search_evals(run_dir, {seed_id, best_id}), seed_id, best_id,
                                      goal["kpi"], goal["direction"])
    if goal["kpi"] and candidate:
        by_case["held"] = _by_case(check_runs, BASELINE, candidate, goal["kpi"], goal["direction"])

    kpis = []
    for name in study.kpis:
        try:
            resolved = resolve_kpi(study, harness, name)
        except KeyError:
            continue
        def value(row: dict[str, Any] | None) -> Any:
            # A goal measured per case as a share of the start is kept beside
            # its plain mean (`<name>_raw`): people read the plain one.
            measured = (row or {}).get("kpis") or {}
            return measured.get(f"{name}_raw", measured.get(name))

        kpis.append({
            "name": name, "says": resolved.get("says") or name, "unit": resolved.get("unit") or "",
            "help": _kpi_help(study, harness, name), "direction": resolved.get("direction", "lower"),
            "training": {"start": value(seed), "best": value(best)},
            "test": {"start": held_start.get(name), "best": held_best.get(name)},
        })
    guardrails = [{"kpi": g.kpi, "max": g.max, "min": g.min} for g in study.guardrails]
    rules = [c.says for c in study.constraints]

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
            column = (lever.columns.get(change.column) if lever and change.column else None) or change.column or "in code"
            label = change.says or f"{lever.label if lever else change.lever}: {column} where {change.where or 'all rows'}"
            help_text = lever.help if lever else ""
            today = _today(root, harness, change)
            factor = {"scale": "×", "add": "+"}.get(change.mode, "=")
            if today and _finite(new):
                # The values it makes, not the factor: "3 → 2.82 (×0.939)".
                changed.append({"name": name, "label": label, "old": old, "new": new, "changed": old != new,
                                "help": f"{help_text} Today's value on the smallest case, and what the change makes of it.".strip(),
                                "old_text": _span_text(today),
                                "new_text": f"{_span_text(_changed_values(today, change.mode, new))} ({factor}{float(new):,.3g})"})
                continue
        changed.append({"name": name, "label": label, "help": help_text, "old": old, "new": new,
                        "old_text": _describe_value(old), "new_text": _describe_value(new),
                        "changed": old != new})
    kinds = {"settings"} if study.tuned_settings() else set()
    if study.data:
        kinds.add("data")
    downloads = [{"name": name, "label": export.label} for name, export in harness.exports.items() if export.applies_to in kinds]
    downloads.append({"name": "report", "label": "The report as a file (report.html)"})
    compiled = _read(run_dir / STUDY_RUN) or {}
    context = {
        "time_per_case_s": float(compiled.get("time_limit_s") or study.limits.time_per_case_s),
        "hours": float(plan.get("hours") or study.budget.hours),
        "runs_per_case": study.limits.runs_per_case,
        "tried": sum(1 for r in _rows(run_dir) if not r.get("rejected")),
        "started_at": started.astimezone().strftime("%Y-%m-%d %H:%M") if started else None,
        "took": _duration(used_s) if used_s is not None else None,
        "compared_with": baseline,
    }
    data = {
        "run": run, "phase": job.get("phase"), "study": study.name, "harness": harness.title,
        "headline": headline, "improvement_pct": pct, "goal": goal, "final": final, "kpis": kpis,
        "guardrails": guardrails, "rules": rules, "changed": changed, "downloads": downloads, "by_case": by_case,
        "context": context,
        "best_id": (best or {}).get("id"), "is_seed": bool(best and seed and best.get("id") == seed.get("id")),
        "cases": {"training": list(study.training), "test": list(study.test)},
    }
    data["summary_text"] = _summary_text(data)
    return data


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
    harness = load_harness(root / "harness")
    if export == "report":
        data = results(root, run, document_of)
        # The file the result is used through, in the report: the first
        # export that is one file (not the per-case requests).
        attached = None
        if not data["is_seed"]:
            primary = next((d["name"] for d in data["downloads"] if d["name"] not in ("report", "requests")), None)
            if primary:
                try:
                    attached = (harness.exports[primary].label, _export(root, run_dir, primary, _best_values(run_dir, document_of)).decode("utf-8", "replace"))
                except AppError:
                    attached = None
        return report_html(data, attached).encode("utf-8"), "text/html; charset=utf-8", f"{slug}-report.html"
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


def report_html(data: dict[str, Any], attached: tuple[str, str] | None = None) -> str:
    """The results as one self-contained page, for sharing; `attached` is
    (label, text) of the file the result is used through."""
    esc = html.escape
    goal, final, context = data["goal"], data["final"], data["context"]

    def change(pct: float | None) -> str:
        if pct is None:
            return "–"
        if abs(pct) < 0.05:
            return "the same"
        word = "lower" if (pct > 0) == (goal["direction"] == "lower") else "higher"
        return f"<span class='{'up' if pct > 0 else 'down'}'>{abs(pct):.1f} % {word}</span>"

    def case_table(rows: list[dict[str, Any]]) -> str:
        body = "".join(
            f"<tr><td>{esc(str(c['case']))}"
            + (f"<div class='help'>{c['failed']} run{'s' if c['failed'] != 1 else ''} did not finish</div>" if c["failed"] else "")
            + f"</td><td class='n'>{_number(c['start'])}</td><td class='n'>{_number(c['best'])}</td><td class='n'>{change(c['change_pct'])}</td></tr>"
            for c in rows)
        return ("<div class='card wrap'><table><thead><tr><th>Case</th><th class='n'>Start</th><th class='n'>Best</th>"
                f"<th class='n'>Change</th></tr></thead><tbody>{body}</tbody></table></div>")

    kpi_rows = "".join(
        f"<tr><td>{esc(k['says'])}{' (' + esc(k['unit']) + ')' if k['unit'] else ''}"
        f"<div class='help'>{esc(k.get('help') or '')} {'Lower' if k['direction'] == 'lower' else 'Higher'} is better.</div></td>"
        f"<td class='n'>{_number(k['training']['start'])}</td><td class='n'>{_number(k['training']['best'])}</td>"
        f"<td class='n'>{_number(k['test']['start'])}</td><td class='n'>{_number(k['test']['best'])}</td></tr>"
        for k in data["kpis"]
    )
    changed_rows = "".join(
        f"<tr><td>{esc(c['label'])} <code>{esc(c['name'])}</code><div class='help'>{esc(c['help'])}</div></td><td>{esc(c['old_text'])}</td>"
        f"<td>{'<b>' if c['changed'] else ''}{esc(c['new_text'])}{'</b>' if c['changed'] else ''}</td></tr>"
        for c in data["changed"]
    )
    by_case = ""
    if data["by_case"]["held"]:
        by_case += "<h3>Held-back cases</h3>" + case_table(data["by_case"]["held"])
    if data["by_case"]["learned"]:
        by_case += "<h3>Cases the search learned from</h3>" + case_table(data["by_case"]["learned"])
    if by_case:
        label = goal.get("label") or goal["says"]
        by_case = f"<h2>{esc(label[:1].upper() + label[1:])}, case by case</h2>" + by_case
    facts = [("Time per case", _duration(context["time_per_case_s"])), ("Time for the study", _duration(context["hours"] * 3600)),
             ("Combinations tried", str(context["tried"]))]
    if context.get("took"):
        facts.append(("It took", context["took"]))
    if context.get("started_at"):
        facts.append(("Started", context["started_at"]))
    facts_html = "".join(f"<div><div class='k'>{esc(k)}</div><div class='v'>{esc(v)}</div></div>" for k, v in facts)
    seeds = final.get("seeds")
    check_note = (f"<p class='help'>The best and the starting point, each run {'once' if seeds == 1 else f'{seeds} times'} on every "
                  "held-back case: cases the search never saw.</p>") if seeds else ""
    attached_html = (f"<h2>{esc(attached[0])}</h2><div class='card'><pre>{esc(attached[1])}</pre></div>" if attached else "")
    rules_html = ("<h2>Rules every combination kept</h2><div class='card'><ul>"
                  + "".join(f"<li>{esc(rule)}</li>" for rule in data.get("rules") or []) + "</ul></div>") if data.get("rules") else ""
    learned = ", ".join(Path(c).stem for c in data["cases"]["training"])
    held = ", ".join(Path(c).stem for c in data["cases"]["test"]) or "none"
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(data['study'])}: results</title>
<style>
:root {{ color-scheme: light dark; --ink:#0b0b0b; --ink-2:#52514e; --page:#f6f6f3; --surface:#fff; --hair:rgba(11,11,11,.12); --good:#006300; --bad:#b42525; --warn:#9a6600; }}
@media (prefers-color-scheme: dark) {{ :root {{ --ink:#fff; --ink-2:#c3c2b7; --page:#0d0d0d; --surface:#1a1a19; --hair:rgba(255,255,255,.14); --good:#4fd14f; --bad:#ef6a6a; --warn:#fab219; }} }}
body {{ margin:0; background:var(--page); color:var(--ink); font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }}
main {{ max-width:880px; margin:0 auto; padding:32px 20px 64px; }}
h1 {{ font-size:24px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:32px 0 8px; }} h3 {{ font-size:15px; margin:18px 0 6px; }}
.meta {{ color:var(--ink-2); font-size:13px; }}
.card {{ background:var(--surface); border:1px solid var(--hair); border-radius:12px; padding:16px 18px; margin-top:12px; }}
.headline {{ font-size:18px; font-weight:600; }}
.advice {{ margin-top:10px; font-weight:600; }}
.advice[data-state=confirmed] {{ color:var(--good); }} .advice[data-state=worse] {{ color:var(--bad); }} .advice[data-state=unclear], .advice[data-state=single] {{ color:var(--warn); }}
.final[data-state=confirmed] {{ border-left:4px solid var(--good); }} .final[data-state=worse] {{ border-left:4px solid var(--bad); }}
.final[data-state=unclear], .final[data-state=single] {{ border-left:4px solid var(--warn); }}
.facts {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fill, minmax(150px, 1fr)); margin-top:14px; }}
.facts .k {{ color:var(--ink-2); font-size:13px; }} .facts .v {{ font-size:18px; font-weight:650; }}
table {{ width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }}
th, td {{ text-align:left; padding:8px 6px; border-bottom:1px solid var(--hair); vertical-align:top; }}
th {{ font-size:12px; color:var(--ink-2); font-weight:600; }} .n {{ text-align:right; }} th.group {{ text-align:center; border-bottom:0; }}
.help {{ color:var(--ink-2); font-size:12px; }}
.up {{ color:var(--good); font-weight:600; }} .down {{ color:var(--bad); font-weight:600; }}
.wrap {{ overflow-x:auto; }}
pre {{ white-space:pre-wrap; word-break:break-word; margin:0; font:13px/1.5 ui-monospace,Consolas,monospace; }}
</style></head><body><main>
<h1>{esc(data['study'])}</h1>
<div class="meta">{esc(data['harness'])} · run {esc(data['run'])} · report made {generated}</div>
<div class="card"><div class="headline">{esc(data['headline'])}</div>
<div class="advice" data-state="{esc(str(final.get('state')))}">{esc(final.get('advice') or '')}</div></div>
<div class="facts">{facts_html}</div>
<h2>The final check</h2>
<div class="card final" data-state="{esc(str(final.get('state')))}">{esc(final['line'])}{check_note}</div>
{by_case}
<h2>Every measure</h2>
<div class="card wrap"><table><thead><tr><th rowspan="2">Measure</th><th class="n group" colspan="2">Cases the search learned from</th><th class="n group" colspan="2">Held-back cases</th></tr>
<tr><th class="n">Start</th><th class="n">Best</th><th class="n">Start</th><th class="n">Best</th></tr></thead>
<tbody>{kpi_rows}</tbody></table></div>
<h2>What changed</h2>
<div class="card wrap"><table><thead><tr><th>What</th><th>Before</th><th>Now</th></tr></thead><tbody>{changed_rows}</tbody></table></div>
{rules_html}
{attached_html}
<p class="meta">The search learned from: {esc(learned)}. Held back for the final check: {esc(held)}.</p>
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
            reason = str(final["skipped"]).split(":")[0].split(",")[0]  # "the starting point stayed the best"
            return {"kind": "finished", "run": run_dir.name, "line": f"finished · {reason}"}
        comparison = _read(run_dir / final["comparison"]) if final.get("comparison") else None
        summary = (((comparison or {}).get("per_candidate") or {}).get(final.get("candidate") or "") or {}).get("summary") or {}
        mean = summary.get("mean")
        if final.get("confirmed"):
            amount = f" {_pct(mean)} {_better_word(goal['direction'])} {goal['says']}" if isinstance(mean, (int, float)) else ""
            return {"kind": "finished", "run": run_dir.name, "line": "finished · confirmed" + amount}
        return {"kind": "finished", "tone": "warn", "run": run_dir.name, "line": "finished · not confirmed on the held-back cases"}
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
