# App improvement 5: everyday study management — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After the tenth study the home page is still usable: a study can be renamed, duplicated, archived and deleted (with undo); a study's past runs are listed with their verdicts; two studies or runs compare side by side; a finished study continues with more time; a study travels as one zip; removing a case, measure or rule can be undone.

**Architecture:** All of it is API and store work on top of what exists: studies are folders, so delete is a move to `studies/.trash/`, archive is a marker file, duplicate is a copy without `runs/` and `preview/`. Continue reuses the engine's "K more generations" on the same run directory and the final check again. The page gets the smallest controls that reach each function; the redesign restyles them.

**Tech stack:** Python ≥ 3.11 standard library (`shutil`, `zipfile`); pytest with the demo harness.

**Spec:** `~/.claude/plans/find-ways-to-significantly-hidden-sutherland.md`, proposal 5; handoff *What the app will do by then* §4.

## Global constraints

- Nothing is deleted at once: the trash keeps a study for 7 days (cleaned when the app starts); the page offers Undo for 10 s and *Restore* from the trash list after that.
- A study that is running (or queued) can be neither deleted, renamed, archived nor continued; the API says so with 409.
- Slugs stay stable: rename changes `name` only.
- Branch `app/management` from `app/assistant`, stacked; `python tasks.py check` green.

## Review focus

1. Deleting a study whose run is queued must dequeue it first, or refuse. Test in Task 1.
2. Duplicating a study must not copy runs, previews, the assistant history or `downloads/`, and must give the copy a new slug and name. Test in Task 1.
3. Continuing a run must spend only the extra time, then check again, and the results must then show the new best (which may be the old one). Test in Task 4.
4. Importing a study zip must refuse paths outside the folder (zip slip) and a zip without `study.yaml`. Test in Task 5.
5. Comparing a run with no final check must say so, not crash. Test in Task 3.

---

## Task 1: delete with undo, rename, duplicate, archive

**Files:**
- Modify: `evolvekit/app/store.py` (`Home.delete_study`, `restore_study`, `duplicate_study`, `set_archived`, `trash`, `clean_trash`), `evolvekit/app/api.py` (routes, `home` lists archived and trashed), `evolvekit/app/server.py` (`clean_trash` at start), `evolvekit/app/static/app.html` (a menu on the study card; an Archived section; an undo bar)
- Test: `tests/test_app_store.py`, `tests/test_app_server.py`

**Interfaces:**
- Produces: `DELETE /api/studies/{slug}` → moves to `studies/.trash/<slug>~<YYYYMMDD-HHMMSS>` and returns `{"trashed": "<slug>~<stamp>"}` (409 while running or queued); `POST /api/trash/{entry}/restore` → the study back under its slug (or `slug-2` if taken) and returns the home; `GET /api/home` gains `"trash": [{entry, name, deleted_at}]` and each study `"archived": bool`; `PATCH /api/studies/{slug}` accepts `{"name": "..."}` (already) and `{"archived": true|false}` (marker file `.archived` in the folder; not a study field); `POST /api/studies/{slug}/duplicate` `{name}` → the new document; the copy takes `study.yaml`, `harness/`, `cases/`, `inputs/`, `.inspected.json`, not `runs/`, `preview/`, `assistant.jsonl`. `Home.clean_trash(days=7)`.

- [ ] **Step 1: Failing tests** — `test_a_study_can_be_trashed_restored_duplicated_and_archived(app)` through HTTP: create; duplicate → a second slug with the cases; archive → `home.studies[i].archived`; delete → `home.trash` has it and `GET /api/studies/{slug}` is 404; restore → back. `test_a_running_study_cannot_be_deleted(app)` (409). — [ ] **Steps 2–5** as usual; commit `app: rename, duplicate, archive and delete a study, with undo`.

## Task 2: a study's past runs, each with its verdict

**Files:**
- Modify: `evolvekit/app/jobs.py:129-137, 1072-1110` (`runs`, `run_line` factored out of `state_line`), `evolvekit/app/static/app.html` (Results: a *Runs* list; the hash carries `?run=`)
- Test: `tests/test_app_results.py`

**Interfaces:**
- Produces: `jobs.run_line(root, study, run_dir, document_of=None) -> dict` (the card words for one run; `state_line` returns it for the latest); `jobs.runs(root)` entries gain `"line"` and `"kind"`. Page: Results and Running read `run` from the hash query (`#/study/<slug>/results?run=<id>`), the Results page lists this study's runs with their lines, the current one marked.

- [ ] Steps as usual; commit `app: a study's runs, each with its verdict`.

## Task 3: compare two studies or two runs

**Files:**
- Create: `evolvekit/app/compare.py` (`compare(home, a: str, b: str) -> dict`), route `GET /api/compare?a=<slug>[/<run>]&b=<slug>[/<run>]`
- Modify: `evolvekit/app/static/app.html` (`#/compare?a=…&b=…`, a *Compare* control on the home page and on Results)
- Test: `tests/test_app_compare.py`

**Interfaces:**
- Produces: `{"a": side, "b": side, "same_goal": bool}` with `side = {slug, name, run, harness, goal: {says, direction}, headline, final: {state, decision, line, advice, chance, ci95}, held: [{case, start, best, change_pct}], changes: [...], setup: {time_per_case_s, hours, combinations}}`; a run with no final check gives `final.state == "missing"` and the line *There is no final check for this run.*; a side that does not exist is 404. Page: two columns, rows aligned by label; cases joined by name.

- [ ] Steps as usual; commit `app: compare two studies or runs side by side`.

## Task 4: continue a finished study with more time

**Files:**
- Modify: `evolvekit/harness/execute.py` (`continue_study`), `evolvekit/app/job.py` (`--continue --hours H`), `evolvekit/app/jobs.py` (`start(..., extend=...)`, `status["can_continue"]`), `evolvekit/app/api.py` (`POST /api/studies/{slug}/continue {run, hours}`), `evolvekit/app/static/app.html` (Results: *Continue for another {hours}*)
- Test: `tests/test_study_run.py`, `tests/test_app_jobs.py`

**Interfaces:**
- Produces: `execute.continue_study(root, run_id, *, hours: float, models=None, log=print) -> dict`: requires a `done` or `stopped` run with `runs.jsonl`; `Job.resume(run_dir, state, "search")` with `continued_at` and `hours_added`; `K = make_plan(... hours=hours ...).rounds` more generations via `Driver(config, run_dir).run(generations=K)` under a `RunStop(max_hours=hours)`; then `_final_check` again (the comparison directory is reused; `confirm` keeps pairs it already has for the same candidate). `jobs.can_continue(run_dir, job)`: phase `done` or `stopped` with a finished search. `status` gains `can_continue`; `state_line` for a continued run says `running · continued · 12 min left`. The page's button uses `study.budget.hours` as the extra time.

- [ ] **Step 1: Failing test** — on the demo with a fixed plan: run to done, `continue_study(hours=0.004)` → `phase == "done"`, `search.generations` grew, `final` present, `continued_at` set. — [ ] Steps 2–5; commit `app: continue a finished study with more time`.

## Task 5: a study as one zip; undo after removing a case, measure or rule

**Files:**
- Modify: `evolvekit/app/jobs.py` or `store.py` (`export_study(root) -> bytes`, `Home.import_study(data, filename) -> slug`), `evolvekit/app/api.py` (`GET /api/studies/{slug}/download/study`, `POST /api/studies` with a zip body), `evolvekit/app/store.py:299-313` (`delete_case` moves to `.trash/cases/`; `restore_case`), `evolvekit/app/static/app.html` (Undo bar after removing a case, measure, rule or data change: cases restore through the API, the others re-PATCH the removed object)
- Test: `tests/test_app_store.py`, `tests/test_app_server.py`

**Interfaces:**
- Produces: the zip holds `study.yaml`, `harness/`, `cases/`, `inputs/`; import refuses a member outside the folder or a zip without `study.yaml` (400 with the reason) and gives the study a new slug from its name; `POST /api/studies/{slug}/cases/{name}/restore`.

- [ ] Steps as usual; commit `app: a study travels as one zip; undo after removing something`.

## Task 6: the gate, the docs and the PR

- [ ] `docs/app.md` *Your library* and *The results* gain the menu, the runs list, compare, continue, the zip; `tasks.py check`; walk the home page once in the app (menu, undo, archive); `gh pr create --base app/assistant`, the trash rule and the continue rule in the body, attribution last.
