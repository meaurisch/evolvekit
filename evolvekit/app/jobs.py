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
import re
import shutil
import socket
import sqlite3
import sys
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import fmean
from typing import Any, Callable

from evolvekit.app import AppError, detach
from evolvekit.app.prices import price_of
from evolvekit.app.store import safe_relative
from evolvekit.app import verdict as verdict_doc
from evolvekit.app.verdict import (  # noqa: F401 - the words live in verdict.py; callers read them here too
    _better_word, _chance_of, _chance_words, _decision_of, _final_words, _pct,
)
from evolvekit.confirm import BASELINE
from evolvekit.evaluate.process import scrubbed_environment
from evolvekit.events import HEARTBEAT_INTERVAL_S, read_heartbeat
from evolvekit.harness import HarnessError
from evolvekit.harness.compile import CONFIG, STUDY_RUN
from evolvekit.harness.execute import (
    BAKEOFF,
    JOB,
    ExportFailed,
    export_path,
    read_job,
    run_study_of,
    write_exports,
)
from evolvekit.harness.plan import CHECK_SEEDS, DEFAULT_CHECK_SEEDS, plan_notes
from evolvekit.harness.manifest import Harness, load_harness
from evolvekit.harness.study import Study, load_study, resolve_kpi, save_study, study_problems
from evolvekit.ledger import _atomic_write, read_jsonl
from evolvekit.lock import pid_alive, pid_started_at
from evolvekit.stopping import STOP_REQUEST

__all__ = ["active_run", "can_continue", "can_resume", "continue_run", "download", "latest_run", "launch_queued",
           "check_refs", "next_study", "phase", "report_html", "results", "resume_final_check", "run_alive", "run_line",
           "running", "runs",
           "start", "state_line", "status", "stop"]

LAUNCH = "launch.json"
MODELS = "models.json"
QUEUED = "queued.json"
"""`{"queued_at": iso}` in a run directory that waits for another study's
run to finish; `launch_queued` launches it and removes the file."""
LOG = "job.log"
ACTIVE = ("starting", "search", "check")
"""The phases in which a run has a process: one such run per home at a time."""
HEARTBEAT_MAX_AGE_S = 12 * HEARTBEAT_INTERVAL_S
"""A run on another computer is alive while its last beat is younger than
this: a minute, so a sync client that is a few beats behind does not make
it dead."""
LAUNCH_SLACK_S = 120.0
"""A process that started this long after its launch was written is not the
one launched: the pid was recycled, by a reboot or by chance."""
MAX_CONTINUE_HOURS = 48.0
"""The most a finished run is given at a time when it goes on."""
REPORTS_COST = ("openrouter", "claude-cli", "copilot-cli")
"""The providers that say what every call cost, so the dollar cap of AI search
help holds without a price in the table. `copilot-cli` counts Copilot
requests, not dollars, and reports 0.0: the cap holds trivially."""
_RUN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
DocumentOf = Callable[[Path], Any]
_launch_guard = threading.Lock()
"""Held from the question "is anything active?" to the launch that answers
it, so a start from the page and the server's ticker never launch two."""


def _read(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def run_alive(run_dir: Path, pid: Any, launched_at: str | None, host: str | None, *,
              now: datetime | None = None) -> bool:
    """Whether the process a run was launched as is still running -- that
    process, not whoever holds its pid today.

    A pid is recycled quickly on Windows and always across a reboot, so a
    live pid is only the run's when the process started no later than the
    launch (with `LAUNCH_SLACK_S` for clocks and a slow launcher). A run on
    another computer cannot be asked, so its heartbeat answers: alive while
    the last beat is younger than `HEARTBEAT_MAX_AGE_S`. Here, the process
    check wins over the heartbeat: a machine that slept has a live process
    with a stale beat, and that run is still running."""
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if host and host != socket.gethostname():
        beat = _moment((read_heartbeat(run_dir) or {}).get("ts"))
        return beat is not None and ((now or _now()) - _utc(beat)).total_seconds() < HEARTBEAT_MAX_AGE_S
    if not pid_alive(pid):
        return False
    launched, born = _moment(launched_at), pid_started_at(pid)
    if launched is not None and born is not None and _utc(born) > _utc(launched) + timedelta(seconds=LAUNCH_SLACK_S):
        return False
    return True


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


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
    # A folder that was never submitted -- no job, no launch, no place in the
    # queue, such as the files `study compile` writes -- is not a run.
    found = [p for p in folder.iterdir() if p.is_dir() and _RUN.match(p.name)
             and any((p / name).is_file() for name in (JOB, LAUNCH, QUEUED))]

    def started(path: Path) -> str:
        launch, job = _read(path / LAUNCH) or {}, read_job(path) or {}
        queued = (_read(path / QUEUED) or {}) if not launch else {}
        return str(launch.get("started_at") or job.get("started_at") or launch.get("at") or queued.get("queued_at") or "")

    return sorted(found, key=lambda p: (started(p), p.name))


def _run_dir(root: Path, run: str) -> Path:
    if not isinstance(run, str) or not _RUN.match(run) or not (root / "runs" / run).is_dir():
        raise AppError(f"there is no run {run!r} in this study", 404)
    return root / "runs" / run


def phase(run_dir: Path) -> dict[str, Any]:
    """`job.json`, with the phase corrected for a process that is gone."""
    job = read_job(run_dir)
    launch = _read(run_dir / LAUNCH) or {}
    if not launch and job is None and (run_dir / QUEUED).is_file():
        # Waiting its turn: a launch or a job, once there is one, is the newer
        # word -- a run that started is never queued again.
        return {"phase": "queued", "queued_at": (_read(run_dir / QUEUED) or {}).get("queued_at")}
    if launch.get("pending"):
        # A launch under way (`_mark_pending`), in this process or another:
        # starting while it is young; a launcher that never wrote the real
        # launch within LAUNCH_SLACK_S did not start the run.
        at = _moment(launch.get("at"))
        young = at is not None and (_now() - _utc(at)).total_seconds() < LAUNCH_SLACK_S
        if job is None:
            if young:
                return {"phase": "starting", "started_at": launch.get("at")}
            return {"phase": "failed", "started_at": launch.get("at"),
                    "error": f"the run did not start: {_log_tail(run_dir)}"}
        if young and job.get("phase") in ("done", "stopped", "failed"):
            # Resumed, continued or checked again: the old job is still the last word.
            return {"phase": "starting", "started_at": job.get("started_at"), "plan": job.get("plan")}
        launch = {}  # the job, written after the marker, is the newer word
    if job is None:
        if launch and run_alive(run_dir, launch.get("pid"), launch.get("started_at"), launch.get("host")):
            return {"phase": "starting", "started_at": launch.get("started_at")}
        if launch:
            return {"phase": "failed", "started_at": launch.get("started_at"),
                    "error": f"the run did not start: {_log_tail(run_dir)}"}
        return {"phase": "unknown"}
    # The launch says when this process began; a run without one (started by
    # hand) is dated by its job, from the resume when there was one.
    launched_at = launch.get("started_at") or job.get("resumed_at") or job.get("started_at")
    if (job.get("phase") in ("search", "check")
            and not run_alive(run_dir, job.get("pid"), launched_at, launch.get("host"))):
        return {**job, "phase": "failed",
                "error": "the run's process ended without finishing: it was ended from outside, or the computer "
                         f"restarted. The last thing it wrote: {_log_tail(run_dir, 1)}"}
    if (job.get("phase") in ("done", "stopped", "failed") and launch.get("pid") and launch.get("pid") != job.get("pid")
            and run_alive(run_dir, launch.get("pid"), launch.get("started_at"), launch.get("host"))):
        # Resumed (the search, or the final check): a younger process than the
        # one that wrote the job is up, and has not written its first word yet.
        return {"phase": "starting", "started_at": job.get("started_at"), "plan": job.get("plan")}
    return job


def runs(root: Path) -> list[dict[str, Any]]:
    """Every run of the study, oldest first, each with the card's words for it
    (`run_line`: `kind`, `line`, `tone`)."""
    dirs = _run_dirs(root)
    if not dirs:
        return []
    try:
        study: Study | None = load_study(root)
    except Exception:  # noqa: BLE001 - the list still renders for a damaged study
        study = None
    listed = []
    for run_dir in dirs:
        job = phase(run_dir)
        line = run_line(root, study, run_dir)
        listed.append({"id": run_dir.name, "phase": job.get("phase"), "started_at": job.get("started_at"),
                       "finished_at": job.get("finished_at"), "kind": line["kind"], "line": line["line"],
                       "tone": line.get("tone"), "decision": line.get("decision")})
    return listed


def latest_run(root: Path) -> str | None:
    dirs = _run_dirs(root)
    return dirs[-1].name if dirs else None


def running(root: Path) -> bool:
    """Whether the study has a run with a process, or one waiting its turn:
    either way it is not to be edited or started again."""
    return any(phase(d).get("phase") in (*ACTIVE, "queued") for d in _run_dirs(root))


def _study_roots(studies_dir: Path) -> list[Path]:
    if not studies_dir.is_dir():
        return []
    return sorted(p for p in studies_dir.iterdir() if (p / "study.yaml").is_file())


def _active_runs(studies_dir: Path) -> list[tuple[str, str, str]]:
    """`(started_at, slug, run)` of every run with a process across the home's
    studies, oldest first."""
    found = []
    for root in _study_roots(studies_dir):
        for run_dir in _run_dirs(root):
            job = phase(run_dir)
            if job.get("phase") in ACTIVE:
                found.append((str(job.get("started_at") or ""), root.name, run_dir.name))
    return sorted(found)


def active_run(home: Any) -> tuple[str, str] | None:
    """The study and run that has the computer now -- the oldest of any
    `starting | search | check` run across the home -- or None."""
    found = _active_runs(home.studies_dir)
    return (found[0][1], found[0][2]) if found else None


def _waiting_for(root: Path) -> str:
    """The name of the study a queued run of `root` waits for: the active run's
    study, read from its `study.yaml`; "" when nothing is active."""
    found = _active_runs(root.parent)
    if not found:
        return ""
    try:
        return load_study(root.parent / found[0][1]).name
    except Exception:  # noqa: BLE001 - a damaged study still has a slug
        return found[0][1]


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
    price_in, price_out = price_of(model, settings.get("prices")) or (0.0, 0.0)
    slot = {"provider": provider, "model": model, "price_in_per_mtok": price_in, "price_out_per_mtok": price_out,
            "max_tokens": 4096}
    return {"small": dict(slot), "strong": dict(slot)}


def _cap_holds(home: Any, study: Study) -> None:
    """AI search help's dollar cap can hold only when every call's cost is
    known: reported by the provider, or in the price table."""
    settings = home.settings()
    model = settings["models"]["search"]
    if settings["provider"] in REPORTS_COST or price_of(model, settings.get("prices")) is not None:
        return
    raise AppError(f"AI search help is on with a ${study.budget.ai_max_usd:g} cap, but the cost of {model} is not known, "
                   "so the cap cannot hold: add its price under Settings › Models, or turn AI help off", 409)


def _launch(home: Any, root: Path, run_dir: Path, extra: list[str], *, with_keys: bool, claimed: bool = False) -> int:
    """Start the run so that nothing that ends the app ends the run, through
    `detach.spawn_detached`: nobody's child, detached from any console, in a
    process group (or session) of its own, outside the app's job object where
    Windows allows that.

    The run's environment is the app's without its secrets (the app carries
    the API keys for its assistant), plus the keys the app keeps when
    `with_keys`: only a run with AI search help on has a use for them.

    `claimed`: the caller already wrote the pending marker (the claim on a
    queued run), so it is not written again -- each truncation of
    `launch.json` is a moment a reader sees nothing."""
    env = {**scrubbed_environment(), "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
    if with_keys:
        env.update(home.job_environment())
    argv = [sys.executable, "-m", "evolvekit.app.job", str(root), run_dir.name, *extra]
    try:
        previous: str | None = (run_dir / LAUNCH).read_text(encoding="utf-8")
    except OSError:
        previous = None
    # The pending marker: between here and the real launch below, any process
    # that asks (the app's ticker, `study wait`) sees the run as starting.
    if not claimed:
        _mark_pending(run_dir)
    try:
        pid = _spawn(env, argv, root, run_dir)
    except AppError:
        # The launcher printed no pid: nothing was launched, and the run
        # reads as it did before. Any other failure (a launcher that timed
        # out) may have started the run: the marker stays, and the run's
        # own job.json, or the marker's age, says what became of it.
        if previous is None:
            (run_dir / LAUNCH).unlink(missing_ok=True)
        else:
            (run_dir / LAUNCH).write_text(previous, encoding="utf-8")
        raise
    _record_launch(run_dir, pid, argv)
    return pid


def _mark_pending(run_dir: Path, *, exclusive: bool = False) -> bool:
    """Write `launch.json` as `{"pending": true, "at": iso}`: a launch is
    under way. With `exclusive`, only when there is no `launch.json` yet --
    the claim on a queued run, which one process wins; False when another
    process holds it."""
    text = json.dumps({"pending": True, "at": _now().isoformat(timespec="seconds")}, indent=2)
    try:
        with open(run_dir / LAUNCH, "x" if exclusive else "w", encoding="utf-8") as out:
            out.write(text)
    except FileExistsError:
        return False
    return True


def _spawn(env: dict[str, str], argv: list[str], root: Path, run_dir: Path) -> int:
    """The launcher, run to its end: the pid of the detached run it started."""
    try:
        return detach.spawn_detached(argv, cwd=root, env=env, log=run_dir / LOG)
    except detach.DetachError as exc:
        raise AppError(f"the run could not be started: {exc}", 500) from None


def _record_launch(run_dir: Path, pid: int, argv: list[str]) -> None:
    """The real launch over the pending marker, in one step (a temp file
    replaced): a reader sees the marker or the launch, never nothing."""
    _atomic_write(run_dir / LAUNCH, json.dumps({"pid": pid, "started_at": _now().isoformat(timespec="seconds"),
                                               "host": socket.gethostname(), "argv": argv[1:]}, indent=2))


def _wants_keys(root: Path, run_dir: Path) -> bool:
    """Whether a run that goes on (resumed, continued, launched from the
    queue) has a use for the API keys: its own study -- the copy it pinned
    when it started, where there is one -- asked for AI search help."""
    try:
        return bool(run_study_of(root, run_dir).budget.ai_enabled)
    except HarnessError:
        return False


def can_resume(run_dir: Path, job: dict[str, Any]) -> bool:
    """Whether a run can go on where it stopped: it ended (stopped, or its
    process died) with a search on record that has rounds of its plan left.
    A run that never searched, or searched to the end, starts again instead;
    a check that stopped is `resume_final_check`'s."""
    if (job.get("phase") not in ("failed", "stopped") or not (run_dir / "runs.jsonl").is_file()
            or job.get("bakeoff")):  # a bake-off has no search: its check is checked again instead
        return False
    search = job.get("search") or {}
    planned = int(_plan(run_dir, job).get("generations") or 0)
    return not search or int(search.get("generations") or 0) < planned


def can_continue(run_dir: Path, job: dict[str, Any]) -> bool:
    """Whether a run can go on with more time (`continue_run`): it ended --
    done, or stopped -- with a search on record that was not aborted, on a
    directory that still holds the compiled study and the runs."""
    if job.get("phase") not in ("done", "stopped") or job.get("bakeoff"):  # a bake-off has no search to go on with
        return False
    search = job.get("search") or {}
    if not search or search.get("aborted") or search.get("generations") is None:
        return False
    return (run_dir / "runs.jsonl").is_file() and (run_dir / CONFIG).is_file()


def continue_run(home: Any, root: Path, run: str, hours: float | None = None) -> dict[str, Any]:
    """A finished run searched on for `hours` more (the study's hours when not
    said), then checked again (`job.py --continue --hours H`). Launched at
    once, so not while any study's run has the computer."""
    run_dir = _run_dir(root, run)
    if running(root):
        raise AppError("the study is already running", 409)
    if not can_continue(run_dir, phase(run_dir)):
        raise AppError("this run did not finish its search, so it cannot be given more time", 409)
    if hours is None:
        hours = load_study(root).budget.hours
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 < float(hours) <= MAX_CONTINUE_HOURS:
        raise AppError(f"hours: a number of hours, more than 0 and at most {MAX_CONTINUE_HOURS:g}", 400)
    with _launch_guard:
        _one_at_a_time(home)
        (run_dir / STOP_REQUEST).unlink(missing_ok=True)
        _launch(home, root, run_dir, ["--continue", "--hours", f"{float(hours):g}"], with_keys=_wants_keys(root, run_dir))
    return {"run": run, "continued": True}


def check_refs(root: Path, study: Study, harness: Harness, refs: list[str], *,
               base: str | None = None) -> list[dict[str, Any]]:
    """The branches a bake-off compares, checked before anything runs:
    `[{"ref", "commit", "files"}]` in the order given. Each ref's change is its
    own (`tree.diff_between`, from the merge-base with the base), may touch only
    the open files, and must apply on the base. `base` is the commit the run
    pinned; by default the study's `vary.code.from` as it is now (for `working`,
    `HEAD`: the snapshot is taken once, when the run is written). Raises
    `AppError` (409) with the sentence that says what to do."""
    if study.code is None:
        raise AppError("study bakeoff compares branches of a code study; this study tunes settings", 409)
    if not isinstance(refs, list) or not refs or not all(isinstance(r, str) and r.strip() for r in refs):
        raise AppError("refs: name at least one branch", 400)
    problems = study_problems(study, harness, root=root, for_search=False)
    if problems:
        raise AppError(f"the study is not ready yet: {problems[0]}", 409)
    if not study.test:
        raise AppError("cases.test: hold back at least one case; branches are compared on held-back cases", 409)
    twice = next((r for i, r in enumerate(refs) if r in refs[:i]), None)
    if twice is not None:
        raise AppError(f"{twice} is given twice", 409)
    from evolvekit import tree  # only a code study needs git

    open_files = list(study.code.open or (harness.code.open if harness.code else ()))
    try:
        repo = tree.Repo(study.code.repo)
        if base is None:
            base = tree.resolve_commit(repo, "HEAD" if study.code.from_ref == "working" else study.code.from_ref)
        checked = []
        for ref in refs:
            commit = tree.resolve_commit(repo, ref)
            diff = tree.diff_between(repo, base, ref)
            prefix = repo.prefix

            def own(name: str) -> str:  # relative to the source folder, as `open`'s globs are
                return name[len(prefix):] if prefix and name.startswith(prefix) else name

            outside = tree.touches_only(diff, open_files, prefix=prefix)
            if outside:
                raise AppError(f"{ref} changes files outside the open files: {', '.join(own(f) for f in outside)}; "
                               "widen vary.code.open or the harness's code.open", 409)
            if not tree.applies(repo, base, diff):
                raise AppError(f"{ref}: does not apply on the base; rebase {ref} onto it", 409)
            checked.append({"ref": ref, "commit": commit, "files": [own(f) for f in tree.changed_files(diff)]})
    except tree.TreeError as exc:
        raise AppError(str(exc), 409) from None
    return checked


def _start_bakeoff(home: Any, root: Path, refs: list[str]) -> dict[str, Any]:
    study, harness = load_study(root), load_harness(root / "harness")
    check_refs(root, study, harness, refs)  # now, so a bad ref is refused before any run exists
    run_dir = _new_run_dir(root)
    _atomic_write(run_dir / BAKEOFF, json.dumps({"refs": list(refs)}, indent=2) + "\n")
    with _launch_guard:
        if active_run(home) is not None:
            (run_dir / QUEUED).write_text(json.dumps({"queued_at": _now().isoformat(timespec="seconds")}, indent=2),
                                          encoding="utf-8")
            return {"run": run_dir.name, "queued": True}
        _launch(home, root, run_dir, ["--bakeoff"], with_keys=False)
    return {"run": run_dir.name}


def _new_run_dir(root: Path) -> Path:
    """A new run directory, named by the time; `-2`, `-3`, ... for a second start within the second."""
    base = _now().astimezone().strftime("%Y%m%d-%H%M%S")
    run_id, number = base, 2
    while (root / "runs" / run_id).exists():
        run_id, number = f"{base}-{number}", number + 1
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True)  # new, so no stale stop request can be waiting in it
    return run_dir


def bakeoff_refs_of(run_dir: Path, job: dict[str, Any]) -> list[str] | None:
    """The branches a bake-off run compares: its `job.json`'s, or -- a run that
    failed before it recorded them -- `bakeoff.json`'s; None for any other run."""
    if job.get("bakeoff"):
        return [str(entry.get("ref")) for entry in job["bakeoff"]]
    asked = _read(run_dir / BAKEOFF)
    refs = (asked or {}).get("refs")
    return [str(r) for r in refs] if isinstance(refs, list) and refs else None


def _launch_args(run_dir: Path) -> list[str]:
    """What `job.py` is told for a run launched from the queue: a bake-off compares its branches."""
    return ["--bakeoff"] if (run_dir / BAKEOFF).is_file() else []


def start(home: Any, root: Path, *, resume: str | None = None, bakeoff: list[str] | None = None) -> dict[str, Any]:
    """Start the study: a new run, or with `resume` the run of that name
    again from where it stopped (`job.py --resume`). A stop request left in
    the run directory is removed first: it was answered, and the resume is
    not to answer it again. One study runs at a time: while another study's
    run is active, a new run is queued (`{"run": id, "queued": True}`) and
    launched by the server when that one is done.

    With `bakeoff` (branch names), a run with no search that checks each
    branch against the starting point on the held-back cases: the refs are
    checked first (`check_refs`), then written to `runs/<id>/bakeoff.json`,
    and the run is `job.py --bakeoff`."""
    if running(root):
        raise AppError("the study is already running", 409)
    if bakeoff is not None:
        return _start_bakeoff(home, root, bakeoff)
    if resume is not None:
        run_dir = _run_dir(root, resume)
        if not can_resume(run_dir, phase(run_dir)):
            raise AppError("this run cannot be continued: start the study again", 409)
        with _launch_guard:
            _one_at_a_time(home)
            (run_dir / STOP_REQUEST).unlink(missing_ok=True)
            _launch(home, root, run_dir, ["--resume"], with_keys=_wants_keys(root, run_dir))
        return {"run": resume, "resumed": True}
    study, harness = load_study(root), load_harness(root / "harness")
    problems = study_problems(study, harness, root=root)
    if problems:
        raise AppError(f"the study is not ready yet: {problems[0]}", 409)
    models = None
    if study.budget.ai_enabled:
        models = ai_models(home)
        if models is None:
            raise AppError("AI search help is on, and there is no key for it: add one in Settings, or turn AI help off", 409)
        _cap_holds(home, study)
    run_dir = _new_run_dir(root)
    run_id = run_dir.name
    if models:
        (run_dir / MODELS).write_text(json.dumps(models, indent=2), encoding="utf-8")
    with _launch_guard:
        if active_run(home) is not None:
            # Another study has the computer: this run waits, and the
            # server's ticker launches it when that one is done.
            (run_dir / QUEUED).write_text(json.dumps({"queued_at": _now().isoformat(timespec="seconds")}, indent=2),
                                          encoding="utf-8")
            return {"run": run_id, "queued": True}
        _launch(home, root, run_dir, [], with_keys=study.budget.ai_enabled)
    return {"run": run_id}


def _one_at_a_time(home: Any) -> None:
    """A run continued by hand (a resume, a final check again) is launched
    at once, so it may not be while another study's run has the computer."""
    active = active_run(home)
    if active is not None:
        name = _waiting_for(home.studies_dir / active[0]) or active[0]
        raise AppError(f"'{name}' is running: continue this one when it has finished", 409)


def launch_queued(home: Any) -> str | None:
    """Launch the run that has waited longest across the home, when nothing is
    active; `"slug/run"` of what was launched, or None. The server calls
    this every few seconds, and so do `evolvekit study wait` and `watch`.

    Two processes may ask at once (the app's ticker, a terminal that waits):
    `active_run` is asked again just before the launch, and the launch
    claims the run by creating its pending `launch.json` exclusively, so
    the same queued run is never launched twice."""
    with _launch_guard:
        if active_run(home) is not None:
            return None
        waiting = []
        for root in _study_roots(home.studies_dir):
            for run_dir in _run_dirs(root):
                job = phase(run_dir)
                if job.get("phase") == "queued":
                    waiting.append((str(job.get("queued_at") or ""), root.name, run_dir))
        if not waiting:
            return None
        _, slug, run_dir = min(waiting)
        root = run_dir.parent.parent
        if active_run(home) is not None or not _mark_pending(run_dir, exclusive=True):
            return None  # another process launched something meanwhile
        # The claim ends the wait: whoever reads the run from here on sees it starting.
        try:
            waited: str | None = (run_dir / QUEUED).read_text(encoding="utf-8")
        except OSError:
            waited = None
        (run_dir / QUEUED).unlink(missing_ok=True)
        try:
            _launch(home, root, run_dir, _launch_args(run_dir), with_keys=_wants_keys(root, run_dir) and not _launch_args(run_dir),
                    claimed=True)
        except AppError:
            # The launcher started nothing: queued again, as it was, and the
            # next tick tries once more. Any other failure may have started
            # the run, so it is never queued again (`_launch` keeps the marker).
            if waited is not None:
                (run_dir / QUEUED).write_text(waited, encoding="utf-8")
            (run_dir / LAUNCH).unlink(missing_ok=True)
            raise
        return f"{slug}/{run_dir.name}"


def stop(root: Path, run: str) -> dict[str, Any]:
    run_dir = _run_dir(root, run)
    state = phase(run_dir).get("phase")
    if state == "queued":
        # It never started: nothing to keep. The dequeue claims the run as a
        # launch does (`launch_queued`), so a launch in another process and
        # this stop cannot both win: the directory goes only when this claim
        # does, and the study is a draft again.
        with _launch_guard:
            if _mark_pending(run_dir, exclusive=True):
                shutil.rmtree(run_dir, ignore_errors=True)
                return {"stopping": False, "dequeued": True}
        state = phase(run_dir).get("phase")  # claimed by a launch meanwhile: it is starting
    if state not in ACTIVE:
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
    if not run_study_of(root, run_dir).test:
        raise AppError("the run held no cases back, so there is no final check", 409)
    if seeds is not None and not 1 <= int(seeds) <= len(CHECK_SEEDS):
        raise AppError(f"the final check takes 1 to {len(CHECK_SEEDS)} runs per case", 400)
    with _launch_guard:
        _one_at_a_time(home)
        # A check only runs the application: nothing in it has a use for a key.
        _launch(home, root, run_dir, ["--check"] + (["--seeds", str(int(seeds))] if seeds is not None else []),
                with_keys=False)
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


def _what(study: Study) -> str:
    if study.code is not None:
        return "code change"
    settings, data = bool(study.tuned_settings()), bool(study.data)
    return "combination of settings and data changes" if settings and data else "data changes" if data else "settings"


def _baseline(study: Study, harness: Harness, seed: dict[str, Any] | None) -> str:
    """What the starting point is, in words: "PyVRP's own defaults", "the
    settings in today.json", "the data as it is", or both; for a code study
    "the code at <ref>"."""
    if study.code is not None:
        ref = study.code.from_ref
        return "the files as they were when the run started" if ref == "working" else f"the code at {ref}"
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


def _continued(job: dict[str, Any]) -> bool:
    """Whether the run's latest session is a continuation with more time
    (`execute.continue_study`): `continued_at` is the session's own
    `resumed_at`; a resume after it, of a continuation that died, is younger."""
    moment = job.get("continued_at")
    return bool(moment) and str(job.get("resumed_at") or moment) <= str(moment)


def _window(job: dict[str, Any], run_dir: Path, hours: float) -> tuple[datetime | None, float]:
    """When the run's time started and how much there is: the run's start
    and `hours` -- or, continued with more time, the continuation and the
    hours added."""
    if _continued(job) and job.get("hours_added"):
        return _moment(job["continued_at"]), float(job["hours_added"])
    return _started(job, run_dir), hours


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
            todo = "Runs failed with an error. Show the output to see the message; if every run fails, run the test run again."
        grouped.append({"count": count, "stage": reason.get("stage"), "message": text, "todo": todo})
    return grouped


def _closest(run_dir: Path, finished: bool = False, rows: list[dict[str, Any]] | None = None) -> str:
    """How close the tries came to the starting point when none beat it.
    `rows` are the run's `runs.jsonl` rows when the caller read them already."""
    rows = [r for r in (_rows(run_dir) if rows is None else rows) if _finite(r.get("score"))]
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
    """Where a run is, in the words the Running screen uses. The run's own
    study (the copy it keeps) says what it learns from and holds back."""
    run_dir = _run_dir(root, run)
    study, harness = run_study_of(root, run_dir), load_harness(root / "harness")
    job = phase(run_dir)
    plan = _plan(run_dir, job)
    document = document_of(run_dir) if (run_dir / "events.jsonl").exists() else {}
    health, progress = document.get("health") or {}, document.get("progress") or {}
    rows = _rows(run_dir)  # read once: the seed, the closest try and the gated count all come from here
    goal = _goal(study, harness)
    started, hours = _window(job, run_dir, float(plan.get("hours") or study.budget.hours))
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
    verdict = improvement.get("verdict")
    # The best so far is one figure everywhere (`verdict.best_so_far`): on the cases learned from, as
    # the verdict's learned-from figure says it -- not the status document's, which is on another base.
    best_now = verdict_doc.best_so_far(run_dir, goal, rows, job) if document else {"id": None, "pct": None}
    pct = best_now["pct"]
    better = _better_word(goal["direction"])
    seed, _ = _seed_and_best(run_dir, document, rows) if document else (None, None)
    start_words = f"the starting point ({_baseline(study, harness, seed)})"
    runs = study.limits.runs_per_case
    learned = (f"on the {len(study.training)} case{'s' if len(study.training) != 1 else ''} the search "
               f"{'learned' if finished else 'learns'} from ({'one run' if runs == 1 else f'{runs} runs'} each)")
    final_state = job.get("final") or {}
    # The verdict's own test for "there is a final check": the comparison it reads must exist.
    checked = (job.get("phase") == "done" and bool(final_state) and not (final_state.get("skipped") or final_state.get("stopped"))
               and verdict_doc._check_of(run_dir, final_state)[0] is not None)
    if pct is None or verdict in ("none", None) or (pct or 0) <= 0:
        best_line = (f"No combination beat {start_words}." if finished
                     else f"No combination has beaten {start_words} yet.") + _closest(run_dir, finished, rows)
    elif not finished:
        # Only the final check says whether it is better: the search picks
        # what did best on its own cases, which flatters it.
        best_line = (f"So far {_pct(pct)} {better} {goal['says']} than {start_words}, {learned}. "
                     "Whether that holds on cases the search never saw, the final check tells.")
    else:
        # A finished run's best is past: the verdict is the answer, or there was no final check.
        best_line = (f"The search's best was {_pct(pct)} {better} {goal['says']} than {start_words}, {learned}. "
                     + ("The final check's verdict is the answer on the cases the search never saw." if checked
                        else "It was not checked on cases the search never saw."))
    levels = (progress.get("levels") or {})
    series = [[s.get("elapsed_s"), s.get("improvement_pct")] for s in progress.get("series") or []
              if s.get("elapsed_s") is not None]

    stage = health.get("stage") or {}
    written = writing(run_dir, job) if not stage else None
    # The round being run is the one after the last finished; round 0 is the
    # starting point alone.
    current = done_rounds + 1 if isinstance(done_rounds, int) else (0 if stage else None)
    if job.get("phase") == "check":
        # The check adds a run per case at a time while the answer is open
        # (`execute._extend_check`); `check.seeds` says how many it is on.
        on = int((job.get("check") or {}).get("seeds") or plan.get("check_seeds") or DEFAULT_CHECK_SEEDS)
        now_line = (f"The final check: {_runs_word(on)} per case so far on the held-back cases; "
                    "it adds runs while time remains and the answer is open.")
    elif stage and isinstance(current, int):
        # The cases of the stage that is running: a quick look runs on a few of the study's cases.
        per_combination = int(stage.get("runs_planned") or 0) // max(1, int(stage.get("candidates") or 1))
        cases = max(1, per_combination // max(1, study.limits.runs_per_case)) if per_combination else len(study.training)
        on = f"on {cases} case{'s' if cases != 1 else ''}"
        runs = f"{stage.get('runs_done')} of {stage.get('runs_planned')} runs done"
        count = stage.get("candidates") or 0
        now_line = (f"Measuring the starting point {on}: {runs}." if current == 0
                    else f"Round {current}: {count} combination{'s' if count != 1 else ''} {on}, {runs}.")
    elif written:
        # The round's changes are being written, one after another, before any of them runs.
        now_line = f"Writing changes: {written['done']} of {written['planned']}."
    elif job.get("phase") in ("starting", "search"):
        now_line = "Getting ready: preparing the runs."
    else:
        now_line = ""

    waiting = _waiting_for(root) if job.get("phase") == "queued" else ""
    bakeoff_refs = bakeoff_refs_of(run_dir, job)
    sentences = {
        "queued": f"Waiting for '{waiting}' to finish." if waiting else "Waiting for its turn.",
        "starting": "Starting.",
        "search": ("Searching on, with more time." if _continued(job)
                   else "Searching again, from where it stopped." if job.get("resumed_at")
                   else "Searching for better " + ("code" if study.code is not None else _what(study)) + "."),
        "check": ("Checking each branch against the starting point on the held-back cases." if job.get("bakeoff")
                  else "Checking the best on the cases the search never saw."),
        "done": "Finished.",
        "stopped": ("Stopped before the branches were compared." if bakeoff_refs else
                    "Stopped." + (f" {job['search'].get('stop_reason')}." if (job.get("search") or {}).get("stop_reason") else "")),
        "failed": "It failed: " + str(job.get("error") or "no reason recorded") + ".",
    }
    gated = sum(1 for r in rows if r.get("gated"))
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
        # `says` is the figure in `study status`'s words ("6.1 % lower tour length"), so the page never
        # formats it a second way; `id` is the combination it belongs to.
        "best": {"pct": pct, "id": best_now["id"], "says": _best_says(pct, goal), "verdict": verdict, "line": best_line,
                 "levels": levels.get("levels") if levels.get("available") else None,
                 "levels_verdict": levels.get("verdict") if levels.get("available") else None},
        "series": series,
        # What the run's AI spent so far, of its cap (`verdict.ai_spent`); None for a run without AI.
        "spent": verdict_doc.ai_spent(run_dir, study, job),
        # What to hear before and while the study runs, besides the plan: too few held-back cases for a firm answer.
        "notes": plan_notes(len(study.test)),
        # After the first round: how many of the planned rounds the pace so far buys, when fewer (`pace`).
        "pace": pace(progress.get("series") or [], plan, job),
        "now": now_line,
        # While a round's changes are being written: how many of how many (`writing`); else None.
        "writing": written,
        "failures": _failures_in_words((health.get("evaluations") or {}).get("failure_reasons") or [],
                                       float((_read(run_dir / STUDY_RUN) or {}).get("time_limit_s") or study.limits.time_per_case_s)),
        "gated": gated,
        "gated_line": (f"{gated} combination{'s' if gated != 1 else ''} broke a guardrail and {'do' if gated != 1 else 'does'} not count."
                       if gated else ""),
        "busy_line": ("Other programs kept this computer busy during the study. A run with a time limit then finds a "
                      "little less than it would alone, so close heavy programs while a study runs." if busy else ""),
        "final": job.get("final"),
        "bakeoff": job.get("bakeoff"),
        "bakeoff_refs": bakeoff_refs,
        # A run stopped before its search began has a `search` but no runs, and nothing to check.
        "can_check": (bool(job.get("search")) and job.get("phase") in ("stopped", "failed") and bool(study.test)
                      and (run_dir / "runs.jsonl").is_file()),
        "can_resume": can_resume(run_dir, job),
        "can_continue": can_continue(run_dir, job),
        "dashboard": f"/studies/{root.name}/runs/{run}/dashboard/",
    }


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


def _seed_and_best(run_dir: Path, document: dict[str, Any],
                   rows: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The starting point's row and the best candidate's; `rows` are the run's
    `runs.jsonl` rows when the caller read them already."""
    rows = _rows(run_dir) if rows is None else rows
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
    if final.get("rule"):
        lines.append(str(final["rule"]))
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
    """The results of a run, as the study it was started with describes them:
    its goal, cases and choices, not today's edited ones."""
    run_dir = _run_dir(root, run)
    study, harness = run_study_of(root, run_dir), load_harness(root / "harness")
    job = phase(run_dir)
    plan = _plan(run_dir, job)
    document = document_of(run_dir) if (run_dir / "events.jsonl").exists() else {}
    goal = _goal(study, harness)
    seed, best = _seed_and_best(run_dir, document)
    if best is None and job.get("bakeoff"):  # no search, so no status document: the branch the check chose
        best = verdict_doc._best_of(job, _rows(run_dir))
    improvement = ((document.get("progress") or {}).get("improvement") or {})
    pct = improvement.get("pct")
    what = _what(study)
    learned = "on the cases the search learned from"
    start_words = f"your starting point ({_baseline(study, harness, seed)})"
    if job.get("bakeoff"):
        count = len(job["bakeoff"])
        headline = (f"{count} branch{'es' if count != 1 else ''} checked against {start_words} "
                    "on the held-back cases, with no search.")
    elif best is None or seed is None:
        headline = "The search has no finished result yet."
    elif best.get("id") == seed.get("id") or not pct or pct <= 0:
        headline = f"No {what} found beat {start_words} {learned}."
    else:
        headline = (f"The best {what} found give{'s' if what.startswith(('combination', 'code')) else ''} {_pct(pct)} "
                    f"{_better_word(goal['direction'])} {goal['says']} than {start_words} {learned}.")
    final_state = job.get("final") or {}
    comparison, check_runs = verdict_doc._check_of(run_dir, final_state)
    baseline = _baseline(study, harness, seed)
    facts = verdict_doc._run_facts(run_dir, job)
    started, used_s = facts["started"], facts["used_s"]
    final = _final_words(job, comparison, goal, what, baseline)
    final["recheck"] = (_recheck(final, plan, comparison, used_s)
                        if job.get("phase") == "done" and not job.get("bakeoff") else None)
    if final.get("state") in ("unclear", "worse", "single"):
        final["next"] = ("For a firmer answer: bring more cases (other days or weeks) and start a new study from "
                         "this result that holds more of them back.")
    candidate = str(final_state.get("candidate") or "")
    held_start, held_best = _means(_paired(check_runs, BASELINE, candidate)) if candidate else ({}, {})
    seed_id, best_id = str((seed or {}).get("id") or ""), str((best or {}).get("id") or "")
    by_case = verdict_doc._by_case(run_dir, goal, seed_id, best_id, candidate, check_runs)

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

    changed = verdict_doc._changed(root, study, harness, seed, best)
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
        "tried": facts["tried"],
        "started_at": started.astimezone().strftime("%Y-%m-%d %H:%M") if started else None,
        "took": _duration(used_s) if used_s is not None else None,
        "compared_with": baseline,
    }
    # The learned-from figure is the verdict's own (the change of the plain averages), as the report
    # says it; `pct` is the search's figure (the average of the per-case changes), kept for the Tries plot.
    learned_pct = verdict_doc._learned_pct(seed, best, goal)
    # The headline, the advice, the decision and its numbers are the verdict
    # document's, so the page and `build_verdict` cannot say two things.
    verdict = verdict_doc._document(root, run_dir, study, harness, job, final, comparison, by_case["held"], changed,
                                    facts, learned_pct, baseline=baseline, check_runs=check_runs)
    final["line"], final["advice"] = verdict["line"], verdict["advice"]
    # The held-back table's change is the document's: the paired mean over the final check's runs.
    paired = {row["name"]: row["change_pct"] for row in verdict["by_case"]}
    by_case["held"] = [{**row, "change_pct": paired.get(row["case"], row["change_pct"])} for row in by_case["held"]]
    if "decision" in final:
        final["decision"] = verdict["decision"]
    for key, name in (("mean", "mean_pct"), ("ci95", "ci95_pct"), ("worst_pct", "worst_pct"), ("chance", "chance"),
                      ("chance_words", "chance_words")):
        if key in final and name in verdict:
            final[key] = verdict[name]
        elif key in final and job.get("bakeoff"):
            final.pop(key)  # a bake-off with no branch shown ahead: no unnamed branch's figure on the page
    if job.get("bakeoff") and "mean_pct" not in verdict:
        final.pop("levels", None)
        by_case["held"], changed = [], []
        for row in kpis:
            row["test"] = {"start": row["test"]["start"], "best": None}
    data = {
        "run": run, "phase": job.get("phase"), "study": study.name, "harness": harness.title,
        "headline": headline, "improvement_pct": pct, "learned_pct": learned_pct, "goal": goal, "final": final, "kpis": kpis,
        "guardrails": guardrails, "rules": rules, "changed": changed, "downloads": downloads, "by_case": by_case,
        "context": context,
        "best_id": (best or {}).get("id"), "is_seed": bool(best and seed and best.get("id") == seed.get("id")),
        "cases": {"training": list(study.training), "test": list(study.test)},
        "can_continue": can_continue(run_dir, job),
    }
    for key in ("how_it_ran", "planned", "spent"):  # how much of the plan ran and what the AI spent, as the verdict says
        if verdict.get(key):
            data[key] = verdict[key]
    if "candidates" in verdict:  # a bake-off: every branch with its own decision and line
        data["candidates"], data["bakeoff"] = verdict["candidates"], job.get("bakeoff")
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
    """One export of `values`: the file the final check wrote when it is of
    these values, else written now (`execute.write_exports`)."""
    target = str(safe_relative(root, case)) if case else None
    out = export_path(run_dir, name, target)
    if not (out.is_file() and _read(run_dir / "downloads" / "values.json") == values):
        try:
            write_exports(root, run_dir, values, names=[name], case=target)
        except ExportFailed as exc:
            raise AppError(str(exc), 500) from None
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
        study = run_study_of(root, run_dir)
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
    rule_note = f"<div class='help' style='margin-top:6px'>{esc(str(final['rule']))}</div>" if final.get("rule") else ""
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
<div class="advice" data-state="{esc(str(final.get('state')))}">{esc(final.get('advice') or '')}</div>{rule_note}</div>
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
    round 2 of 6 · 1 h 10 min left · 3.2 % lower real cost so far, not checked yet" (the round only with
    `document_of`; no time left when the rounds were set by hand), "finished · probably better: 3.1 %
    lower real cost", "finished · not proven on the held-back cases". A draft
    is `study` as it is today; a run is described by the study it was
    started with (the copy the latest run keeps). Besides the words, the
    facts the page draws the card from (`run_line`)."""
    dirs = _run_dirs(root)
    if not dirs:
        return {"kind": "draft", "line": f"draft · step {max(1, min(study.step, 7))} of 7",
                "step": max(1, min(study.step, 7)), "what": _kind_of(study)}
    return run_line(root, study, dirs[-1], document_of)


def _kind_of(study: Study | None) -> str:
    """"settings", "data", "both" or "code": what a study changes, for "keep today's settings"."""
    if study is None:
        return "settings"
    if study.code is not None:
        return "code"
    settings, data = bool(study.tuned_settings()), bool(study.data)
    return "both" if settings and data else "data" if data else "settings"


def _check_progress(run_dir: Path, job: dict[str, Any], plan: dict[str, Any], study: Study | None) -> dict[str, int] | None:
    """How far the final check is: runs finished of runs planned (the
    starting point and the best, on every held-back case, `seeds` times each),
    counted from the check's own event log."""
    seeds = int((job.get("check") or {}).get("seeds") or plan.get("check_seeds") or 0)
    cases = len(study.test) if study is not None else 0
    if not cases or not seeds:
        return None
    done = 0
    events = run_dir / "confirm" / "final" / "events.jsonl"
    if events.is_file():
        with events.open(encoding="utf-8", errors="replace") as handle:
            done = sum(1 for line in handle if '"eval_finished"' in line)
    planned = (1 + len(job.get("bakeoff") or [None])) * cases * seeds  # the starting point and each candidate
    return {"done": min(done, planned), "planned": planned}


def _rounds_by_hand(plan: dict[str, Any], study: Study | None) -> bool:
    """Whether the run's rounds were set by hand (`plan: {auto: false, generations: …}`):
    then the rounds stop the search, and the hours are only an upper bound."""
    if "adjusted" in plan:
        return bool({"rounds", "generations"} & set(plan.get("adjusted") or ()))
    chosen = getattr(study, "plan", None) or {}
    return not chosen.get("auto", True) and bool({"rounds", "generations"} & set(chosen))


WRITING_TAIL = 256 * 1024
"""How much of the end of `events.jsonl` `writing` reads: a round's writing phase is a
`generation_started` and one `candidate_bred` per child, a few hundred bytes each."""


def writing(run_dir: Path, job: dict[str, Any]) -> dict[str, int] | None:
    """While a round's changes are being written, before any of them runs:
    `{"done", "planned"}`, the round's `candidate_bred` events since its
    `generation_started` (each child written, or given up on) of its
    `children_planned`. None outside a search, for the starting point's round,
    once the round's runs started (`stage_started`), or when the round began
    before the end of the log `WRITING_TAIL` reads. Read from the end of the
    run's own event log, which every child's writing appends to."""
    if job.get("phase") != "search":
        return None
    path = run_dir / "events.jsonl"
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - WRITING_TAIL))
            tail = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return None
    done = 0
    for line in reversed(tail if size <= WRITING_TAIL else tail[1:]):  # the first line may be cut
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type") if isinstance(event, dict) else None
        if kind in ("stage_started", "generation_finished", "run_finished"):
            return None
        if kind == "candidate_bred":
            done += 1
        elif kind == "generation_started":
            planned = event.get("children_planned")
            if not isinstance(event.get("generation"), int) or event["generation"] < 1 or not isinstance(planned, int):
                return None
            return {"done": min(done, planned), "planned": planned}
    return None


def pace(series: list[dict[str, Any]], plan: dict[str, Any], job: dict[str, Any]) -> dict[str, Any] | None:
    """After the first round of a live search: how many of the planned rounds
    the time will buy at the pace so far, `{"rounds", "planned", "line"}` --
    "At this pace about 2 of the 11 planned rounds will run." -- when that is
    fewer than planned; else None. `series` is the status document's
    `progress.series` (each round's `generation` and the active seconds when it
    ended, `elapsed_s`). The search stops on time as `RunStop.before_generation`
    does: no round is started that the longest of the last two would not let
    finish within `plan.max_hours`. None for a run whose rounds were set by hand
    (they, not the hours, stop it), continued or resumed (its plan is not this
    session's), or not searching. It claims nothing about any result."""
    if job.get("phase") != "search" or _continued(job) or job.get("resumed_at") or job.get("bakeoff"):
        return None
    if {"rounds", "generations"} & set(plan.get("adjusted") or ()):
        return None
    planned = plan.get("rounds")
    limit = plan.get("max_hours")
    limit_s = float(limit) * 3600.0 if isinstance(limit, (int, float)) else float(plan.get("search_s") or 0.0)
    if not isinstance(planned, int) or planned <= 0 or limit_s <= 0:
        return None
    ends = {int(s["generation"]): float(s["elapsed_s"]) for s in series
            if isinstance(s.get("generation"), int) and isinstance(s.get("elapsed_s"), (int, float))}
    done = sorted(g for g in ends if g >= 1 and g - 1 in ends)
    if not done:
        return None
    last = done[-1]
    longest = max(ends[g] - ends[g - 1] for g in done[-2:])
    if longest <= 0:
        return None
    rounds = last + max(0, math.floor((limit_s - ends[last]) / longest))
    if rounds >= planned:
        return None
    return {"rounds": rounds, "planned": planned,
            "line": f"At this pace about {rounds} of the {planned} planned round{'s' if planned != 1 else ''} will run."}


def _best_says(pct: float | None, goal: dict[str, Any]) -> str | None:
    """The best so far in `study status`'s words, "6.1 % lower tour length"; None until it beats the
    starting point. Not "better": nothing claims that before the final check does."""
    if not isinstance(pct, (int, float)) or pct <= 0:
        return None
    return f"{_pct(pct)} {_better_word(goal.get('direction') or 'lower')} {goal.get('says') or 'the goal'}"


_FINISHED_CARDS: dict[tuple[Any, ...], dict[str, Any]] = {}
"""Finished runs' cards without a status document (`run_line`), by `_finished_key`: what the runs route
(asked every few seconds by an open Study page) would otherwise read from every paired run each time."""
_FINISHED_LOCK = threading.Lock()
_FINISHED_MAX = 512


def _stamp(path: Path | None) -> tuple[int, int] | None:
    try:
        stat = path.stat() if path is not None else None
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size) if stat is not None else None


def _finished_key(run_dir: Path, job: dict[str, Any]) -> tuple[Any, ...]:
    """What a finished run's card is made of: its job (as `phase` read it), and as file stamps its final
    check's comparison and runs and the copy of the study it kept. A check run again, or a job rewritten,
    is a new key."""
    final = job.get("final") or {}
    comparison = run_dir / str(final["comparison"]) if final.get("comparison") else None
    return (str(run_dir), json.dumps(job, sort_keys=True, default=str), _stamp(comparison),
            _stamp(comparison.parent / "results.json") if comparison is not None else None,
            _stamp(run_dir / "study.yaml"))


def run_line(root: Path, study: Study | None, run_dir: Path, document_of: DocumentOf | None = None) -> dict[str, Any]:
    """`_run_line`; a finished run's card without a status document is read once and then kept while
    the files it is made of stay as they are (`_finished_key`): the runs route asks for it every few seconds."""
    job = phase(run_dir)
    if document_of is not None or job.get("phase") != "done":
        return _run_line(root, study, run_dir, document_of, job)
    key = _finished_key(run_dir, job)
    with _FINISHED_LOCK:
        kept = _FINISHED_CARDS.get(key)
    if kept is not None:
        return json.loads(json.dumps(kept))  # a copy: no caller changes the kept card
    card = _run_line(root, study, run_dir, None, job)
    with _FINISHED_LOCK:
        if len(_FINISHED_CARDS) >= _FINISHED_MAX:
            _FINISHED_CARDS.clear()
        _FINISHED_CARDS[key] = json.loads(json.dumps(card))
    return card


def _run_line(root: Path, study: Study | None, run_dir: Path, document_of: DocumentOf | None,
              job: dict[str, Any]) -> dict[str, Any]:
    """One run's state in the card's few words (`state_line`): `kind`, `run`,
    `line`, and a `tone` where the news is bad or open. The run is described
    by the study it was started with; `study` (today's) is the fallback for a
    run that kept no copy. Without `document_of`, a running run's line says
    how much time is left but not how far it got: that reads the run's
    status document, which a list of runs need not pay for.

    The facts the page draws a card from ride along: `phase`, `rounds`
    (done, planned), `what` (settings, data or both), and by state
    `finish_at`, `left_s`, `hours`, `continued`, `best_pct`, `check`,
    `waiting_for`; for a finished run its `decision` (use, keep, worse,
    single, skipped) with `mean`, `ci95`, `chance` and `n` held-back cases,
    and `same` (no figures: "finished · no difference in any run") when every
    run gave exactly the starting point's result -- for a bake-off, every
    branch's; `check_stopped` for a final check stopped before it decided.
    `best_pct` is `verdict.best_so_far`'s figure, `best_says` that figure in words."""
    state = job.get("phase")
    try:
        study = run_study_of(root, run_dir)
        goal = _goal(study, load_harness(root / "harness"))
    except Exception:  # noqa: BLE001 - a card must render even for a damaged study
        goal = {"says": "the goal", "direction": "lower"}
    plan = _plan(run_dir, job) or {}
    # How far a run got is read from its status document, which is not free on a long run: only for a
    # card that shows it (running, stopped, failed), never for a finished one.
    wants = state in ACTIVE or state in ("stopped", "failed")
    document = (document_of(run_dir) or {}) if wants and document_of is not None and (run_dir / "events.jsonl").exists() else {}
    last = ((document.get("health") or {}).get("generation") or {}).get("last_finished")
    facts: dict[str, Any] = {"phase": state, "what": _kind_of(study),
                             "goal": {"says": goal.get("says"), "label": goal.get("label"), "direction": goal.get("direction")},
                             "rounds": {"done": max(0, int(last)) if isinstance(last, int) else 0,
                                        "planned": int(plan.get("rounds") or 0)}}
    # The best so far as `study status`, the status document and the app's strip all say it.
    pct = verdict_doc.best_so_far(run_dir, goal, job=job)["pct"] if document else None
    if isinstance(pct, (int, float)):
        facts["best_pct"] = pct
        if _best_says(pct, goal):
            facts["best_says"] = _best_says(pct, goal)
    if state == "queued":
        waiting = _waiting_for(root)
        return {"kind": "queued", "run": run_dir.name, "line": "queued" + (f" · after {waiting}" if waiting else ""),
                **facts, "waiting_for": waiting, "hours": float(plan.get("hours") or (study.budget.hours if study is not None else 0))}
    if state in ACTIVE:
        started, hours = _window(job, run_dir, float(plan.get("hours")
                                                      or (study.budget.hours if study is not None else 0)))
        left = max(0.0, hours * 3600 - (_now() - started).total_seconds()) if started and hours else None
        if _rounds_by_hand(plan, study) and not _continued(job):
            # The rounds stop a manual plan, not the hours: the hours' "time left" would be wrong.
            left = None
        planned = facts["rounds"]["planned"]
        where = ""
        if state == "check":
            where = " · comparing branches" if bakeoff_refs_of(run_dir, job) else " · final check"
        elif isinstance(last, int):
            current = max(1, last + 1)
            current = min(current, planned) if planned else current
            where = f" · round {current}" + (f" of {planned}" if planned else "")
        elif not _continued(job) and not job.get("resumed_at") and (
                document_of is not None or not (run_dir / "events.jsonl").exists()):
            # Live, and no round has reported yet (known: the status was read, or there are no events).
            where = " · starting" + (f" round 1 of {planned}" if planned else "")
        line = ("running" + (" · continued" if _continued(job) else "") + where
                + (f" · {_duration(left)} left" if left is not None else ""))
        if document_of is not None and (run_dir / "events.jsonl").exists():
            if pct is not None and pct > 0:
                # Not "better": nothing claims that before the final check does.
                line += f" · {_best_says(pct, goal)} so far, not checked yet"
        facts.update({"left_s": left, "hours": hours, "continued": _continued(job) or bool(job.get("resumed_at")),
                      "finish_at": (started + timedelta(hours=hours)).astimezone().strftime("%H:%M") if started and hours else None})
        if state == "check":
            facts["check"] = _check_progress(run_dir, job, plan, study)
        ahead = pace(((document.get("progress") or {}).get("series") or []), plan, job)
        if ahead:
            facts["pace"] = ahead
        spent = verdict_doc.ai_spent(run_dir, study, job) if document_of is not None and study is not None else None
        if spent is not None:
            facts["spent"] = spent
        return {"kind": "running", "run": run_dir.name, "line": line, **facts}
    if state == "done":
        final = job.get("final") or {}
        if final.get("skipped"):
            reason = str(final["skipped"]).split(":")[0].split(",")[0]  # "the starting point stayed the best"
            return _with_verdict(root, run_dir, document_of, {"kind": "finished", "run": run_dir.name,
                                                              "line": f"finished · {reason}", **facts, "decision": "skipped"})
        comparison = verdict_doc._check_of(run_dir, final)[0]  # marks a change no run told from today's
        results = (comparison or {}).get("per_candidate") or {}
        own = results.get(final.get("candidate") or "") or {}
        summary = own.get("summary") or {}
        # Every run gave exactly today's result -- for a bake-off, every branch's: no figure, no range.
        same = (bool(results) and all(r.get("identical") for r in results.values()) if job.get("bakeoff")
                else bool(own.get("identical")))
        mean = summary.get("mean")
        decision = _decision_of(final, summary) if summary else ("use" if final.get("confirmed") else "keep")
        facts.update({"decision": decision, "mean": mean if isinstance(mean, (int, float)) else None,
                      "ci95": summary.get("ci95"), "chance": _chance_of(summary) if summary else None,
                      "n": int(summary.get("n") or 0)})
        if same:
            facts.update({"decision": "keep", "mean": None, "ci95": None, "chance": None, "same": True})
            card = {"kind": "finished", "tone": "warn", "run": run_dir.name, "line": "finished · no difference in any run",
                    **facts}
        elif decision == "use":
            amount = f": {_pct(mean)} {_better_word(goal['direction'])} {goal['says']}" if isinstance(mean, (int, float)) else ""
            card = {"kind": "finished", "run": run_dir.name, "line": "finished · probably better" + amount, **facts}
        elif decision == "worse":
            card = {"kind": "finished", "tone": "bad", "run": run_dir.name, "line": "finished · worse on the held-back cases", **facts}
        else:
            card = {"kind": "finished", "tone": "warn", "run": run_dir.name, "line": "finished · not proven on the held-back cases", **facts}
        return _with_verdict(root, run_dir, document_of, card)
    if state == "stopped":
        return {"kind": "stopped", "run": run_dir.name, "line": "stopped", **facts,
                "check_stopped": bool((job.get("final") or {}).get("stopped"))}
    return {"kind": "failed", "run": run_dir.name, "line": "failed · " + str(job.get("error") or "see the run")[:80], **facts,
            "error": str(job.get("error") or "")}


def _with_verdict(root: Path, run_dir: Path, document_of: DocumentOf | None, card: dict[str, Any]) -> dict[str, Any]:
    """A finished card with the verdict's own words beside it (design 3.4): `verdict` holds the
    document's `decision`, `line` and `advice`, and `says`, the decision in the state line's words
    ("not proven on the held-back cases", "probably better"), and when the document has them its
    `how_it_ran` and, for a bake-off, `candidates` (each branch's `ref` and `line`). Only with `document_of`, as the card
    asks for it: a list of runs need not pay for a verdict per run; a run whose verdict cannot be
    read keeps its card without one."""
    if document_of is None:
        return card
    try:
        document = verdict_doc.build_verdict(root, run_dir.name)
    except Exception:  # noqa: BLE001 - a card must render even for a damaged run
        return card
    says = str(card["line"]).removeprefix("finished · ").split(":")[0]
    verdict = {"decision": document.get("decision"), "line": document.get("line", ""),
               "advice": document.get("advice", ""), "says": says}
    if document.get("how_it_ran"):
        verdict["how_it_ran"] = document["how_it_ran"]
    if document.get("spent"):
        verdict["spent"] = document["spent"]
    if document.get("candidates"):
        # A bake-off: each branch's own line, by its ref.
        verdict["candidates"] = [{"ref": c.get("ref"), "line": c.get("line", "")} for c in document["candidates"]]
    return {**card, "verdict": verdict}


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
                    base = json.loads(safe_relative(root, study.inputs[settings_input]).read_text(encoding="utf-8"))
                except (OSError, ValueError, AppError):  # a path that is not the study's reads as nothing
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
