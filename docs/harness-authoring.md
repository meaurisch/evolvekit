# Writing a harness

A harness puts one application — a solver, a simulator, any program with
settings — behind evolvekit once, so that studies on it are a matter of
choices: which settings or data to vary, which KPIs count, which cases to use.
This page is for the person (or the AI coding tool) who writes one. Using
harnesses is in the README, under
[Harnesses and studies](../README.md#harnesses-and-studies).

Two harnesses in this repository are the worked examples:

- [`harnesses/demo-tour/`](../harnesses/demo-tour/) — `kind: program`: a
  command-line solver that evolvekit's own Python drives. Small; start here.
- [`harnesses/pyvrp/`](../harnesses/pyvrp/) — `kind: python`: PyVRP in its own
  Python, with request loading, unit conversion, a lever applied in code, and
  drift detection. The full picture.

## Start from something that passes

```
python -m evolvekit harness new my-solver --from demo-tour    # the closest harness, copied
python -m evolvekit harness new my-solver --kind program      # or a runnable skeleton
python -m evolvekit harness new my-solver --kind python
```

Either way the folder gets the current SDK (`evk_harness.py`) and an
`AGENTS.md` for AI coding tools, and its first `harness check` passes. Change
one thing at a time and check again:

```
python -m evolvekit harness check my-solver --app PATH --json
```

`PATH` is the program (`kind: program`) or the Python that has the
application installed (`kind: python`). Every failed check names the key or
hook it is about and says how to fix it. Exit code 0: all pass; 1: warnings
only; 2: failures.

## The folder

```
my-solver/
  harness.yaml      the declaration
  runner.py         the application-specific hooks
  evk_harness.py    the SDK: never edit it (`harness new` refreshes it)
  kit/              helper code for runner.py
  templates/        study templates, one YAML file each
  samples/          three to ten small cases that solve in a second or two
  AGENTS.md         instructions for AI coding tools
  README.md         shown in the app under "About this harness"
```

A study copies the harness into its own folder, without `samples/`, and keeps
that copy: a new version of a harness never changes a study that exists.
Bump `version:` whenever what a harness does changes.

## `harness.yaml`

Loading checks everything below and names the key path of every mistake;
unknown keys are refused.

```yaml
harness: 1                  # the format
id: my-solver               # lower case, digits, dashes
version: 1.0.0
title: My solver
summary: One sentence for the harness list.

application:
  kind: program             # or: python
  label: The solver program
  probe: "{app} --version"  # program: run with a 10 s timeout
  expect: "mysolver (\\d+\\.\\d+)"   # program: group 1 is the version
  # python instead: requires: {module: pyvrp, version: ">=0.14,<0.15"}
  help: Where people find it.

cases:
  label: request            # what a case is called in the app
  formats: [.json]
  describe: What a case file holds.

inputs:                     # optional extra files a study may upload
  solver_settings: {label: Settings to start from, formats: [.json], provides: settings}

time_limit: {accepts: true, default_s: 60, min_s: 1}   # accepts: false if it cannot stop itself
seeds: true                 # it takes a seed
```

**Settings** are the engine's typed parameters (`int`, `float` with
`log: true` where a range spans orders of magnitude, `bool`, `choice`), each
with a `label` and a one-sentence `help` in plain words for people, a longer
`explain` for a model — what it does, which way it trades off, when it
matters — a `group`, and `recommended: true` on the six to ten that matter
most. Every default lies in its range.

**Tables** are the vocabulary of every study (next section). Each column has
a `type` (`int`, `float`, `text`, `bool`), a `unit`, a one-line `describe`,
and `categorical: true` only when its distinct values are safe to show a
model: tags, classes, kinds — never names, addresses or coordinates. The app's
assistant is otherwise told a numeric column's minimum, mean and maximum;
`private: true` withholds even that, which coordinates need.

**Levers** name a request table, the numeric columns they may change, and
their modes: `scale` multiplies today's value, `set` replaces it, `add` adds
to it. A study instantiates a lever with a `where` (an SQL condition over the
table), a mode and a range. `code: true` hands the selected rows to the
runner's `apply_lever` instead: for changes that are not a column edit, such
as widening a time window around its middle.

**KPIs** are SQL returning one number (the first column of the first row;
`NULL` counts as 0 and is noted), or `measure: true` for the runner's
`measure` hook. `direction` is `lower` or `higher`; `positive: true` when every
sensible solution has a value above 0; `changes_with_levers: true` when a
data change alters what it measures (see the comparability trap below).

**KPI templates** are KPIs with blanks, for studies without an AI assistant:

```yaml
kpi_templates:
  late_with_tag:
    label: "Late tasks with tag {tag}"
    direction: lower
    params:
      tag: {type: choice, from: "SELECT DISTINCT tag FROM task_tags ORDER BY tag"}
    sql: "SELECT COUNT(*) FROM visits v JOIN task_tags t USING (task_id) WHERE t.tag = :tag AND v.late_s > 0"
```

A blank is `choice` (listed, or `from` a query over the tables), `number`,
`int` or `text`, and the SQL's `:names` must match the blanks exactly.

**Exports** offer the tuned result for download. The SDK writes four itself —
`settings_json`, `settings_flags`, `data_changes` (JSON and CSV) and
`requests` (the changed cases, through `write_case`) — each marked `for:
settings` or `for: data`; any other name goes to the runner's `export` hook.

**Defaults** are the study wizard's starting values: `time_per_case_s`,
`retries`, `budget_hours`, `test_share`, `runs_per_case`.

## Tables: the vocabulary

A study never sees the application's own data structures. It sees tables, and
writes everything against them:

- **request tables** (`source: request`) show a case as the application
  receives it, built by `read_case`. Levers change their rows. The untouched
  copy is always there too, as `orig_<name>`.
- **solution tables** (`source: solution`) show what the application made of
  it, built by `solution_tables`.

The SQL runs in an in-memory SQLite database that can only be read, and a
query is stopped after 2 seconds. Good tables:

- carry units in their names where a unit exists (`distance_m`, `wait_s`,
  `capacity_kg`), and in the declaration always;
- use the people's words (`tasks`, `routes`), not the solver's (`clients`,
  `ScheduledActivity`);
- give every row a stable id that means the same thing in the request file,
  so a consultant can check a number by hand;
- show money in currency, even when the application computes in something
  else — then convert in `solve` (the PyVRP harness shows costs per km and per
  hour and hands PyVRP integers at a finer unit);
- report both sides when a lever can make them differ: what the solver saw,
  and what it really costs (`routes.solver_cost` and `routes.cost`).

`harness check` fails when the tables the runner produces differ from the
declaration: a missing table, a column that is not declared, one that is
declared and never produced.

## `runner.py`

```python
import evk_harness as evk

def read_case(path): ...                           # -> evk.Case(tables, native=..., summary=...)
def solve(case, settings, time_limit_s, seed): ... # -> the application's solution
def solution_tables(case, solution): ...           # -> {"table": [row, ...], ...}
# optional:
def write_case(case, path): ...                    # the changed case (the "requests" export)
def apply_lever(case, name, rows, value, mode=None): ...   # levers with `code: true`
def measure(case, solution, tables): ...           # KPIs easier in Python than in SQL
def discover(): ...                                # {"version": ..., "settings": {name: {"default": ...}}}
def export(format, values, out): ...               # exports beyond the SDK's four

if __name__ == "__main__":
    evk.main(globals())
```

- It runs under the **application's** Python (`kind: python`) or evolvekit's
  (`kind: program`, where `evk.APP` is the program's path). It never imports
  evolvekit; the SDK is standard library only and runs on Python 3.9 and
  later.
- **`solve` builds the problem from `case.tables`** for every column a lever
  can change: the SDK edits those cells and never your native object. Keep
  whatever else you need in `case.native`.
- `settings` holds every setting — the study's base values with the tuned
  ones on top — and `time_limit_s` is the study's time per case.
- **Keep to the time limit.** A run that has not finished by 1.5 times the
  limit plus 30 seconds is stopped and counts as failed. If the application
  can hang for some settings, run it in a process of its own and stop that
  after the limit, keeping its best answer so far — the PyVRP harness's
  `kit/solving.py` does exactly that.
- `apply_lever` gets the rows (dicts) the lever's `where` selected: change
  them in place. It gets `mode=` (`scale`, `set` or `add`) when it takes a
  `mode` argument.
- **Errors are sentences.** `raise evk.InvalidValues("…")` for a value the
  application cannot take, or a data change that leaves an impossible case —
  it ends the run with exit code 2, before any solving, and the search moves
  on. `evk.CaseError` (exit 3) is a case that cannot be read; anything else is
  exit 1. A failure in `read_case` becomes "cannot read <file>: …"
  automatically. The last line on stderr is what people see.

evolvekit drives the runner with four subcommands, never by hand:

| Subcommand | Does |
|---|---|
| `solve --case F --seed N --values V.json --study S.json [--time-limit T] [--tables-out DB]` | applies the data changes and settings, checks the constraints, solves, builds the tables, computes the KPIs, weighted sums and guardrails, and prints one JSON object as the last line of stdout |
| `inspect --case F` | reads a case and prints a one-line summary |
| `describe` | the SDK and Python versions, and `discover()` |
| `export --format X --values V.json --study S.json --out F [--case F]` | a tuned result in a native format |

## Settings: every value in range must be valid

The search tries values anywhere in a setting's range; a range in which some
values crash the application wastes whole runs. In order of preference:

1. **By construction.** If `min_x` may not exceed `max_x`, give them ranges
   that cannot overlap, or declare `min_x` and `extra_x >= 0` and compute
   `max_x` in `solve`.
2. **A declared constraint** in a study template (`{says, expr}`): the search
   draws again instead of trying a combination that breaks it.
3. **An SQL constraint** on the changed request, for rules that depend on the
   data.

The check solves three random settings vectors to find ranges that break.

## When the application changes

`discover()` reports what the installed application offers (the PyVRP
harness reads PyVRP's parameter classes and operators). `harness check`
compares it with `settings:` and names every setting that appeared,
disappeared or changed its default, with the key to edit. Fix it, bump
`version:`, check again.

## The comparability trap

A lever that changes what the solver *sees* — costs, weights, penalties —
changes every KPI computed from what it saw: a lower `solver_cost` after
halving every cost says nothing about the plan. Mark such KPIs
`changes_with_levers: true`, and give the harness a KPI of the untouched
request for data studies to aim at (the `orig_*` tables hold it). The check
warns when a study template aims a data study at a marked KPI.

## Samples and study templates

`samples/` holds three to ten small cases that solve within a second or two,
with the script that makes them next to them; the check solves up to three.
`templates/<id>.yaml` are partial `study.yaml` files — `title`, `summary`,
`vary`, `constraints`, `kpis`, `goal`, `guardrails` — and loading the harness
compiles every one of them. `vary: {settings: recommended}` tunes the
recommended settings.

## What `harness check` runs

1. The manifest: its schema, every SQL statement against the declared tables,
   every study template compiled; and the comparability trap.
2. The application probe: the program's version, or the Python module and
   its version.
3. `describe`, and drift.
4. On up to three samples: `inspect`; a solve at the defaults; every KPI
   finite; the tables against the declaration; every KPI template with its
   first choice. On the first sample also: every lever at a value in range,
   changing exactly the selected cells (or, in code, at least one row); every
   export.
5. Three random settings vectors.

## Sharing

```
python -m evolvekit harness pack my-solver            # checks the manifest, writes my-solver-1.0.0.zip
python -m evolvekit harness install my-solver-1.0.0.zip
```

`install` unpacks into the library home (`EVOLVEKIT_HOME`, else
`~/evolvekit`), refuses an archive with a path that would land outside its
folder, and checks the manifest.
