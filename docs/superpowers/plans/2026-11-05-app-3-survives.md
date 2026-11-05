# App improvement 3: a study survives the night — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A study that was set up, started and left alone is still there in the morning: a run that died with the computer resumes where it was; a dead run never looks alive; a stop pressed early is honoured; a second study waits its turn instead of fighting for the cores; the running page costs nothing while it waits.

**Architecture:** `evolvekit/app/jobs.py` keeps owning the run lifecycle (launch, phase, stop) and gains resume, a queue and a sound liveness check built on what the engine already writes (`heartbeat.json`, `.lock` with pid start times). `evolvekit/harness/execute.py` gains `resume_study()` next to `run_study()`; the engine's `Driver` already resumes from `runs.jsonl`. `evolvekit/app/server.py` runs a 5-second ticker that launches queued runs and caches the status document per file change. The page gains a button and three states; the redesign gets its own later.

**Tech stack:** Python ≥ 3.11 standard library (`threading`, `socket`, `ctypes` on Windows); pytest; the demo harness.

**Spec:** `~/.claude/plans/find-ways-to-significantly-hidden-sutherland.md`, proposal 3; the handoff *What the app will do by then* §2.

## Global constraints

- Runs stay detached processes the app only reads; nothing in the app may kill a run (a stop is a request file).
- A run directory is the record: resume is "the same command again on the same directory".
- Words: *resumed*, *waiting for … to finish*, *stopped before it started*; the Running page's `phase` values are `queued | starting | search | check | done | stopped | failed`.
- `python tasks.py check` green on Windows and Linux; branch `app/survives` from `app/verdict` (stacked), PR against `master`.

## Review focus

1. PID reuse after a reboot: a `launch.json` whose pid now belongs to a younger process must read *failed*, not *running*. Test in Task 1.
2. A machine that slept: a live process with a stale heartbeat must still read *running* (the pid check wins where it can). Test in Task 1.
3. A queued run must never be launched while any run of any study in the home is active, including one that is `starting`. Test in Task 4.
4. A stop file written before the run began must stop it before the preview or the seed runs, and must not survive into a resume. Tests in Tasks 2 and 3.
5. Two `start` calls within the same second must give two different run ids, both visible to the app. Test in Task 4.

---

## Task 1: a run is alive when its process is, and that process is the one we started

**Files:**
- Modify: `evolvekit/app/jobs.py:108-127` (`phase`, `_alive`), `:175-190` (`_launch`: write `host` and `launched_at`)
- Test: `tests/test_app_jobs.py`

**Interfaces:**
- Consumes: `evolvekit.lock.pid_alive`, `pid_started_at`; `evolvekit.events.read_heartbeat`, `HEARTBEAT_INTERVAL_S`.
- Produces: `jobs.run_alive(run_dir: Path, pid: int | None, launched_at: str | None, host: str | None, *, now: datetime | None = None) -> bool`:
  - `pid` missing or `≤ 0` → `False`;
  - `host` given and `≠ socket.gethostname()` → alive iff the heartbeat is younger than `12 * HEARTBEAT_INTERVAL_S` (60 s);
  - `pid_alive(pid)` false → `False`;
  - `pid_started_at(pid)` known and later than `launched_at + 120 s` → `False` (a recycled pid);
  - otherwise `True`.
  `launch.json` gains `"host"` and keeps `"started_at"`. `phase()` uses `run_alive` everywhere `_alive` was used.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_recycled_pid_is_not_a_running_run(tmp_path):
    long_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    assert jobs.run_alive(tmp_path, os.getpid(), long_ago, socket.gethostname()) is False, "this process is younger than the launch"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    assert jobs.run_alive(tmp_path, os.getpid(), now, socket.gethostname()) is True
    assert jobs.run_alive(tmp_path, None, now, None) is False


def test_a_run_from_another_computer_is_judged_by_its_heartbeat(tmp_path):
    (tmp_path / "heartbeat.json").write_text(json.dumps({"ts": utc_now()}), encoding="utf-8")
    assert jobs.run_alive(tmp_path, 1, utc_now(), "elsewhere") is True
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="milliseconds")
    (tmp_path / "heartbeat.json").write_text(json.dumps({"ts": old}), encoding="utf-8")
    assert jobs.run_alive(tmp_path, 1, utc_now(), "elsewhere") is False


def test_a_dead_run_reads_as_failed_with_the_last_thing_it_wrote(tmp_path):
    run_dir = tmp_path / "runs" / "r"; run_dir.mkdir(parents=True)
    (run_dir / "launch.json").write_text(json.dumps({"pid": os.getpid(), "started_at": "2020-01-01T00:00:00+00:00",
                                                      "host": socket.gethostname()}), encoding="utf-8")
    (run_dir / "job.json").write_text(json.dumps({"phase": "search", "pid": os.getpid(), "started_at": "2020-01-01T00:00:00+00:00"}), encoding="utf-8")
    assert jobs.phase(run_dir)["phase"] == "failed"
```

Check the heartbeat's timestamp key against `evolvekit/events.py` (`Heartbeat` writes it) before writing the test.

- [ ] **Step 2: Run** `pytest tests/test_app_jobs.py -k "recycled or heartbeat or dead_run" -v` — Expected: FAIL, `run_alive` missing.
- [ ] **Step 3: Implement** as specified; `_launch` adds `host`; `phase()` passes `launch["host"]` and `launch["started_at"]` (fall back to `job["started_at"]`).
- [ ] **Step 4: Run** `pytest tests/test_app_jobs.py -v` — Expected: PASS.
- [ ] **Step 5: Commit** `app: a run is alive only when its own process is`.

## Task 2: a stop pressed before the run began is honoured

**Files:**
- Modify: `evolvekit/harness/execute.py:310-348` (`run_study`), `evolvekit/app/jobs.py:208-216` (`start`)
- Test: `tests/test_study_run.py`

**Interfaces:**
- Produces: `run_study` checks `run_dir / STOP_REQUEST` first thing (before `measured()` and `write_run`): when present, it writes `job.json` with `phase: "stopped"`, `search: {"stop_reason": "stopped on request before the search began", "generations": 0, "aborted": False}` and returns. It no longer deletes the file. `jobs.start` deletes a stale `STOP_REQUEST` when it creates the run directory (there is none: the directory is new) and `jobs.resume` (Task 3) deletes it before launching.

- [ ] **Step 1: Failing test**

```python
def test_a_stop_before_the_run_began_ends_it_without_a_search(tmp_path):
    folder = _study(tmp_path)
    run_dir = folder / "runs" / "r3"; run_dir.mkdir(parents=True)
    (run_dir / STOP_REQUEST).write_text("stop", encoding="utf-8")
    began = time.monotonic()
    job = run_study(folder, "r3", log=lambda _m: None)
    assert job["phase"] == "stopped" and job["search"]["stop_reason"] == "stopped on request before the search began"
    assert not (run_dir / "runs.jsonl").exists() and time.monotonic() - began < 5
```

- [ ] **Step 2: Run to fail.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Run `tests/test_study_run.py`.** — [ ] **Step 5: Commit** `study run: a stop pressed while starting stops it`.

## Task 3: a run that died resumes where it was

**Files:**
- Modify: `evolvekit/harness/execute.py` (new `resume_study`), `evolvekit/app/job.py` (`--resume`), `evolvekit/app/jobs.py` (`start`, `status`), `evolvekit/app/api.py:468-471` (`start`), `evolvekit/app/static/app.html:1768` (the button)
- Test: `tests/test_study_run.py`, `tests/test_app_jobs.py`

**Interfaces:**
- Produces: `execute.resume_study(root, run_id, *, models=None, log=print) -> dict`: requires `run_dir / CONFIG` and `runs.jsonl`; `Job.resume(run_dir, state, "search")`; `Driver(config, run_dir=run_dir, log=log).run()` (the engine resumes and finishes the planned generations); then `_final_check` exactly as `run_study`. `jobs.can_resume(run_dir, job) -> bool`: phase in `("failed", "stopped")`, `runs.jsonl` exists, and `(job.search or {}).generations < plan.generations` (or no `search` at all). `jobs.start(home, root, *, resume: str | None = None)`: with `resume`, checks `can_resume`, removes `STOP_REQUEST`, launches with `["--resume"]` on that run directory and returns `{"run": run, "resumed": True}`. `job.py` takes `--resume`. `status` gains `"can_resume": bool` and the `search` sentence says `Searching again, from where it stopped.` while `job.resumed_at` is set. API: `POST /api/studies/{slug}/start` with body `{"resume": "<run>"}`. Page: on `failed` or `stopped` with `can_resume`, the primary button is *Continue where it stopped* (calls start with `resume`); *Start it again* stays as the secondary.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.slow
def test_a_stopped_search_is_resumed_to_the_end_of_its_plan(tmp_path):
    folder = _study(tmp_path, hours=0.05)
    _fix_plan(folder, generations=4)
    # stop it during the search, as test_a_stop_request_during_the_search_ends_the_job_as_stopped does
    ...
    assert job["phase"] == "stopped"
    resumed = resume_study(folder, "r4", log=lambda _m: None)
    assert resumed["phase"] == "done" and resumed["search"]["generations"] == 4
    assert resumed["resumed_at"] and resumed["final"]


@pytest.mark.slow
def test_the_app_offers_to_continue_a_run_that_died(app):      # tests/test_app_jobs.py
    # start, wait for phase search, write STOP_REQUEST through the API, wait for stopped
    status, s = call(app, "GET", f"/api/studies/{slug}/status")
    assert s["can_resume"] is True
    status, body = call(app, "POST", f"/api/studies/{slug}/start", {"resume": s["run"]})
    assert status == 200 and body == {"run": s["run"], "resumed": True}
    final = _until(app, f"/api/studies/{slug}/status", lambda b: b["phase"] in ("done", "failed"), timeout=180)
    assert final["phase"] == "done" and final["can_resume"] is False
```

- [ ] **Step 2: Run to fail.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Run both test files.** — [ ] **Step 5: Commit** `app: a run that died continues where it stopped`.

## Task 4: one study at a time, and unique run names

**Files:**
- Modify: `evolvekit/app/jobs.py` (`start`, `phase`, `running`, `stop`, `state_line`, `status`), `evolvekit/app/store.py` (`Home.active_run`), `evolvekit/app/server.py` (the ticker)
- Test: `tests/test_app_jobs.py`

**Interfaces:**
- Produces: run ids `YYYYMMDD-HHMMSS`, then `-2`, `-3` … (never `~`). `QUEUED = "queued.json"` (`{"queued_at": iso}`), written instead of launching when `home.active_run()` is not `None`; `phase()` returns `{"phase": "queued", "queued_at": …}` for a directory with `queued.json` and no `launch.json`; `running(root)` counts `queued`; `stop()` on a queued run deletes the directory and returns `{"stopping": False, "dequeued": True}`. `Home.active_run() -> tuple[str, str] | None` (the slug and run of any `starting | search | check` run across the home, oldest first). `jobs.launch_queued(home) -> str | None`: when `active_run()` is `None`, launches the oldest queued run in the home (by `queued_at`), removing `queued.json`, and returns `"slug/run"`. `AppServer._tick()` runs `launch_queued` every 5 s in a daemon thread started by `start()` / `serve_forever()`. `status` for `queued`: `sentence = "Waiting for '<other study name>' to finish."`, `time.left_s = None`. `state_line`: `{"kind": "queued", "line": "queued · after <other study name>"}`.

- [ ] **Step 1: Failing tests**

```python
@pytest.mark.slow
def test_a_second_study_waits_its_turn(app):
    first, second = _ready_study(app, "First"), _ready_study(app, "Second")
    call(app, "POST", f"/api/studies/{first}/start")
    status, body = call(app, "POST", f"/api/studies/{second}/start")
    assert status == 200 and body["queued"] is True
    s = call(app, "GET", f"/api/studies/{second}/status")[1]
    assert s["phase"] == "queued" and s["sentence"] == "Waiting for 'First' to finish."
    assert call(app, "GET", "/api/home")[1]["studies"][1]["line"] == "queued · after First"
    _until(app, f"/api/studies/{first}/status", lambda b: b["phase"] == "done", timeout=180)
    _until(app, f"/api/studies/{second}/status", lambda b: b["phase"] != "queued", timeout=30)   # the ticker launched it


def test_a_queued_run_can_be_taken_back(app):
    ...  # stop on a queued run -> {"dequeued": True}, directory gone, study editable again


def test_two_starts_in_one_second_get_two_visible_run_ids(app, monkeypatch):
    monkeypatch.setattr(jobs, "_now", lambda: fixed_datetime)
    ...  # start twice (the second is queued) -> ids "20260101-120000" and "20260101-120000-2", both in document["runs"]
```

- [ ] **Step 2: Run to fail.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Run `tests/test_app_jobs.py tests/test_app_server.py`.** — [ ] **Step 5: Commit** `app: one study runs at a time; the next waits its turn`.

## Task 5: the running page costs nothing while it waits

**Files:**
- Modify: `evolvekit/app/server.py:276-287` (`status_document`), `evolvekit/app/jobs.py:377-472` (`status`: one read of `runs.jsonl`)
- Test: `tests/test_app_server.py`

**Interfaces:**
- Produces: `status_document(run_dir)` caches by the stamp `(size, mtime_ns)` of `events.jsonl`, `runs.jsonl` and `heartbeat.json`; rebuilt only when a stamp changed. `jobs._rows(run_dir)` is read once per `status()` call and passed to `_seed_and_best`, `_closest` and the gated count (new parameter `rows`).

- [ ] **Step 1: Failing test**

```python
def test_the_status_document_is_rebuilt_only_when_a_run_file_changed(tmp_path, monkeypatch):
    built = []
    monkeypatch.setattr("evolvekit.status.build_status", lambda run_dir: built.append(1) or {"n": len(built)})
    server = AppServer(tmp_path / "home", port=0)
    run_dir = tmp_path / "run"; run_dir.mkdir(); (run_dir / "events.jsonl").write_text("{}\n")
    assert server.status_document(run_dir) == server.status_document(run_dir) and len(built) == 1
    time.sleep(0.01); (run_dir / "events.jsonl").write_text("{}\n{}\n")
    server.status_document(run_dir); assert len(built) == 2
```

- [ ] **Step 2: Run to fail.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Run `tests/test_app_server.py tests/test_app_jobs.py`.** — [ ] **Step 5: Commit** `app: the status is rebuilt only when a run file changed`.

## Task 6: the gate, the docs and the PR

- [ ] **Step 1:** `docs/app.md`: *While it runs* gains the queued and resumed states; *When something is wrong* gains the row *It failed after a restart of the computer → Continue where it stopped*.
- [ ] **Step 2:** `.venv/Scripts/python.exe tasks.py check` — Expected: green.
- [ ] **Step 3:** With the app running (`preview_start` `evolvekit-app`): start a demo study, end its job process from Task Manager, reload: the page says failed and offers *Continue where it stopped*; press it: it finishes. Start two studies: the second says *Waiting for … to finish* and starts by itself.
- [ ] **Step 4:** `git push -u origin app/survives`; `gh pr create --base app/verdict`, the decisions (liveness rule, 120 s slack, 5 s ticker, 60 s heartbeat age) in the body, the attribution line last.
