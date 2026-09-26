# Harness part 3: the app — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `evolvekit app`: a local web app in which a consultant who is not a programmer sets up a study on a harness, runs it, and understands and hands over the result — design §7, §8, §11, §12, §13, §14.3.

**Architecture:** `evolvekit/app/` is a thin layer over `evolvekit/harness/` (parts 1–2 do the work: studies, compilation, the plan, the preview, the test run, a study run with its final check). A standard-library `ThreadingHTTPServer` bound to 127.0.0.1 serves one self-contained HTML page and a JSON API; everything it knows lives on disk in the library home (`studies/<slug>/`, `harnesses/`, `settings.yaml`, `.env`), so the app can be closed and reopened at any time. Runs are detached processes (`python -m evolvekit.app.job`) that write `job.json`; the app only reads run directories. The assistant proposes study parts as cards, each validated locally on the preview's tables before it is shown.

**Tech stack:** Python ≥ 3.11 standard library (`http.server`, `sqlite3`, `subprocess`, `concurrent.futures`, `zipfile`, `tkinter` for the native file dialog where present), PyYAML; the page is plain HTML/CSS/JS with no build step and no network; the assistant uses `evolvekit.providers` (OpenRouter by default, `fake` in tests).

## Global constraints

- Security as the dashboard: bind `127.0.0.1`; `Host` must be a loopback name with the server's port; every state-changing request (POST, PUT, PATCH, DELETE) needs a same-origin `Origin`; `Sec-Fetch-Site: cross-site` is refused; uploaded names are sanitised and every path stays inside the library home (judged as text before the filesystem sees it).
- The page uses only the JSON API; no CDN, works offline; the dashboard's colour tokens, light and dark; usable from 360 px; labels, focus rings, `aria-live` for progress; every status colour with an icon and a word; tabular figures.
- Plain words first, detail behind "Details"; explanations where the decision is taken; one primary action per screen.
- API keys: written to `<home>/.env` by the app, never returned (a read says set / not set), passed to a job's environment explicitly.
- The assistant never sends rows, names, addresses or coordinates: only the declared schema, the catalogues, the study, and summaries of the preview (row counts, min/mean/max of numeric columns, at most 50 distinct values of `categorical` columns).
- Public repository: nothing private in code, docs, screenshots or the review log.
- Branch `harness/3-app` from `harness/2-harness`; PR against `master`, stacked; merge commit.

---

## File map

| File | Responsibility |
|---|---|
| `evolvekit/app/__init__.py` | package; `AppError` (a sentence and an HTTP status) |
| `evolvekit/app/store.py` | the library home: settings, keys, harness entries, studies (create, read, save a step, problems per step), cases (upload, inspect, samples, split), inputs, study state lines for the home cards |
| `evolvekit/app/detect.py` | finding applications: candidate interpreters probed in parallel (10 s each), matches first |
| `evolvekit/app/work.py` | background work inside the app: the preview and the test run, with a state file each |
| `evolvekit/app/jobs.py` | starting a detached run, stopping it, the simplified status, the results, downloads, `report.html` |
| `evolvekit/app/job.py` | `python -m evolvekit.app.job STUDY RUN [--check]`: the detached process |
| `evolvekit/app/assistant.py` | prompts, cards, local validation with at most two corrections, cost in `assistant.jsonl` |
| `evolvekit/app/api.py` | the handlers: one function per endpoint, JSON in and out |
| `evolvekit/app/server.py` | routing, security, static page, the per-run dashboard, `serve()` |
| `evolvekit/app/static/app.html` | the page |
| `evolvekit/cli.py` | `evolvekit app [--home DIR] [--port N] [--no-browser] [--shortcut]` |
| `evolvekit/harness/execute.py` | + `final_check(root, run_id)` to resume the final check of a stopped run |
| `docs/app.md` | the app's manual, with screenshots |
| `docs/app/*.png` | screenshots: every screen, light and dark, and at phone width |
| `docs/superpowers/reviews/2026-09-26-intern-review-log.md` | the review loop's log |
| tests | `test_app_store.py`, `test_app_server.py`, `test_app_detect.py`, `test_app_jobs.py`, `test_app_assistant.py`, `test_app_end_to_end.py` |

## The API (design §7.1)

All JSON; errors are `{"error": "<sentence>"}` with 400 (the request is wrong), 403 (refused), 404, 409 (the study's state forbids it), 500.

| Endpoint | Handler returns |
|---|---|
| `GET /api/home` | `{studies: [{slug, name, harness, state, line, updated_at}], harnesses: [...], keys: {name: bool}, home}` |
| `POST /api/harnesses` (zip body) | the installed harness's entry |
| `GET /api/harnesses/{id}` | settings, tables, levers, KPIs, KPI templates, study templates, exports, README |
| `POST /api/studies` `{harness, template, name}` | the study document (below) |
| `GET /api/studies/{slug}` | the study document: `study` (the YAML as JSON), `problems` by step, `cases` with inspections, `inputs`, `preview`, `runs`, `harness` summary |
| `PATCH /api/studies/{slug}` | save a step: the changed top-level parts of the study; returns the study document |
| `GET /api/detect?harness=ID` | `[{path, ok, message, version, python}]`, matches first |
| `POST …/application` `{path}` | the probe, and saves the path and version when it matches |
| `POST /api/pick-file` `{kind, title}` | `{path}` or `{cancelled: true}` |
| `PUT …/cases/{name}` | the case with its inspection |
| `DELETE …/cases/{name}` | the study document |
| `POST …/cases/samples` | the study document, with the harness's samples copied in |
| `POST …/split` `{test}` | the study document, with a test set that spans the sizes |
| `PUT …/inputs/{name}/{file}` | the study document |
| `POST …/preview`, `GET …/preview` | the preview's state and result |
| `POST …/kpis/try` `{sql, params, rows_sql}` | `{value, note, rows: {columns, rows}}` on the preview's tables |
| `POST …/assistant` `{message}` | `{say, cards, usd, total_usd}` |
| `GET …/plan` | the plan in words and numbers, or blocked with three ways out |
| `POST …/test-run`, `GET …/test-run` | the test run's state and result |
| `POST …/start` | `{run}` |
| `POST …/stop` | `{stopping: true}` |
| `POST …/final-check` | `{run}`: resume the final check of a stopped run |
| `GET …/status[?run=]` | the simplified status |
| `GET …/results[?run=]` | the results |
| `GET …/download/{export}[?run=]` | a file: the harness's exports, `report.html` |
| `POST …/next` | a new study from this result: the best settings as its starting settings |
| `GET /studies/{slug}/runs/{run}/dashboard/…` | the existing dashboard for that run |
| `GET /api/settings`, `PUT /api/settings` | provider, models, recent applications; keys write-only |

---

### Task 1: The library home and studies on disk (`store.py`)
Produces `Home(root)` with `settings()` / `save_settings(changes)` (`settings.yaml`: `provider`, `models.assistant`, `models.search`, `recent_applications`), `keys()` → `{name: bool}` and `set_key(name, value)` (`.env`, never read back), `harnesses()` (library entries), `create_study(harness_id, template, name) -> slug` (unique slug), `study(slug) -> dict` (the study document), `save_step(slug, changes, step)` (parse, `study_problems`, save), `problems_by_step(study, harness, root)` (the sentences of `study_problems`, sorted onto the wizard's seven steps by key), `put_case(slug, name, data)` (sanitised name; the harness's formats; inspected with the runner, cached in `cases/.inspected.json` by size and mtime), `delete_case`, `use_samples`, `split(slug, test)` (sizes from the inspection's task counts, else bytes; evenly spaced ranks, smallest kept for training), `put_input(slug, name, filename, data)`, `state_line(slug)` ("draft · step 4 of 7", "running · 1 h 10 min left · 3.2 % better so far", "finished · confirmed 3.1 % lower real cost"). Tests: names with `..`, drives, UNC, reserved names and control characters refused; a case with the wrong extension refused with the harness's formats; the split spans sizes and keeps the smallest for training; keys never read back; problems land on the right steps.

### Task 2: Finding applications (`detect.py`)
Produces `candidates(harness, home, cwd) -> list[Path]` (the interpreter running evolvekit, `py -0p` on Windows, `.venv*` under the working directory, the evolvekit checkout and the home directory, `~/venvs/*`, `~/Envs/*`, conda environments, recently used paths; deduplicated) and `detect(harness, ...) -> list[Probe-like dict]` probing in parallel with `probe_application` (10 s each), matches first. Tests with fake interpreters (scripts that print a version or fail), ordering, deduplication, the timeout.

### Task 3: The server, security and the settings (`server.py`, `api.py`)
Produces `AppServer(home, port)`, the routing table, `serve(home, port, open_browser)`; `GET /` (the page), `GET/PUT /api/settings`, `GET /api/home`, `POST /api/harnesses`, `GET /api/harnesses/{id}`. Tests (a real server on port 0, `http.client`): a foreign `Host` → 403; a POST without or with a foreign `Origin` → 403; `Sec-Fetch-Site: cross-site` → 403; path traversal in every path parameter refused; unknown route 404 as JSON; a key PUT is never echoed; an upload over the size limit refused.

### Task 4: The study endpoints (`api.py`)
Create, read, save a step, the application probe, `pick-file` (tkinter in a subprocess; 501 with a sentence where there is none), cases (upload, delete, samples), split, inputs, the plan. Tests through HTTP with the demo harness: create from a template, save each step, problems move as steps are filled, cases inspected ("✓ 30 stops" / "✗ this is not a …"), the split, the plan blocked with three ways out and then fine.

### Task 5: Preview, KPI try-out and the test run (`work.py`)
The preview and the test run in a background thread each, with `preview/state.json` and `preview/test-run/state.json` (`running | done | failed`, started, finished), so a reload or a restarted app sees them; `kpis/try` on `preview/tables.sqlite` (the SDK's read-only guard, the 2 s limit; `rows_sql` → at most 50 rows). Tests: a preview that finishes, one that fails with the runner's sentence, a SQL that writes refused, a query returning text refused, rows shown.

### Task 6: Runs: start, stop, status, results, downloads (`jobs.py`, `job.py`)
`start(root)` writes the run id, `models.json` (AI search models from the settings), `launch.json` (pid, time) and starts `python -m evolvekit.app.job` detached (Windows `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`, elsewhere `start_new_session`), output to `runs/<id>/job.log`, keys from `.env` in its environment; refuses a second start while a run is alive (409). `stop` writes `stop-request`. `final_check` resumes a stopped check (`execute.final_check`). `status(root, run)`: phase, sentence, time used / left / expected finish, rounds done of the plan, best improvement with a plain verdict (per level for levels), best-so-far series, what runs now, failures grouped with what to do — from `job.json`, `plan.json` and `build_status`. `results(root, run)`: the headline, the final check (verdict, interval), every study KPI and guardrail start vs best on training and test, every changed value old → new with its help, the downloads. `download(root, run, export)`: the runner's `export` with the best values (`requests` zipped per case), `report.html` self-contained. `next_study(root, run)`: a new study whose starting settings are the best ones. Tests: start twice → 409; a dead job without `job.json` says so from `job.log`; status and results on a finished demo run; downloads exist and parse.

### Task 7: The assistant (`assistant.py`)
`ask(root, message, provider=None) -> {say, cards, usd, total_usd}`: the system prompt (the declared schema with units and descriptions, the harness catalogues, the study so far, the preview summaries, the card format and the rules: by-construction constraints first, never SQL that writes, a KPI of the untouched request for data studies), the model's JSON answer parsed, every card checked locally — SQL read-only on the preview returning one number, `rows_sql` runs, a `where` selecting at least one row, a range inside the setting's, constraints holding at the starting values, unique names, KPIs a goal or weighted sum names exist — failures sent back with the errors at most twice, then the card says honestly it could not be expressed. Every call's cost (reported, else the model's price) appended to `assistant.jsonl`. Without a key: a sentence on how to add one. Tests with the `fake` provider: valid cards pass with their value on the preview; a broken SQL is corrected on the second answer; three broken answers give an honest card; nothing but summaries is sent (no row values); the cost is recorded.

### Task 8: The page (`static/app.html`)
Hash routes: `#/` home, `#/new`, `#/study/{slug}/{step}`, `#/study/{slug}/running`, `#/study/{slug}/results`, `#/settings`. Home (study cards with their state line, New study, the harness library with "Add a harness", keys status); the wizard's seven steps on a left rail (saved as each is left, always back): Question, Application (detection list, Browse…, paste, probe in words, "Where do I find this?"), Cases (drop zone for files and folders, samples, inspections, the test-set question with suggestion and warnings, the preview started on leaving), What may change (settings grouped with Tune · Keep fixed at … · Default and "Why would I tune this?", import settings from a file, data changes as rows with "applies to 7 vehicle types", constraints as sentences with "holds today ✓", the tunables counter), Goal (KPIs with today's value and "show which rows count", one KPI / a combination as exchange rates / in order of importance, guardrails with today's value), Limits and budget (consequences in sentences, the plan in words, Adjust), Review and start (everything in sentences, the test run, Start); the assistant panel in steps 4–5; Running; Results; Settings. Light and dark via `prefers-color-scheme` and `?theme=`; 360 px; keyboard and screen reader.

### Task 9: `evolvekit app`, docs, screenshots
The CLI command (`--home`, `--port`, `--no-browser`, `--shortcut` writing `evolvekit.cmd` to the Windows desktop). `docs/app.md`; README pointer. Screenshots of every screen in light, dark and at 390 px, made with the browser in headless mode against a prepared home (`docs/app/make_screenshots.py`), checked by eye in the in-app browser.

### Task 10: The end-to-end test and the gate
`test_app_end_to_end.py` (slow): the demo harness through HTTP only — create, application, upload, split, preview, a KPI try-out, the plan, the test run, start, wait for the job, status, results, every download, a new study from the result. `python tasks.py check`.

### Task 11: The intern reviewer loop (design §12)
A fresh general-purpose subagent per round, one at a time, told only the persona, the materials and the note: a clean library home, a handover folder (`pyvrp-1.0.0.zip` from `harness pack`, `cases/` with eight small requests), the manager's note. It drives the real app in the in-app browser with real PyVRP runs and reports the overall score, the sub-scores, the frictions with severity and what would make it a 10. Fix the worst frictions, log the round, repeat until 8 or more with no blocker. Then one extra, non-gating review of a data study through the assistant. The log: `docs/superpowers/reviews/2026-09-26-intern-review-log.md`.

### Task 12: PR 3
Push `harness/3-app`, open the PR against `master` (stacked on part 2), bind it.

## Self-review

§7.1 → Tasks 1–6, 9. §7.2 → Task 8 (+ the endpoints of Tasks 3–7). §7.3 → Task 8. §8 → Task 7 (+ its panel in Task 8). §11 → Global constraints, Tasks 1, 6. §12 → Task 11. §13 app, assistant, end to end, screenshots → Tasks 3–10. §14.3 → Task 12.
