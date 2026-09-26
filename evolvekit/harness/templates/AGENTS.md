# Working on this harness — instructions for AI coding tools

This folder is an **evolvekit harness**: the plumbing between evolvekit and one
application (a solver, a simulator, any program with settings), plus the
vocabulary consultants use to set up studies on it. These instructions are
for Claude Code, Copilot CLI, Codex and any other coding agent editing it.

## The loop

1. Make a change.
2. Run the check, with the application it is for:

   ```
   python -m evolvekit harness check . --app PATH_TO_THE_APPLICATION --json
   ```

   For a `python` harness `PATH` is the Python that has the application
   installed; for a `program` harness it is the program.
3. Read every check with `"ok": false`: each has a `message` naming the key or
   hook, and a `fix`. Fix the failures first, then the warnings.
4. Repeat until the exit code is 0. Never declare the work done before that.

## The files

| File | What it is | Edit it? |
|---|---|---|
| `harness.yaml` | the declaration: application, cases, settings, tables, levers (data changes), KPIs, KPI templates, exports, defaults | yes |
| `runner.py` | the application-specific hooks | yes |
| `evk_harness.py` | the SDK, copied from evolvekit | **never** — `evolvekit harness new` refreshes it |
| `kit/` | helper code for `runner.py` | yes |
| `templates/*.yaml` | study templates: partial `study.yaml` files | yes |
| `samples/` | a few *small* cases that solve in seconds; `harness check` solves them | yes |

## The contract (`runner.py`)

```python
import evk_harness as evk

def read_case(path): ...                           # -> evk.Case(tables={...}, native=...)
def solve(case, settings, time_limit_s, seed): ... # -> the application's solution
def solution_tables(case, solution): ...           # -> {"table": [row, ...], ...}
# optional:
def write_case(case, path): ...          # the changed request (the "requests" export)
def apply_lever(case, name, rows, value, mode=None): ...  # levers declared with `code: true`
def measure(case, solution, tables): ...  # KPIs easier in Python than in SQL
def discover(): ...                       # {"version": ..., "settings": {name: {"type", "default"}}}
def export(format, values, out): ...      # exports beyond the built-in four

if __name__ == "__main__":
    evk.main(globals())
```

- `runner.py` runs under the **application's** Python (or evolvekit's, for a
  program harness). It must not import evolvekit, and should import only what
  that environment has.
- **Tables are the vocabulary.** Every KPI, data constraint and lever
  selection a consultant writes is SQL over the tables you declare: request
  tables (the case as the solver receives it; `read_case`) and solution tables
  (the plan; `solution_tables`). Every column is declared in `harness.yaml`
  with a type, a unit and a one-line description; mark a column
  `categorical: true` only when its distinct values are safe to show a model
  (tags, classes — never names, addresses or coordinates), and
  `private: true` when nothing about it may reach a model, not even its range
  (coordinates). The check fails when the tables you produce differ from the
  declaration.
- **`solve` must build the problem from `case.tables`** for every column a
  lever can change: the SDK changes those cells, never your native object.
- A lever with `code: true` is yours to apply: `apply_lever` gets the rows
  (dicts) its `where` selected — change them in place — the value, and the
  mode (`scale`, `set` or `add`) when it takes a `mode` argument. Raise
  `evk.InvalidValues("…")` for a value it cannot take; that ends the run
  before any solving.
- **Errors are sentences.** Raise `evk.HarnessStop("…")` with a sentence a
  consultant can act on. `read_case` failures become "cannot read <file>: …"
  automatically.

## Settings: parametrise so that every value in range is valid

The search tries values anywhere in a setting's range. A range in which some
values crash the application wastes whole runs. In order of preference:

1. **By construction.** If `min_x` must not exceed `max_x`, declare `min_x`
   and `extra_x >= 0` and compute `max_x = min_x + extra_x` in `solve`.
2. **A declared constraint** in a study template (`constraints: [{says, expr}]`):
   checked before any solving, and the samplers draw again.
3. **An SQL check** on the changed request, for rules that depend on the data.

The check's "three random settings vectors" step finds ranges that break.

Every setting has `label` and `help` (for people: plain words, one sentence)
and `explain` (for the model: what it does, which way it trades off, when it
matters). Mark about eight as `recommended: true` — the ones that matter most.

## When the application's API changes

`harness check` reports drift, one sentence per setting, naming the key:

- a setting the application has and the harness lacks → add it under
  `settings:` if it is worth tuning;
- a setting the harness declares and the application no longer has → remove
  or rename it;
- a changed default → update `settings.<name>.default`.

Then bump `version:` in `harness.yaml`. Existing studies keep their pinned
copy of the old version.

## The comparability trap

A lever that changes what the solver *sees* (costs, weights, penalties) also
changes any KPI computed from what the solver saw. Such a KPI must be marked
`changes_with_levers: true`, and a study that varies levers must measure
success with a KPI computed from the **untouched** request (the `orig_*`
tables hold it). `harness check` warns when a template gets this wrong.

## Samples and templates

- `samples/`: three to ten small cases that solve within a second or two.
  Keep the generator next to them.
- `templates/<id>.yaml`: `title`, `summary`, and the study parts (`vary`,
  `constraints`, `kpis`, `goal`, `guardrails`, `limits`, `budget`).
  `vary: {settings: recommended}` tunes the recommended settings. Every
  template must compile — loading the harness checks that.

## Sharing

```
python -m evolvekit harness pack .     # writes <id>-<version>.zip next to this folder
```
