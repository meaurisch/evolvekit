# Harness part 2: harnesses and studies — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The harness format, its SDK and authoring tools, the study model, study → evolvekit config compilation with the automatic plan, study execution with a final check, and two harnesses (a fast demo around the example CLI solver, and the PyVRP reference harness) — design §4, §5, §9, §10, §14.2.

**Architecture:** `evolvekit/harness/` is a layer *above* the engine: it compiles a harness plus a study into an ordinary `evolvekit.yaml` (engine unchanged except part 1) and a `study-run.json` the harness's runner reads. The runner is the application-specific half (`runner.py`, the hooks) on top of the SDK (`evk_harness.py`, standard library only, copied into every harness because it runs under the *application's* Python). Tables in an in-memory SQLite database are the shared vocabulary for KPIs, constraints and lever selections.

**Tech stack:** Python ≥ 3.11 standard library (sqlite3, zipfile, ast, ctypes for the core layout), PyYAML (evolvekit side only). PyVRP 0.14 for the PyVRP harness only (tests skip without it).

## Global constraints

- The SDK file is standard library only and runs on Python ≥ 3.9 (application environments are older than evolvekit's); no f-string `=`, no `match`, no `X | Y` types at runtime.
- One plain sentence per error on stderr, never a bare traceback as the last word; exit codes 0 result / 2 invalid values or a broken constraint / 3 unreadable case / 1 anything else.
- A study folder is self-contained with relative paths; only the application path is machine-specific.
- SQL: read-only authorizer, 2 s progress limit, first column of first row, `NULL` → 0 and noted.
- Harness validation: a sentence per mistake naming the key path; unknown keys refused.
- Public repository: nothing private; the PyVRP harness uses only the public benchmark generator's format.
- Branch `harness/2-harness` from `harness/1-engine`; PR against `master`, stacked.

---

## Contracts

### The runner and the SDK

```python
import evk_harness as evk
def read_case(path): ...                          # -> evk.Case(tables={name: [row dicts]}, native=...)
def solve(case, settings, time_limit_s, seed): ...  # -> native solution; must build from case.tables for lever columns
def solution_tables(case, solution): ...          # -> {name: [row dicts]}
def write_case(case, path): ...                   # optional
def apply_lever(case, name, rows, value): ...     # optional, for `code: true` levers; rows = list of row dicts
def measure(case, solution, tables): ...          # optional -> {kpi: number}
def discover(): ...                               # optional -> {"version": str, "settings": {name: {"type", "default"}}}
def export(format, values, out): ...              # optional
if __name__ == "__main__":
    evk.main(globals())
```

Subcommands (`python runner.py SUB ...`): `solve --case F --seed N --values V.json --study S.json [--time-limit T] [--tables-out T.sqlite] [--app PATH]`, `inspect --case F`, `describe`, `export --format X --values V.json --study S.json --out F`. `evk.APP` is `--app` (a program harness's executable) or `sys.executable`.

### `study-run.json` (compiled, static part of a study)

```json
{"study_run": 1, "harness": {"id": "pyvrp", "version": "1.0.0"}, "time_limit_s": 20,
 "settings": {"base": {"num_neighbours": 50, "...": "..."}, "tuned": ["num_neighbours"]},
 "levers": {"van_km_cost": {"lever": "vehicle_costs", "table": "vehicle_types", "column": "unit_distance_cost",
             "where": "class = 'van'", "mode": "scale", "code": false, "integer": false}},
 "constraints": [{"says": "...", "expr": "truck_km_cost >= van_km_cost"}, {"says": "...", "sql": "SELECT ..."}],
 "kpis": {"real_cost": {"sql": "...", "direction": "lower"}, "late_cold": {"sql": "...", "params": {"tag": "cold"}, "direction": "lower"},
          "x": {"measure": true, "direction": "lower"}, "combo": {"weighted": {"late_cold": 1.0, "wait": 0.5}, "direction": "lower"}},
 "guardrails": [{"kpi": "missed_required", "max": 0}],
 "tables": {"tasks": {"task_id": "int", "...": "..."}}}
```

`solve` prints, as its last stdout line: `{"kpis": {<every study KPI>, "guardrail_violation": x, "solve_s": t}, "text_feedback": "..."}`; per case, `guardrail_violation = Σ max(0, v − max)/max(|max|, 1) + Σ max(0, min − v)/max(|min|, 1)`; a weighted sum is `Σ (+w·v for lower-is-better, −w·v for higher-is-better)`, lower is better.

### Study → config (design §5.4 table) and the plan (§5.5) are implemented as written, with:
`budget.max_full_evals_per_day` = generations × children + seed + slack (the default 20 would cap a two-hour run), `stop.patience` = generations, `search.seed` = 1, `novelty.behavioural: "off"` for time-limited applications, the application path written with forward slashes and quoted.

---

## File map

| File | Responsibility |
|---|---|
| `evolvekit/cpus.py` | physical core layout (Windows `GetLogicalProcessorInformationEx`, Linux sysfs, fallback); workers and `pin_cpus` for a plan |
| `evolvekit/harness/__init__.py` | package; `HarnessError` |
| `evolvekit/harness/sdk/evk_harness.py` | the SDK (package data, copied into harnesses) |
| `evolvekit/harness/sql.py` | schema building and SQL checks on the evolvekit side (imports the SDK module) |
| `evolvekit/harness/manifest.py` | `harness.yaml` → `Harness` (validated) |
| `evolvekit/harness/study.py` | `study.yaml` → `Study` (validated), templates, save |
| `evolvekit/harness/plan.py` | the automatic plan |
| `evolvekit/harness/compile.py` | study + harness + plan → `evolvekit.yaml` + `study-run.json` |
| `evolvekit/harness/execute.py` | the preview, and a study run: search, final check, `job.json` |
| `evolvekit/harness/check.py` | `harness check` |
| `evolvekit/harness/library.py` | built-in and installed harnesses, `new`, `pack`, install from zip |
| `evolvekit/harness/templates/AGENTS.md` | instructions for AI coding tools, written into every harness |
| `evolvekit/cli.py` | `harness new/check/pack/list`, `study compile/run/preview` |
| `harnesses/demo-tour/` | `kind: program` harness around `examples/cli-solver/solver.py` |
| `harnesses/pyvrp/` | the PyVRP reference harness: `harness.yaml`, `runner.py`, `kit/`, `templates/`, `samples/`, `README.md` |
| `examples/cli-solver/solver.py` | backward-compatible: optional explicit `stops` with weights in an instance |
| `docs/harness-authoring.md` | for experts |
| tests | `test_cpus.py`, `test_harness_sdk.py`, `test_harness_manifest.py`, `test_study.py`, `test_study_plan.py`, `test_study_compile.py`, `test_harness_demo.py`, `test_harness_check.py`, `test_study_run.py`, `test_harness_pyvrp.py` |

---

### Task 1: The core layout (`evolvekit/cpus.py`)
Produces `physical_cores() -> list[list[int]]` (logical CPUs per core, core 0 first), `plan_workers(runs: int) -> tuple[int, list[int]]` (physical cores − 1, at least 1, at most `runs`; one logical CPU per core, skipping the first core; `[]` where pinning is unavailable). Tests: fallback shape, monkeypatched layouts (4×2 → 3 workers `[2, 4, 6]`; 1 core → 1 worker `[]`; runs cap).

### Task 2: The SDK
Produces `evk_harness.py` with `Case`, `main(hooks)`, `HarnessStop` (exit-code carrying error), `build_database(tables, declared=None)`, `run_kpi(db, sql, params)`, `Expression` (a copy of `evolvekit/expressions.py`), `apply_levers`, `guardrail_violation`, `weighted_sum`. Tests with a toy runner written into `tmp_path`: solve prints the contract JSON; levers scale/set/add exactly the selected cells (and round integer columns); a `where` selecting nothing is an error; `code: true` calls `apply_lever`; expression and SQL constraints exit 2 with the sentence; an unreadable case exits 3; a crash exits 1 with one sentence last on stderr; SQL cannot write, `ATTACH`, or run past 2 s; `NULL` → 0 with a note; `--tables-out` writes request, `orig_*` and solution tables; the engine's `CASES` pass against the SDK's expression copy.

### Task 3: The harness manifest
Produces `load_harness(dir) -> Harness` and `Harness` (application, cases, inputs, time_limit, seeds, settings (a `Parameter` plus label/group/help/explain/recommended), tables, levers, kpis, kpi_templates, exports, defaults, templates). Tests: the demo manifest loads; every refusal (unknown key, bad id, default outside range, a lever on an unknown table/column or a text column, an SQL KPI that fails against the declared tables, a template placeholder without a declared param, a template that does not compile) is one sentence naming the key path.

### Task 4: The study model
Produces `Study` (+ `load_study(dir)`, `save_study(study, dir)`, `study_from_template(harness, template, name)`), tunables (settings and data), KPI resolution (harness / template / custom SQL / weighted), goals (`levels` with `equal_within`), guardrails, limits, budget. Tests: the §5.2 example parses; refusals (unknown KPI, a level on an undefined KPI, a lever column not in the lever, a range outside the setting's range, duplicate names, weights referring to unknown KPIs).

### Task 5: The automatic plan
Produces `make_plan(n, m, runs_per_case, run_s, hours, time_limit_s, workers, pin) -> Plan` with `rounds`, `children`, `generations`, `screening`, `final_check_s`, `search_s`, `blocked` (three ways out), `summary(now)` in words. Tests: the §5.5 arithmetic on hand-computed cases, screening only with n ≥ 6 and a limit ≥ 10 s, c chosen for ≥ 6 rounds else ≥ 3, the blocked case, k = 2 fallback.

### Task 6: Compilation
Produces `compile_study(study, harness, plan, run_dir) -> (config_dict, study_run_dict)` and `write_run(...)`. Tests: the §5.4 mapping row by row; the compiled config passes `build_config`; levels / weighted / single goal; guardrails → gate; constraints split (expr → engine, sql → runner); relative paths only, apart from the application.

### Task 7: The demo harness
`harnesses/demo-tour/` (`kind: program`): stops (with weights, tags), tour, summary tables; lever `stop_weights` (scale); KPIs `tour_length` (real), `solver_length` (changes_with_levers), `longest_leg`, `iterations`; KPI template "legs longer than ___"; templates "Tune solver settings" and "Stop weights → shorter real tour"; samples. `solver.py` accepts an optional `stops: [[x, y, weight], ...]`. Tests: runner subcommands end to end.

### Task 8: Preview and study runs
Produces `preview(study_dir) -> dict` (smallest training case at the starting values, `preview/tables.sqlite`, timing, KPIs) and `run_study(study_dir, run_id) -> dict` (compile, `Driver`, the final check with `confirm(seeds=[1001, 1002, 1003], instances=<test cases>, label="final")`, `job.json` phases `search | check | done | stopped | failed`). Tests: an end-to-end demo study (preview, run, final check, `job.json`); a stop request during the search ends in `stopped`.

### Task 9: `harness check`
Produces `check_harness(dir, app=None) -> list[Check]` (manifest; probe; describe + drift; per sample: inspect, a short solve, KPIs finite, templates instantiate, levers change exactly their cells, constraints and guardrails compute, exports work; three random settings vectors; the comparability trap) and the `--json` shape `{"ok", "checks": [{"id", "ok", "message", "fix"}]}`; exit 0 / 1 warnings / 2 failures. Tests on the demo harness and on broken copies.

### Task 10: CLI, `harness new`, `pack`, `list`, `AGENTS.md`
`evolvekit harness new DIR [--from H] [--kind python|program]`, `check`, `pack`, `list`; `evolvekit study compile | preview | run`. Tests: new → check passes for a copy of the demo; pack writes `<id>-<version>.zip` without caches; list shows built-ins.

### Task 11: The PyVRP harness
`harnesses/pyvrp/`: kit on the benchmark's `instance.py` / `solve.py` (request loading incl. `pyvrp-request/1` with tags, VRPLIB via `pyvrp.read` for settings studies), the 27 settings with labels, groups, help and explanations (8 recommended), the six levers, the twelve KPIs, the four KPI templates, the two study templates, ten tagged samples with a regeneration script, `discover()` from PyVRP's parameter dataclasses. Tests (skip without PyVRP): `harness check` passes; a two-generation settings study and a lever study run end to end on two samples.

### Task 12: Docs, the gate, the PR
`docs/harness-authoring.md`, README section "Harnesses and studies", `python tasks.py check`, PR 2.

## Self-review

§4.1–4.6 → Tasks 2, 3, 7, 9, 10, 11. §5.1–5.5 → Tasks 4, 5, 6, 8. §9 → Task 11. §10 → Task 7. §13 unit tests (manifest, expression copies, SDK, study → config, plan) → Tasks 2–6; end to end and PyVRP → Tasks 8, 11. §14.2 → all. The app, the assistant and the review loop are part 3.
