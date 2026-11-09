# Packaging the Windows app

`python tasks.py dist` builds the installed app for Windows: a folder a
planner unzips and double-clicks, with its own Python inside. It runs on
Windows only (on macOS and Linux, install with `pipx install evolvekit`).

## What it makes

| Path | What it is |
|---|---|
| `dist\evolvekit-app\` | the app's folder: `evolvekit-app.exe`, `evolvekit-cli.exe` and `_internal\` (the bundled Python, evolvekit and its data) |
| `dist\evolvekit-app-<version>-win64.zip` | that folder zipped, with `evolvekit-app\` as its one top folder, so *Extract All* gives one folder; the version is `[project].version` of `pyproject.toml` |
| `dist\pyvrp-1.0.0.zip` | the PyVRP harness, packed with `evolvekit harness pack harnesses/pyvrp --out dist` |
| `build\pyinstaller\` | PyInstaller's work files |

`dist\` and `build\` are gitignored; the zips are never committed. Each build
starts clean: the old folder, the old app zip and every old `pyvrp-*.zip` are
removed first. A running `evolvekit-app.exe` or `evolvekit-cli.exe` from
`dist\evolvekit-app\` holds the folder; `dist` then says so and stops (exit
1): stop it (`evolvekit-cli.exe app stop`) and build again.

PyVRP is not bundled: it needs the planner's own Python with PyVRP installed.
A planner who wants it installs `pyvrp-1.0.0.zip` from Home (the drop zone, or
*choose a zip*) and gives the study the path to that Python.

## Setting up

PyInstaller comes only through the `dist` extra, into the project's venv:

```
.venv\Scripts\python.exe -m pip install -e ".[dist]"
.venv\Scripts\python.exe tasks.py dist
```

`dist` checks the extra before anything runs: no exact pin in it ("the dist
extra has no exact pin for pyinstaller"), no PyInstaller, or a version other
than the pin, is an error (exit 2); the last two name the install command above.
Nothing is installed system-wide; the build makes no Defender exclusion,
registry change or signature.

## The pin

The extra pins PyInstaller exactly (`pyinstaller==X.Y.Z`): an exe is a build
to reproduce. The pin is the newest release whose upload is at least two weeks
old on the day it is chosen. The current pin, 6.22.3, was uploaded on
2026-09-12 and chosen on 2026-11-09. To choose the next one, run

```
.venv\Scripts\python.exe -c "import json,urllib.request,datetime as d; rel=json.load(urllib.request.urlopen('https://pypi.org/pypi/pyinstaller/json'))['releases']; cut=d.datetime.now(d.timezone.utc)-d.timedelta(days=14); ok=[v for v,fs in rel.items() if fs and v.replace('.','').isdigit() and not any(f.get('yanked') for f in fs) and min(d.datetime.fromisoformat(f['upload_time_iso_8601'].replace('Z','+00:00')) for f in fs)<=cut]; print(max(ok,key=lambda v:tuple(map(int,v.split('.')))))"
```

check on `https://pypi.org/pypi/pyinstaller/<version>/json` that
`info.requires_python` admits the venv's Python, write it into
`pyproject.toml`'s `dist` extra, install the extra again, and build.

## What the bundle holds

`packaging\evolvekit-app.spec` is the PyInstaller spec; what it bundles
besides evolvekit's code is decided by `packaging\bundle_files.py`, which the
suite tests without PyInstaller (`tests\test_dist_task.py`):

- evolvekit's package data: the globs of `[tool.setuptools.package-data]` in
  `pyproject.toml`, so the wheel and the bundle ship one list
  (`_internal\evolvekit\`);
- the built-in harnesses `demo-tour` and `prompt-tune`
  (`_internal\harnesses\`), with `evolvekit harness pack`'s file rule
  (samples and their generators kept, caches and bytecode left out);
- their applications, so both work from the zip alone:
  `_internal\examples\cli-solver\` (`solver.py`, `README.md`) and
  `_internal\examples\prompt-pipeline\` (`pipeline.py`, `prompts\`, `items\`).
  Each harness's `code.repo: ../../examples/<name>` points at them; there is no
  git repository there, so a code study of a bundled built-in is refused;
- of all these, only the files git tracks (`git ls-files`): nothing
  untracked on the builder's disk (a `.env`, a scratch run, a local output)
  is ever bundled, so the build runs from a git checkout;
- the whole standard library, because a runner may import any of it, less
  the test packages, IDLE, turtle graphics, `lib2to3`, `ensurepip`, `venv`,
  `antigravity`, `this` and `pydoc_data`; and `tkinter` for the file picker,
  whose code is a string PyInstaller cannot see.

Nothing from `harnesses\pyvrp\`, `tests\`, `runs\`, `docs\`, `benchmarks\` or
the other examples is bundled. `pytest`, `pyvrp` and `IPython` are excluded.
No UPX, icon, version resource or signature.

A module the build missed shows as `ModuleNotFoundError` when an exe runs it:
add it to the spec's `hiddenimports`, with a comment naming who imports it.

## Two exes

Both run the same entry, `evolvekit\app\entry.py`:

- `evolvekit-app.exe` is windowless: a double-click starts the app (its home
  under the user's profile, the browser opened), and the logon task starts it
  as the service.
- `evolvekit-cli.exe` has a console: every `evolvekit ...` command works as
  `evolvekit-cli.exe ...`, and it is evolvekit's Python, which runs
  `-m MODULE`, `-c CODE`, `--script FILE` and `FILE.py`. Runs, runners,
  `{python}` in a harness command and the app's helper processes start on it,
  because their output goes to a log and a windowless process has no streams
  to give it. Python's own value-less flags (`-I`, `-E`, `-s`, `-O`, `-u`, …)
  before a form are accepted and ignored, and `multiprocessing` works: the
  entry calls `multiprocessing.freeze_support()` first, so a worker started as
  `evolvekit-cli.exe --multiprocessing-fork …` runs as a worker, and for a
  program run as a file the worker reruns that file as python's would (a
  frozen Windows interpreter otherwise sends a worker no main module).

The windowless `evolvekit-app.exe` has no streams either: what the app says
goes to `<home>/app.log`, as the service's does, and the browser still
opens.

## Interpreter options

The frozen interpreter ignores `PYTHONUNBUFFERED` and `PYTHONIOENCODING`, which
a run's launch sets, so both exes carry the options themselves:
`("u", None, "OPTION")` (unbuffered: a run's log is written line by line) and
`("X utf8", None, "OPTION")` (UTF-8 mode: a path with `ë` survives in output
and logs). `evolvekit-cli.exe -c "import sys; print(sys.flags.utf8_mode,
sys.stdout.encoding)"` prints `1 utf-8`.

## `packaging\` is not a package

`packaging\` has no `__init__.py`, and must never get one: a regular package
named `packaging` on `sys.path` would shadow the PyPI library `packaging`, which
PyInstaller and pip import. The spec and the tests load `bundle_files.py` by
its path.

## Not part of the gate

The build is never part of `python tasks.py check`: it needs PyInstaller and
minutes. `tests\dist_smoke.py` proves a built zip works: it unzips it into a
fresh folder and runs it there under a simulated new profile. Run it alone
(no gate, no study beside it):

```
.venv\Scripts\python.exe tests\dist_smoke.py [ZIP] [--pyvrp-python PATH] [--keep]
```

Its last line is `smoke: all 14 checks passed` (exit 0); a failed check is
named, with the command's output (exit 1).
