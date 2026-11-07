# Harness part 1: engine additions — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Four generic engine features from the design (§6): `budget.max_hours` with stop requests, `problem.parameter_constraints`, `evaluate.gates` and `evaluate.score.levels` — each useful without the app, each with README sections and tests.

**Architecture:** Config parsing stays in `evolvekit/config.py` (and `space.py` for the parameter space). Two new pure modules hold the new arithmetic: `evolvekit/expressions.py` (the whitelisted expression language) and `evolvekit/evaluate/levels.py` (the lexicographic scalar). A new `evolvekit/stopping.py` holds the run-level stop (time limit, stop request) and the cross-session clock. The cascade gains gating, level scoring and abandonment; the driver gains the stop checks; `status`, the dashboard and `confirm` report the new facts. Every existing consumer of `score` keeps working because levels compile to one finite scalar.

**Tech stack:** Python ≥ 3.11 standard library + PyYAML (unchanged). pytest. No new dependencies.

## Global constraints

- Entry points: `python tasks.py test | full | check | lint`; `check` is the gate (`AGENTS.md`).
- Every score stays a finite float; the status document never contains `NaN`/`Infinity`.
- Config errors name the key path (`ConfigError`), in a sentence a user can act on.
- New `runs.jsonl` fields must not break reading old run directories (absent key = old behaviour).
- `status` stays a pure function of the run directory's files.
- The repository is public: no private names, no secrets.
- Branch `harness/1-engine`; never push to `master`; one PR against `master` at the end.
- Keep `slow` markers on driver-level tests that take seconds.

---

## File map

| File | Change |
|---|---|
| `evolvekit/expressions.py` | **new** — `Expression.parse/evaluate/holds`, `ExpressionError` |
| `evolvekit/space.py` | `Constraint`, `ParameterSpace.constraints`, `with_constraints`, `violations`, `satisfies`, `draw`, `CONSTRAINT_ATTEMPTS` |
| `evolvekit/config.py` | `problem.parameter_constraints`; `GateConfig`, `evaluate.gates`; `LevelConfig`, `score.levels`; `EvaluateConfig.required_kpis`; `budget.max_hours`; cross-checks |
| `evolvekit/evaluate/levels.py` | **new** — `K`, `Anchor`, `make_anchor`, `level_steps`, `level_score` |
| `evolvekit/evaluate/types.py` | `StageOutcome.abandoned`; `EvalResult.gated`, `EvalResult.abandoned` |
| `evolvekit/evaluate/stages.py` | constraint check in the static stage; `cancel` for command stages; abandoned outcomes; required-KPI message |
| `evolvekit/evaluate/fanout.py` | external `stop` event; abandoned outcomes |
| `evolvekit/evaluate/cascade.py` | gates, level scores (anchors in `work/levels.json`), abandonment, `stop` |
| `evolvekit/stopping.py` | **new** — `STOP_REQUEST`, `active_seconds`, `RunStop` |
| `evolvekit/candidate.py` | `Candidate.gated` |
| `evolvekit/search/operators.py`, `search/tuning.py` | redraw under constraints |
| `evolvekit/search/driver.py` | gates in observations and the seed check; levels/gates in the problem identity and `run_started`; time limit, stop request, cut generations |
| `evolvekit/prompts.py` | constraints under the parameter list |
| `evolvekit/preflight.py` | required KPIs; the seed against the gates |
| `evolvekit/confirm.py` | required KPIs; per-level analysis and lexicographic verdict |
| `evolvekit/status.py` | `max_hours` limit; abandoned ≠ failed; `gated` on candidates; `progress.levels`; text |
| `evolvekit/dashboard/index.html` | gated candidates; level rows in the Progress card |
| `evolvekit/cli.py` | `evolvekit stop`; `confirm` exit code with levels |
| `README.md` | four sections + confirm/status notes |
| `tests/test_expressions.py`, `test_parameter_constraints.py`, `test_gates.py`, `test_levels.py`, `test_time_limit.py` | **new** |

---

### Task 1: The expression language

**Files:** Create `evolvekit/expressions.py`, `tests/test_expressions.py`.

**Interfaces — produces:**
- `class ExpressionError(ValueError)`
- `Expression.parse(text: str) -> Expression` (raises `ExpressionError`: syntax, a disallowed construct)
- `Expression.names: frozenset[str]`, `Expression.text: str`
- `Expression.evaluate(values: Mapping[str, Any]) -> Any` (raises `ExpressionError`: unknown name, division by zero, overflow, a type mix)
- `Expression.holds(values) -> bool` (raises `ExpressionError` when the result is not a bool)
- `CASES: list[tuple[str, dict, object]]` in the test module — a shared table (text, values, expected result or the exception type) that part 2's SDK copy is tested against.

- [ ] **Step 1: failing tests** — `tests/test_expressions.py`:

```python
import pytest
from evolvekit.expressions import Expression, ExpressionError

CASES = [
    ("a >= b", {"a": 2, "b": 1}, True),
    ("a >= b", {"a": 1, "b": 2}, False),
    ("0 <= a - b <= 5", {"a": 7, "b": 3}, True),
    ("0 <= a - b <= 5", {"a": 9, "b": 3}, False),
    ("a * 2 + b ** 2 - c / 4 >= c % 3", {"a": 1.5, "b": 2, "c": 8}, True),
    ("abs(a - b) < 1 and not flag", {"a": 1.0, "b": 1.5, "flag": False}, True),
    ("min(a, b) > 0 or max(a, b) > 10", {"a": -1, "b": 11}, True),
    ("round(a) == 3", {"a": 2.6}, True),
    ("mode == 'fast' or a > 1", {"mode": "fast", "a": 0}, True),
    ("mode != \"fast\"", {"mode": "slow"}, True),
    ("-a < +b", {"a": 1, "b": 1}, True),
    ("flag == True", {"flag": True}, True),
    ("a / b > 1", {"a": 1, "b": 0}, ExpressionError),       # division by zero
    ("a ** b > 1", {"a": 10.0, "b": 400}, ExpressionError),  # overflow
    ("a ** 0.5 > 1", {"a": -4.0}, ExpressionError),          # complex
    ("a < mode", {"a": 1, "mode": "x"}, ExpressionError),    # a type mix
    ("a > 1", {}, ExpressionError),                          # unknown name at evaluation
]

@pytest.mark.parametrize("text, values, expected", CASES)
def test_the_shared_cases(text, values, expected):
    expression = Expression.parse(text)
    if isinstance(expected, type) and issubclass(expected, Exception):
        with pytest.raises(expected):
            expression.evaluate(values)
    else:
        assert expression.evaluate(values) == expected

@pytest.mark.parametrize("text", [
    "__import__('os')", "a.b > 1", "a[0] > 1", "(lambda: 1)() > 0", "f(a) > 1",
    "[a] == [1]", "a if b else c", "{a: 1}", "a := 1", "open('x')", "abs(a, key=b) > 1",
    "a > 1; b", "1 < ", "",
])
def test_anything_else_is_refused_when_parsed(text):
    with pytest.raises(ExpressionError):
        Expression.parse(text)

def test_names_are_collected():
    assert Expression.parse("abs(a - b) < c and mode == 'x'").names == frozenset({"a", "b", "c", "mode"})

def test_holds_needs_a_condition():
    with pytest.raises(ExpressionError, match="not a condition"):
        Expression.parse("a + 1").holds({"a": 1})
    assert Expression.parse("a > 0").holds({"a": 1}) is True
```

- [ ] **Step 2:** `python -m pytest tests/test_expressions.py -q` → fails (module missing).
- [ ] **Step 3: implement** `evolvekit/expressions.py`: walk the `ast` (never `eval`); whitelist `Expression, BoolOp(And, Or), UnaryOp(Not, USub, UAdd), BinOp(Add, Sub, Mult, Div, Pow, Mod), Compare(Eq, NotEq, Lt, LtE, Gt, GtE), Call(Name in {abs,min,max,round}, no keywords), Name, Constant(int|float|bool|str)`; `and`/`or` short-circuit and return bools; `**` in floats with `OverflowError` and complex results turned into `ExpressionError`; `ZeroDivisionError`/`TypeError` → `ExpressionError` naming the problem.
- [ ] **Step 4:** tests pass.
- [ ] **Step 5:** commit `expressions: a whitelisted language for conditions on tuned values`.

### Task 2: `problem.parameter_constraints`

**Files:** `evolvekit/space.py`, `evolvekit/config.py`, `evolvekit/evaluate/stages.py` (`run_static_stage`), `evolvekit/prompts.py` (`_parameter_section`), `evolvekit/search/driver.py` (`_describe_run`), `tests/test_parameter_constraints.py`.

**Interfaces — produces:**
- `space.CONSTRAINT_ATTEMPTS = 100`
- `Constraint(text: str, says: str, expression: Expression)`; `Constraint.broken_by(values) -> str | None` (a sentence: `breaks the constraint "<says>" (<text>) with a=1, b=2`, or `... cannot be evaluated: <why>`)
- `ParameterSpace.constraints: tuple[Constraint, ...] = ()`
- `ParameterSpace.with_constraints(raw, path="problem.parameter_constraints") -> ParameterSpace` (raises `SpaceError`)
- `ParameterSpace.violations(values) -> list[str]`, `satisfies(values) -> bool`, `draw(propose: Callable[[], dict], attempts=CONSTRAINT_ATTEMPTS) -> dict`
- YAML: `problem.parameter_constraints: [ "a >= b", {expr: "a >= b", says: "A stays at least B"} ]`

- [ ] **Step 1: failing tests** (`tests/test_parameter_constraints.py`): config accepts both forms; refuses an unknown name, a syntax error, a non-condition, defaults that break a constraint, and constraints without `problem.parameters` — each `ConfigError` names `problem.parameter_constraints[i]`; the static stage refuses a violating `configure()` with the sentence and never runs the command (a command stage whose solver writes a marker file: the marker is absent); the system prompt lists the constraint under the parameters; `space.draw` stops after exactly 100 proposals when nothing satisfies.
- [ ] **Step 2:** run → fail.
- [ ] **Step 3: implement.** `ProblemConfig.parse` adds the known key and calls `parameters.with_constraints(...)`; `run_static_stage` appends `the configuration <violation>` to `problems` after `validate` succeeds; `_parameter_section` adds "Constraints on these values (a configuration that breaks one is rejected before it is evaluated):" with one bullet per constraint (`says` and the expression); `_describe_run` adds `"parameter_constraints": [c.text ...]`.
- [ ] **Step 4:** pass. **Step 5:** commit `config: problem.parameter_constraints, checked before any solving`.

### Task 3: Model-free operators redraw under constraints

**Files:** `evolvekit/search/operators.py` (`param_lhs_typed`, `param_local`, `param_cross`, `param_tpe` fallbacks), `evolvekit/search/tuning.py` (`propose_tpe`), tests in `tests/test_parameter_constraints.py`.

- [ ] **Step 1: failing tests** — a space `a, b ∈ [0, 1]` with `a <= b` (defaults 0.2 / 0.8): 60 seeds of each operator (`param_lhs_typed`, `param_local`, `param_cross` with a mate, `param_tpe` with 12 feasible observations) all return configurations with `a <= b`; with `a == 0.5` (satisfied only by the default) `param_local` still returns a block (the last draw), which the static stage refuses.
- [ ] **Step 2:** fail. **Step 3: implement** — `param_local`: `space.draw(lambda: space.perturb(base, rng))`; `param_cross`: the coin flips inside a closure passed to `draw`, the local fallback through `draw`; `param_lhs_typed`: redraw the hypercube (up to `CONSTRAINT_ATTEMPTS` times) until a variant satisfies, else keep the unfiltered variants; `propose_tpe`: skip infeasible draws, allow up to `CANDIDATES + CONSTRAINT_ATTEMPTS` draws for `CANDIDATES` feasible ones.
- [ ] **Step 4:** pass (+ `tests/test_tuning_operators.py` unchanged). **Step 5:** commit `operators: draw again rather than propose a configuration that breaks a constraint`.

### Task 4: `evaluate.gates` in the engine

**Files:** `evolvekit/config.py`, `evolvekit/evaluate/types.py`, `evolvekit/candidate.py`, `evolvekit/evaluate/cascade.py`, `evolvekit/evaluate/stages.py` (message), `evolvekit/search/driver.py`, `evolvekit/preflight.py`, `evolvekit/confirm.py`, `tests/test_gates.py`.

**Interfaces — produces:**
- `GateConfig(kpi: str, max: float | None, min: float | None)`; `GateConfig.broken_by(kpis) -> str | None` → `"missed_required = 2 > 0"` / `"on_time_share = 0.9 < 0.95"`
- `EvaluateConfig.gates: tuple[GateConfig, ...]`; `EvaluateConfig.required_kpis -> tuple[str, ...]` (objective, level KPIs, gate KPIs; order kept, no duplicates) — used everywhere `(config.evaluate.score.objective,)` was passed as `required_kpis`
- `EvalResult.gated`, `Candidate.gated: str | None`; `finished_final_stage(..., gated=None)`
- Seed that breaks a gate → `RunSummary.aborted`, stop reason starting `the seed breaks a gate: <gated>`

- [ ] **Step 1: failing tests** (`tests/test_gates.py`, driver-level ones `slow`): config refuses a gate with neither/both bounds and unknown keys; a stage run missing a gate KPI fails with a sentence naming it; a candidate whose stage KPIs break a gate keeps its score, carries `gated`, is not promoted (a two-stage config: the gated child never reaches stage 2), does not compete, is never `best`; `_observations` counts it at the floor minus one; a seed that breaks a gate aborts the run (`summary.aborted`, reason names the gate and value) and `cli.main(["run", ...])` exits 4; preflight reports it as a failure (exit 2); the problem identity refuses a resumed run whose gates changed.
- [ ] **Step 2:** fail. **Step 3: implement.** Cascade: `_broken_gate(kpis)`; after `_note_behaviour` a broken gate sets `result.gated` and drops the candidate from the survivors; the hold-out run appends ` on the hold-out`; the race incumbent is never a gated candidate; `competes` passes `gated`. Driver: `candidate.gated = result.gated`; `_settle_competes` passes `row.get("gated")`; `_observations` puts gated with failed; `_seed_failed` includes `gated` with its own stop reason; identity key `gates` (`None` when empty) and `run_started.gates`. Preflight: each command stage's outcome against the gates. Confirm: `required_kpis=config.evaluate.required_kpis`.
- [ ] **Step 4:** pass. **Step 5:** commit `evaluate.gates: a candidate that breaks one does not compete`.

### Task 5: Gates in status and the dashboard

**Files:** `evolvekit/status.py` (`candidates`, `_candidate_detail`), `evolvekit/dashboard/index.html` (`status()`, the unfinished tooltip, the drawer's "not ranked" line), tests in `tests/test_gates.py`.

- [ ] **Step 1: failing test** — a finished run with a gated child: `status["candidates"]` row has `gated` and `competes: false`, is not in `failures`, and `reason` stays empty (the dashboard reads `reason` as "failed"); `candidate_detail` has `gated`; the dashboard HTML contains `broke a gate`.
- [ ] **Step 2:** fail. **Step 3: implement**: `"gated": row.get("gated")` in both; dashboard `status(c)`: `c.gated ? ["dim", "broke a gate"]` before `raced_out`; tooltip and drawer name the gate.
- [ ] **Step 4:** pass. **Step 5:** commit `status, dashboard: a gated candidate is not competing, not failed`.

### Task 6: `evaluate.score.levels` in the engine

**Files:** `evolvekit/config.py`, create `evolvekit/evaluate/levels.py`, `evolvekit/evaluate/cascade.py`, `evolvekit/search/driver.py`, `tests/test_levels.py`.

**Interfaces — produces:**
- `LevelConfig(kpi, direction, tolerance: float | None, relative: bool)`; `ScoreConfig.levels: tuple[LevelConfig, ...]`; `MAX_LEVELS = 4`
- `levels.K = 10_000`; `Anchor(values: dict[str, float], tolerances: dict[str, float], scale: float)` with `to_json()`/`from_json()`; `make_anchor(levels, values) -> Anchor` (a relative tolerance is a fraction of the seed's value; when that value is 0 the tolerance is used as an absolute one); `level_steps(levels, anchor, values) -> list[int]` (levels 1 … L−1, clamped to ±K); `level_score(levels, anchor, values) -> float` (`Σ q_i·(2K+1)^(L−2−i) + 0.5·tanh(s_L·(v_L−b_L)/scale_L)`)
- `work/levels.json`: `{stage_key: anchor.to_json()}`, keys `stage.id` and `stage.id + "/private"`

Config rules (each a `ConfigError` naming the key): 2 ≤ levels ≤ 4; unique KPIs; `direction` required per level; `tolerance > 0` required on every level but the last and refused on the last; `relative` only with a tolerance; `objective`/`direction` default to level 1 and must match it when given; no `weights`, no `penalties`, no `stop.target`, no `race`; a per-instance stage defaults to `normalize: none` and refuses an explicit `baseline`.

- [ ] **Step 1: failing tests**: every config rule; arithmetic — seed scores exactly 0; a level-1 gain of one tolerance beats any level-2 loss; within half a tolerance level 2 decides; clamping at K; L=4 keeps integer steps exact and the last level finite; relative tolerance of a 0 anchor becomes absolute; driver (slow) — a solver with two KPIs where the lexicographic best differs from the level-1 best: `summary.best` is the lexicographic one; `work/levels.json` exists; a resumed run reuses the anchor (edit the seed's solver output after the seed: the anchor stays); identity refuses changed levels.
- [ ] **Step 2:** fail. **Step 3: implement** `levels.py`; cascade `_level_score(key, candidate, values)` sets the anchor on the first pass (the seed in a run) and persists it atomically; `_absorb` and `_run_private` use it when `score.levels` is set (penalties 0); driver identity key `levels` and `run_started.levels`.
- [ ] **Step 4:** pass. **Step 5:** commit `evaluate.score.levels: goals in order of importance`.

### Task 7: Levels in status and the dashboard

**Files:** `evolvekit/status.py` (`progress.levels`, `render_text`), `evolvekit/dashboard/index.html` (`renderProgress`), tests in `tests/test_levels.py`.

**Interfaces — produces:** `progress.levels = {"available", "levels": [{"level", "kpi", "direction", "tolerance", "relative", "tolerance_abs", "baseline", "best", "difference", "difference_pct", "steps", "state"}], "deciding": int | None, "verdict": str, "saturated": [ids], "warning": str | None}`; `state` ∈ `better | worse | equal`.

- [ ] **Step 1: failing tests** — a finished levels run: per-level rows, the deciding level, a verdict sentence naming it, `saturated` empty; a synthetic row far beyond K steps is listed with a warning; the text rendering prints one line per level; the dashboard contains the level table markup (`id="levels"` rows rendered from `pr.levels`).
- [ ] **Step 2:** fail. **Step 3: implement.** **Step 4:** pass. **Step 5:** commit `status, dashboard: progress per level`.

### Task 8: Levels in `confirm`

**Files:** `evolvekit/confirm.py` (`Comparison.levels`, `_analyse_levels`, markdown), `evolvekit/cli.py` (`cmd_confirm` exit code), tests in `tests/test_levels.py`.

**Interfaces — produces:** `comparison.per_candidate[cid]["levels"] = {"levels": [{"level", "kpi", "direction", "tolerance_abs", "mean_difference", "mean_pct", "ci95", "state", "clear"}], "verdict": str, "confirmed": bool}`. Differences are absolute, paired per (instance, seed), averaged over seeds per instance, signed so positive is better; a one-sided failure counts as the largest difference measured on that level, against the side that failed; a relative tolerance is a fraction of the baseline's mean on these runs. A level is `equal` when `|mean| ≤ tolerance`; the first level that is not equal decides (`clear` when its interval excludes 0); the last level decides by its interval. Exit code 0 only when every candidate is `confirmed` (better and clear on the deciding level).

- [ ] **Step 1: failing tests** — synthetic `Comparison` runs: equal on level 1 and better on level 2 → verdict `equal on level 1 (... within ...), better on level 2 (...): clear`, confirmed; worse on level 1 beyond tolerance → not confirmed; the markdown has a levels table; an end-to-end `confirm` on a small levels run writes `levels` into `comparison.json`.
- [ ] **Step 2:** fail. **Step 3: implement.** **Step 4:** pass. **Step 5:** commit `confirm: compare level by level`.

### Task 9: `budget.max_hours` and stop requests

**Files:** create `evolvekit/stopping.py`; `evolvekit/config.py` (`BudgetConfig.max_hours`); `evolvekit/evaluate/types.py`, `stages.py`, `fanout.py`, `cascade.py`; `evolvekit/search/driver.py`; `evolvekit/cli.py` (`stop`); `tests/test_time_limit.py`.

**Interfaces — produces:**
- `stopping.STOP_REQUEST = "stop-request"`; `active_seconds(events, *, exclude_session=None) -> float`
- `RunStop(run_dir, *, max_hours, clock=time.monotonic, interval=1.0)`: `.event` (a `threading.Event`), `.reason`, `.triggered`, `.start(used_s)`, `.elapsed_s()`, `.poll() -> str | None`, `.before_generation(projected_s) -> str | None`, `.close()` (stops the watcher; removes `stop-request` when it was the reason)
- Stop reasons: `stopped on request`; `time limit reached: …`; `time limit: another round would not finish within <h> h (…)`
- `StageOutcome.abandoned: bool`, `EvalResult.abandoned: str | None`; `eval_finished` carries `abandoned: true`
- `run_command_stage(..., cancel=None)`, `run_instance_stage(..., stop=None)`, `Cascade(..., stop=None)`
- `python -m evolvekit stop --run-dir DIR` writes the request

Driver behaviour: the watcher polls every second; before the seed and before every generation `before_generation(projected)` (the longest of the last two generations; with only the seed, its duration × `children_per_generation`); a generation with any abandoned result records nothing, keeps `pending.json`, emits no `generation_finished`, and ends the run with the reason (exit 0); the resumed run finishes it first (`generation_adopted`); a cut seed is not recorded and is evaluated again on resume; the run's active time across sessions comes from `events.jsonl`.

- [ ] **Step 1: failing tests** — `RunStop` with a fake clock (limit, projection, request, `close` removes the request); `active_seconds` over two synthetic sessions; config refuses `max_hours: 0`; driver (slow): a solver that writes `stop-request` on its third call and sleeps → the run ends in seconds with `stopped on request`, the cut generation is absent from `runs.jsonl`, `pending.json` holds it, an `eval_finished` is `abandoned`, the request file is gone; running again finishes that generation first; projection stop before generation 1; mid-generation time limit kills a 20 s evaluation within a few seconds with `time limit reached`; the same `max_hours` on resume stays stopped, a larger one continues; `evolvekit stop` writes the file.
- [ ] **Step 2:** fail. **Step 3: implement.** **Step 4:** pass. **Step 5:** commit `budget.max_hours and stop requests`.

### Task 10: The time limit and abandoned runs in `status`

**Files:** `evolvekit/status.py`, `evolvekit/search/driver.py` (`run_started.budget.max_hours`), tests in `tests/test_time_limit.py`.

- [ ] **Step 1: failing tests** — `health.limits` has `budget.max_hours` (used in hours, from the elapsed time); abandoned evaluations count under `abandoned`, not `failed`, and are absent from `failures` and `failure_reasons`; the text says so.
- [ ] **Step 2:** fail. **Step 3: implement.** **Step 4:** pass. **Step 5:** commit `status: the time limit, and abandoned is not failed`.

### Task 11: README, the gate, the PR

- [ ] README: "A time limit, and stopping on request", "Constraints between parameters", "Gates", "Goals in order of importance" (with the exact scalar and its documented quirks: steps counted from the starting point, saturation beyond K steps, at most four levels), `confirm` per level, `status` sections, the exit-code note for `stop`.
- [ ] `python tasks.py check` → all green (lint + every test).
- [ ] Commit `README: time limit, constraints, gates, levels`; push `harness/1-engine`; open the PR against `master` (spec + these features), body with the attribution line.

---

## Self-review

- §6.1: max_hours (Task 9), projection/mid-generation/abandoned/resume (Task 9), stop request + Stop button file (Task 9), `health.limits` (Task 10). ✓
- §6.2: language (Task 1), config errors incl. defaults (Task 2), static rejection (Task 2), operators redraw ×100 (Task 3), LLM prompt (Task 2). ✓
- §6.3: gates after every command stage, `gated` row field, not competing/not promoted, status + dashboard, observations, seed abort exit 4 (Tasks 4–5). ✓
- §6.4: levels config + scalar + anchor persistence + saturation warning (Task 6–7), status `progress.levels` + dashboard (Task 7), confirm per level (Task 8), racing refused (Task 6). ✓
- §13 unit tests for the engine additions: covered by Tasks 1–10. README (Task 11). ✓
