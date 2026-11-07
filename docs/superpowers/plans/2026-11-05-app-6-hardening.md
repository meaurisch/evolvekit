# App improvement 6: local-server hardening — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Only the page the app opened can drive the app; API keys never reach a harness or a solver; a path saved into a study can never point outside it; a harness zip cannot fill the disk or crash the install.

**Architecture:** A per-launch secret in the URL the app opens, kept by the page and required on every API request, closes the gap the `Origin` check leaves for local processes. The evaluator scrubs secrets from the environment it gives solvers. The existing but unused `store.safe_relative` guards every path that enters `study.yaml`. `install_harness` gets limits and catches everything a zip can throw.

**Tech stack:** Python ≥ 3.11 standard library (`secrets`, `zipfile`, `zlib`); pytest with real HTTP against `AppServer(port=0)`.

**Spec:** `~/.claude/plans/find-ways-to-significantly-hidden-sutherland.md`, proposal 6.

## Global constraints

- Nothing changes for the user: the app still opens its page itself; the printed address works when pasted into a browser; `--shortcut` still works.
- No new dependency. Branch `app/hardening` from `app/management`, stacked; `python tasks.py check` green.

## Review focus

1. A request with the right `Origin` but no token must be refused, and the refusal must be sent before the body is read. Test in Task 1.
2. A bookmarked `http://127.0.0.1:8780/` without the token must show a page that says to open the app from the address it printed, not a blank error. Test in Task 1.
3. A harness runner started by the evaluator must not see `OPENROUTER_API_KEY` even when AI search help is on. Test in Task 2.
4. `remove_input` with an absolute path in `study.yaml` must refuse, and must delete nothing. Test in Task 3.
5. A zip with 10 000 members or 1 GB unpacked must be refused before anything is written. Test in Task 4.

---

## Task 1: a per-launch secret

**Files:**
- Modify: `evolvekit/app/server.py:245-260` (`AppServer.token`), `:130-139` (`_refusal`), `:170-180` (the page: inject nothing; the page reads the token from the URL), `evolvekit/cli.py:560-590` (print and open `server.url_with_token`), `evolvekit/app/static/app.html` (`api()` sends `X-Evolvekit-Token`; on load, take `?token=` into `sessionStorage` and strip it from the URL with `history.replaceState`)
- Test: `tests/test_app_server.py` (`call()` sends the token by default; new refusal tests)

**Interfaces:**
- Produces: `AppServer.token: str` (`secrets.token_urlsafe(24)`), `AppServer.url_with_token`; every `/api/...` request and the dashboard proxy require `X-Evolvekit-Token: <token>` or `?token=<token>`; a missing or wrong token → 403 `{"error": "refused: open the app from the address it printed, which carries its key"}`; `GET /` without a token still serves the page, and the page, finding no token in the URL or `sessionStorage`, shows one sentence: *Open the app from the address printed in the window it runs in.* The dashboard links the page builds carry the token.

- [ ] **Step 1: Failing tests** — `test_a_request_without_the_token_is_refused_before_its_body_is_read` (POST with a 1 MB body and no token → 403, and the handler's `_body` not called: monkeypatch or assert on timing), `test_the_token_is_accepted_as_a_header_or_in_the_query`, existing tests keep passing with `call()` sending the header. — [ ] Steps 2–5; commit `app: only the page the app opened can drive it`.

## Task 2: keys never reach a solver or a harness

**Files:**
- Modify: `evolvekit/evaluate/process.py:200-260` (`start_tree`: scrub), `evolvekit/app/jobs.py:175-190` (`_launch`: pass the keys only when AI help is on), `evolvekit/app/store.py:182-184` (`.env` written with mode 0o600 on POSIX)
- Test: `tests/test_process_tree.py` or `tests/test_evaluate*.py`, `tests/test_app_jobs.py`

**Interfaces:**
- Produces: `evaluate.process.SECRET_NAMES = re.compile(r"(_API_KEY$|^AZURE_OPENAI_|_SECRET$|_TOKEN$)", re.I)`; `start_tree(argv, ..., env=None)` builds `env` from `os.environ` without matching names when `env` is not given; `jobs._launch(home, root, run_dir, extra, *, with_keys: bool)` passes `home.job_environment()` only with `with_keys` (true only when `study.budget.ai_enabled`).

- [ ] **Step 1: Failing test** — start a child through `start_tree` that prints `os.environ.get("OPENROUTER_API_KEY")` with the variable set in the parent → the child prints `None`. — [ ] Steps 2–5; commit `evaluate: secrets never reach a solver`.

## Task 3: every path in a study is judged as text first

**Files:**
- Modify: `evolvekit/app/store.py:260-277` (`save_step` validates `cases`, `training`, `test`, `inputs`), `:398-402` (`remove_input`), `evolvekit/app/jobs.py:905-912` (`_export`'s case path), `:1140-1145` (`next_study`'s input path)
- Test: `tests/test_app_store.py`

**Interfaces:**
- Produces: `save_step` refuses (400 `… is not a file of this study`) any `training`, `test` or `inputs` value that `safe_relative(root, value)` refuses, and any that does not start with `cases/` or `inputs/`; `remove_input`, `_export` and `next_study` resolve through `safe_relative`.

- [ ] **Step 1: Failing tests** — `test_a_path_outside_the_study_is_refused_when_saved` (PATCH `inputs: {"today": "C:/Windows/notepad.exe"}` → 400; `training: ["../other/x.json"]` → 400); `test_removing_an_input_with_a_hostile_path_deletes_nothing` (write the hostile path straight into `study.yaml`, create the target file, call `remove_input` → `AppError`, file still there). — [ ] Steps 2–5; commit `app: every path in a study stays inside it`.

## Task 4: harness zips with limits

**Files:**
- Modify: `evolvekit/harness/library.py:130-165` (`install_harness`), `evolvekit/app/store.py:208-216` (`install`)
- Test: `tests/test_harness_cli.py` (where zip slip is tested today)

**Interfaces:**
- Produces: `MAX_MEMBERS = 2000`, `MAX_UNPACKED = 500 * 1024 * 1024`; `install_harness` checks `len(infolist())` and `sum(i.file_size)` before writing anything and refuses with `HarnessError("<name>: <n> files / <size> unpacked is more than a harness can be")`; it catches `zipfile.BadZipFile, RuntimeError, NotImplementedError, zlib.error, OSError` into `HarnessError` with the reason, always removing the staging folder; `store.install` maps `HarnessError` to 400 as today.

- [ ] **Step 1: Failing tests** — an encrypted zip, a zip with 2001 empty members, a zip whose declared sizes sum past the limit → `HarnessError` and no `.incoming-*` left behind. — [ ] Steps 2–5; commit `harness install: limits, and every way a zip can fail is a sentence`.

## Task 5: no side effects on GET

**Files:**
- Modify: `evolvekit/app/api.py` (`/api/detect` becomes `POST`), `evolvekit/app/static/app.html` (the call), `docs/app.md` (the API list)
- Test: `tests/test_app_server.py` (GET `/api/detect` → 404 or 405)

- [ ] Steps as usual; commit `app: detection is a POST`.

## Task 6: the gate, the docs and the PR

- [ ] `docs/app.md` *Starting it*: the address carries a key; *For developers*: the token header. `tasks.py check`; start the app from the CLI, open the printed address, use it; open the bare address in a new tab and read the sentence. `gh pr create --base app/management`, attribution last.
