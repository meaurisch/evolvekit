# Agent path, project 5: The installer — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Before executing:** this plan is at task level. Re-read `evolvekit/cli.py`'s `cmd_app`, `evolvekit/harness/library.py`'s `find_harnesses`, `pyproject.toml`'s package data and `tasks.py` as they are after project 4 landed, expand every task to step level with superpowers:writing-plans (keep the tasks, files, interfaces and test names below), and run the builder check on the result.
>
> Expanded to step level on 2026-11-08 against agent/4-over-time (44b42bd). The given Task 2 is split in two and a Stop control is added (D21), so the tasks are: 1 resources, 2 the entry, 3 evolvekit's Python everywhere, 4 the spec and `dist`, 5 Stop the app, 6 the smoke test, docs, gate and PR. Every given test name is kept, in the task that owns its code. Tasks run in order; Task 5 touches only the page and may run beside Task 4. Where a step names a body, the tests are the contract and the body is a sketch.

**Goal:** a planner on Windows unzips one folder, double-clicks `evolvekit-app.exe`, installs a harness zip from Home and runs a study, with no Python of their own for evolvekit.

**Architecture:** PyInstaller builds a one-folder app from an entry module that calls `evolvekit.cli.main(["app", *argv])`, bundling the page, fonts, the dashboard, the SDK, the templates and the built-in harnesses; the code that finds package data and built-in harnesses resolves the bundle's root when frozen. `python tasks.py dist` builds and zips it; a hand-run smoke test proves the exe.

**Tech stack:** PyInstaller (a `dist` extra, pinned to a version at least two weeks old at build time), Python ≥ 3.11, Windows 11.

**Spec:** [docs/superpowers/specs/2026-11-07-agent-path-design.md](../specs/2026-11-07-agent-path-design.md), §2 and §7 (with §8 for the gate, §9 for testing, §10, §11).

## Global constraints

- Windows only; macOS and Linux use `pipx install evolvekit` (spec §7).
- The application (PyVRP's Python) stays the planner's own; detection is unchanged (spec §7).
- No CI builds the package: `tasks.py dist` runs on the owner's machine (spec §2).
- Branch `agent/5-installer` from `agent/4-over-time`; PR against `master`, stacked; merge commit.
- **The exe is its own Python** (spec §7): the job processes, the runners of `program` harnesses, the compiled stages' `{python}`, the detached launcher, the file picker and the candidate import checks run `evolvekit-cli.exe` when frozen; one module, `evolvekit/resources.py`, decides it; `sys.executable` appears nowhere else in the package except the harness SDK. A `python` harness still runs under the application's own Python.
- PyInstaller enters only through the `dist` extra, pinned exactly to the newest release uploaded at least 14 days before the build day (from `https://pypi.org/pypi/pyinstaller/json`); nothing is installed system-wide; no Defender exclusion, registry edit or code signing (controller's ruling). It is a build-time dependency: no runtime dependency is added, and the suite never imports it.
- No Windows account is created: the persona gate runs under a fresh simulated profile (controller's ruling; Task 6 Step 6).
- The harness SDK's contract and file do not change; it stays standard library only and Python 3.9-compatible (spec §2, §10).
- The app's words in every sentence for people (study, case, held-back case, starting point, measure, goal, rule, combination, round, final check; never KPI, seed, generation, instance, config, baseline). Nothing says "better" outside a verdict's `line`.
- Windows first, Linux must pass: frozen behaviour is tested everywhere by faking `sys.frozen`/`sys._MEIPASS`/`sys.executable` with `monkeypatch`; nothing Windows-only without a platform guard. Test paths carry a space and a non-ASCII letter (`tmp_path / "Zoë Intern"`).
- CLI tested through `main([...])` and `capsys`; tests that run real demo-tour studies are `@pytest.mark.slow`; the smoke test and the persona gate never run beside `tasks.py check` or each other.
- Python is `.venv/Scripts/python.exe`; files LF-ended (a `.cmd` the code writes is CRLF on purpose), written with the Write/Edit tools; never `git stash`, never commit `.superpowers/`.
- Docs are part of each task: a command without its README row is not done.

## Review focus

1. Windows SmartScreen and antivirus on an unsigned exe: the README says what the planner will see and how to proceed, and the zip's name carries the version (Task 6 Step 3; Task 4's `test_dist_zip_name_carries_the_version`).
2. A planner's user name with a space or non-ASCII letters: the library home, the bundle's temp paths and the harness install must work (Task 4's `test_frozen_paths_with_spaces_and_unicode`).
3. The exe started from a network drive or a read-only folder: the home is under the user profile, never beside the exe (Task 2's `test_home_is_under_profile_when_frozen`).
4. A second double-click while the app runs: the second exe finds `app.json`, opens the browser at the running app's URL and exits, rather than starting a second app (Task 2's `test_second_start_opens_running_app`).
5. Updating: unzipping a new version over the old while the old runs must not corrupt the home; the home has no files in the bundle folder (Task 2's `test_bundle_folder_holds_no_state`), and the planner can stop the windowless app first (Task 5's `test_settings_offers_stop_the_app`).

Carried from projects 3 and 4's gates, each with its test:

6. **Non-ASCII through `cmd.exe`.** `cmd.exe` reads a `.cmd` in the OEM code page, so a home under `C:\Users\Zoë` written into `evolvekit.cmd` (`app --shortcut`) is mangled: the file is CRLF, UTF-8, with `chcp 65001 >nul` before any path (Task 3's `test_shortcut_is_crlf_utf8_with_chcp`); an argv element with a space stays one element through every `python_*_argv` (Task 3's `test_python_argv_frozen_and_not`).
7. **8.3 short temp paths** (CI's `C:\Users\RUNNER~1\…`, fixed once in f7dd038): no test compares a `tmp_path` string with a resolved path; paths are compared with `os.path.samefile` or both sides `.resolve()`d, bundle snapshots use paths relative to the bundle, and `resources` never resolves `sys.executable` (Task 2's `test_bundle_folder_holds_no_state`, Task 3's `test_python_argv_frozen_and_not`).
8. **CRLF.** A harness zip packed from a CRLF checkout (`core.autocrlf=true` on this machine) installs and is offered from the bundle exactly like an LF one (Task 4's `test_frozen_paths_with_spaces_and_unicode` packs CRLF files).
9. **Stale processes.** An exe from `dist\evolvekit-app` still running holds the folder on Windows: `dist` says so in a sentence instead of PyInstaller's traceback (Task 4's `test_dist_says_a_running_exe_holds_the_folder`); the smoke test stops what it started and fails when any process from its bundle folder is left (Task 6 Step 1, check 12).
10. **Nothing claimed before a verdict.** The Stop control, the installer docs and the smoke test's output claim nothing; the smoke test prints the verdict's own `line` and the PR body quotes it (Task 5's `test_no_banned_word_reaches_the_screen` over the new strings; Task 6 Step 1, check 10).

---

## File map

| File | Responsibility |
|---|---|
| `evolvekit/resources.py` | new: `is_frozen`, `package_root`, `builtin_harnesses`, `bundle_dir`, `APP_EXE`/`CLI_EXE`; `python_argv`, `python_module_argv`, `python_script_argv`, `python_code_argv`, `app_argv` |
| `evolvekit/app/entry.py` | new: the frozen entry `main(argv)`: the app form, the CLI pass-through, `-m`/`-c`/`--script`/`*.py` |
| `evolvekit/harness/library.py` | `BUILT_IN` and `find_harnesses` from `builtin_harnesses()`; `AGENTS_TEMPLATE` from `package_root()` |
| `evolvekit/harness/sdk/__init__.py`, `evolvekit/app/server.py`, `evolvekit/dashboard/__init__.py`, `evolvekit/skills/__init__.py` | `SDK_PATH`, `PAGE`, the dashboard's `PAGE`, the skills' root from `package_root()` |
| `evolvekit/app/detach.py`, `jobs.py`, `service.py`, `api.py`, `detect.py`, `evolvekit/cli.py`, `config.py`, `evaluate/stages.py`, `harness/execute.py`, `harness/check.py`, `harness/probe.py`, `doctor.py` | every `sys.executable` use through `resources` (inventory in Task 3) |
| `evolvekit/app/static/js/pages/settings.js` | "This app": Stop the app |
| `packaging/evolvekit-app.spec`, `packaging/bundle_files.py`, `packaging/README.md` | the PyInstaller spec; what it bundles (testable without PyInstaller); how to build |
| `tasks.py`, `pyproject.toml`, `.gitignore`, `AGENTS.md` | `dist`; the `dist` extra; `/dist/`, `/build/`; the entry-point line |
| `tests/test_resources.py`, `tests/test_entry.py`, `tests/test_dist_task.py`, `tests/dist_smoke.py` | tests; the hand-run smoke test (not collected) |
| `tests/test_app_service.py`, `tests/test_app_page.py` | one existing test's patch target; the Stop control |
| `README.md`, `docs/app.md` | "For planners: the installer" |
| `docs/superpowers/reviews/2026-11-08-installer-gate-log.md`, `…/2026-11-07-agent-path-build-log.md` | the gate's log; project 5's entries |

---

### Task 1: `resources.package_root()` and frozen-aware lookups

**Files:**
- Create: `evolvekit/resources.py`, `tests/test_resources.py`
- Modify: `evolvekit/harness/library.py:52` (`BUILT_IN`), `:83-104` (`find_harnesses`), `:255` (the AGENTS.md template); `evolvekit/harness/sdk/__init__.py:7`; `evolvekit/app/server.py:54`; `evolvekit/dashboard/__init__.py:49`; `evolvekit/skills/__init__.py:21-22`

**Interfaces:**
- Produces (`evolvekit/resources.py`, standard library only, imports nothing from evolvekit):
  - `APP_EXE = "evolvekit-app.exe"` and `CLI_EXE = "evolvekit-cli.exe"` on win32, `"evolvekit-app"` / `"evolvekit-cli"` elsewhere (so the Linux CI can fake a bundle).
  - `is_frozen() -> bool` — `bool(getattr(sys, "frozen", False))`, read on every call.
  - `package_root() -> Path` — unfrozen `Path(__file__).resolve().parent` (the `evolvekit` package folder); frozen `Path(sys._MEIPASS) / "evolvekit"`.
  - `builtin_harnesses() -> Path` — unfrozen `package_root().parent / "harnesses"` (a checkout's; absent in an installed wheel); frozen `Path(sys._MEIPASS) / "harnesses"`.
  - `bundle_dir() -> Path | None` — frozen `Path(sys.executable).parent` (the folder holding both exes; never `.resolve()`d), else `None`.
- Produces elsewhere: `library.BUILT_IN = builtin_harnesses()` (kept for `detect.py` and `tests/test_prior.py`); `find_harnesses` calls `builtin_harnesses()` on every call (so a frozen process — or a test faking one — finds the bundle's); `library.AGENTS_TEMPLATE = package_root() / "harness" / "templates" / "AGENTS.md"`; `SDK_PATH = package_root() / "harness" / "sdk" / "evk_harness.py"`; `server.PAGE = package_root() / "app" / "static" / "app.html"`; `dashboard.PAGE = package_root() / "dashboard" / "index.html"`; `skills._root()` returns `package_root() / "skills"` (a `Path`: every `Traversable` call the module makes — `iterdir`, `joinpath`, `is_file`, `is_dir`, `read_bytes`, `name` — is a `Path` method).
- Test helpers (in `tests/test_resources.py`, imported by later tests as `from test_resources import fake_bundle, fake_profile`):
  - `fake_bundle(monkeypatch, root: Path, *, harnesses: tuple[str, ...] = (), skills: bool = False) -> Path` — lays out `root/APP_EXE`, `root/CLI_EXE` (empty files) and `root/_internal/`, copies the named folders of the checkout's `harnesses/` into `_internal/harnesses/` (with `pack_harness`'s ignore rule) and, with `skills`, `evolvekit/skills/` into `_internal/evolvekit/skills/`; then `monkeypatch.setattr(sys, "frozen", True, raising=False)`, `sys._MEIPASS = str(root / "_internal")` (`raising=False`), `sys.executable = str(root / APP_EXE)`. Returns `root`.
  - `fake_profile(monkeypatch, root: Path) -> Path` — makes `root` and sets `USERPROFILE`, `HOME` to it, `APPDATA`, `LOCALAPPDATA` under it, deletes `EVOLVEKIT_HOME`; returns `root`.

- [ ] **Step 1: Write the failing tests** (`tests/test_resources.py`)

```python
import ast, os, shutil, sys
from pathlib import Path

import pytest

import evolvekit
from evolvekit import resources
from evolvekit.harness.library import find_harnesses

PACKAGE = Path(evolvekit.__file__).resolve().parent
CHECKOUT = PACKAGE.parent

def _uses(predicate) -> list[str]:
    """`file:line` of every AST node in the package that `predicate` accepts (string literals are not code)."""

def test_package_root_unfrozen_is_the_package():
    assert resources.package_root() == PACKAGE
    assert resources.builtin_harnesses() == CHECKOUT / "harnesses"
    assert resources.bundle_dir() is None and resources.is_frozen() is False

def test_package_root_frozen_uses_meipass(tmp_path, monkeypatch):
    bundle = fake_bundle(monkeypatch, tmp_path / "Zoë Intern" / "evolvekit app")
    assert resources.is_frozen()
    assert resources.package_root() == bundle / "_internal" / "evolvekit"
    assert resources.builtin_harnesses() == bundle / "_internal" / "harnesses"
    assert resources.bundle_dir() == bundle

def test_every_data_lookup_uses_package_root():
    hits = [h for h in _uses(lambda n: isinstance(n, ast.Name) and n.id == "__file__") if not h.startswith("resources.py:")]
    assert hits == [], "package data is found through evolvekit.resources.package_root()"

def test_package_data_paths_are_under_package_root():
    from evolvekit import dashboard
    from evolvekit.app import server
    from evolvekit.harness import library
    from evolvekit.harness.sdk import SDK_PATH
    for path in (server.PAGE, dashboard.PAGE, SDK_PATH, library.AGENTS_TEMPLATE):
        assert path.is_file() and resources.package_root() in path.parents

def test_find_harnesses_offers_the_bundles_built_ins_when_frozen(tmp_path, monkeypatch):
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app", harnesses=("demo-tour",))
    entries = find_harnesses(tmp_path / "my home")
    assert [(e.harness.id, e.built_in) for e in entries] == [("demo-tour", True)]
    assert entries[0].path == bundle / "_internal" / "harnesses" / "demo-tour"

def test_skills_come_from_package_root_when_frozen(tmp_path, monkeypatch):
    from evolvekit import skills
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app", skills=True)
    extra = bundle / "_internal" / "evolvekit" / "skills" / "evolvekit-extra"
    extra.mkdir()
    (extra / "SKILL.md").write_text("---\nname: evolvekit-extra\ndescription: one line\n---\n", encoding="utf-8")
    assert "evolvekit-extra" in skills.available()
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_resources.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'evolvekit.resources'`.

- [ ] **Step 3: Implement `evolvekit/resources.py`** with the Task 1 names above, a module docstring that says what "frozen" is (PyInstaller one-folder: `sys.frozen`, `sys._MEIPASS` = `<folder>\_internal`, `sys.executable` = the exe that runs) and that every lookup of package data and every "evolvekit's Python" goes through it.

- [ ] **Step 4: Point the five `__file__` lookups at it** — the constants listed in Produces; `find_harnesses` replaces its `BUILT_IN.is_dir()` / `BUILT_IN.iterdir()` with a local `built_in = builtin_harnesses()`; `new_harness` copies `AGENTS_TEMPLATE`; `skills/__init__.py` drops `from importlib import resources` (the name would now shadow nothing but reads as `evolvekit.resources`) for `from evolvekit.resources import package_root`.

- [ ] **Step 5: Run the task's tests and the neighbours**

Run: `.venv/Scripts/python.exe -m pytest tests/test_resources.py tests/test_skills_content.py tests/test_skills_install.py tests/test_harness_cli.py tests/test_app_server.py tests/test_dashboard.py tests/test_prior.py tests/test_app_detect.py -q -m "not slow"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add evolvekit/resources.py evolvekit/harness/library.py evolvekit/harness/sdk/__init__.py evolvekit/app/server.py evolvekit/dashboard/__init__.py evolvekit/skills/__init__.py tests/test_resources.py
git commit -m "feat: evolvekit.resources -- package data and built-in harnesses from one root, the bundle's when frozen"
```

---

### Task 2: The entry module

**Files:**
- Create: `evolvekit/app/entry.py`, `tests/test_entry.py`

**Interfaces:**
- Consumes: Task 1 (`is_frozen`, `fake_bundle`, `fake_profile`); `cli.main`, `cli._COMMANDS`; `library.default_home`.
- Produces: `entry.main(argv: list[str] | None = None) -> int` (`argv` defaults to `sys.argv[1:]`), and `if __name__ == "__main__": sys.exit(main())` (PyInstaller runs this file as the script of both exes). The rule, on the first argument (D4):
  1. `-m MODULE [args]` — `sys.argv = [MODULE, *args]`, `runpy.run_module(MODULE, run_name="__main__", alter_sys=True)`, return 0; `-m` alone prints `usage: -m MODULE [args]` to stderr and returns 2.
  2. `-c CODE [args]` — `sys.argv = ["-c", *args]`, `exec(compile(CODE, "<string>", "exec"), {"__name__": "__main__", "__builtins__": builtins})`, return 0; `-c` alone → 2.
  3. `--script FILE [args]`, or a first argument whose lower-cased text ends in `.py` — `sys.argv = [FILE, *args]`, `sys.path.insert(0, <FILE's folder, absolute>)`, `runpy.run_path(FILE, run_name="__main__")`, return 0 (python's own `python FILE` behaviour: a sibling module imports); `--script` alone → 2.
  4. A name in `cli._COMMANDS`, or `--version`, `-h`, `--help` — `return cli.main(argv)`.
  5. Anything else, including nothing (`--service`, `--home H`, `--no-browser`, `--port N`, `--clock …`, `stop`, `install-startup`, `uninstall-startup`, `--shortcut`) — the app form: when frozen, the home (`--home`'s value when given, read with a tiny `parse_known_args`, else `default_home()`) is made and becomes the working directory (D5); then `return cli.main(["app", *argv])`.
  - A `SystemExit` raised by the module, script, code or `cli.main` passes through (the exe's exit code is its code).
  - The entry calls `cli.main` and reads `cli._COMMANDS` through the module (`from evolvekit import cli`), at call time, so a test can patch `cli.main`.
  - When frozen, before dispatching: `sys.dont_write_bytecode = True`, and a `sys.stdout`/`sys.stderr` that is `None` (the windowed exe) becomes `open(os.devnull, "w", encoding="utf-8")` (D6).
- The browser: the app form with no `--no-browser` opens it through `cmd_app` as today; a running app for the home is opened, not a second one (`cmd_app`'s `read_running` branch, unchanged).

- [ ] **Step 1: Write the failing tests** (`tests/test_entry.py`; `_NeverStarts` is a class whose `__init__` calls `pytest.fail("a second app was started")`; `_snapshot(folder)` maps each path under `folder`, relative and in posix form, to `(is_dir, size, mtime_ns)`; every test that dispatches restores `sys.argv`, `sys.path` and `sys.dont_write_bytecode` with `monkeypatch.setattr(sys, "argv", ["entry"])`, `monkeypatch.setattr(sys, "path", list(sys.path))`, `monkeypatch.setattr(sys, "dont_write_bytecode", sys.dont_write_bytecode)`, and runs in `monkeypatch.chdir(tmp_path)`)

```python
@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.append(list(argv)) or 0)
    return seen

def test_entry_no_args_runs_app(tmp_path, monkeypatch, calls):
    fake_bundle(monkeypatch, tmp_path / "evolvekit app"); fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    assert entry.main([]) == 0
    assert calls == [["app"]]

@pytest.mark.parametrize("argv, expected", [
    (["doctor", "--json"], ["doctor", "--json"]),
    (["study", "list"], ["study", "list"]),
    (["--version"], ["--version"]),
    (["app", "stop"], ["app", "stop"]),
    (["stop"], ["app", "stop"]),
    (["install-startup"], ["app", "install-startup"]),
    (["--no-browser"], ["app", "--no-browser"]),
    (["--service", "--home", "HOME"], ["app", "--service", "--home", "HOME"]),
    (["--clock", "2026-11-13T17:30", "--clock-speed", "120"], ["app", "--clock", "2026-11-13T17:30", "--clock-speed", "120"]),
])
def test_entry_passthrough(tmp_path, monkeypatch, calls, argv, expected):
    home = str(tmp_path / "my home ë")
    fake_bundle(monkeypatch, tmp_path / "evolvekit app"); fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    swap = lambda items: [home if a == "HOME" else a for a in items]
    assert entry.main(swap(argv)) == 0
    assert calls == [swap(expected)]

def test_entry_m_runs_a_module(tmp_path, monkeypatch):
    (tmp_path / "ek_probe_mod.py").write_text(
        "import json, sys\nopen(sys.argv[-1], 'w', encoding='utf-8').write(json.dumps([__name__, sys.argv[1:]]))\n", encoding="utf-8")
    (tmp_path / "ek_exit_mod.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    out = tmp_path / "out ë.json"
    assert entry.main(["-m", "ek_probe_mod", "a b", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == ["__main__", ["a b", str(out)]]
    with pytest.raises(SystemExit) as stopped:
        entry.main(["-m", "ek_exit_mod"])
    assert stopped.value.code == 3
    assert entry.main(["-m"]) == 2

def test_entry_c_runs_code(tmp_path, monkeypatch):
    out = tmp_path / "c.json"
    code = "import json, sys; open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps([__name__, sys.argv]))"
    assert entry.main(["-c", code, str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == ["__main__", ["-c", str(out)]]

@pytest.mark.parametrize("form", [["--script"], []])
def test_entry_script_runs_a_file(tmp_path, monkeypatch, form):
    folder = tmp_path / "my scripts ë"; folder.mkdir()
    (folder / "helper_ek.py").write_text("WORD = 'beside'\n", encoding="utf-8")
    script = folder / "run me.py"
    script.write_text("import json, sys, helper_ek\n"
                      "open(sys.argv[1], 'w', encoding='utf-8').write(json.dumps([__name__, helper_ek.WORD, sys.argv]))\n",
                      encoding="utf-8")
    monkeypatch.delitem(sys.modules, "helper_ek", raising=False)
    out = tmp_path / "out.json"
    assert entry.main([*form, str(script), str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == ["__main__", "beside", [str(script), str(out)]]

def test_entry_gives_a_windowless_process_streams(tmp_path, monkeypatch):
    fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    monkeypatch.setattr(sys, "stdout", None); monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(cli, "main", lambda argv=None: (sys.stdout.write("x"), sys.stdout.flush(), 0)[-1])
    assert entry.main(["study", "list"]) == 0
    assert sys.dont_write_bytecode is True

def test_second_start_opens_running_app(tmp_path, monkeypatch):
    fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    home = fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern") / "evolvekit"
    write_running(home, pid=os.getpid(), port=8790, url="http://127.0.0.1:8790/?token=k")
    opened = []
    monkeypatch.setattr("evolvekit.dashboard.open_in_browser", opened.append)
    monkeypatch.setattr("evolvekit.app.server.AppServer", _NeverStarts)
    assert entry.main([]) == 0
    assert opened == ["http://127.0.0.1:8790/?token=k"]

def test_home_is_under_profile_when_frozen(tmp_path, monkeypatch):
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    profile = fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    monkeypatch.chdir(bundle)                                   # a double-click starts in the app's folder
    seen = {}
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.update(cwd=Path.cwd(), home=default_home()) or 0)
    assert entry.main([]) == 0
    assert os.path.samefile(seen["home"], profile / "evolvekit") and os.path.samefile(seen["cwd"], profile / "evolvekit")
    assert bundle.resolve() not in seen["home"].resolve().parents

def test_bundle_folder_holds_no_state(tmp_path, monkeypatch, capsys):
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app", harnesses=("demo-tour",), skills=True)
    profile = fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    monkeypatch.chdir(bundle)
    before = _snapshot(bundle)
    wrote = []
    monkeypatch.setattr("evolvekit.dashboard.open_in_browser", lambda url: None)
    monkeypatch.setattr(AppServer, "serve_forever", lambda self: wrote.append((profile / "evolvekit" / "app.json").is_file()))
    assert entry.main(["harness", "list"]) == 0
    assert entry.main(["study", "new", "Zoë's depot", "--harness", "demo-tour", "--template", "tune"]) == 0
    assert entry.main(["skill", "install", "--dir", str(tmp_path / "project")]) == 0
    assert entry.main(["doctor", "--json"]) in (0, 1)
    assert entry.main([]) == 0 and wrote == [True]   # the app ran, its app.json in the home (cmd_app reads port 0 as the default)
    assert _snapshot(bundle) == before
    assert (profile / "evolvekit" / "studies").is_dir() and not (profile / "evolvekit" / "app.json").exists()
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_entry.py -q`
Expected: FAIL — `ImportError: cannot import name 'entry' from 'evolvekit.app'`.

- [ ] **Step 3: Implement `entry.main`** in `evolvekit/app/entry.py` by the rule above; a module docstring naming the two exes (`evolvekit-app.exe`, windowless, for a double-click and the logon task; `evolvekit-cli.exe`, with a console, for the command line and as evolvekit's Python) and that both run this module.

- [ ] **Step 4: Run them to see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_entry.py tests/test_app_service.py -q -m "not slow"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add evolvekit/app/entry.py tests/test_entry.py
git commit -m "feat: the frozen entry -- the app on a double-click, the CLI, and -m / -c / --script as evolvekit's Python"
```

---

### Task 3: evolvekit's Python, everywhere

**Files:**
- Modify: `evolvekit/resources.py` (add the Produces below); and every site of the inventory:

| Site (agent/4-over-time) | Today | Becomes |
|---|---|---|
| `app/detach.py:49-58` `python_for_runs` | `sys.executable`, `python.exe` for `pythonw.exe` | `return python_argv()[0]` (the swap moves into `python_argv`) |
| `app/detach.py:90` launcher | `[python_for_runs(), "-c", _LAUNCHER, …]` | `[*python_code_argv(_LAUNCHER), …]` |
| `app/jobs.py:389` `_launch` | `[python_for_runs(), "-m", "evolvekit.app.job", …]` | `[*python_module_argv("evolvekit.app.job"), …]` |
| `app/service.py:234-235` `service_argv` | `[windowless(python or sys.executable), "-m", "evolvekit", "app", "--service", "--home", H]` | `argv = app_argv() if python is None else [python, "-m", "evolvekit", "app"]`; `[windowless(argv[0]), *argv[1:], "--service", "--home", str(H)]` — frozen: `[<bundle>\evolvekit-app.exe, "--service", "--home", H]`, so `install-startup`'s task XML runs the exe (D8) |
| `cli.py:1351` `_study_open` | `[python_for_runs(), "-m", "evolvekit", "app", "--home", H, "--service"]` | `[*app_argv(), "--home", H, "--service"]` |
| `cli.py:757-765` `--shortcut` | `"<sys.executable>" -m evolvekit app --home "<home>"` | unfrozen `"<python_argv()[0]>" -m evolvekit app --home "<home>"` then `pause`; frozen `start "" "<app exe>" --home "<home>"`; both with `chcp 65001 >nul` as the second line (D9) |
| `app/api.py:338` file picker | `[sys.executable, "-c", _PICK, title, kind]` | `[*python_code_argv(_PICK), title, kind]` |
| `app/detect.py:79` candidates | `[Path(sys.executable)]` first | `[] if is_frozen() else [Path(python_argv()[0])]` (D10) |
| `config.py:214`, `:237` `problem.tree.python` | `sys.executable` | `field(default_factory=lambda: python_argv()[0])`; `data.get("python", python_argv()[0])` |
| `evaluate/stages.py:247` `build_command` | `python or sys.executable` | `python or python_argv()[0]` |
| `evaluate/stages.py:548` `build_argv` `{python}` | `sys.executable` | `python_argv()[0]` (one token; frozen the cli exe, whose entry runs the `.py` after it, D3) |
| `evaluate/stages.py:408`, `:478` import checks | `[sys.executable, "-c", snippet, path]` | `[*python_code_argv(snippet), str(path)]` |
| `harness/execute.py:130` `runner_command` (program) | `[sys.executable, runner, "--app", app]` | `[*python_script_argv(runner), "--app", app]` |
| `harness/check.py:87` program base | `[sys.executable, runner, "--app", app]` | `[*python_script_argv(runner), "--app", app]` |
| `harness/check.py:228` `_interpreter` | `sys.executable` | `python_argv()[0]` |
| `harness/check.py:523` python harness with no `--app` | `sys.executable` | `python_argv()[0]` |
| `harness/probe.py:123` `program_command` | `[sys.executable, app]` for `.py` | `python_script_argv(app)` for `.py` |
| `doctor.py:30` `_python` | `"<version> at <sys.executable>"` | unfrozen `"<version> at <python_argv()[0]>"`; frozen `"bundled (Python <version>)"`, ok (D11) |

  Untouched, and why: `harness/sdk/evk_harness.py:64`, `:808` (runs under the application's Python, standard library, 3.9; frozen, its `[sys.executable, app.py]` is the cli exe and the entry's `.py` rule runs it); `harness/probe.py:31` and `harness/skeleton.py:202` (text inside string literals, run by another interpreter); the agent providers (`providers/*.py`: they start `claude`/`copilot`, never a Python); `harness/compile.py` (writes `{python}` as text, substituted in `build_argv`).
- Modify: `tests/test_app_service.py:215-223` — `test_runs_are_launched_with_python_for_runs` patches `resources.python_argv` (`lambda: ["the-python"]`) instead of `detach.python_for_runs`, and asserts `seen[0][:3] == ["the-python", "-m", "evolvekit.app.job"]`.
- Test: `tests/test_resources.py`, `tests/test_entry.py`

**Interfaces:**
- Consumes: Task 1 (`is_frozen`, `bundle_dir`, `APP_EXE`, `CLI_EXE`); Task 2 (the entry's `-m`/`-c`/`--script`/`.py` forms).
- Produces (`evolvekit/resources.py`):
  - `python_argv() -> list[str]` — unfrozen `[sys.executable]`, or `[<python.exe beside it>]` when it is `pythonw.exe` and that file exists (D16 of project 4, D24 here); frozen `[str(bundle_dir() / CLI_EXE)]` whichever exe runs, or `[sys.executable]` when that file is missing (D2).
  - `python_module_argv(module: str) -> list[str]` — `[*python_argv(), "-m", module]` (the same shape frozen).
  - `python_script_argv(path: str | os.PathLike) -> list[str]` — unfrozen `[*python_argv(), str(path)]`; frozen `[*python_argv(), "--script", str(path)]`.
  - `python_code_argv(code: str) -> list[str]` — `[*python_argv(), "-c", code]`.
  - `app_argv() -> list[str]` — the program that serves the app: unfrozen `[*python_module_argv("evolvekit"), "app"]`; frozen `[str(bundle_dir() / APP_EXE)]` (windowless itself).

- [ ] **Step 1: Write the failing tests**

`tests/test_resources.py`:

```python
ALLOWED_EXECUTABLE = {"resources.py", "harness/sdk/evk_harness.py"}   # the SDK runs under the application's Python

def _sys_executable(node) -> bool:
    return ((isinstance(node, ast.Attribute) and node.attr == "executable" and isinstance(node.value, ast.Name)
             and node.value.id == "sys")
            or (isinstance(node, ast.ImportFrom) and node.module == "sys" and any(a.name == "executable" for a in node.names)))

def test_every_sys_executable_use_goes_through_resources():
    hits = [h for h in _uses(_sys_executable) if h.split(":")[0] not in ALLOWED_EXECUTABLE]
    assert hits == [], "evolvekit's Python is evolvekit.resources.python_argv()"

def test_python_argv_frozen_and_not(tmp_path, monkeypatch):
    here = sys.executable
    script = str(tmp_path / "Zoë Intern" / "run me.py")
    assert resources.python_argv() == [here]
    assert resources.python_module_argv("evolvekit.app.job") == [here, "-m", "evolvekit.app.job"]
    assert resources.python_script_argv(script) == [here, script]
    assert resources.python_code_argv("pass") == [here, "-c", "pass"]
    assert resources.app_argv() == [here, "-m", "evolvekit", "app"]
    bundle = fake_bundle(monkeypatch, tmp_path / "Zoë Intern" / "evolvekit app")
    cli = str(bundle / resources.CLI_EXE)
    assert resources.python_argv() == [cli], "the console exe, also when the windowless one asks"
    assert resources.python_module_argv("evolvekit.app.job") == [cli, "-m", "evolvekit.app.job"]
    assert resources.python_script_argv(script) == [cli, "--script", script]
    assert resources.python_code_argv("pass") == [cli, "-c", "pass"]
    assert resources.app_argv() == [str(bundle / resources.APP_EXE)]
    (bundle / resources.CLI_EXE).unlink()
    assert resources.python_argv() == [str(bundle / resources.APP_EXE)]

def test_python_argv_is_never_pythonw(tmp_path, monkeypatch):
    (tmp_path / "python.exe").write_bytes(b""); (tmp_path / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    assert resources.python_argv() == [str(tmp_path / "python.exe")]

def test_stages_python_is_the_cli_exe_when_frozen(tmp_path, monkeypatch):
    from evolvekit.evaluate.stages import build_argv, build_command
    from evolvekit.harness.probe import program_command
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    cli = str(bundle / resources.CLI_EXE)
    assert build_argv("{python} runner.py --seed {seed}", candidate=tmp_path, inputs=(), out=tmp_path / "o.json", seed=1)[:2] == [cli, "runner.py"]
    assert build_command("{python} -m pip install -q {tree}", tree=tmp_path)[:3] == [cli, "-m", "pip"]
    assert program_command(r"C:\Zoë\solver.py") == [cli, "--script", r"C:\Zoë\solver.py"]
    assert program_command(r"C:\Zoë\solver.exe") == [r"C:\Zoë\solver.exe"]

def test_detect_leaves_out_the_bundled_exe_when_frozen(tmp_path, monkeypatch):
    from evolvekit.app import detect
    monkeypatch.setattr(detect, "_py_launcher", lambda: [])
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    listed = detect.candidates(cwd=tmp_path, home=tmp_path / "user")
    assert not any(Path(p).parent == bundle for p in listed)

def test_doctor_says_python_is_bundled_when_frozen(tmp_path, monkeypatch):
    from evolvekit import doctor
    fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    line = doctor._python()
    assert line["ok"] and line["message"].startswith("bundled (Python ")
```

`tests/test_entry.py`:

```python
def test_install_startup_names_the_exe_when_frozen(tmp_path, monkeypatch):
    bundle = fake_bundle(monkeypatch, tmp_path / "Zoë Intern" / "evolvekit app")
    home = tmp_path / "Zoë Intern" / "evolvekit"; home.mkdir(parents=True)
    ok = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", "")
    assert service.install_startup(home, platform="win32", run=ok, out=lambda line: None, user="PC\\zoe") == 0
    xml = (home / service.TASK_XML).read_bytes().decode("utf-16")
    assert f"<Command>{bundle / resources.APP_EXE}</Command>" in xml
    assert f'<Arguments>--service --home "{home}"</Arguments>' in xml

def test_study_open_starts_the_app_exe_when_frozen(tmp_path, monkeypatch, capsys):
    home = tmp_path / "my home"
    assert cli.main(["study", "new", "Depot", "--harness", "demo-tour", "--home", str(home)]) == 0  # before faking: the checkout's demo-tour
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    started = []
    monkeypatch.setattr("evolvekit.app.detach.spawn_detached", lambda argv, **kw: started.append(argv) or 1)
    monkeypatch.setattr(cli, "OPEN_WAIT_S", 0.2)
    assert cli.main(["study", "open", "depot", "--no-browser", "--home", str(home)]) == 1   # nothing really starts
    assert started == [[str(bundle / resources.APP_EXE), "--home", str(home.resolve()), "--service"]]

def test_jobs_launch_runs_the_cli_exe_when_frozen(tmp_path, monkeypatch):
    from evolvekit.app import jobs
    from evolvekit.app.store import Home
    bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    seen = []
    monkeypatch.setattr(jobs, "_spawn", lambda env, argv, root, run_dir: seen.append(argv) or 4242)
    run_dir = tmp_path / "study" / "runs" / "r1"; run_dir.mkdir(parents=True)
    jobs._launch(Home(tmp_path / "home"), tmp_path / "study", run_dir, [], with_keys=False)
    assert seen[0][:3] == [str(bundle / resources.CLI_EXE), "-m", "evolvekit.app.job"]

@pytest.mark.parametrize("frozen", [False, True])
def test_shortcut_is_crlf_utf8_with_chcp(tmp_path, monkeypatch, capsys, frozen):
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(cli, "_desktop", lambda: tmp_path)
    if frozen:
        bundle = fake_bundle(monkeypatch, tmp_path / "evolvekit app")
    home = tmp_path / "Zoë Intern" / "evolvekit"
    assert cli.main(["app", "--shortcut", "--home", str(home)]) == 0
    raw = (tmp_path / "evolvekit.cmd").read_bytes()
    lines = raw.decode("utf-8").split("\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b"") and lines[1] == "chcp 65001 >nul"
    if frozen:
        assert f'start "" "{bundle / resources.APP_EXE}" --home "{home.resolve()}"' in lines
    else:
        assert f'"{sys.executable}" -m evolvekit app --home "{home.resolve()}"' in lines and "pause" in lines
```

(`--home` is `.resolve()`d by `cmd_app`, so `home.resolve()` is what the file names; patching `os.name` is safe here because `cmd_app` reads it only for `--shortcut` — if the builder finds another read on that path, patch `cli.os` with a namespace instead.)

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_resources.py tests/test_entry.py -q`
Expected: FAIL — `AttributeError: module 'evolvekit.resources' has no attribute 'python_argv'`, and `test_every_sys_executable_use_goes_through_resources` listing the 21 sites of the inventory.

- [ ] **Step 3: Implement the five functions** in `evolvekit/resources.py` (Produces above).

- [ ] **Step 4: Change every site** of the inventory table; `test_runs_are_launched_with_python_for_runs` as described. Import inside functions where the module's top already imports heavily (`from evolvekit.resources import python_argv`); `config.py` needs `field` from `dataclasses`.

- [ ] **Step 5: Run the task's tests and every test the sites touch**

Run: `.venv/Scripts/python.exe -m pytest tests/test_resources.py tests/test_entry.py tests/test_app_service.py tests/test_app_detect.py tests/test_app_jobs.py tests/test_doctor.py tests/test_driver_tree.py tests/test_evaluate.py tests/test_harness_check.py tests/test_harness_code.py tests/test_harness_demo.py tests/test_no_window.py tests/test_config.py tests/test_cli_study.py -q -m "not slow"`
Expected: PASS (unfrozen, `python_argv()[0] == sys.executable`, so every existing expectation of `sys.executable` holds).

- [ ] **Step 6: Commit**

```bash
git add evolvekit tests/test_resources.py tests/test_entry.py tests/test_app_service.py
git commit -m "feat: evolvekit's Python through resources.python_argv -- the cli exe when frozen, for runs, runners, {python}, the launcher, the picker and the logon task"
```

---

### Task 4: The PyInstaller spec and `tasks.py dist`

**Files:**
- Create: `packaging/evolvekit-app.spec`, `packaging/bundle_files.py`, `packaging/README.md`, `tests/test_dist_task.py`
- Modify: `tasks.py` (docstring, `dist`, the dispatch dict), `pyproject.toml` (`[project.optional-dependencies] dist`), `.gitignore` (`/dist/`, `/build/`), `AGENTS.md` (the entry-point line gains `dist`, pending Q1), `README.md` (Quickstart's `tasks.py` block gains `python tasks.py dist   # Windows: the app folder and its zip (packaging/README.md)`)
- Test: `tests/test_resources.py` (`test_frozen_paths_with_spaces_and_unicode`, as the given plan placed it in this task)

**Interfaces:**
- Consumes: Tasks 1–3 (`fake_bundle`, `fake_profile`, `resources.*`); `library.pack_harness`, `install_harness`, `find_harnesses`, `create_study`.
- Produces:
  - `packaging/bundle_files.py` (standard library only; loaded by path, never as `packaging.bundle_files`: `packaging/` has and must keep **no** `__init__.py`, so `import packaging` still finds the PyPI library PyInstaller and pip need, D15):
    - `data_files(root: Path) -> list[tuple[str, str]]` — `(source file, destination folder relative to the bundle's _internal)` for: every file matching the `[tool.setuptools.package-data].evolvekit` globs of `root/pyproject.toml` (read with `tomllib`, so the wheel and the bundle ship one list) at `evolvekit/<its folder>`; every file of `root/harnesses/<id>/` except `__pycache__`, `.pytest_cache` and `*.pyc` (`pack_harness`'s rule, samples and their generators kept) at `harnesses/<id>/<its folder>`; `examples/cli-solver/solver.py` and `README.md` at `examples/cli-solver` (D13). Nothing from `tests/`, `runs/`, `docs/`, `benchmarks/`, other examples or `.superpowers/`.
    - `stdlib_modules() -> list[str]` — sorted: each name in `sys.stdlib_module_names` not starting with `_`, not in `EXCLUDED = {"antigravity", "ensurepip", "idlelib", "lib2to3", "pydoc_data", "test", "this", "turtle", "turtledemo", "venv"}`, with `importlib.util.find_spec(name)` not `None`; and for a package, its submodules (`pkgutil.walk_packages(spec.submodule_search_locations, prefix=name + ".", onerror=lambda _: None)`) except any whose dotted parts include `test`, `tests` or `idle_test` (D14).
  - `packaging/evolvekit-app.spec`: `ROOT = Path(SPECPATH).parent`; `bundle_files` loaded with `importlib.util.spec_from_file_location`; `Analysis([str(ROOT / "evolvekit" / "app" / "entry.py")], pathex=[str(ROOT)], datas=data_files(ROOT), hiddenimports=collect_submodules("evolvekit") + stdlib_modules() + ["tkinter", "tkinter.filedialog"], excludes=["pytest", "_pytest", "pyvrp", "IPython"])`; `pyz = PYZ(a.pure)`; `options = [("u", None, "OPTION"), ("X utf8", None, "OPTION")]` (unbuffered and UTF-8 mode: the frozen interpreter ignores `PYTHONUNBUFFERED`/`PYTHONIOENCODING`, which `jobs._launch` sets, D16 — if this PyInstaller spells the UTF-8 option differently, use its spelling; the smoke test's check 3 decides); `exe_app = EXE(pyz, a.scripts, options, exclude_binaries=True, name="evolvekit-app", console=False, upx=False)`; `exe_cli = EXE(…, name="evolvekit-cli", console=True, upx=False)`; `COLLECT(exe_app, exe_cli, a.binaries, a.datas, name="evolvekit-app", upx=False)`. No icon, no version resource, no signing (D17).
  - `tasks.py`:
    - `project_version() -> str` — `[project].version` of `ROOT / "pyproject.toml"` (`tomllib`).
    - `zip_name(version: str) -> str` — `f"evolvekit-app-{version}-win64.zip"`.
    - `pinned_pyinstaller() -> str | None` — the `X.Y.Z` of `pyinstaller==X.Y.Z` in `[project.optional-dependencies].dist`.
    - `_pyinstaller_version() -> str | None` — `importlib.metadata.version("pyinstaller")`, `None` when not installed (the test patches this: "patched import").
    - `dist() -> int`, in order: not `sys.platform == "win32"` → `error: dist builds the Windows app; on macOS and Linux install with  pipx install evolvekit` on stderr, 2. `_pyinstaller_version()` is `None` → `error: the dist extra is not installed: .venv\Scripts\python.exe -m pip install -e ".[dist]"`, 2. It differs from `pinned_pyinstaller()` → `error: PyInstaller <found> is installed, the dist extra pins <pinned>: …pip install -e ".[dist]"`, 2. `ROOT/dist/evolvekit-app` exists and `shutil.rmtree` raises `PermissionError` → `error: dist\evolvekit-app is in use -- an evolvekit-app.exe or evolvekit-cli.exe from it is still running; stop it (evolvekit-cli.exe app stop) and build again`, 1. Then `subprocess.call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"), str(ROOT / "packaging" / "evolvekit-app.spec")], cwd=str(ROOT))`; non-zero → that code. Then the zip: `ROOT/dist/<zip_name(project_version())>` (an old one removed), `ZIP_DEFLATED`, every file of `dist/evolvekit-app/` as `evolvekit-app/<relative posix path>`; prints `wrote <zip path>: <zip MB:.1f> MB (<folder MB:.0f> MB unpacked)`, 0.
    - The dispatch dict gains `"dist": dist`; the docstring lists `dist`.
  - `pyproject.toml`: `dist = ["pyinstaller==X.Y.Z"]` with a comment: "Building the Windows app (`python tasks.py dist`) only; pinned to a release at least two weeks old when chosen (packaging/README.md). The suite never imports it."

- [ ] **Step 1: Choose the pin** (the controller's ruling): run

```bash
.venv/Scripts/python.exe -c "import json,urllib.request,datetime as d; rel=json.load(urllib.request.urlopen('https://pypi.org/pypi/pyinstaller/json'))['releases']; cut=d.datetime.now(d.timezone.utc)-d.timedelta(days=14); ok=[v for v,fs in rel.items() if fs and v.replace('.','').isdigit() and not any(f.get('yanked') for f in fs) and min(d.datetime.fromisoformat(f['upload_time_iso_8601'].replace('Z','+00:00')) for f in fs)<=cut]; print(max(ok,key=lambda v:tuple(map(int,v.split('.')))))"
```

  Expected: one version, e.g. `6.x.y`. Confirm on `https://pypi.org/pypi/pyinstaller/<that>/json` that `info.requires_python` admits 3.13 (the venv's Python). Write it into `pyproject.toml`'s `dist` extra.

- [ ] **Step 2: Install the extra into the venv** (the ruling's only install): `.venv/Scripts/python.exe -m pip install -e ".[dist]"`. Expected: `Successfully installed … pyinstaller-X.Y.Z …`. Nothing else is installed anywhere.

- [ ] **Step 3: Write the failing tests** (`tests/test_dist_task.py`; `import tasks` works because `conftest.py` puts the checkout on `sys.path`; `bundle_files` is loaded with `importlib.util.spec_from_file_location("evolvekit_bundle_files", ROOT / "packaging" / "bundle_files.py")`; `_never(*a, **kw)` calls `pytest.fail("PyInstaller ran")`, `_raise(exc)` returns a function that raises `exc`)

```python
def _root(tmp_path, monkeypatch):
    shutil.copyfile(tasks.ROOT / "pyproject.toml", tmp_path / "pyproject.toml")
    (tmp_path / "packaging").mkdir(); (tmp_path / "packaging" / "evolvekit-app.spec").write_text("# spec\n", encoding="utf-8")
    monkeypatch.setattr(tasks, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    return tmp_path

def test_dist_task_refuses_without_extra(tmp_path, monkeypatch, capsys):
    _root(tmp_path, monkeypatch)
    monkeypatch.setattr(tasks, "_pyinstaller_version", lambda: None)
    monkeypatch.setattr(tasks.subprocess, "call", _never)
    assert tasks.dist() == 2
    assert 'pip install -e ".[dist]"' in capsys.readouterr().err

def test_dist_task_refuses_a_different_pyinstaller(tmp_path, monkeypatch, capsys):   # "1.0.0" installed: 2, names both versions

def test_dist_task_refuses_off_windows(tmp_path, monkeypatch, capsys):               # platform "linux": 2, "pipx install evolvekit"

def test_dist_zip_name_carries_the_version():
    assert tasks.zip_name("0.3.0") == "evolvekit-app-0.3.0-win64.zip"
    assert tasks.project_version() == evolvekit.__version__

def test_dist_builds_then_zips(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path, monkeypatch)
    monkeypatch.setattr(tasks, "_pyinstaller_version", tasks.pinned_pyinstaller)
    seen = []
    def build(argv, cwd=None):
        seen.append(argv)
        folder = root / "dist" / "evolvekit-app"
        (folder / "_internal" / "evolvekit").mkdir(parents=True)
        for name in ("evolvekit-app.exe", "evolvekit-cli.exe", "_internal/evolvekit/x.txt"):
            (folder / name).write_bytes(b"x")
        return 0
    monkeypatch.setattr(tasks.subprocess, "call", build)
    assert tasks.dist() == 0
    assert seen[0][1:4] == ["-m", "PyInstaller", "--noconfirm"] and seen[0][-1] == str(root / "packaging" / "evolvekit-app.spec")
    target = root / "dist" / tasks.zip_name(tasks.project_version())
    with zipfile.ZipFile(target) as archive:
        assert sorted(archive.namelist()) == ["evolvekit-app/_internal/evolvekit/x.txt", "evolvekit-app/evolvekit-app.exe",
                                              "evolvekit-app/evolvekit-cli.exe"]
    out = capsys.readouterr().out
    assert str(target) in out and " MB" in out

def test_dist_says_a_running_exe_holds_the_folder(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path, monkeypatch)
    (root / "dist" / "evolvekit-app").mkdir(parents=True)
    monkeypatch.setattr(tasks, "_pyinstaller_version", tasks.pinned_pyinstaller)
    monkeypatch.setattr(tasks.shutil, "rmtree", _raise(PermissionError(32, "in use")))
    monkeypatch.setattr(tasks.subprocess, "call", _never)
    assert tasks.dist() == 1
    assert "app stop" in capsys.readouterr().err

def test_bundle_data_files_cover_package_data_and_built_ins():
    files = {Path(dest, Path(src).name).as_posix() for src, dest in bundle_files.data_files(tasks.ROOT)}
    for wanted in ("evolvekit/app/static/app.html", "evolvekit/app/static/fonts/Overpass-Variable.woff2",
                   "evolvekit/dashboard/index.html", "evolvekit/harness/sdk/evk_harness.py",
                   "evolvekit/harness/templates/AGENTS.md", "evolvekit/skills/shared/evolvekit.md",
                   "harnesses/demo-tour/harness.yaml", "harnesses/demo-tour/samples/town-40.json",
                   "harnesses/pyvrp/kit/solving.py", "harnesses/prompt-tune/runner.py", "examples/cli-solver/solver.py"):
        assert wanted in files
    assert not any("__pycache__" in f or f.endswith(".pyc") or f.startswith(("tests/", "runs/", "docs/")) for f in files)

def test_bundle_stdlib_modules_leave_out_tests_and_idle():
    names = set(bundle_files.stdlib_modules())
    assert {"sqlite3", "csv", "json.decoder", "email.mime.text", "statistics"} <= names
    assert not any(n.split(".")[0] in bundle_files.EXCLUDED or {"test", "tests", "idle_test"} & set(n.split(".")) for n in names)
    if importlib.util.find_spec("tkinter") is not None:
        assert "tkinter.filedialog" in names
```

`tests/test_resources.py`:

```python
def test_frozen_paths_with_spaces_and_unicode(tmp_path, monkeypatch, capsys):
    source = tmp_path / "work" / "demo-tour"
    shutil.copytree(CHECKOUT / "harnesses" / "demo-tour", source, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("runner.py", "harness.yaml"):                    # packed from a CRLF checkout (core.autocrlf=true)
        path = source / name
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    (tmp_path / "Downloads ë").mkdir()
    zipped = pack_harness(source, tmp_path / "Downloads ë")
    bundle = fake_bundle(monkeypatch, tmp_path / "Zoë Intern" / "Apps" / "evolvekit app", harnesses=("prompt-tune",))
    profile = fake_profile(monkeypatch, tmp_path / "Users" / "Zoë Intern")
    home = default_home()
    assert os.path.samefile(home.parent, profile)
    installed = install_harness(zipped, home)
    assert installed.root == home / "harnesses" / "demo-tour-1.1.0"
    entries = {e.harness.id: e for e in find_harnesses(home)}
    assert not entries["demo-tour"].built_in and entries["prompt-tune"].built_in
    assert bundle in entries["prompt-tune"].path.parents
    assert main(["study", "new", "Zoë's depot", "--harness", "demo-tour", "--template", "tune", "--json"]) == 0
    slug = json.loads(capsys.readouterr().out)["slug"]
    runner = home / "studies" / slug / "harness" / "runner.py"
    assert runner.is_file()
    assert resources.python_script_argv(runner) == [str(bundle / resources.CLI_EXE), "--script", str(runner)]
```

- [ ] **Step 4: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dist_task.py tests/test_resources.py -q`
Expected: FAIL — `AttributeError: module 'tasks' has no attribute 'dist'`, and `FileNotFoundError` for `packaging/bundle_files.py`.

- [ ] **Step 5: Implement** `packaging/bundle_files.py`, `tasks.py`'s functions, and the spec (Produces above). `test_frozen_paths_with_spaces_and_unicode` needs no new code; if it fails, the defect is in Tasks 1–3's code and is fixed there.

- [ ] **Step 6: Run them to see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_dist_task.py tests/test_resources.py tests/test_entry.py -q`
Expected: PASS. Also `.venv/Scripts/python.exe -m ruff check --select F,E9 tasks.py packaging tests` → `All checks passed!`

- [ ] **Step 7: Build for real, alone** (no demo study, no gate running): `.venv/Scripts/python.exe tasks.py dist`
Expected: last line `wrote C:\Users\norri\studio\evolvekit\dist\evolvekit-app-0.3.0-win64.zip: <n> MB (<m> MB unpacked)`. Then, from a plain terminal:
  - `dist\evolvekit-app\evolvekit-cli.exe --version` → `evolvekit 0.3.0`
  - `dist\evolvekit-app\evolvekit-cli.exe -c "import sys, sqlite3, csv, tkinter.filedialog; print(sys.flags.utf8_mode, sys.stdout.encoding)"` → `1 utf-8`
  - `dist\evolvekit-app\evolvekit-cli.exe doctor` → a `✓ python: bundled (Python 3.13.…)` line; exit 0 or 1.
  - `dist\evolvekit-app\evolvekit-cli.exe harness list` → `demo-tour`, `prompt-tune`, `pyvrp`, each `built-in`, each path under `dist\evolvekit-app\_internal\harnesses`.
  A hidden import the build missed shows as `ModuleNotFoundError` here or in Task 6's smoke test: add it to the spec's `hiddenimports` with a comment naming who imports it. If Defender quarantines or blocks an exe, stop and report it to the controller: no exclusion is made (the ruling).

- [ ] **Step 8: `packaging/README.md`** — what `tasks.py dist` makes and where; the pin and how to choose the next one (Step 1's command; the two-week rule); what the bundle holds and leaves out (`bundle_files.py`); the two exes and why (windowless for a double-click and the logon task, console for the command line and as evolvekit's Python); interpreter options; that `packaging/` must never get an `__init__.py`; that the build is never part of `tasks.py check` and that `tests/dist_smoke.py` proves it (Task 6).

- [ ] **Step 9: Commit**

```bash
git add packaging tasks.py pyproject.toml .gitignore AGENTS.md README.md tests/test_dist_task.py tests/test_resources.py
git commit -m "feat: python tasks.py dist -- the PyInstaller one-folder app (evolvekit-app.exe, evolvekit-cli.exe) and evolvekit-app-<version>-win64.zip"
```

---

### Task 5: Stop the app, from Settings

**Files:**
- Modify: `evolvekit/app/static/js/pages/settings.js` (`draw`, `acts`), `tests/test_app_page.py` (`test_every_route_the_old_page_used_is_still_reachable`'s `unused` set loses `"/api/shutdown"`), `docs/app.md` (`## Starting it`: one sentence on stopping)
- Test: `tests/test_app_page.py`

**Interfaces:**
- Consumes: `POST /api/shutdown` (project 4: key-guarded, answers `{"stopping": true}`, the app then ends cleanly and removes `app.json`); `EK.post`, `EK.button`, `EK.notice`, `EK.mount`.
- Produces: at the end of the Settings page, before the Library folder link, a sub-section `<h2 class="type-heading">This app</h2>`, the sentence `The app runs on this computer until you stop it. Studies that are running carry on.` and `EK.button("Stop the app", { kind: "secondary", act: "stop-app" })`. `stop-app` posts `/api/shutdown`; on its answer the page mounts, in place of everything, `EK.notice("info", "The app has stopped. Studies that are running carry on. To open it again, start evolvekit-app.exe, or run evolvekit app.")`, and no poll or request follows (the page's `gen` is advanced, or the router is told the app is gone — whichever the page's own mechanism is); a failed post is `EK.fail`. No confirmation dialog: stopping loses nothing, and starting again reopens every study.

- [ ] **Step 1: Write the failing test**

```python
SETTINGS = (STATIC / "js" / "pages" / "settings.js").read_text(encoding="utf-8")

def test_settings_offers_stop_the_app():
    assert '"Stop the app"' in SETTINGS and "/api/shutdown" in SETTINGS
    assert "Studies that are running carry on." in SETTINGS
    assert "start evolvekit-app.exe, or run evolvekit app" in SETTINGS
```

and remove `"/api/shutdown"` from `unused` in `test_every_route_the_old_page_used_is_still_reachable`.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_page.py -q -k "stop_the_app or still_reachable"`
Expected: FAIL — `assert '"Stop the app"' in SETTINGS`, and `POST /api/shutdown: nothing in the page calls it`.

- [ ] **Step 3: Implement** the section and the act in `settings.js`.

- [ ] **Step 4: Run the page tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_page.py -q`
Expected: PASS (including `test_every_script_parses` and `test_no_banned_word_reaches_the_screen` over the new strings).

- [ ] **Step 5: Look at it once in the real app** — `.venv/Scripts/python.exe -m evolvekit app --home "<scratch>\my home" --no-browser`, open the printed address in the browser pane, Settings: the section reads as above at 1280 px and at phone width; press Stop the app: the notice shows and the terminal's app has ended (`app.json` gone). Nothing is left running.

- [ ] **Step 6: Commit**

```bash
git add evolvekit/app/static/js/pages/settings.js tests/test_app_page.py docs/app.md
git commit -m "feat(app): Stop the app in Settings -- the windowless app of the installer can be stopped, and updated"
```

---

### Task 6: The smoke test, the docs, the gate, the PR

**Files:**
- Create: `tests/dist_smoke.py`, `docs/superpowers/reviews/2026-11-08-installer-gate-log.md`
- Modify: `README.md` (`## Harnesses and studies`: new `### For planners: the installer` after `### The app`), `docs/app.md` (`## Starting it`: `### From the installer (Windows)`; the `--shortcut` row), `docs/superpowers/reviews/2026-11-07-agent-path-build-log.md` (project 5's entries)

**Interfaces:**
- Consumes: the zip of Task 4; every task's behaviour.
- Produces: `tests/dist_smoke.py`, run by hand: `.venv/Scripts/python.exe tests/dist_smoke.py [ZIP] [--keep]` (ZIP defaults to the newest `dist/evolvekit-app-*-win64.zip`; none → `run python tasks.py dist first`, exit 2; not Windows → exit 2). Not collected by pytest (no `test_` prefix); passes ruff F, E9. Exit 0 and `smoke: all 13 checks passed` as its last line, else exit 1 at the first failed check, named, with the command's output.
  - `python_free_path(path: str) -> str` — the `os.pathsep` entries of `path` without any folder that holds `python.exe`, `python3.exe`, `pythonw.exe` or `py.exe`, nor any `…\WindowsApps` folder (the Store's python aliases).
  - `clean_environment(profile: Path) -> dict[str, str]` — `os.environ` with `USERPROFILE` and `HOME` = `profile`, `HOMEDRIVE`/`HOMEPATH` split from it, `APPDATA` = `profile\AppData\Roaming`, `LOCALAPPDATA` = `profile\AppData\Local`, `TEMP` = `TMP` = `profile\AppData\Local\Temp` (all made), `PATH` = `python_free_path(os.environ["PATH"])`, and `EVOLVEKIT_HOME`, `VIRTUAL_ENV`, `PYTHONPATH`, `PYTHONHOME`, `EVOLVEKIT_NO_DOTENV` removed. Every subprocess gets it, `cwd=profile`, `timeout`s, `encoding="utf-8", errors="replace"`.

- [ ] **Step 1: Write `tests/dist_smoke.py`**, its checks in this order (D25), each printed as `✓ <n> <what> (<seconds> s)`:
  0. Setup: a new folder `tempfile.mkdtemp(prefix="evolvekit smoke ë ")`; the zip extracted there (`zipfile`), so the bundle is `<tmp>\evolvekit-app`; the profile `<tmp>\Users\Zoë Smoke`; `env = clean_environment(profile)`; `shutil.which(name, path=env["PATH"])` is `None` for `python`, `python3`, `py` (else the check fails: the smoke test would not prove anything); `before` = `{relative path: (size, crc32)}` of every file in the bundle. The home is the default one, `profile\evolvekit` — no `--home` anywhere, so the default home is what is tested.
  1. `evolvekit-cli.exe --version` → exit 0, stdout `evolvekit <tasks.project_version()>`.
  2. `evolvekit-cli.exe -c "import sys; print(sys.executable)"` → the bundle's `evolvekit-cli.exe` (`os.path.samefile`).
  3. `evolvekit-cli.exe -c "import sys; print(sys.flags.utf8_mode, sys.stdout.encoding)"` → `1 utf-8`.
  4. `evolvekit-cli.exe doctor --json` → exit 0 or 1; a JSON list whose every item has exactly the keys `ok`, `name`, `message`, `fix`; the `python` item's message starts with `bundled`; the `home` item names `profile\evolvekit`.
  5. `evolvekit-cli.exe harness list` → lines for `demo-tour`, `prompt-tune` and `pyvrp`, each `built-in`.
  6. `evolvekit-app.exe --no-browser` started with `subprocess.Popen(..., creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)`; within 15 s `profile\evolvekit\app.json` exists and names a `url` with a `token` (fails with "no app.json within 15 s — an antivirus scan of a new exe can take longer; run it again" when not).
  7. GET `<url's origin>/api/home` with header `X-Evolvekit-Token: <token>` → 200, a JSON object with `studies` (a list) and `harnesses` (three built-ins).
  8. `evolvekit-cli.exe study list` → exit 0.
  9. A demo-tour study through the exe alone: `evolvekit-cli.exe study new "Smoke tour" --harness demo-tour --template tune --app <bundle>\_internal\examples\cli-solver\solver.py --json` → its `slug`; `… study cases <slug> --add <the four bundle samples> --hold-back 1`; the small plan of the suite's `_ready` written into the home with `Home(profile / "evolvekit").save_step(slug, …)` (`time_per_case_s` 0.5, `retries` 1, `runs_per_case` 1, `budget.hours` 0.05, AI off, `plan` `auto: False, children: 2, generations: 2`) — preparation, not a run; `… study start <slug> --json` → `started` true; `… study wait <slug> --json --timeout 900` → exit 0.
  10. The verdict: the JSON of check 9's `wait` has `decision` in `evolvekit.app.verdict.DECISIONS` and a non-empty `line`, printed as `verdict: <line>` — the only claim the smoke test makes. `… study verdict <slug>` → exit 0.
  11. `evolvekit-cli.exe app stop` → exit 0 and `stopped the app`; `app.json` is gone; the Popen of check 6 has ended within 15 s.
  12. No process left from the bundle: `powershell -NoProfile -Command "@(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '<bundle>\*' }).Count"` → `0`.
  13. The bundle holds no state: `{relative path: (size, crc32)}` of the bundle again equals `before` (nothing added, removed or changed); `profile\evolvekit\studies\<slug>\runs\` holds the run.
  - `finally`, whatever failed: `evolvekit-cli.exe app stop`, then the Popen killed if alive, then (unless `--keep`) the temp folder removed (`shutil.rmtree(..., ignore_errors=True)`); with `--keep` its path is printed.
  - The last lines: the zip's name and size in MB, and `smoke: all 13 checks passed` — pasted into the PR body.

- [ ] **Step 2: Build and run it, alone** (nothing else running: no gate, no demo study): `.venv/Scripts/python.exe tasks.py dist`, then `.venv/Scripts/python.exe tests/dist_smoke.py`
Expected: thirteen `✓` lines, a `verdict: …` line, `smoke: all 13 checks passed`, exit 0. A failure in the bundle (a hidden import, a path) is fixed in Task 4's spec or Tasks 1–3's code, with a test where the suite can hold one, and the build and the smoke test run again. Then `.venv/Scripts/python.exe -m ruff check --select F,E9 tests/dist_smoke.py` → `All checks passed!`

- [ ] **Step 3: README — `### For planners: the installer`**, in the app's words, a few short paragraphs and one list:
  - Get `evolvekit-app-<version>-win64.zip` from whoever builds evolvekit for your team (`python tasks.py dist` makes it; no service builds it). Right-click → *Extract All* into a folder of your own (Documents is fine; a network drive or a read-only folder works too, since nothing is written there).
  - Double-click `evolvekit-app.exe`. The exe is not signed, so Windows SmartScreen may say *Windows protected your PC*: choose *More info*, then *Run anyway* — only for a zip you got from your team. An antivirus may scan it for a few seconds the first time.
  - The browser opens at the app. The app has no window of its own: it runs until you stop it in Settings (*Stop the app*); runs carry on when it stops. Double-clicking again opens the running app. If no browser opens, open a terminal in that folder (type `cmd` in Explorer's address bar) and run `evolvekit-cli.exe --no-browser`: it prints the address.
  - Your library is `C:\Users\<you>\evolvekit` (or `EVOLVEKIT_HOME`), never the app's folder.
  - A harness from a colleague (`pyvrp-1.0.0.zip`): drop it on Home, or *Add a harness or zip*. The application is still your own: for PyVRP, the Python that has PyVRP installed — Set up lists the ones it finds, or paste its path. The demo solver is in the app's folder at `_internal\examples\cli-solver\solver.py`. demo-tour and other `program` harnesses need no Python at all.
  - The command line: every `evolvekit …` command in this README works as `evolvekit-cli.exe …` in that folder.
  - Updating: stop the app (Settings → *Stop the app*), then extract the new zip over the old folder (or into a new one); studies, harnesses and settings stay in your library. Removing: delete the folder (first `evolvekit-cli.exe app uninstall-startup` if you made it start at logon).
  - macOS and Linux: `pipx install evolvekit`, then `evolvekit app`.
  Nothing in it says "better".
- [ ] **Step 4: `docs/app.md`** — `### From the installer (Windows)` under `## Starting it`: the same facts in fewer words with a link to the README section; *Stopping it*: Settings → *Stop the app*, or `evolvekit app stop`; the `--shortcut` row: "with the installer, the shortcut starts `evolvekit-app.exe`".
- [ ] **Step 5: The docs' own checks** — `.venv/Scripts/python.exe -m pytest tests/test_app_page.py tests/test_skills_content.py -q` → PASS.
- [ ] **Step 6: The persona gate (design §8, project 5)**

  Materials, made by the controller (never in the evolvekit checkout), per the ruling — no Windows account is created:
  - The zip of Step 2 and `pyvrp-1.0.0.zip` made with `.venv/Scripts/python.exe -m evolvekit harness pack harnesses/pyvrp --out <folder>`.
  - A template profile `C:\gate\p5\template\Users\Zoë Intern\` with `Desktop`, `Documents`, `Downloads` (the two zips), `AppData\Roaming`, `AppData\Local\Temp`. Each round starts by deleting `C:\gate\p5\Users\` and copying the template there (and by building the zip again when code changed).
  - `C:\gate\p5\as-intern.cmd`, UTF-8 without BOM, CRLF:

    ```
    @echo off
    chcp 65001 >nul
    set "USERPROFILE=C:\gate\p5\Users\Zoë Intern"
    set "HOME=%USERPROFILE%"
    set "HOMEDRIVE=C:"
    set "HOMEPATH=\gate\p5\Users\Zoë Intern"
    set "APPDATA=%USERPROFILE%\AppData\Roaming"
    set "LOCALAPPDATA=%USERPROFILE%\AppData\Local"
    set "TEMP=%LOCALAPPDATA%\Temp"
    set "TMP=%TEMP%"
    set "PATH=C:\Windows\System32;C:\Windows\System32\Wbem;C:\Windows\System32\WindowsPowerShell\v1.0"
    set "EVOLVEKIT_HOME="
    set "VIRTUAL_ENV="
    set "PYTHONPATH="
    set "PYTHONHOME="
    cd /d "%USERPROFILE%"
    %*
    ```

    (`C:\Windows` itself is left out of `PATH`: it holds `py.exe`.) The controller first checks it: `C:\gate\p5\as-intern.cmd where python` → not found; `… echo %USERPROFILE%` → the path with `ë` intact.
  - The PyVRP Python: `C:\Users\norri\studio\evolvekit\.venv-pyvrp\Scripts\python.exe` (PyVRP 0.14.0, Python 3.12; the project venv `.venv` has no PyVRP — D26).
  - Before the round the controller records: `schtasks /Query /TN "evolvekit app"` (expected: not found), `Get-Process evolvekit-*` (none), and the file count and newest write time under `C:\Users\norri\evolvekit` (the real home, which must not change).
  - The reviewer is a fresh agent in Claude Code with its working folder `C:\gate\p5\`, the built-in browser, and only `README.md` and `docs/app.md` of the evolvekit checkout as reading. It never sees the code, the design, the plans or earlier reviews.

  The note, verbatim:

  > You are an intern in the planning team of a delivery company. You are not a programmer, and this is a new Windows computer with nothing installed for you except PyVRP, the route planner the team uses. Your manager wants to know whether PyVRP could plan the depot's routes with less driving if its settings were different, and says a tool called evolvekit can find out. IT left two files in your Downloads folder, `C:\gate\p5\Users\Zoë Intern\Downloads`: `evolvekit-app-0.3.0-win64.zip`, the app, and `pyvrp-1.0.0.zip`, which a colleague made so evolvekit can work with PyVRP. PyVRP's Python is `C:\Users\norri\studio\evolvekit\.venv-pyvrp\Scripts\python.exe`. You may read evolvekit's README (its part for planners first) and docs/app.md.
  >
  > You are logged in as Zoë Intern: run every command, and start every program, through `C:\gate\p5\as-intern.cmd` — for example `C:\gate\p5\as-intern.cmd start "" "<the folder you extracted>\evolvekit-app.exe"` is your double-click, and `C:\gate\p5\as-intern.cmd powershell -NoProfile -Command Expand-Archive …` is Windows's *Extract All*. Your browser is the browser pane: a browser the app opens by itself is one you cannot see, so where the docs say a browser opens, do what they say for when it does not.
  >
  > 1. Install evolvekit from the zip and start it.
  > 2. Add PyVRP to it from `pyvrp-1.0.0.zip`.
  > 3. Make a study of PyVRP's settings on its sample delivery requests, with some held back for the final check, kept to about ten minutes; start it and wait for its answer.
  > 4. Find out what it found and whether you should use it. Then stop the app, as you would before going home.
  >
  > Score the whole experience out of 10 for an intern who must install this alone and trust what it says. Name any blocker: something that stopped you or that you would not trust. Say what Windows or the app showed you that worried you, whether anything claimed "better" before the final check did, and where the numbers came from.

  Pass: 8 or more with no blocker. Each round is logged in `docs/superpowers/reviews/2026-11-08-installer-gate-log.md` (the round, the code's commit, the zip's name and size, the score, the blocker, the fixes made, the commits), the transcripts with any token redacted. Fix and repeat until it passes. The controller checks afterwards:
  - **Nothing written into the bundle folder:** every member of the zip is in the extracted folder with the same size and CRC-32, and the folder holds no other file (a short script run with `.venv/Scripts/python.exe -I`, the folder and the zip as arguments).
  - **The home is under the simulated profile:** `C:\gate\p5\Users\Zoë Intern\evolvekit` holds `harnesses\pyvrp-1.0.0\` (the zip installed: listed as installed, not built in) and the study, whose run's `job.json` phase is `done` with a verdict; the real home `C:\Users\norri\evolvekit` has the same file count and newest write time as before.
  - **The runs used the bundle:** while the study runs, `Get-CimInstance Win32_Process -Filter "Name='evolvekit-cli.exe'"` shows a command line with `-m evolvekit.app.job` from the extracted folder, and the solves run under the PyVRP Python.
  - **No scheduled task:** `schtasks /Query /TN "evolvekit app"` → not found (nobody runs `install-startup` in the gate).
  - **The app stopped:** no `app.json` in the home and no `evolvekit-app.exe`/`evolvekit-cli.exe` process. If the reviewer did not stop it, that is a finding, and the controller stops it with `C:\gate\p5\as-intern.cmd "<folder>\evolvekit-cli.exe" app stop`.
- [ ] **Step 7: The gate** — the controller runs `python tasks.py check` (never beside the smoke test, a demo study or the persona gate) → exit 0. The suite never needs PyInstaller.
- [ ] **Step 8: The build log** — project 5's entries in `2026-11-07-agent-path-build-log.md` in the shape of project 4's: the plan, the decisions a reader should know, the gate table, the persona gate table, the PR.
- [ ] **Step 9: Commit and the PR**

```bash
git add README.md docs tests/dist_smoke.py
git commit -m "docs: the installer -- for planners, the smoke test, the gate's log"
git push -u origin agent/5-installer
gh pr create --base master --head agent/5-installer --title "Agent path 5: the installer" --body-file <the PR body: what it adds; PyInstaller as a build-time dependency only (the dist extra, pinned to X.Y.Z, chosen by the two-week rule), no new runtime dependency; the smoke test's output and the zip's size; that the exe is unsigned and SmartScreen was not met in the gate (no Mark of the Web on a local copy); that the gate ran under a simulated profile, not a second Windows account (the controller's ruling), so a defect only a real account shows is for the owner to find; that install-startup with the exe is tested with schtasks patched only; the gate's score; stacked on agent/4-over-time (#75)>
```

---

## Decisions

Choices made where the design was silent, each with its reason and what it costs if wrong:

- **D1 — The shape of `python_argv`.** The design gives `python_argv()` as `[<exe>, "-m"]` / `[<exe>, "--script"]`, which one function cannot be for both. `python_argv()` is the interpreter alone (`[sys.executable]` / `[<cli exe>]`) and the given helpers add the form: `python_module_argv` (`-m`, the same shape frozen), `python_script_argv` (`--script` frozen), and a fourth, `python_code_argv` (`-c`), for the four `-c` uses the design did not list (the detached launcher, the file picker, the two candidate import checks). Cost: one more form in the entry.
- **D2 — evolvekit's Python is the console exe.** Frozen, `python_argv()` is `evolvekit-cli.exe` beside whichever exe runs, the bundle's twin of project 4's D16 ("a run is never `pythonw`"): runs and launchers write to logs, and a windowless process has no streams. `CREATE_NO_WINDOW` (already on every launch) keeps its console hidden. When the cli exe is missing, `sys.executable`. Cost: none.
- **D3 — `{python}` stays one token, and the entry runs a `.py` like python does.** A harness command is `{python} runner.py …`; the SDK (standard library, 3.9, it cannot import evolvekit) starts a `.py` program as `[sys.executable, app.py]`; demo-tour's runner does the same. So frozen, `{python}` is the cli exe's path and the entry runs a first argument ending in `.py` as a script, with its folder first on `sys.path`. Cost: an interpreter flag after `{python}` (`{python} -u x.py`) is not understood frozen, and `{python} -m pip` fails (pip is not bundled); no built-in harness does either.
- **D4 — The entry's rule.** Empty → the app; `-m`/`-c`/`--script`/`*.py` → python's forms; a `cli._COMMANDS` name, `--version`, `-h`, `--help` → `cli.main(argv)`; anything else → `cli.main(["app", *argv])`. Both exes share it, so `evolvekit-app.exe --service`, `evolvekit-app.exe stop`, `evolvekit-cli.exe doctor` and `evolvekit-cli.exe --no-browser` all mean what they say. Cost: a future top-level command named like an app action would be read as the command; none is.
- **D5 — The app form works in the home.** A double-click starts in the bundle folder; frozen, the app form makes the home and changes to it, so nothing relative lands in the bundle and `.env` lookup starts in the home. The CLI forms keep the caller's folder (relative paths are the user's). Cost: detection's "`.venv*` in the working folder" looks in the home for the app form.
- **D6 — Frozen processes write no bytecode, and a windowless one gets streams.** `sys.dont_write_bytecode = True` keeps `__pycache__` out of the bundle; `sys.stdout`/`sys.stderr` that are `None` become `os.devnull`, because CLI code calls `sys.stdout.flush()`. Cost: a frozen script imports a little slower.
- **D7 — The app exe is windowless, and Settings can stop it.** The given plan hides the console for `app`; then nothing stops the app but `evolvekit-cli.exe app stop`, and Windows locks a running exe, so an update over the old folder fails. Task 5 adds *Stop the app* to Settings (the existing key-guarded `POST /api/shutdown`). Cost: a small new section of the page, shown to every user.
- **D8 — The service and `study open` frozen.** `app_argv()` is `[evolvekit-app.exe]` frozen (windowless itself); `service_argv` puts it in the logon task as `<Command>` with `--service --home "<home>"`; `windowless()` leaves a name that is not `python.exe` alone; `study open` starts `[*app_argv(), "--home", H, "--service"]`; `--shortcut` frozen writes `start "" "<exe>" --home "<home>"` without `pause`. Cost: none known; the logon task with the exe is tested with `schtasks` patched only, as in project 4 (D51 there).
- **D9 — `.cmd` files carry `chcp 65001`.** `cmd.exe` reads a `.cmd` in the OEM code page; `evolvekit.cmd` naming `C:\Users\Zoë\evolvekit` would start the app on a mangled path. The file stays UTF-8 and CRLF, with `chcp 65001 >nul` as its second line, in both modes. Cost: none.
- **D10 — Detection frozen leaves out the exe.** The design keeps detection unchanged; its first candidate, "the interpreter running evolvekit", is frozen the exe, which is not a Python an application can be installed in. That candidate is dropped frozen; the rest is unchanged. Cost: none.
- **D11 — `doctor` frozen** says `python: bundled (Python 3.13.…)`, ok; the design's "Python: bundled" in the line's own form.
- **D12 — All three built-in harnesses are bundled** (design §7), with `pack_harness`'s file rule, so a built-in is what its zip would hold. PyVRP is among them: the intern's `pyvrp-1.0.0.zip` then installs over it (an installed copy wins, `find_harnesses`), and Home lists it as installed. Cost: a planner may use the built-in PyVRP without the zip, which makes the gate's step 2 skippable (Q3).
- **D13 — The demo solver is bundled** (`_internal\examples\cli-solver\solver.py`, `README.md`) so demo-tour works from the zip alone, and its `code.repo: ../../examples/cli-solver` keeps pointing at the solver; there is no git repository there, so a code study of the bundled demo-tour is refused as for any folder outside git. Cost: 10 KB; the README names the path.
- **D14 — The whole standard library is bundled.** The design promises "any `program` harness" runs on the exe; a runner may import any standard module. `stdlib_modules()` lists them all (less test packages, IDLE, `turtledemo`, `lib2to3`, `ensurepip`, `venv`, `antigravity`, `this`, `pydoc_data`), plus `tkinter` for the file picker, whose code is a string PyInstaller cannot see. Cost: perhaps 15–25 MB more unpacked.
- **D15 — What the bundle holds is code, not a list in the spec.** `packaging/bundle_files.py` reads the package data from `pyproject.toml` (one list for the wheel and the bundle) and is tested without PyInstaller. It is loaded by path and `packaging/` never gets an `__init__.py`: a regular package named `packaging` on `sys.path` would shadow the PyPI library PyInstaller and pip import. Cost: none.
- **D16 — Interpreter options in the exe:** unbuffered and UTF-8 mode, because the frozen interpreter ignores the `PYTHONUNBUFFERED`/`PYTHONIOENCODING` that `jobs._launch` sets, and a run's log would otherwise be cp1252 and buffered. The smoke test's check 3 proves it. Cost: none.
- **D17 — No UPX, icon, version resource or signature** (UPX invites antivirus false alarms; signing is ruled out). Cost: the default PyInstaller icon.
- **D18 — The pin is exact** (`pyinstaller==X.Y.Z`): the ruling says "pinned", and an exe is a build to reproduce. `dist` refuses another installed version. Cost: raising it is a deliberate edit with the two-week rule.
- **D19 — `dist`'s edges.** Windows only (exit 2, naming `pipx` elsewhere); the extra checked before anything runs; a clean build into `dist\`, work files in `build\pyinstaller\` (both gitignored); the zip's one top folder is `evolvekit-app\`, so *Extract All* gives one folder; the version from `pyproject.toml`; a folder held by a running exe said in a sentence (exit 1). Cost: none.
- **D20 — `AGENTS.md`'s entry-point line gains `dist`.** It says "never invent others", and the design adds this one; without the edit an agent would be told not to run it. Cost: a line in a file every agent reads (Q1).
- **D21 — Six tasks, not four.** The given Task 2 holds two concerns a reviewer can judge apart: the entry's dispatch (Task 2 here) and moving every `sys.executable` use (Task 3 here, which takes the given `test_python_argv_frozen_and_not` and `test_every_sys_executable_use_goes_through_resources`). The given Task 3 is Task 4; Task 5 is new (D7); the given Task 4 is Task 6. `test_frozen_paths_with_spaces_and_unicode` stays in the spec task, as given, though it tests Tasks 1–3's code. Cost: the task numbers differ from the task-level plan's.
- **D22 — Exe names off Windows** are `evolvekit-app` / `evolvekit-cli`, only so the Linux CI can fake a bundle; no Linux bundle is built.
- **D23 — The `sys.executable` test is an AST walk** with one allowed file besides `resources.py`: the SDK, which runs under the application's Python. String literals (the probe's snippet, the skeleton's templates) are code for another interpreter and are not nodes the walk sees. Cost: none.
- **D24 — `detach.python_for_runs()` stays**, as `python_argv()[0]`; the `pythonw.exe` → `python.exe` swap moves into `python_argv` (unfrozen), so every use of evolvekit's Python keeps project 4's D16. The existing `test_runs_are_launched_with_python_for_runs` now patches `resources.python_argv`.
- **D25 — The smoke test's shape.** It extracts the zip into a fresh folder whose name has a space and `ë`, simulates a profile there (the ruling's variables), passes no `--home` (so the default home is what is tested), strips every Python from `PATH` for its subprocesses and proves it with `shutil.which`, and compares the bundle by size and CRC before and after. The small plan is written with `Home.save_step` from the checkout, as the suite's `_ready` does: preparation, not a run. Cost: it needs the checkout beside it; it is a developer's check.
- **D26 — The gate's PyVRP Python is `.venv-pyvrp`.** The brief said the venv's python has PyVRP; `.venv` has none, and `.venv-pyvrp\Scripts\python.exe` has PyVRP 0.14.0 (the harness wants `>=0.14,<0.15`). A venv's interpreter runs from any profile (its `pyvenv.cfg` names the base by an absolute path). The intern is given the path, as a planner would be by IT.
- **D27 — First run needs no change.** Home's *No studies yet* already offers the harness zip (the drop zone and *choose a zip*), the design's "offers the harness zip install on Home"; the gate judges it.
- **D28 — The version stays 0.3.0**, so the first zip is `evolvekit-app-0.3.0-win64.zip`; a release number is the maintainer's call (Q2).
- **D29 — `harness install`, `skill install` and the rehearsal clock need nothing frozen-specific** beyond `package_root()`: a zip unpacks into the home as today; skills are copied from `_internal\evolvekit\skills`; `--clock`/`--clock-speed` pass through the entry to `cmd_app`, and `clock.json` lives in the home.
- **D30 — No Mark of the Web in the gate.** A zip copied locally carries none, so SmartScreen does not show; adding one would put a prompt the reviewer cannot answer in its way. The README's SmartScreen paragraph is unverified by the gate, and the PR says so.

## Open questions for the controller

- **Q1 —** May the builder edit `AGENTS.md` (D20) to add `dist` to its entry-point line? It is the project's instruction file for every agent; the plan assumes yes.
- **Q2 —** Should the first installer zip carry a new version (D28)? The plan keeps 0.3.0.
- **Q3 —** Bundle PyVRP as a built-in (D12, the design's "the built-in harnesses") although the gate's intern is asked to install it from its zip? The plan bundles all three; the alternative is to bundle demo-tour and prompt-tune only, which makes the zip install necessary and the bundle about 1 MB smaller.
