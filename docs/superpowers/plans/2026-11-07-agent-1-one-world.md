# Agent path, project 1: One world — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the CLI and the app share one store, one runner and one verdict, so a study made or started by either is the same study everywhere; plus `doctor`, `harness discover` and `skill install`.

**Architecture:** every new CLI command is a thin function in `evolvekit/cli.py` that calls the library the app already uses (`evolvekit.app.store.Home`, `evolvekit.app.jobs`), never HTTP. The verdict's words move out of `jobs.py` into `evolvekit/app/verdict.py`, and both the page's results and the CLI render from its one document. The app announces itself in `<home>/app.json`; `study open` reads it or starts an app.

**Tech stack:** Python ≥ 3.11 standard library, PyYAML; the app's existing modules; pytest.

**Spec:** [docs/superpowers/specs/2026-11-07-agent-path-design.md](../specs/2026-11-07-agent-path-design.md), §2 and §3. Line numbers below are those of `master` at 7 November 2026 (commit c548864).

## Global constraints

- Generic names; no vendor names in new files, commands or folders (spec §2).
- Exit codes: 0 success; 1 a problem the message names (every `AppError`, whatever its HTTP status); 2 a configuration or validation error (`ConfigError`, `HarnessError`, `study check` with problems); 4 a timeout (spec §3.2).
- The CLI never speaks HTTP to the app; both call the same library functions; all state is on disk (spec §3.1).
- People's wording follows the app's vocabulary; `--json` keeps the engine's key names (spec §2).
- Windows first: detached processes, paths with spaces, no symlinks (spec §2).
- Branch `agent/1-one-world` from `master`; PR against `master`; merge commit; the README and `docs/app.md` updated in the same PR.
- The full gate (`python tasks.py check`) has timing-sensitive tests: never run a demo study beside it.

## Review focus

1. A positional that is both an existing directory and a valid slug (`studies/depot-a` run from the home's parent): the directory wins, and the message names which it took (Task 3's `test_study_root_prefers_directory`).
2. `study wait` while the app is also running and ticking the queue: both call `launch_queued`; `_launch_guard` is per process, so the on-disk pending marker in `launch.json`, read by `phase`, must make a double launch impossible (Task 5's `test_pending_marker_counts_as_active`).
3. A stale `app.json` whose pid now belongs to another program: `study open` must start a new app, not print a dead URL (Task 1's `test_running_ignores_pid_of_another_program`).
4. `study verdict` on a run whose final check was stopped halfway, and on a study with no run at all: a document with `decision: stopped` / exit 1 with a sentence, never a traceback (Task 2's `test_verdict_stopped_check`, Task 6's `test_verdict_no_run`).
5. `harness discover --program` on a program whose `--help` is not argparse-shaped or that hangs: a list that may be empty with a note, within the timeout (Task 9's `test_discover_help_unparseable`, `test_discover_help_hangs`).

---

## File map

| File | Responsibility |
|---|---|
| `evolvekit/app/running.py` | new: `app.json` — write, read with liveness, clear |
| `evolvekit/app/verdict.py` | new: the verdict document; the chance words, the decision and the final words moved from `jobs.py` |
| `evolvekit/app/report.py` | new: the Markdown report from the verdict document and the results |
| `evolvekit/app/detach.py` | new: `spawn_detached`, lifted from `jobs._launch`, used by `_launch` and `study open` |
| `evolvekit/app/jobs.py` | `results` renders from `verdict.build_verdict`; the moved functions become imports; the pending launch marker in `_launch` and `phase` |
| `evolvekit/app/store.py` | `create_study` accepts a harness folder; `add_cases`; `set_application` lifted from `api.application` |
| `evolvekit/app/server.py` | writes `app.json` after binding, clears it on stop |
| `evolvekit/harness/execute.py` | the final check writes the four exports into `runs/<id>/downloads/` |
| `evolvekit/cli.py` | `main()` catches `AppError`; the new `study` commands, `doctor`, `harness discover`, `skill` |
| `evolvekit/doctor.py` | new: the environment checks |
| `evolvekit/harness/discover.py` | new: `--help` and module probing |
| `evolvekit/skills/__init__.py`, `evolvekit/skills/<name>/SKILL.md`, `evolvekit/skills/shared/evolvekit.md` | new: the shipped skills and the installer |
| `pyproject.toml` | package data for `evolvekit/skills/**` |
| `tests/test_app_running.py`, `tests/test_verdict.py`, `tests/test_report.py`, `tests/test_cli_study.py`, `tests/test_doctor.py`, `tests/test_harness_discover.py`, `tests/test_skills_install.py` | new tests |
| `README.md`, `docs/app.md`, `docs/harness-authoring.md` | the command table, `app.json`, the one-world rule, `harness discover` |

Shared-file map, for dispatch order: `cli.py` is touched by Tasks 3, 4, 5, 6, 7, 8, 9, 10 (strictly sequential); `jobs.py` by Tasks 2 and 5; `store.py` by Tasks 3 and 4; `execute.py` by Task 2 only; `server.py` by Task 7 only.

Fixtures the tests reuse: `tests/test_app_results.py` has `finished_run(run_dir, *, check, decision, mean)` (line 235: hand-written `job.json`, `comparison.json`, an `events.jsonl` of `{}`; `n` is always 1; `decision` lands in `job.json`'s `final`), `document_of` (274, a stub status document) and `study_with_runs(tmp_path)` (279). `tests/test_app_jobs.py` has `app(tmp_path)` (31), `call(...)` (imported from `test_app_server`), `_ready_study(app, name)` (58: application set, samples, split with one held back, plan `children 2, generations 2, 0.05 h`) and the slow real run at 100.

---

### Task 1: The app says it is running (`running.py`)

**Files:**
- Create: `evolvekit/app/running.py`
- Test: `tests/test_app_running.py`

**Interfaces:**
- Consumes: the pid-and-start-time rule of `jobs.run_alive` (95) and `evolvekit.lock.pid_started_at` (71–83; returns None on macOS). Duplicate the rule in `running.py` with a comment naming the twin; do not touch `jobs.py` in this task.
- Produces: `APP_FILE = "app.json"`; `write_running(home_root: Path, *, pid: int, port: int, url: str, host: str = "127.0.0.1") -> Path`; `read_running(home_root: Path) -> dict | None` (None when missing or stale; a stale file is deleted); `clear_running(home_root: Path, *, pid: int) -> None` (removes the file only when it names this pid).

- [ ] **Step 1: Write the failing tests**

```python
def test_write_then_read_round_trips(tmp_path):
    write_running(tmp_path, pid=os.getpid(), port=8780, url="http://127.0.0.1:8780/?token=x")
    doc = read_running(tmp_path)
    assert doc["port"] == 8780 and doc["url"].endswith("token=x") and doc["pid"] == os.getpid()
    assert "started_at" in doc and doc["host"] == "127.0.0.1"

def test_stale_pid_reads_as_not_running_and_is_removed(tmp_path):
    (tmp_path / "app.json").write_text(json.dumps({"pid": 999999, "port": 1, "url": "u", "host": "127.0.0.1",
                                                   "started_at": "2026-01-01T00:00:00+00:00"}))
    assert read_running(tmp_path) is None
    assert not (tmp_path / "app.json").exists()

def test_clear_only_removes_own_file(tmp_path):
    write_running(tmp_path, pid=os.getpid(), port=1, url="u")
    clear_running(tmp_path, pid=os.getpid() + 1)
    assert (tmp_path / "app.json").exists()
    clear_running(tmp_path, pid=os.getpid())
    assert not (tmp_path / "app.json").exists()

@pytest.mark.skipif(sys.platform == "darwin", reason="no process start time on macOS")
def test_running_ignores_pid_of_another_program(tmp_path):
    # start `python -c "import time; time.sleep(30)"`, write its pid with a started_at one hour in the past,
    # assert read_running(tmp_path) is None, then kill it
```

- [ ] **Step 2: Run them to see them fail** — `pytest tests/test_app_running.py -v` → ImportError.
- [ ] **Step 3: Implement `running.py`** with the three functions and `APP_FILE`: atomic write (temp file then `os.replace`), ISO 8601 UTC `started_at`, liveness = the pid exists and, where `pid_started_at` answers, its start time is within 120 s of `started_at`.
- [ ] **Step 4: Run the tests** → PASS.
- [ ] **Step 5: Commit** — `git commit -m "app: the app says it is running (app.json)"`.

### Task 2: The verdict document (`verdict.py`), and the final check writes the exports

**Files:**
- Create: `evolvekit/app/verdict.py`
- Modify: `evolvekit/app/jobs.py` (`_chance_words` 860, `_chance_of` 876, `_decision_of` 890, `_final_words` 906, `_export` 1253, `results` 1120), `evolvekit/harness/execute.py` (`_final_check` 620)
- Test: `tests/test_verdict.py`; `tests/test_app_results.py` and `tests/test_study_run.py` must keep passing

**Interfaces:**
- Consumes: `evolvekit.confirm.decide`, `RULE_WORDS`, `read_job`, `confirm/final/comparison.json`, `jobs.phase`, `jobs.latest_run`, the study's `goal`, the `finished_run`/`document_of`/`study_with_runs` helpers of `tests/test_app_results.py`.
- Produces: `build_verdict(root: Path, run: str | None = None) -> dict` with exactly the keys of spec §3.4; `DECISIONS = ("use", "keep", "worse", "none", "single", "pending", "stopped", "skipped")`; the moved functions keep their names and signatures in `verdict.py`, and `jobs.py` imports them (`from .verdict import _chance_words, _chance_of, _decision_of, _final_words`) so every caller stays as it is. The mapping from `_final_words`'s `state` to `decision`: `confirmed` → `decide`'s word (`use`/`keep`/`worse`); `unclear` → `keep`; `missing` → `pending`; `stopped` → `stopped`; `single` → `single`; `worse` → `worse`; `skipped` → `none` when the job's `final.skipped` text is the "starting point stayed the best" sentence of `execute.py` 630, else `skipped`; both carry `reason`. `files`: the paths under `runs/<id>/downloads/` that exist. `execute._final_check` calls the export writer (lift `jobs._export`'s runner call into `execute.write_exports(root, run_dir) -> list[Path]`, used by both) as soon as the decision is computed.

- [ ] **Step 1: Write the failing tests** (fixtures through `finished_run`; `decision` through its `decision=` argument, `pending` through `check=False`; hand-written `job.json` for `stopped` (`final: {"stopped": true}`), `skipped` (`final: {"skipped": "no cases were held back …"}` — copy the sentence from `execute.py` 625) and `none` (the sentence of 630); `single` through `comparison.json` with `n: 1`):

```python
def test_verdict_keys_and_schema(use_run):
    v = build_verdict(root, run)
    assert v["schema"] == 1 and v["decision"] in DECISIONS
    assert set(v) >= {"study", "run", "phase", "line", "advice", "measure", "held_back", "rule", "by_case", "changed", "files"}

@pytest.mark.parametrize("decision", ["use", "keep", "worse", "single", "pending", "stopped", "skipped", "none"])
def test_page_line_equals_verdict_line(decision, run_of):
    v = build_verdict(root, run)
    r = jobs.results(root, run, document_of)      # the stub document_of of test_app_results
    assert r["final"]["line"] == v["line"] and r["final"]["advice"] == v["advice"]

def test_verdict_pending_has_no_numbers(pending_run): "mean_pct" not in v and v["decision"] == "pending"
def test_verdict_stopped_check(stopped_run): v["decision"] == "stopped"
def test_verdict_skipped_names_reason(skipped_run): v["decision"] == "skipped" and v["reason"].startswith("no cases")
def test_verdict_none_when_start_stayed_best(none_run): v["decision"] == "none" and v["reason"]
def test_verdict_single_case(single_run): v["decision"] == "single"
def test_verdict_levels_mark_the_deciding_level(levels_run): sum(l["deciding"] for l in v["levels"]) == 1
def test_verdict_never_contains_nan(use_run): json.dumps(v, allow_nan=False)
def test_verdict_files_list_existing_downloads(use_run): write runs/<id>/downloads/settings.json → "downloads/settings.json" in v["files"] paths (relative to root)
@pytest.mark.parametrize("p,words", [(0.996, "almost certain"), (0.95, "about 19 in 20"), (0.897, "under 9 in 10"), (0.5, "about 10 in 20"), (0.04, "1 in 20 or less"), (None, "")])
def test_chance_words_table(p, words): _chance_words(p) == words
@pytest.mark.slow
def test_final_check_writes_exports(tmp_path): a real `run_study` on demo-tour (as tests/test_study_run.py does) → runs/<id>/downloads/ holds the settings json and flags files
```

- [ ] **Step 2: Run to see them fail** → ImportError on `build_verdict`.
- [ ] **Step 3: Move** the four functions into `verdict.py` unchanged; re-import them in `jobs.py`. Run `pytest tests/test_app_results.py tests/test_app_jobs.py -q -m "not slow"` → still green.
- [ ] **Step 4: Implement `build_verdict`** on top of `_final_words`, with the mapping above; lift the pieces of `results` that compute `by_case`, `changed` and the run facts into functions both share (`verdict._by_case(...)`, `verdict._changed(...)`); never call a subprocess.
- [ ] **Step 5: `jobs.results` takes `final`'s `line`, `advice`, decision and numbers from `build_verdict`** so the page and the document cannot diverge; every existing key of `results` keeps its value (`tests/test_app_results.py` is the guard).
- [ ] **Step 6: `execute.write_exports` and its call in `_final_check`;** `jobs._export` calls it when the files are missing.
- [ ] **Step 7: Run** `pytest tests/test_verdict.py tests/test_app_results.py tests/test_app_jobs.py tests/test_app_page.py tests/test_study_run.py -q` → PASS.
- [ ] **Step 8: Commit** — `git commit -m "app: the verdict is one document (verdict.py); the page renders from it; the final check writes the exports"`.

### Task 3: `main()` catches the library's errors; `study new` into the home; slugs everywhere

**Files:**
- Modify: `evolvekit/cli.py` (`build_parser` 395–409, `cmd_study` 819, `main` 927–941), `evolvekit/app/store.py` (`create_study` 317)
- Test: `tests/test_cli_study.py` (new); `tests/test_harness_cli.py` lines 97 and 120 (`study new PATH --harness demo-tour --template tune --name "…"` becomes `study new "…" --harness demo-tour --template tune --dir PATH`)

**Interfaces:**
- Consumes: `Home(root)`, `Home.create_study`, `Home.study_root` (289; raises `AppError` 404), `library.create_study`, `library.default_home`, `cli._harness_folder` (771), `evolvekit.app.AppError` (`app/__init__.py` 17, with `.status`).
- Produces: `main()` gains `except AppError as e: print(e, file=sys.stderr); return 1` beside the existing handlers; `_home(args) -> Home` (from `--home`, else `default_home()`); `_study_root(value: str, home: Home) -> Path` (an existing directory wins, with "using the folder <path>" on stderr when the value is also a slug in the home; else `home.study_root(value)`); the `study new` parser: positional `name`, `--harness` (required), `--template`, `--app`, `--dir`, `--home`, `--json`; `--name` is dropped. `Home.create_study(harness: str, template: str | None, name: str) -> str` accepts an id or a folder path (`Path(harness).is_dir()` → that folder is the entry's path). `--app` is wired in Task 4 (it calls `Home.set_application`); in this task the parser accepts it and `cmd_study` ignores it with a `# Task 4` comment.

- [ ] **Step 1: Write the failing tests**

```python
def test_study_new_lands_in_the_home(tmp_path, capsys):
    assert main(["study", "new", "Depot A", "--harness", "demo-tour", "--home", str(tmp_path)]) == 0
    assert (tmp_path / "studies" / "depot-a" / "study.yaml").exists()
    assert capsys.readouterr().out.strip() == "depot-a"

def test_study_new_json(tmp_path, capsys):
    main(["study", "new", "Depot A", "--harness", "demo-tour", "--home", str(tmp_path), "--json"])
    doc = json.loads(capsys.readouterr().out); assert doc["slug"] == "depot-a" and Path(doc["root"]).is_dir()

def test_study_new_dir_writes_a_standalone_folder(tmp_path):
    target = tmp_path / "elsewhere" / "s"
    assert main(["study", "new", "S", "--harness", "demo-tour", "--dir", str(target), "--home", str(tmp_path)]) == 0
    assert (target / "study.yaml").exists()
    assert not (tmp_path / "studies").exists() or not any((tmp_path / "studies").iterdir())

def test_study_new_harness_folder(tmp_path): --harness <ROOT/harnesses/demo-tour> works into the home
def test_second_study_same_name_gets_dash_two(tmp_path): slugs "depot-a", "depot-a-2"
def test_study_root_prefers_directory(tmp_path, monkeypatch, capsys): chdir into tmp_path, make a folder "depot-a" beside a home study "depot-a"; `study preview depot-a --home …` uses the folder and stderr contains "using the folder"
def test_preview_accepts_a_slug(tmp_path): after `study new`, `study preview depot-a --home …` returns what it returns with the path
def test_missing_study_is_exit_1_with_a_sentence(tmp_path, capsys): `study preview nope --home …` == 1 and "there is no study" in stderr (the AppError text of Home.study_root)
```

- [ ] **Step 2: Run** → the parser rejects `--dir`/`--json`; the missing-study test shows a traceback.
- [ ] **Step 3: Implement.** The `except AppError` in `main()`; the parser; `_home`, `_study_root`; `cmd_study`'s `new` branch calls `home.create_study(...)` or, with `--dir`, `library.create_study(Path(args.dir), _harness_folder(...), name, template)`; `preview`/`compile`/`run` resolve their positional through `_study_root`. `Home.create_study`'s folder form.
- [ ] **Step 4: Update** `tests/test_harness_cli.py` lines 97 and 120.
- [ ] **Step 5: Run** `pytest tests/test_cli_study.py tests/test_harness_cli.py -q` → PASS (the slow ones included; nothing else running).
- [ ] **Step 6: Commit** — `git commit -m "cli: study new makes the study in the library home; slugs work everywhere; AppError is exit 1"`.

### Task 4: `study application`, `study list`, `study check`, `study cases`

**Files:**
- Modify: `evolvekit/cli.py`, `evolvekit/app/store.py`, `evolvekit/app/api.py` (`application` handler: the probe-and-record logic moves into `Home.set_application`; the handler calls it)
- Test: `tests/test_cli_study.py`, `tests/test_app_detect.py` (must keep passing)

**Interfaces:**
- Consumes: the body of `api.application` (the `POST /api/studies/{slug}/application` handler: the probe through `evolvekit.harness.probe`, `remember_application`, the version recorded in `study.yaml`), `evolvekit.app.detect` (the "found for you" candidates), `Home.slugs`, `jobs.state_line(root, study, document_of=None)` (1428; a draft's line is `draft · step N of 7`), `Home.problems(slug)` (337; the first problem of a fresh demo-tour study is `application: say where the application is`, study.py 544; with the application set, `cases.training: the search needs at least one case to learn from`, 552), `Home.put_case(slug, filename, data)` (552; a wrong format raises `AppError("<name>: this harness reads .json files")`, store.py 110), `Home.suggested_test_count(slug)` (762), `Home.split(slug, test)` (777).
- Produces: `Home.set_application(slug: str, path: str) -> dict` (what the handler returned: at least `path` and `version`); `Home.add_cases(slug: str, files: list[Path], *, hold_back: int | None = None) -> dict` returning `{"added": [names], "refused": [{"file", "reason"}], "test": [names]}` where `reason` is the existing `put_case` message; parsers `study application SLUG PATH [--json]` and `study application SLUG --detect [--json]`, `study list [--json]`, `study check SLUG`, `study cases SLUG --add FILE... [--hold-back N]`; `study new --app PATH` now calls `set_application` after creating.

- [ ] **Step 1: Write the failing tests**

```python
def test_study_application_records_path_and_version(tmp_path, capsys): `study application depot-a <solver.py> --home …` == 0; study.yaml has application.path and application.version; out names the version
def test_study_application_detect_lists_candidates(tmp_path, capsys): --detect --json → a list (may be empty on CI) with the keys evolvekit.app.detect returns
def test_study_new_app_sets_the_application(tmp_path): `study new … --app <solver.py>` → application.path set
def test_study_application_bad_path_is_exit_1(tmp_path, capsys): a path that is not the program → 1, the probe's sentence on stderr
def test_study_list_shows_slug_name_harness_state(tmp_path, capsys): two studies; each line has slug, name, "Demo tour" and "draft · step"
def test_study_list_json(tmp_path, capsys): a list of {"slug","name","harness","state"}
def test_study_check_names_problems_and_exits_2(tmp_path, capsys): a fresh study → 2; stderr's first line is "application: say where the application is"
def test_study_check_after_application_names_cases(tmp_path, capsys): first line "cases.training: the search needs at least one case to learn from"
def test_study_check_ready_exits_0(ready_study)
def test_study_cases_adds_and_splits(tmp_path, capsys): the four demo-tour samples with --hold-back 1 → cases/ has 4, study.yaml `cases.test` has 1 name, out says "4 cases added, 1 held back"
def test_study_cases_refuses_wrong_format(tmp_path, capsys): a .csv → refused with "this harness reads .json files", exit 1, the others still added
def test_study_cases_default_hold_back_is_the_suggestion(tmp_path): len(test) == Home.suggested_test_count
```

- [ ] **Step 2: Run** → fail.
- [ ] **Step 3: Implement** `Home.set_application` (lifted from the handler, which now calls it), `Home.add_cases`, the four commands and their wording.
- [ ] **Step 4: Run** `pytest tests/test_cli_study.py tests/test_app_detect.py tests/test_app_server.py -q` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "cli: study application, list, check and cases"`.

### Task 5: `study start`, `stop`, `status`, `watch`, `wait`

**Files:**
- Modify: `evolvekit/cli.py`, `evolvekit/app/jobs.py` (`_launch` 303, `phase` 157–169, `launch_queued` 439)
- Test: `tests/test_cli_study.py` (slow tests on demo-tour with `_ready_study`'s plan), `tests/test_app_jobs.py` (must keep passing)

**Interfaces:**
- Consumes: `jobs.start(home, root, *, resume=None)` (383), `jobs.stop(root, run)` (461), `jobs.status(root, run, document_of)` (647; `time.used_s`/`left_s` come from `_now()` while a run lives), `jobs.latest_run(root)` (207), `jobs.phase(run_dir)` (157), `jobs.launch_queued(home)` (439), `jobs.active_run(home)` (236), `jobs.ACTIVE` (58), `jobs.state_line`, `evolvekit.status.build_status`.
- Produces: `study start SLUG [--wait] [--json]` printing `{"run", "queued"|"started"}` (people: "started run 20261107-101500" / "queued behind Depot B; it starts when that study finishes (the app or `evolvekit study wait` starts it)"); `study stop SLUG`; `study status SLUG [--run ID] [--json]` (people: the state line; `--json`: `jobs.status(root, run, build_status)`); `study watch SLUG [--every S] [--json]`; `study wait SLUG [--timeout S] [--json]` with exit 0/1/4 as spec §3.2. In `cli.py`: `_tick_queue(home) -> None` calling `jobs.launch_queued(home)` and swallowing `AppError`. **The pending marker:** `_launch` writes `launch.json` as `{"pending": true, "at": iso}` before it spawns, then the real document after; `phase` treats a pending marker younger than 120 s as `starting` (not `failed`), so `active_run` in any process sees the run as active between the two writes; `launch_queued` re-checks `active_run` inside `_launch_guard` before launching.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.slow
def test_start_wait_verdict_round_trip(tmp_path, capsys): new, application, cases, study.yaml plan {auto: false, children: 2, generations: 2} and budget 0.05 h → `study start --json` prints "run" → `study wait --json --timeout 600` == 0 and the printed document's "decision" is in DECISIONS; job.json phase "done"
@pytest.mark.slow
def test_watch_prints_documents_until_done(tmp_path, capsys): `study watch --json --every 1` → every line parses; the last phase is terminal
def test_start_twice_is_refused(started_study): 1 and "already running" on stderr
def test_start_queues_behind_another(tmp_path, capsys): another study's run active (a launch.json naming a sleeping subprocess's pid and its start) → "queued" true; out names the other study's name
def test_pending_marker_counts_as_active(tmp_path, monkeypatch): patch `jobs._launch`'s spawn to a no-op that leaves the pending marker; after `jobs.start`, `jobs.active_run(home)` is not None and `phase(run_dir)["phase"] == "starting"`; a second `jobs.start` on another study queues
def test_pending_marker_expires(tmp_path): a pending marker 10 minutes old → phase "failed" with the "did not start" error
def test_wait_ticks_the_queue(tmp_path, monkeypatch): a queued run and no active run; `_launch` patched to record → one `study wait` tick launches it
def test_wait_timeout_exits_4(started_study): --timeout 1 → 4
def test_stop_dequeues_a_queued_run(tmp_path): queued.json gone, out "dequeued"
def test_status_json_equals_api_document(app, finished_study): on a finished run, `study status --json` == GET /api/studies/{slug}/status body
```

- [ ] **Step 2: Run** → fail.
- [ ] **Step 3: Implement** the commands; `wait`/`watch` loop with `time.sleep(every)` and `_tick_queue`; the pending marker in `_launch` and `phase`.
- [ ] **Step 4: Run** `pytest tests/test_cli_study.py tests/test_app_jobs.py -q` (slow included; nothing else running) → PASS.
- [ ] **Step 5: Commit** — `git commit -m "cli: study start, stop, status, watch and wait through the app's runner"`.

### Task 6: `study verdict` and `study report`

**Files:**
- Create: `evolvekit/app/report.py`
- Modify: `evolvekit/cli.py`
- Test: `tests/test_report.py`, `tests/test_cli_study.py`

**Interfaces:**
- Consumes: `verdict.build_verdict`, `jobs.results`, `jobs.latest_run`.
- Produces: `render_report(verdict: dict, results: dict | None = None) -> str` (Markdown, spec §3.5, sections in this order: `## Verdict`, `## Held-back cases`, `## What changed`, `## How it ran`, `## Files`; numbers in the verdict's wording, e.g. "1.3 % lower", "between 0.3 % worse and 2.8 % better"); `study verdict SLUG [--run ID] [--json]` (people: `line` then `advice`, one per line); `study report SLUG [--run ID] [--markdown|--json]` (default `--json` = the verdict document; `--markdown` = `render_report`).

- [ ] **Step 1: Write the failing tests**

```python
def test_report_markdown_sections(use_run): the five headings in order
def test_report_uses_the_verdict_line_verbatim(use_run): v["line"] in text
def test_report_never_says_better_without_use(keep_run): "better" appears only inside v["line"] (count occurrences)
def test_report_numbers_use_the_verdict_wording(use_run): the ci95 pair appears as "between … and …" and no "+1.3 %" style number appears
def test_verdict_no_run(tmp_path, capsys): a study with no runs → 1 and "no run yet" on stderr
def test_verdict_for_people(use_run, capsys): out == line + "\n" + advice + "\n"
def test_report_markdown_cli(use_run, capsys): `study report … --markdown` == render_report(build_verdict(...), results)
```

- [ ] **Step 2: Run** → fail. **Step 3: Implement** `report.py` and the two commands. **Step 4: Run** `pytest tests/test_report.py tests/test_cli_study.py -q -m "not slow"` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "cli: study verdict and study report, written from the verdict document"`.

### Task 7: `study open`, and the app writes `app.json`

**Files:**
- Create: `evolvekit/app/detach.py`
- Modify: `evolvekit/app/server.py` (`AppServer.__init__` 340–357, `stop` 432), `evolvekit/app/jobs.py` (`_launch` 303: its spawn moves into `detach.spawn_detached`), `evolvekit/cli.py`
- Test: `tests/test_app_server.py` (one test), `tests/test_cli_study.py`, `tests/test_app_jobs.py` (must keep passing)

**Interfaces:**
- Consumes: `running.write_running/read_running/clear_running`.
- Produces: `detach.spawn_detached(argv: list[str], *, cwd: Path | None = None, env: dict | None = None, log: Path | None = None) -> int` (the launcher-then-exit pattern, the Windows flags and the POSIX session of `_launch`, unchanged in behaviour; `_launch` calls it); `AppServer.__init__` writes `app.json` right after `_bind` (so `start()` and `serve_forever()` are both covered; `cmd_app` calls only `serve_forever`); `stop()` clears it; `study open SLUG [--no-browser] [--home]` prints `<url>#/study/<slug>` (the page's route, `evolvekit/app/static/js/core.js` 2 and 44), starting `python -m evolvekit app --home <home> --no-browser` through `spawn_detached` when `read_running` is None and waiting up to 15 s for the file (exit 1 with "the app did not start; run `evolvekit app` and look at its output" otherwise); opens `webbrowser` unless `--no-browser`.

- [ ] **Step 1: Write the failing tests**

```python
def test_app_writes_and_clears_running_file(tmp_path): AppServer(home, port=0) → read_running(home)["port"] == server.port and ["url"] == server.url_with_token, before start(); stop() → None
def test_open_prints_study_url_of_running_app(app, capsys): main(["study","open",slug,"--no-browser","--home",…]) == 0; out == app.url_with_token + "#/study/" + slug + "\n"
def test_open_starts_an_app_when_none_runs(tmp_path, monkeypatch, capsys): spawn_detached patched to start AppServer in a thread (which writes app.json) → the URL is printed; the patch was called once with argv ending in ["app", "--home", str(home), "--no-browser"]
def test_open_times_out_with_a_sentence(tmp_path, monkeypatch, capsys): spawn patched to a no-op; the wait limit monkeypatched to 1 s → 1 and the sentence on stderr
def test_launch_still_detaches(…): the existing slow tests of test_app_jobs.py are the guard; no new test
```

- [ ] **Step 2: Run** → fail. **Step 3: Implement** `detach.py`, the server's write/clear, `study open`. **Step 4: Run** `pytest tests/test_app_server.py tests/test_app_jobs.py tests/test_cli_study.py -q` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "app: app.json says the app is running; study open finds or starts it"`.

### Task 8: `doctor`

**Files:**
- Create: `evolvekit/doctor.py`
- Modify: `evolvekit/cli.py`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `library.default_home`, `library.find_harnesses`, `Home.settings` (132), `Home.keys` (211; booleans only), `running.read_running`, `shutil.which` of the provider CLI's executable (`CLAUDE_CLI_PATH`/`claude`, `COPILOT_CLI_PATH`/`copilot`), `evolvekit.harness.probe` for `--app`.
- Produces: `checks(home: Path, *, app: str | None = None) -> list[dict]` each `{"ok": bool, "name": str, "message": str, "fix": str}` with names in this order: `python`, `evolvekit`, `home`, `app`, `provider`, `harnesses`, then one `probe:<harness id>` per harness when `--app` is given; `doctor [--home] [--app PATH] [--json]`, exit 0 when all ok else 1; keys are never printed ("key set" / "no key").

- [ ] **Step 1: Write the failing tests**

```python
def test_doctor_json_is_a_list_in_order(tmp_path, capsys): [c["name"] for c in doc][:6] == ["python", "evolvekit", "home", "app", "provider", "harnesses"]
def test_doctor_reports_app_running(tmp_path): with a live app's app.json → the "app" check ok and its message contains the url
def test_doctor_never_prints_a_key(tmp_path, capsys): Home.set_keys({"OPENROUTER_API_KEY": "sk-secret"}) → "sk-secret" not in stdout
def test_doctor_exit_1_when_home_not_writable(tmp_path, monkeypatch): os.access patched → 1 and the "home" check's fix names the folder
def test_doctor_with_app_probes_each_harness(tmp_path, capsys): --app <solver.py> → a "probe:demo-tour" check
def test_doctor_text_has_marks(tmp_path, capsys): each line starts with "✓ " or "✗ "
```

- [ ] **Step 2–4:** fail, implement, `pytest tests/test_doctor.py -q` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "cli: doctor"`.

### Task 9: `harness discover`

**Files:**
- Create: `evolvekit/harness/discover.py`
- Modify: `evolvekit/cli.py` (`harness` subparsers 367)
- Test: `tests/test_harness_discover.py`

**Interfaces:**
- Consumes: `evolvekit.harness.probe.program_command(app)` (probe.py 120: how a `.py` program is run on Windows).
- Produces: `discover_program(path: str, *, timeout_s: float = 10.0) -> dict`; `discover_python(python: str, module: str, *, timeout_s: float = 30.0) -> dict`; both return the document of spec §3.7; `parse_help(text: str) -> list[dict]` (pure; argparse and GNU `--flag VALUE  help (default: x)` shapes; every flag except `--help`/`--version`; `choices` from `{a,b}`; `required` when the usage line shows the flag outside brackets; type guess: `int` if the default parses as int, `float` if as float, `bool` for flags without a value, `choice`, else `text`); `harness discover (--program PATH | --python PATH --module NAME) [--json]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_parse_help_argparse_shape(): the real `solver.py --help` text → the names ⊇ {"instance","seed","time-limit","neighbours","restart-after","accept-worse","init","or-opt","print-tour"} and ∌ {"help","version"}; init has choices ["random","nearest"]; accept-worse type "float" default 0.0; instance required True; print-tour type "bool"
def test_parse_help_gnu_shape(): a hand-written GNU-style text → two settings with defaults
def test_discover_help_unparseable(): "Usage: thing\nSee the manual." → settings == [] and one note
def test_discover_help_hangs(tmp_path): a script that sleeps 60 s on --help → returns within 12 s with a note containing "did not answer --help"
def test_discover_program_version(): solver.py → version == "cli-solver 1.0" and version_number == "1.0"
def test_discover_python_module(): --python sys.executable --module json → a setting named "dumps.indent" with default None and source "json.dumps"
def test_cli_discover_json(capsys): exit 0 and the output parses
```

- [ ] **Step 2–4:** fail, implement, `pytest tests/test_harness_discover.py -q` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "cli: harness discover"`.

### Task 10: Skills ship with the package: `skill list`, `skill install`

**Files:**
- Create: `evolvekit/skills/__init__.py`, `evolvekit/skills/evolvekit-study/SKILL.md`, `evolvekit/skills/evolvekit-harness/SKILL.md`, `evolvekit/skills/shared/evolvekit.md` (placeholders: a frontmatter with `name` and a one-line `description`, and the body "Written in project 2.")
- Modify: `pyproject.toml` (package data: `skills/*/SKILL.md`, `skills/*/references/*`, `skills/shared/*`), `evolvekit/cli.py`
- Test: `tests/test_skills_install.py`

**Interfaces:**
- Produces: `available() -> list[str]` (folder names with a `SKILL.md`, `shared` excluded, sorted); `install(target: Path, *, force: bool = False) -> dict` returning `{"written": [paths], "skipped": [paths]}`, writing `<target>/.agents/skills/<name>/SKILL.md`, `<target>/.agents/skills/<name>/references/evolvekit.md` (a copy of `shared/evolvekit.md`), and the pointer `<target>/.claude/skills/<name>/SKILL.md` with the same `name` and `description` and the body "Read and follow `.agents/skills/<name>/SKILL.md` in this repository."; `frontmatter(text: str) -> dict`; `skill list`, `skill install [--dir DIR] [--force]` (prints what it wrote and what it skipped).

- [ ] **Step 1: Write the failing tests**

```python
def test_available_lists_the_two_skills(): == ["evolvekit-harness", "evolvekit-study"]
def test_install_writes_generic_and_pointer(tmp_path): the six files exist; the pointer's frontmatter name and description equal the skill's
def test_install_skips_existing_without_force(tmp_path): second install → all in "skipped"; with force → all in "written"
def test_shared_reference_identical_in_both(tmp_path): byte-equal, and equal to the package's shared file
def test_installed_files_are_package_data(): importlib.resources.files("evolvekit.skills") lists both SKILL.md files
def test_cli_skill_list_and_install(tmp_path, capsys)
```

- [ ] **Step 2–4:** fail, implement, `pytest tests/test_skills_install.py -q` → PASS. Then `pip wheel . -w <scratch>` once and check the wheel lists the skill files (a one-line note in the PR body).
- [ ] **Step 5: Commit** — `git commit -m "cli: skill list and skill install (.agents/skills, with a pointer for .claude/skills)"`.

### Task 11: Docs, the persona gate, the PR

**Files:**
- Modify: `README.md` (the "A study from the command line" section: the slug form, `study application`, the command table of spec §3.2, `doctor`, `harness discover`, `skill install`), `docs/app.md` (the library section: `app.json`; the one-world rule), `docs/harness-authoring.md` (`harness discover` as the first step)
- Create: `docs/superpowers/reviews/2026-11-07-agent-path-gate-log.md` (project 1's rounds)

- [ ] **Step 1:** Update the three docs; every new command has its row; the README's walk-through shows `study application` before `study check`.
- [ ] **Step 2:** `python tasks.py check` (nothing else running) → green; paste the count into the PR body.
- [ ] **Step 3: The persona gate** (spec §8, project 1): a fresh reviewer agent, told only "you are an engineer; the README is your manual; from a terminal alone make a study on the demo tour (the solver is `examples/cli-solver/solver.py`), start it, wait for the answer and read it, then find the study in the app"; it scores out of 10 and names blockers; fix, repeat until 8+ with no blocker; log each round.
- [ ] **Step 4:** Commit the docs and the log; push `agent/1-one-world`; open the PR against `master` (merge commit) with: the decisions taken while building, the gate count, the review log's final score, and this plan's path. A maintainer merges.
