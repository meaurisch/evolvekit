# Agent path, project 5: The installer — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Before executing:** this plan is at task level. Re-read `evolvekit/cli.py`'s `cmd_app`, `evolvekit/harness/library.py`'s `find_harnesses`, `pyproject.toml`'s package data and `tasks.py` as they are after project 4 landed, expand every task to step level with superpowers:writing-plans (keep the tasks, files, interfaces and test names below), and run the builder check on the result.

**Goal:** a planner on Windows unzips one folder, double-clicks `evolvekit-app.exe`, installs a harness zip from Home and runs a study, with no Python of their own for evolvekit.

**Architecture:** PyInstaller builds a one-folder app from an entry module that calls `evolvekit.cli.main(["app", *argv])`, bundling the page, fonts, the dashboard, the SDK, the templates and the built-in harnesses; the code that finds package data and built-in harnesses resolves the bundle's root when frozen. `python tasks.py dist` builds and zips it; a hand-run smoke test proves the exe.

**Tech stack:** PyInstaller (a `dist` extra, pinned to a version at least two weeks old at build time), Python ≥ 3.11, Windows 11.

**Spec:** [docs/superpowers/specs/2026-11-07-agent-path-design.md](../specs/2026-11-07-agent-path-design.md), §2 and §7.

## Global constraints

- Windows only; macOS and Linux use `pipx install evolvekit` (spec §7).
- The application (PyVRP's Python) stays the planner's own; detection is unchanged (spec §7).
- No CI builds the package: `tasks.py dist` runs on the owner's machine (spec §2).
- Branch `agent/5-installer` from `agent/4-over-time`; PR against `master`, stacked; merge commit.

## Review focus

1. Windows SmartScreen and antivirus on an unsigned exe: the README says what the planner will see and how to proceed, and the zip's name carries the version (Task 4).
2. A planner's user name with a space or non-ASCII letters: the library home, the bundle's temp paths and the harness install must work (Task 3's `test_frozen_paths_with_spaces_and_unicode`).
3. The exe started from a network drive or a read-only folder: the home is under the user profile, never beside the exe (Task 2's `test_home_is_under_profile_when_frozen`).
4. A second double-click while the app runs: the second exe finds `app.json`, opens the browser at the running app's URL and exits, rather than starting a second app (Task 2's `test_second_start_opens_running_app`).
5. Updating: unzipping a new version over the old while the old runs must not corrupt the home; the home has no files in the bundle folder (Task 2's `test_bundle_folder_holds_no_state`).

---

## File map

| File | Responsibility |
|---|---|
| `evolvekit/app/entry.py` | new: the frozen entry (`main(argv)`) |
| `evolvekit/resources.py` | new: `package_root()` that knows `sys._MEIPASS`; used by the page, the dashboard, the SDK copy, the templates and the built-in harnesses |
| `evolvekit/harness/library.py` | built-in harnesses from the bundle when frozen |
| `packaging/evolvekit-app.spec`, `packaging/README.md` | the PyInstaller spec and how to build |
| `tasks.py` | `dist` |
| `pyproject.toml` | the `dist` extra |
| `tests/test_resources.py`, `tests/test_entry.py`, `tests/dist_smoke.py` | tests; the hand-run smoke test |
| `README.md`, `docs/app.md` | "For planners: the installer" |

### Task 1: `resources.package_root()` and frozen-aware lookups

**Produces:** `package_root() -> Path` (the package directory, or `Path(sys._MEIPASS) / "evolvekit"` when `getattr(sys, "frozen", False)`); every `Path(__file__).parent`-style lookup of package data (the app's `static/`, the dashboard HTML, `harness/sdk/evk_harness.py`, `harness/templates/AGENTS.md`, `skills/`) goes through it; `library.find_harnesses` finds the built-in `harnesses/` under the bundle root when frozen.

**Tests:** `test_package_root_unfrozen_is_the_package`, `test_package_root_frozen_uses_meipass` (monkeypatched `sys.frozen`/`sys._MEIPASS`), `test_every_data_lookup_uses_package_root` (grep the package for `__file__` uses outside `resources.py`; the allowed list is empty).

### Task 2: The entry module

**Produces:** `entry.main(argv: list[str] | None = None) -> int`: no arguments → `app` with the default home under the user profile and the browser opened; `--service`, `doctor`, `study …` passed through to `cli.main`; `-m <module> [args]` → `runpy.run_module(module, run_name="__main__")` with `sys.argv` set; `--script <file> [args]` → `runpy.run_path(file, run_name="__main__")`; when `app.json` names a running app, `main([])` opens the browser at its URL and returns 0 without starting a second server; the home is never beside the exe; nothing is written into the bundle folder. `resources.python_argv() -> list[str]` (`[sys.executable]` unfrozen; `[sys.executable, "-m"]` for modules and `[sys.executable, "--script"]` for scripts when frozen, through `python_module_argv(module)` and `python_script_argv(path)`), and every place that today builds `[sys.executable, "-m", "evolvekit.app.job", …]` (`jobs._launch`), runs a program harness's `runner.py` (`compile.command`, `execute.runner_command`) or substitutes `{python}` (`stages.build_argv`) uses it. `doctor`'s `python` line says "bundled" when frozen.

**Tests:** `test_entry_no_args_runs_app` (patched `cli.main`), `test_entry_passthrough`, `test_entry_m_runs_a_module`, `test_entry_script_runs_a_file`, `test_python_argv_frozen_and_not`, `test_every_sys_executable_use_goes_through_resources` (grep the package: `sys.executable` appears only in `resources.py` and tests), `test_second_start_opens_running_app`, `test_home_is_under_profile_when_frozen`, `test_bundle_folder_holds_no_state`.

### Task 3: The PyInstaller spec and `tasks.py dist`

**Produces:** `packaging/evolvekit-app.spec` (one-folder, console hidden for `app`, console shown for `doctor`: two executables in one folder, `evolvekit-app.exe` and `evolvekit-cli.exe`, sharing the bundle; data: `evolvekit/app/static/**`, `dashboard/*.html`, `harness/sdk/*.py`, `harness/templates/*.md`, `skills/**`, `harnesses/**` without `samples/` generators' outputs; hidden imports as the build reveals); `tasks.py dist` (checks the `dist` extra, runs PyInstaller, zips `dist/evolvekit-app/` as `dist/evolvekit-app-<version>-win64.zip`, prints the path and size); `pyproject` `dist = ["pyinstaller>=…"]`.

**Tests:** `test_dist_task_refuses_without_extra` (patched import); `test_frozen_paths_with_spaces_and_unicode` (a temp home with such a path; `resources` and `Home` work); the build itself is verified by Task 4's smoke test, not by pytest.

### Task 4: The smoke test, the docs, the gate, the PR

- `tests/dist_smoke.py` (run by hand after `python tasks.py dist`): `evolvekit-cli.exe --version` prints the version; `evolvekit-cli.exe doctor --json` exits 0 or 1 with the JSON shape and says the Python is bundled; `evolvekit-app.exe --no-browser` started detached writes `app.json` within 15 s; GET `/api/home` with the token answers 200; `evolvekit-cli.exe study list` answers; a demo-tour study made, started and waited for through the exe alone (no Python on `PATH`: the smoke test clears `PATH` of every Python for its subprocesses) reaches a verdict; the app is stopped; the home holds no file from the bundle.
- README: *For planners: the installer* (download the zip, unzip, what SmartScreen shows and that the exe is unsigned, double-click, where the home is, how to install a harness zip, how to update: unzip the new version over the old); `docs/app.md`: the same in the app's words.
- The persona gate of spec §8 for project 5: an intern on a clean Windows user account (a second local account on the owner's machine) installs from the zip, installs `pyvrp-1.0.0.zip`, points the app at a PyVRP Python, runs a study; score 8+ with no blocker; log it.
- `python tasks.py check` green (the suite never needs PyInstaller); PR `agent/5-installer` against `master`, stacked, with the smoke test's output and the zip's size in the body.
