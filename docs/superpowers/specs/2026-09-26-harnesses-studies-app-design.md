# Harnesses, studies and the evolvekit app: design

- **Date:** 2026-09-26
- **Status:** approved in conversation, section by section. This document is the written record for review.
- **Branches:** `harness/1-engine` → `harness/2-harness` → `harness/3-app` (stacked PRs against `master`).

## 1. Why

evolvekit already tunes the configuration of a program without anyone writing code: it has
`problem.parameters`, one run per case, `timeout`, `retries`, search with or without a model,
`confirm` and `export`. Setting that up still takes an expert, though. Somebody has to write the
wrapper around the application, write the YAML by hand, and read an expert-grade dashboard.
Consultants, juniors and interns cannot do that.

This design separates the part that needs an expert from the part that does not:

- **An expert writes a harness once per application** (PyVRP, say). The harness holds all the
  plumbing, plus a vocabulary of data tables, catalogues and templates.
- **A consultant makes a study per analysis.** The study says what may change, under which
  constraints, what counts as success, and how much time to spend. It is made in a local app,
  with an assistant that turns plain language into checked definitions.
- **evolvekit runs the study** and reports the result in plain words, checked on cases the
  search never saw.

## 2. Who does what

| Role | Makes | With | Needs to know |
|---|---|---|---|
| Expert (a developer with an AI coding tool) | a **harness** per application | `evolvekit harness new / check / pack`, `AGENTS.md`, Copilot CLI or Claude Code | Python, the application |
| Consultant, junior, intern | a **study** per analysis | `evolvekit app`: the study wizard and the assistant | what they want to find out |
| evolvekit | the run, the final check, the result | the engine (unchanged except for §6) | nothing about the application |

Consultants never create harnesses. That stays a possible end goal; §8 leaves room for the
assistant to draft harness code later.

## 3. Concepts and words

| Term | Meaning | Word used in the app |
|---|---|---|
| Harness | Plumbing plus vocabulary for one application. A folder, or a `.zip` for sharing. | harness |
| Study | One analysis on top of a harness, kept in one self-contained folder. | study |
| Study template | A pre-filled study that ships with a harness ("Tune solver settings"). | template |
| Case | One input request, i.e. one instance file. | case |
| Training set / test set | The cases the search learns from / the cases held back for the final check. | training set / test set |
| Tunable | One thing the search varies: a solver setting, or a lever applied to some rows. | "what may change" |
| Lever | A kind of data change the harness allows, e.g. "scale vehicle-type costs". | data change |
| KPI | One number measured per case on the solution (and the request). | KPI |
| Goal | What the search optimises: one KPI, a weighted sum, or levels in order of importance. | goal |
| Guardrail | A KPI condition that must hold on every case. | must hold |
| Constraint | A condition on the tuned values, checked before any solving. | constraint |
| Round | A generation of the search. | round |
| Starting point | The seed candidate: the study's starting values. | starting point |

## 4. The harness

### 4.1 Layout

```
pyvrp/                       a harness; zipped as pyvrp-1.0.0.zip for sharing
  harness.yaml               declaration: application, tables, catalogues, templates, defaults
  runner.py                  the application-specific hooks (§4.5)
  evk_harness.py             the harness SDK, copied in (standard library only, versioned)
  kit/                       helper code of the harness (PyVRP: request loading, schedules)
  templates/                 study templates, one YAML per template
  samples/                   a few small cases for a first try
  AGENTS.md                  instructions for AI coding tools that edit this harness
  README.md                  optional; shown in the app under "About this harness"
```

A harness is self-contained and pinned. A study copies its harness in (without `samples/`), so
an updated harness never changes a study that already exists; the app offers the update
instead.

### 4.2 `harness.yaml`

```yaml
harness: 1                              # format version
id: pyvrp                               # lower-case letters, digits, dashes
version: 1.0.0
title: PyVRP
summary: Vehicle routing with PyVRP 0.14: tune its settings, or the data you give it.

application:
  kind: python                          # python | program
  label: Python with PyVRP installed
  requires: {module: pyvrp, version: ">=0.14,<0.15"}
  # kind: program instead declares
  #   probe: "{app} --version"          # run with a 10 s timeout
  #   expect: "mysolver (\\d+\\.\\d+)"  # the first group is the version

cases:
  label: delivery request
  formats: [.json, .vrp, .txt]
  describe: A PyVRP request in JSON (see README.md) or a VRPLIB instance file.

inputs:                                 # extra files besides the cases; optional unless required
  solver_settings: {label: Solver settings to keep fixed, formats: [.json],
                    describe: "Settings to start from. PyVRP's defaults if absent."}

time_limit: {accepts: true, default_s: 60, min_s: 1}   # the application stops itself; else accepts: false
seeds: true                             # the application takes a seed

settings:                               # solver settings, passed to the solve hook
  num_neighbours:
    type: int                           # int | float | bool | choice (the engine's types)
    low: 10
    high: 150
    default: 50
    label: Neighbourhood size
    group: Neighbourhood
    help: How many nearby clients each client considers when PyVRP tries a move.
    explain: >
      Size of the granular neighbourhood. Larger values let the local search consider more
      moves per client (better quality per iteration, fewer iterations per second) ...
    recommended: true                   # pre-selected by the "Tune solver settings" template
  # ...

tables:                                 # what the SQL of a study can read (§4.3)
  tasks:
    describe: One row per stop that has to be (or may be) served.
    columns:
      task_id: {type: int, describe: Stable id of the task within the case}
      kind: {type: text, categorical: true, describe: "client, pickup or delivery"}
      tw_early_s: {type: int, unit: s, describe: Earliest start of service, seconds from 06:00}
      # ...
  # task_tags, vehicle_types, vehicle_tags, depots, routes, visits, unassigned, summary

levers:                                 # data changes a study may make (§4.4)
  vehicle_costs:
    label: Vehicle costs
    table: vehicle_types
    columns:
      fixed_cost: {label: cost per vehicle used}
      unit_distance_cost: {label: cost per metre}
      unit_duration_cost: {label: cost per second}
    modes: [scale, set]
    help: Change the costs PyVRP sees for some vehicle types.
    explain: >
      PyVRP minimises the sum of these costs. Changing them changes which plans it prefers,
      not what the plans really cost; measure success with real_cost or deliveries_per_hour.

kpis:                                   # ready-made KPIs
  real_cost:
    label: Real cost
    unit: currency
    direction: lower
    positive: true                      # > 0 on every sensible solution: may be normalised per case
    help: What the plan costs at the rates in the untouched request.
    sql: "SELECT TOTAL(cost) FROM routes"
  solver_cost:
    label: PyVRP objective
    direction: lower
    positive: true
    changes_with_levers: true           # not comparable once a lever changes costs (§4.6)
    sql: "SELECT solver_objective FROM summary"
  # distance_km, duration_h, routes_used, served_tasks, deliveries_per_hour,
  # late_tasks, late_h, wait_h, missed_required, feasible

kpi_templates:                          # KPIs with blanks, for studies without the assistant
  late_with_tag:
    label: "Late tasks with tag {tag}"
    direction: lower
    params:
      tag: {type: choice, from: "SELECT DISTINCT tag FROM task_tags ORDER BY tag"}
    sql: >
      SELECT COUNT(*) FROM visits v JOIN task_tags t ON t.task_id = v.task_id
      WHERE t.tag = :tag AND v.late_s > 0

exports:                                # what the results page offers for download
  settings_json: {label: Solver settings (JSON), for: settings}
  settings_flags: {label: Solver settings as command-line flags, for: settings}
  data_changes: {label: The data changes (JSON and CSV), for: data}
  requests: {label: The changed requests (one file per case), for: data}

defaults:                               # the study wizard's starting values
  time_per_case_s: 60
  retries: 1
  budget_hours: 2
  test_share: 0.3
  runs_per_case: 1
```

Validation happens when the harness is loaded:
- a sentence per mistake, naming its key path;
- unknown keys are refused;
- every setting default lies inside its range;
- every KPI and template SQL is syntactically valid (checked against the declared tables);
- every study template compiles.

### 4.3 Tables: the vocabulary

The harness exposes a case, and the solution for that case, as named tables. Every study
definition (KPIs, data constraints, lever `where` clauses) is written against these tables.

- **Request tables** describe the case as the solver receives it. For PyVRP:
  - `tasks`, `task_tags`, `vehicle_types`, `vehicle_tags`, `depots`.
  - Levers change rows of these tables. `measure` sees both the original and the changed
    version (`orig_*` tables hold the untouched case).
- **Solution tables** describe the plan. For PyVRP:
  - `routes`: one row per route. It includes the vehicle type, start, end, duration, distance
    and overtime, plus the cost at the **original** rates and at the rates the solver saw.
  - `visits`: one row per stop in route order. It includes the task, trip, start and end of
    service, wait, lateness (time warp), load, and the previous and next task.
  - `unassigned`: one row per task not served.
  - `summary`: one row, holding the solver objective, feasibility, runtime and iterations.
- Every column is declared in `harness.yaml`: type, unit, a one-line description, and whether
  it is `categorical` (only categorical columns may be summarised to the assistant, §8).
  `harness check` fails when the tables the runner produces differ from the declaration.

SQL runs in an in-memory SQLite database from Python's standard library:
- an authorizer allows reads only (no `ATTACH`, no `PRAGMA` writes, no writes at all);
- a progress handler stops a query after 2 s;
- a KPI query must return one number: the first column of the first row, with `NULL` taken as
  0 and recorded as such in the run's notes.

### 4.4 Catalogues

- **Settings.** Every setting of the application worth tuning, with its type, range, default,
  plain `help` for people, a longer `explain` for the LLM, a `group`, and whether it is
  `recommended`.
- **Levers.** Each names a table, the columns it may change, and its modes:
  - `scale`: multiply today's value;
  - `set`: an absolute value;
  - `add`: add to today's value.

  A study instantiates a lever as a tunable: `{lever, column, where, mode, low, high, start}`.
  The `where` is an SQL condition over the lever's table ("class = 'van'"), and the number of
  rows it selects is shown in the app. A lever with `code: true` is applied by the runner's own
  `apply_lever` hook instead: an escape hatch for changes that are not a column edit, such as
  widening time windows on both sides.
- **KPIs.** Ready-made measures, written in SQL or computed by the `measure` hook.
- **KPI templates.** SQL with named blanks. A blank's choices can come from the data
  (`from: SELECT DISTINCT ...`). This gives studies without an LLM key specific KPIs through a
  form.
- **Study templates** (`templates/*.yaml`) are partial `study.yaml` files. The PyVRP harness
  ships two:
  - "Tune solver settings";
  - "Route cost sets → deliveries per hour".

### 4.5 The runner and the SDK

`runner.py` holds the application-specific hooks. `evk_harness.py` (the SDK, standard library
only, because the runner runs under the *application's* Python) does everything else.

```python
import evk_harness as evk

def read_case(path):                     # -> evk.Case: tables + whatever native object you need
def solve(case, settings, time_limit_s, seed):   # -> native solution
def solution_tables(case, solution):     # -> dict of solution tables
def write_case(case, path):              # optional: the changed request (for the "requests" export)
def apply_lever(case, name, rows, value):        # optional: levers with `code: true`
def measure(case, solution, tables):     # optional: KPIs that are easier in Python than in SQL
def discover():                          # optional: what the installed application supports (§4.6)
def export(format, values, out):         # optional: exports beyond the SDK's json / flags / data

if __name__ == "__main__":
    evk.main(globals())
```

Subcommands, all run by evolvekit and never by the consultant:

| Subcommand | Does | Used by |
|---|---|---|
| `solve --case F --seed N --values V.json --study S.json [--tables-out T.sqlite]` | Applies levers and settings, checks constraints, solves, builds the tables, computes the KPIs, guardrail violations and weighted sums, and prints one JSON object as the last line of stdout. | every evaluation (the stage command), the preview, the test run |
| `inspect --case F` | Reads the case and returns a summary ("1,200 tasks, 18 vehicle types, 3 depots"), or a sentence saying why it cannot be read. | the Cases step |
| `describe` | The application's version and `discover()` output. | `harness check`, the Application step |
| `export --format X --values V.json --study S.json --out F` | A tuned result in a native format. | the Results page |

`--study S.json` is the compiled static part of a study (§5.4):
- fixed settings and the input settings file's values;
- lever instances, constraints, KPI definitions, weighted sums, guardrails;
- the time limit.

`--values` is evolvekit's `{params_json}`: the tuned values of this candidate.

Output of `solve`, read with `kpis_from: stdout`:

```json
{"kpis": {"real_cost": 123456.0, "late_tag_d": 3, "combo": 4.5, "guardrail_violation": 0.0,
          "solve_s": 20.1},
 "text_feedback": "real cost 123,456; 3 late tasks with tag D; guardrails hold"}
```

Exit codes:

| Code | Meaning |
|---|---|
| 0 | a result was produced |
| 2 | invalid values, or a constraint broken on this case; decided before any solving, one line on stderr naming the constraint |
| 3 | the case cannot be read |
| 1 | anything else |

Errors are one plain sentence on stderr, never a bare traceback. The traceback goes to the full
log.

### 4.6 Authoring tools

- **`evolvekit harness new DIR [--from HARNESS] [--kind python|program]`** copies the closest
  harness, or a skeleton with commented hooks. It writes a fresh `AGENTS.md` and the current
  `evk_harness.py`.
- **`evolvekit harness check DIR [--app PATH] [--json]`**, from cheap to expensive:
  1. The manifest: its schema, and every SQL statement against the declared tables.
  2. The application probe.
  3. `describe`, and drift: settings the installed application has that the harness lacks,
     settings the harness declares that it no longer has, and defaults that changed. Each is
     reported as one sentence naming the key to edit.
  4. For up to three sample cases:
     - `inspect`;
     - a solve at the defaults with a short time limit;
     - every ready-made KPI comes back finite;
     - every KPI template instantiates with its first choice;
     - every lever, applied at a random value in range, changes exactly the selected cells;
     - constraints evaluate and guardrails compute;
     - every export works.
  5. Three random settings vectors solve without error.
  6. **The comparability trap:** it warns when a study template uses a KPI marked
     `changes_with_levers` as its goal while varying levers.

  `--json` prints `{"ok": bool, "checks": [{"id", "ok", "message", "fix"}]}`, so an AI tool can
  iterate until everything passes. The exit code is 0 when all checks pass, 1 for warnings only,
  2 for failures.
- **`evolvekit harness pack DIR`** validates the manifest and writes `<id>-<version>.zip`.
- **`AGENTS.md`** is written for Copilot CLI, Claude Code and Codex. It covers:
  - the contract (§4.5) and the tables' role;
  - "parametrise so every value in range is valid" (constraints by construction first,
    declared constraints second, SQL checks last);
  - the comparability trap;
  - how to add a setting when the application's API changes;
  - to run `harness check --json` until it passes.
- **When the application's API changes**, `harness check` names the drift. Fixing it means
  editing one entry under `settings:` in `harness.yaml`.

## 5. The study

### 5.1 Where things live

```
~/evolvekit/                 the library home: EVOLVEKIT_HOME or `evolvekit app --home`
  settings.yaml              app settings: provider and models for the assistant and AI search,
                             folders, recently used applications
  .env                       API keys for this machine, written by the app, never shown again
  harnesses/<id>-<version>/  installed harnesses
  studies/<slug>/            one folder per study
    study.yaml               every choice the consultant made; relative paths only
    harness/                 the pinned copy of the harness
    cases/                   the uploaded cases
    inputs/                  uploaded extra inputs (a solver settings file ...)
    preview/                 the baseline preview: tables.sqlite, timing, KPIs (§7.2 step 3)
    assistant.jsonl          the assistant conversation and its cost
    runs/<run-id>/           an evolvekit run directory, plus evolvekit.yaml, study-run.json,
                             job.json (§7.1) and confirm/final/ (the final check)
```

The harnesses under `harnesses/` in this repository are found as built-ins when the app runs
from a checkout. Everywhere else they arrive as `.zip` files.

### 5.2 `study.yaml`

```yaml
study: 1
name: Route costs for the March cases
harness: {id: pyvrp, version: 1.0.0}
template: route-costs
application: {path: "C:/work/.venv-pyvrp/Scripts/python.exe", version: 0.14.0}  # re-checked on open

cases:
  training: [cases/a.json, cases/b.json, cases/c.json, cases/d.json, cases/e.json]
  test: [cases/f.json, cases/g.json]
inputs: {solver_settings: inputs/tuned-settings.json}

vary:
  settings:                              # anything not listed: the input file's value, else the default
    num_neighbours: tune                 # the harness range, starting at the default
    penalty_increase: {tune: true, low: 1.1, high: 3.0, start: 1.5}
    exhaustive_on_best: {fixed: false}
  data:
    van_km_cost: {lever: vehicle_costs, column: unit_distance_cost, where: "class = 'van'",
                  mode: scale, low: 0.5, high: 2.0, start: 1.0}
    truck_km_cost: {lever: vehicle_costs, column: unit_distance_cost, where: "class = 'box_truck'",
                    mode: scale, low: 0.5, high: 2.0, start: 1.0}

constraints:
  - {says: Trucks stay at least as dear per metre as vans, expr: "truck_km_cost >= van_km_cost"}
  - {says: No vehicle type ends up below 1 per metre,
     sql: "SELECT COUNT(*) = 0 FROM vehicle_types WHERE unit_distance_cost < 1"}

kpis:
  real_cost: {from: harness}
  deliveries_per_hour: {from: harness}
  late_cold: {template: late_with_tag, params: {tag: cold}}
  wait_after_early:
    says: Waiting after tasks whose window opens between 08:00 and 10:00, in hours
    direction: lower
    unit: h
    sql: >
      SELECT TOTAL(n.wait_s) / 3600.0 FROM visits v JOIN tasks t ON t.task_id = v.task_id
      JOIN visits n ON n.route_id = v.route_id AND n.seq = v.seq + 1
      WHERE t.tw_early_s BETWEEN 7200 AND 14400
  service_penalty:                       # a weighted sum in natural units, computed per case
    says: Late cold tasks, plus half an hour per hour of waiting
    direction: lower
    weighted: {late_cold: 1.0, wait_after_early: 0.5}

goal:
  levels:                                # in order of importance; one level = a plain goal
    - {kpi: deliveries_per_hour, direction: higher, equal_within: "1 %"}
    - {kpi: service_penalty, direction: lower}
guardrails:
  - {kpi: missed_required, max: 0}

limits: {time_per_case_s: 20, retries: 1, runs_per_case: 1}
budget: {hours: 2.0, ai: {enabled: false, max_usd: 2.0}}
plan: {auto: true}                       # or explicit overrides (§5.5)
```

### 5.3 Goals, guardrails and constraints

**Goals come in three forms, and they combine.**

1. **One KPI.** "Lowest real cost". It compiles to the engine's `objective` and `direction`.
   For a KPI marked `positive` in the harness, each case counts equally (`normalize: baseline`,
   every case as a percentage of the starting point). Otherwise the goal is the plain mean over
   cases, i.e. the total.
2. **A weighted sum in natural units.** "1 late cold task counts as much as 2 h of waiting." It
   is a derived KPI that the SDK computes **per case**, and it is always lower-is-better:
   - a lower-is-better component adds `weight × value`;
   - a higher-is-better component subtracts it.

   The app shows the weights as exchange rates between pairs of components. A weighted sum can
   be the goal itself, or one level of a hierarchy.
3. **Levels in order of importance (hierarchical).** For example: first the most deliveries per
   hour, where two plans within 1 % count as equal; then the least waiting; then the lowest
   cost. Semantics: *A is better than B if, at the first level where they differ by more than
   that level's tolerance, A is better.* The last level has no tolerance. Tolerances are
   absolute ("0.5 tasks") or relative ("1 %", of the starting point's value). This compiles to
   the engine's `score.levels` (§6.4).

**Guardrails** hold on **every** case: "no required task missed", "on-time share at least 95 %".
- Per case, the SDK computes each guardrail's relative violation and emits their sum as
  `guardrail_violation`. For `max: x` the violation is `max(0, value − x) / max(|x|, 1)`; for
  `min: x` it is `max(0, x − value) / max(|x|, 1)`.
- The engine gates on it (§6.3): a candidate that breaks a guardrail on any case does not
  compete. It is reported as "broke a guardrail on case B: 2 required tasks missed", not as a
  failure.
- If the starting point itself breaks a guardrail, the test run says so before anything is
  spent (§7.2 step 7).

**Constraints** are about the tuned values, and there are three ways to state them. The
assistant and `AGENTS.md` prefer them in this order:
1. **By construction.** Tune "truck premium ≥ 0" instead of two costs that must stay ordered;
   tune `scale` within ±20 % instead of stating "no more than 20 % from today".
2. **Expressions over tunable names** (`truck_km_cost >= van_km_cost`). They compile to the
   engine's `problem.parameter_constraints` (§6.2) and are checked before any solving. The
   search's samplers draw again rather than propose a combination that breaks one.
3. **SQL checks on the changed request**, for rules that depend on the data. They run in the
   runner before solving: exit code 2, milliseconds, with the constraint's own sentence.

### 5.4 From study to run

The app compiles a study into two files in the run directory:
- `evolvekit.yaml`: an ordinary evolvekit config, readable by experts and runnable with
  `evolvekit run`;
- `study-run.json`: the runner's static input.

| Study | evolvekit config |
|---|---|
| `vary.settings` marked tune, `vary.data` | `problem.parameters`. `help` is the harness's `explain` (the LLM reads it); the default is the starting value |
| fixed settings, input settings file, lever definitions, KPIs, weighted sums, guardrails | `study-run.json`, passed to the runner as a literal path |
| expression constraints | `problem.parameter_constraints` |
| training cases | `instances:` on the command stages |
| test cases | the final check (`confirm --instances`, §7.1), never the search |
| `limits.time_per_case_s` | the runner's `--time-limit` |
| stop-after | the stage `timeout`: time limit × 1.5 + 30 s when the application stops itself, otherwise `time_per_case_s` |
| `limits.retries`, `limits.runs_per_case` | `retries`, `seeds` |
| a goal with one level | `score: {objective: kpi, direction}`; `normalize` per §5.3 |
| a goal with several levels | `score.levels` (§6.4); `normalize: none`; no racing |
| guardrails | `evaluate.gates: [{kpi: guardrail_violation, max: 0}]` |
| `budget.hours` minus the final check's share | `budget.max_hours` (§6.1) |
| `budget.ai` | `models` from the app settings, `rewrite` among the operators, `budget.max_usd` |
| the plan | the stages (`screen` + `full`, or `full` alone), `workers`, `pin_cpus`, `children_per_generation`, `generations`, operators |

Other fixed choices:
- Operators without AI: `{param_local: 0.5, param_tpe: 0.25, param_lhs: 0.15, param_cross: 0.1}`,
  the mix that worked on the PyVRP benchmark.
- With AI: `{param_local: 0.35, rewrite: 0.3, param_tpe: 0.2, param_lhs: 0.15}`.
- `novelty.behavioural: off` for time-limited applications.
- `problem.description` is the harness summary, plus the study's name, its goal in words and its
  constraints in words.

### 5.5 The automatic plan

**Inputs:**
- n training cases, m test cases, r runs per case;
- T, the measured wall time of one run: the preview's run, scaled to the chosen time limit;
- H, the total hours;
- w workers: the physical cores minus one, at least 1 and at most n × r. One logical CPU is
  pinned per physical core, skipping the first core, where the platform can pin (§6 does not
  change pinning; `evolvekit/cpus.py` finds the core layout).

**Arithmetic:**
- **Final check:** C = ⌈2 · m · k / w⌉ · T, with k = 3 check seeds (2 if that does not fit).
- **Search:** S = 0.95 · H − C.
- **Starting-point round:** B = ⌈n · r / w⌉ · T.
- **One round** tries c combinations.
  - When n ≥ 6 and the time limit is at least 10 s, a screening pass first runs every combination
    on q = min(4, ⌈n/3⌉) cases at a quarter of the time limit. Then the best 2 run on everything:
    R = ⌈c·q·r/w⌉ · (T/4) + ⌈2·n·r/w⌉ · T.
  - Otherwise every combination runs on everything: R = ⌈c·n·r/w⌉ · T.
- **Choosing c:** the value in 4…12 that gives at least 6 rounds, else at least 3. With fewer
  than 3 rounds the app blocks the start, with three concrete ways out: fewer training cases, a
  shorter time per case, or more hours.
- **Settings for the engine:**
  - `generations` = ⌈(S − B)/R⌉ + 2;
  - `budget.max_hours` = S / 3600, which is what stops the run;
  - `stop.patience` = the generations, so the run uses the time it is given.
- **Shown to the user:** "about 7 rounds, about 50 combinations tried, a final check on
  3 held-back cases (≈ 40 min); done around 17:40."
- Experts can override every number under "Adjust".

## 6. Engine additions

All four are generic evolvekit features with their own README sections and tests, useful
without the app.

### 6.1 `budget.max_hours` and stop requests

`budget.max_hours` (off by default) is the active run time across sessions: resumed runs count
what they already used.
- **Before each generation:** if elapsed + projected time > the limit, the run stops with "time
  limit: another round would not finish within 2.0 h". The projection is the longest of the last
  two generations. With only the seed done, it is the seed's duration × `children_per_generation`:
  an upper bound when a screening stage exists.
- **When the limit is reached during a generation:** evaluations still running are stopped and
  counted as **abandoned**, not failed. Evaluations not yet started are not started. Candidates
  that did not finish do not compete. The run ends with `run_finished` and stop reason "time
  limit reached"; the best so far is the answer.
- **Stop requests:** a file `stop-request` in the run directory has the same effect with reason
  "stopped on request". The app's Stop button writes it, because a detached process on Windows
  cannot be sent Ctrl+C reliably.
- **Resume:** a stopped run resumes cleanly (`run` with a larger `--generations` or a larger
  `max_hours`); the cut generation is finished first, as after any interruption.
- `status` reports the limit under `health.limits` like every other stopping criterion.

### 6.2 `problem.parameter_constraints`

A list of expressions over parameter names:
- numbers, `+ - * / ** %`, comparisons (chained), `and or not`, `abs min max round`, `True`,
  `False`, and string literals for choices;
- parsed with Python's `ast` against a whitelist;
- config errors: an unknown name, a syntax error, or defaults that break a constraint (the
  starting point must be valid).

Where they are enforced:
- **The static stage** rejects a violating configuration with the constraint's text, and without
  starting the program.
- **The model-free operators** (`param_lhs`, `param_local`, `param_cross`, `param_tpe`) draw
  again, up to 100 times, before giving up. A combination that still violates is left for the
  static stage to reject.
- **LLM operators** see the constraints in the prompt, under the parameter list.

### 6.3 `evaluate.gates`

`evaluate.gates: [{kpi: name, max: x} | {kpi: name, min: x}]` is checked after every command
stage, on the stage's aggregated KPIs.
- **A candidate that breaks a gate** does not compete and is not promoted. Its row carries
  `gated: "<kpi> = 2 > 0"`. `status` and the dashboard list it among "not competing" with that
  reason, never among evaluation failures, and the model-free operators count it as a bad
  observation.
- **If the seed breaks a gate,** the run aborts (exit code 4) with a sentence naming the gate and
  the value.

### 6.4 `evaluate.score.levels`: hierarchical goals

```yaml
score:
  objective: deliveries_per_hour     # still set: level 1, used by reports
  direction: maximize
  levels:
    - {kpi: deliveries_per_hour, direction: maximize, tolerance: 0.01, relative: true}
    - {kpi: service_penalty, direction: minimize}
```

**Semantics:** the lexicographic order of §5.3. A tolerance is required on every level except
the last.

**Implementation:** a finite, stationary scalar anchored at the seed, so every existing consumer
of `score` (ranking, archive, promotion, parent sampling, patience, TPE) keeps working. With the
seed's aggregated level values `b_i`, a candidate's values `v_i`, sign `s_i` (+1 maximise,
−1 minimise) and absolute tolerance `t_i`:

```
q_i  = clamp(round(s_i · (v_i − b_i) / t_i), −K, K)        levels 1 … L−1, K = 10 000
x_L  = 0.5 · tanh(s_L · (v_L − b_L) / scale_L)              scale_L = 10 % of |b_L|, or 1 when b_L = 0
score = Σ_{i<L} q_i · (2K + 1)^(L−1−i)  +  x_L
```

- At most four levels. The integer steps then stay exact in double precision, and the last level
  keeps a resolution finer than 0.001 of its range.
- The seed scores 0. Its level values are stored in `work/levels.json`, like `reference.json`,
  so a resumed run uses the same anchor.
- Documented precisely:
  - differences within a tolerance count as equal *relative to the fixed steps*, so two values
    just either side of a step boundary still count as different;
  - differences beyond K steps saturate, and `status` warns if any candidate saturates.

**Reporting:**
- `status` gets `progress.levels`: per level the KPI, direction, tolerance, baseline, best,
  difference and difference in %. The overall verdict names the deciding level.
- The dashboard's Progress card shows these rows instead of one percentage.
- `confirm` compares level by level (paired, per case and seed, with an interval per level) and
  gives a lexicographic verdict: "equal on level 1 (within 1 %), better on level 2: clear".
- Racing is refused with levels (a config error), since there is no single objective to race on.

## 7. The app

### 7.1 Architecture

- **`evolvekit app [--home DIR] [--port N] [--no-browser] [--shortcut]`** starts a
  `ThreadingHTTPServer` from the standard library, like the dashboard, and opens the browser.
  `--shortcut` writes `evolvekit.cmd` to the Windows desktop: double-click, keep the window open
  while you work, and runs survive closing it.
- **The page** is one HTML file with inline CSS and JS: no build step, no CDN, it works offline.
  It uses the dashboard's colour tokens, in light and dark.
- **Security** follows the dashboard's rules:
  - it binds to `127.0.0.1`;
  - `Host` must be a loopback name;
  - every state-changing request needs a same-origin `Origin`;
  - uploaded file names are sanitised, and every path stays inside the library home;
  - the native file dialog exists only on loopback.
- **Package:** `evolvekit/app/`:

  | File | Holds |
  |---|---|
  | `server.py` | routing and security |
  | `api.py` | the handlers |
  | `store.py` | studies and harnesses on disk |
  | `detect.py` | finding applications |
  | `jobs.py` | detached runs |
  | `assistant.py` | §8 |
  | `static/app.html` | the page |

  The harness format, the SDK source, the study model, compilation and the plan (§5.5) live in
  `evolvekit/harness/`, so `evolvekit study run` works without the app. The core layout is
  `evolvekit/cpus.py`.
- **API.** Everything is JSON, and the page uses nothing else, so a server can take the app's
  place later:

  | Endpoint | Purpose |
  |---|---|
  | `GET /api/home` | studies with a one-line status, harnesses, and whether keys are present |
  | `POST /api/harnesses` (a zip in the body) | install a harness |
  | `GET /api/harnesses/{id}` | a harness's manifest details |
  | `POST /api/studies` | create a study from a template |
  | `GET /api/studies/{slug}` | read a study |
  | `PATCH /api/studies/{slug}` | save a step |
  | `GET /api/detect?harness=` | detected applications |
  | `POST …/application` | probe an application |
  | `POST /api/pick-file` | the native dialog |
  | `PUT …/cases/{name}` (the file in the body) | upload a case |
  | `DELETE …/cases/{name}` | remove a case |
  | `POST …/split {test: 3}` | assign the test set |
  | `POST …/preview` | start the baseline preview solve |
  | `POST …/kpis/try {sql}` | a value and rows on the preview |
  | `POST …/assistant` | ask the assistant |
  | `GET …/plan` | the automatic plan |
  | `POST …/test-run` | the test run |
  | `POST …/start` | start the run |
  | `POST …/stop` | stop it |
  | `GET …/status` | the simplified status |
  | `GET …/results` | the results |
  | `GET …/download/{export}` | a download |
  | `GET /studies/{slug}/runs/{run}/dashboard/` | the existing dashboard, whose relative `api/status` resolves there |
  | `GET/PUT /api/settings` | settings; keys are write-only, a read returns only "set / not set" |

- **Jobs.** Start runs `python -m evolvekit.app.job STUDY RUN` as a detached process
  (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` on Windows,
  `start_new_session` elsewhere), with its output in `job.log`.
  - The job runs the search (`Driver`), then the **final check**:
    `confirm(config, run_dir, seeds=[1001, 1002, 1003], instances=<test cases>, label="final")`.
    Its state is `job.json`: phase `search | check | done | stopped | failed`, pid, times, exit
    codes.
  - The app reads `job.json` and the run directory, so it can be closed and reopened at any
    time.
  - Stop writes `stop-request` (§6.1). During the check, it stops the check (confirm is cached,
    so "Run the final check now" resumes it).
  - The API key from `~/evolvekit/.env` is passed to the job's environment explicitly; the job
    never reads files above the study.
- **Finding the application** (`kind: python`) means probing, in parallel with a 10 s timeout
  each:
  - the interpreter running evolvekit;
  - `py -0p` on Windows;
  - `.venv*` directories in the working directory, the evolvekit checkout and the home
    directory;
  - `~/venvs/*`, `~/Envs/*`, and conda environments;
  - recently used paths.

  Matches come first ("PyVRP 0.14.0 · C:\…\.venv-pyvrp\Scripts\python.exe"), non-matches are
  explained ("Python 3.13, no PyVRP"). `kind: program` uses Browse… and the probe.

### 7.2 Screens

**Home**
- The studies as cards: name, harness, and state, where the state is one of:
  - *draft · step 4 of 7*;
  - *running · 1 h 10 min left · 3.2 % better so far*;
  - *finished · confirmed 3.1 % lower cost*.
- The primary action, **New study**.
- The harness library: installed harnesses, and "Add a harness" (drop a `.zip`).
- **Settings**: the LLM key, provider and models, and the folders.

**New study**: seven steps on a left rail, each saved as it is left, and you can always go back.

1. **Question.**
   - Pick the harness; the only one is preselected.
   - Pick a template (cards with one-sentence descriptions, plus "Blank: describe it to the
     assistant").
   - Name the study.
2. **Application.**
   - The detection list, with the best match preselected.
   - Browse… and a paste field.
   - The probe result in plain words.
   - "Where do I find this?" help written by the harness (`application.label`).
3. **Cases.**
   - A drop zone for files and folders (folders are walked in the browser), plus "Use the
     sample cases".
   - Every file is inspected: "✓ 1,200 tasks, 18 vehicle types", or "✗ not a PyVRP request: …".
   - **"How many cases do you want to keep as a test set?"** A number, suggested at 30 %, with
     two sentences on what each set is for. The app picks test cases that span the sizes, and
     any case can be swapped.
   - Warnings:
     - fewer than 3 in the test set ("the final check will be weak");
     - fewer than 3 in the training set ("the search may fit these cases too closely");
     - an empty test set ("no final check: the result will be optimistic").
   - Leaving this step starts the **baseline preview**: the smallest training case at the
     starting values, in the background. It gives the assistant real tables, the plan a
     measured time, and early proof that application, harness and case work together.
4. **What may change.**
   - *Solver settings*, grouped:
     - each setting as a segmented control **Tune · Keep fixed at … · Default**;
     - its plain help, and "Why would I tune this?" (the `explain` text);
     - the template's recommended set preselected;
     - "Import current settings from a file" (the `solver_settings` input), which sets the
       starting values.
   - *Data changes:*
     - lever instances as rows (what, which rows, how, range) with "applies to 7 vehicle types";
     - "Add a data change" as a form, or through the assistant.
   - *Constraints*, as sentences. Each is tested on the starting values: "holds today ✓".
   - A counter warns when there are more tunables than the plan can learn about: "27 settings
     in about 5 rounds will not learn much; the 8 recommended ones are a good start".
5. **Goal.**
   - The KPIs: ready-made ones, template ones (a small form for the blanks), and custom ones
     from the assistant or the SQL box. **Each shows today's value on the preview case**, and
     "show which rows count".
   - The goal:
     - **One KPI**;
     - **A combination**: the weights as exchange-rate sentences;
     - **In order of importance**: a reorderable list, each level with "counts as equal within
       ___".
   - Guardrails: checkboxes with a threshold, each showing today's value.
6. **Limits and budget.**
   - Time per case, with the consequence stated: "PyVRP stops itself after 20 s; a run still
     going after 60 s is stopped and counts as failed".
   - Retries, with the consequence stated.
   - Runs per case, under "More reliable, slower".
   - **Total time**.
   - AI search help: off, or on with a dollar cap. It is disabled, with the reason, when there
     is no key.
   - The **automatic plan** in plain words, with "Adjust" for experts.
7. **Review and start.**
   - Everything in sentences.
   - **Test run**: the engine's preflight on the compiled study restricted to the smallest
     training case. It reports each KPI, the guardrails, the duration, and a
     starting-point-breaks-a-guardrail check. Any failure stops here, in plain words, with what
     to do.
   - **Start**.

**Running**
- The state in a sentence; time used, time left and the expected finish; rounds done of the
  plan.
- The best improvement so far, with a plain verdict, and per level for hierarchical goals.
- A small chart of best-so-far over time.
- What is running now ("round 4: 8 combinations on 5 cases, 23 of 40 runs done").
- Failures grouped, in plain words, each with what to do.
- **Stop**, and **Detailed dashboard**.

**Results**
1. **The headline:** "The best settings found give 4.2 % lower real cost than your starting
   point on the training set."
2. **The final check on the test set:**
   - "Confirmed: 3.1 % lower on 3 cases the search never saw (likely between 1.2 % and 5.0 %)",
     or honestly "Not distinguishable from your starting point".
   - For hierarchical goals, the deciding level and every level's verdict.
3. **Every study KPI and guardrail,** starting point against best, on training and test.
4. **Every changed value,** old → new, with its plain help.
5. **Downloads:** the harness's exports, plus `report.html` (self-contained, for sharing).
6. **Next steps:** start a new study from this result.

### 7.3 Visual design

- One primary action per screen.
- Plain words first; the technical detail behind "Details".
- Every status colour travels with an icon and a word.
- Numbers use tabular figures.
- Light and dark mode.
- It works from 360 px wide.
- Keyboard and screen-reader friendly: labels, focus rings, `aria-live` for progress.
- Explanations sit where the decision is taken, never in a separate manual.

## 8. The assistant

**What it does:** a side panel in steps 4 and 5, and the start of a blank study. The consultant
writes in plain language. The assistant proposes, as cards:
- tunables (lever instances with ranges, or solver settings);
- constraints;
- KPIs (SQL, and a second SQL listing the rows that count);
- weighted sums, goal levels and guardrails.

Each card has a plain sentence saying **exactly** what is counted or changed, the value on the
preview case, and **Add · Edit · Dismiss**.

**What it sends:** the tables' declared schema (names, types, units, descriptions), the harness
catalogues, the study so far, and summaries of the preview:
- row counts;
- min, mean and max of numeric columns;
- the distinct values (at most 50) of columns declared `categorical`, such as tag names and
  vehicle classes.

It never sends rows, names, addresses or coordinates, and the panel says so.

**Checking:** every proposal is validated **locally** before it is shown:
- SQL runs read-only on the preview tables and must return one number;
- a lever's `where` must select at least one row;
- constraints must hold at the starting values;
- names must be unique.

Failures go back to the model with the error, at most twice. After that the card says honestly
that it could not be expressed, and suggests rephrasing or a template.

**Model:** the provider and model come from Settings. The default is OpenRouter with
`anthropic/claude-sonnet-5` ($2 / $10 per million tokens). The cost of every answer is shown,
and the total per study is kept in `assistant.jsonl`.

**Without a key:** the panel explains how to add one. Templates, the lever form, expression
constraints and the SQL box still work.

**Later:** the same propose–check–review loop can draft harness hooks for a new application.
That is the "consultants create harnesses" end goal, and not part of this build.

## 9. The PyVRP reference harness (`harnesses/pyvrp/`)

**Cases:**
- JSON requests in the benchmark's format (`pyvrp-hard/1`), extended with optional `tags` on
  tasks and vehicle types, and accepted as `pyvrp-request/1`;
- VRPLIB files through `pyvrp.read`. These support settings studies only: levers need the JSON
  format's vehicle classes and tags, and the app says so.

**Kit:** built on `benchmarks/pyvrp_hard/instance.py` and `solve.py`, which provide request
loading, the private deadline, infeasibility pricing, and the operator flags. The schedule
comes from `Route.schedule()`.

**Settings:**
- The 27 of `solve.py`, with labels, groups, plain help and LLM explanations.
- Recommended: 8 settings, chosen from the two benchmark tuning runs' parameter importance (where
  those logs are still available) and from PyVRP's documentation.

**Levers:**
- `vehicle_costs` (`fixed_cost`, `unit_distance_cost`, `unit_duration_cost`,
  `unit_overtime_cost`);
- `fleet_size` (`num_available`);
- `shifts` (`shift_duration`, `max_overtime`);
- `time_windows` (`code: true`: widen or shift);
- `prizes` (`prize`);
- `service_times` (`service_duration`).

**KPIs:** `solver_cost` (marked `changes_with_levers`), `real_cost`, `distance_km`,
`duration_h`, `routes_used`, `served_tasks`, `deliveries_per_hour`, `late_tasks`, `late_h`,
`wait_h`, `missed_required`, `feasible`.

**KPI templates:**
- late tasks with tag ___;
- waiting after tasks whose window opens between ___ and ___;
- share of tasks served by vehicle class ___;
- routes longer than ___ hours.

**Study templates:**
- *Tune solver settings*: the recommended settings, goal `solver_cost`, no guardrails, because
  the objective already prices infeasibility.
- *Route cost sets → deliveries per hour*:
  - vary `vehicle_costs.unit_distance_cost` and `unit_duration_cost` per vehicle class, as a
    scale within 0.5–2;
  - the solver settings stay fixed;
  - goal `deliveries_per_hour`;
  - guardrail `missed_required` ≤ 0.

**Samples:** ten small requests (60–240 tasks) with tags, from the benchmark generator with a
seeded tag pass, committed with a regeneration script.

**Drift:** `discover()` reads the fields and defaults of PyVRP's parameter dataclasses.

## 10. The demo harness (`harnesses/demo-tour/`)

A `kind: program` harness around `examples/cli-solver/solver.py`. It proves the program path,
keeps the end-to-end tests fast, and needs no PyVRP. Its tables are `stops`, `tour` and
`summary`, and it has one lever (stop weights) and one KPI template.

## 11. Shared later: rules kept now

1. A study is one self-contained folder with relative paths.
2. The application path is re-checked whenever a study is opened.
3. Runs are detached, and the app only reads run directories.
4. The page talks to the backend only through the JSON API, and cases arrive by upload.

Left for later:
- logins and per-user studies;
- a job queue (never two runs on shared cores);
- HTTPS;
- harness trust (on a server, only harnesses an administrator installed).

## 12. The intern reviewer loop

**The reviewer:** a fresh subagent each round, one at a time. It never sees the code, this
document or earlier reviews.
- **Persona:** an intern who has just learned what PyVRP is, what an executable is, and what
  PyVRP's configuration looks like. Not a programmer; takes instructions literally; gets stuck
  on jargon.
- **Materials:** a clean library home, a handover folder (`pyvrp-1.0.0.zip`, a `cases/` folder
  with eight small requests), and a manager's note. The note says:
  - start evolvekit with one given command;
  - PyVRP is installed on this machine;
  - find the PyVRP settings with the lowest cost, at most 20 s per case and 30 minutes in total;
  - send the settings file and say whether they are really better.
- **What it does:** it drives the real app with the in-app browser (verified to work from
  subagents), with real PyVRP runs.

**Report:**
- an overall score from 1 to 10: *how easy was it to tune a configuration to get the most out of
  the objective?*;
- sub-scores for getting started, knowing what to do next, setting up, confidence during the
  run, understanding the result, and handing it over;
- the worst frictions (where, what happened, why it hurt, what would fix it, severity
  blocker / major / minor);
- what would make it a 10.

**Loop:**
- I fix the worst frictions and start a new reviewer.
- The loop stops at an overall score of **8 or more with no blocker**.
- Each round is logged in `docs/superpowers/reviews/2026-09-26-intern-review-log.md`: score,
  frictions, fixes.
- After the gate: **one extra review, not gating,** of a data study through the assistant
  ("route cost sets for more deliveries per hour; trucks must stay dearer than vans"). Serious
  findings get fixed.

## 13. Testing

**Unit tests:**
- the harness manifest, including every refusal;
- the expression language (the engine's and the SDK's copy, against the same cases);
- the SDK: tables, levers, SQL safety, `NULL` handling, weighted sums, guardrail violations,
  exit codes;
- study → config, and the automatic plan;
- the engine additions:
  - `max_hours` (projection, the deadline mid-generation, abandoned versus failed, resume);
  - stop requests;
  - constraints (static rejection, redrawing);
  - gates (the seed, reporting);
  - levels (ordering, tolerance, anchor persistence, confirm per level);
- the app's API (security, uploads, the split, the plan, jobs);
- the assistant against the `fake` provider (validation and self-correction).

**Also:**
- **End to end:** a study through the API with the demo harness. Create, upload, split, preview,
  test run, start, finish, final check, results, downloads.
- **PyVRP:** the harness on the samples, skipped where PyVRP is not installed.
- **Screenshots** of every screen in light and dark, and at phone width, driven through the
  in-app browser.
- **The gate:** `python tasks.py check`.

## 14. Delivery

Three stacked PRs against `master`, each green on its own. Merge commits, not squash, because
they are stacked. Never pushed to `master`.

1. **`harness/1-engine`:** this spec; `budget.max_hours` and stop requests;
   `problem.parameter_constraints`; `evaluate.gates`; `evaluate.score.levels` (with `status`,
   the dashboard and `confirm` per level); README sections; tests.
2. **`harness/2-harness`:**
   - the harness format and loader, the SDK, the study model, compilation and the plan;
   - `evolvekit harness new / check / pack / list` and `evolvekit study compile / run`;
   - the PyVRP harness with its two templates and samples, the demo harness;
   - `AGENTS.md` and `docs/harness-authoring.md`; tests.
3. **`harness/3-app`:** the app (server, page, jobs, detection, assistant), `docs/app.md`,
   screenshots, the review log, tests.

## 15. Out of scope

- Consultants creating harnesses: the end goal (§8, "Later").
- The shared server (§11).
- A Pareto view of several goals at once.
- Harnesses for private applications. Experts build those with `AGENTS.md` and
  `harness check`.
