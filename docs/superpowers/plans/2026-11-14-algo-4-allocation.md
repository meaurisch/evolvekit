# A4 Allocation and Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A study re-plans its rounds from what it measures and ends a search that has settled, giving the saved time to the final check's one extension; grouping says "one group" unless the cases really fall apart, and the rule a planner deploys is the rule the final check validated; warm-start priors decay only for studies that tuned the setting, shrink for unlike cases, and importance counts only correlations that beat a permutation null; a warm-started study's verdict also says how it stands against the harness defaults.

**Architecture:** `harness/plan.py` gains pure functions (`round_records`, `replan`, `converged`) over the `generation_finished` events, which the driver now enriches (the round's own best, its writing time, its TPE score, the noise it was judged with). `Driver` takes an `on_round` hook; `execute` passes one that rewrites `job.json`'s `plan` and returns a stop reason when the search has settled. `groups.compute` clusters the training cases only, keeps k > 1 only at silhouette ≥ 0.5, assigns held-back cases by the stump and records its accuracy. `status._parameter` adds a permutation p-value; `memory.learned` pools on it; `memory.priors_for` counts the right studies and multiplies by a case-similarity factor; the final check of a warm-started study writes a second comparison against the defaults' runs, rendered by the same verdict functions.

**Tech Stack:** Python 3.11 standard library (`statistics`, `random`, `math`, `hashlib`); pytest; ruff; the app's vanilla JS.

**Spec:** `docs/superpowers/specs/2026-11-08-algorithms-design.md` §7 (A4.1, A4.2, A4.3), closing §2.2 S9–S11 and §2.4 P1–P3; read it with §8–§10. Binding amendments: the handoff prompt `docs/superpowers/design/2026-11-09-algorithms-a2-a4-session-prompt.md` "What A0 and A1 changed" (items 1, 3, 7, 8 above all); the A1 plan's Decisions 11–16 (`docs/superpowers/plans/2026-11-08-algo-1-verdict.md`: reference prior, 95 %, Holm one leg, two reads, `CASES_LIMIT_SHARE = 0.25`, percent); the A2 plan's Decisions (`docs/superpowers/plans/2026-11-10-algo-2-noise.md`: `Noise.score_sd` is one candidate's stage score; TPE's prior part `1·uniform + 4w·Gaussian(to_unit(value), 0.1)`, decision 11 Q18); the build log `docs/superpowers/reviews/2026-11-08-algorithms-build-log.md` (A1's measured false-"use" table). Programme B is out of scope.

**Base branch:** `algo/4-allocation`, cut from A3's tip (`algo/3-prompt` at d23f1c0).

```
git switch -c algo/4-allocation d23f1c0
```

## Global Constraints

- Windows first (the dev box); Linux in CI. `pathlib`; files `encoding="utf-8"`, LF endings.
- Stdlib only. No new dependency (§8.7).
- TDD: the failing test first, run it, watch it fail, then the code.
- Per-task test command (written `PYTEST <files>` below), from the worktree root in Git Bash: `PYTHONPATH="$PWD" /c/Users/norri/studio/evolvekit/.venv/Scripts/python.exe -m pytest <files> -q -m "not slow"`. Lint: `PYTHONPATH="$PWD" /c/Users/norri/studio/evolvekit/.venv/Scripts/python.exe -m ruff check --select F,E9 evolvekit tests`. A task's `slow` tests run once, alone, with `-m slow` on the named test only. Never bare `python`/`pytest`; never `python tasks.py check`, a benchmark or a demo study inside a task (the controller runs the gate once).
- **The verdict is one function** (handoff item 1). Every chance, interval and decision comes from `confirm.verdict_summary` / `decide` / `decision_of` over a `Comparison` analysed by `confirm._analyse`. The "against the harness defaults" line is a second comparison through the same functions, labelled; nothing else computes a chance.
- **A comparison is read at most twice** (handoff item 3, A1 decision 12–13). The saved time of a settled search reaches the final check only as a larger time allowance for the **one** extension after the first read, while `confirm.runs_can_help` holds (`CASES_LIMIT_SHARE = 0.25`); never a third read, never more than `len(CHECK_SEEDS) = 12` runs per case. Any change to the final check keeps a seeded null simulation **of `execute._extend_check` itself** under 10 % false "use": a fast test and a slow one asserting `rate + 2·SE < 10 %`.
- **The rule** (A1 decisions 11–16): reference prior; use when chance ≥ 0.95, worse when ≤ 0.05; Holm one leg where a choice is made. Nothing here reintroduces κ0, 90 % or a margin leg.
- **Legacy split** (handoff item 8): a comparison without `posterior` keeps its own words; a `groups.json` of format 1, a status item without `importance.p`, a `generation_finished` event without the new fields each read without raising, by the rule stated where they are read.
- **Schemas.** Status and verdict stay `SCHEMA = 2`: every key added here is new (`importance.p`, `importance.shuffles`; verdict `converged`, `against_defaults`) and is documented in `docs/app.md`, `docs/reference.md` and `evolvekit/skills/shared/evolvekit.md` in the commit that adds it. No existing key changes meaning. `groups.json` changes meaning (held-back cases assigned by the stump) and goes to `"format": 2`. `job.json` gains `plan.rounds_done`, `plan.rounds_left`, `plan.measured`, `search.converged`, `final.saved` (documented in `docs/app.md`'s `job.json` paragraph and `docs/reference.md`).
- **Seeded simulations.** Every statistical rule has a simulation test with a seeded `random.Random` whose **name states the rate it asserts**; each docstring states the estimate below and the test prints the measured rate. No asserted bound may sit within 2 SE of the measured rate; if one does, the implementer stops with NEEDS_CONTEXT and the rates.
- **Words for people** (app, `study` CLI, `rule.md`, verdict lines, `docs/reference.md`'s people sections): study, case, held-back case, starting point, round, final check, combination, measure, setting — never KPI, seed, generation, instance, config, baseline. Chances in percent ("about 96 %"). Engine logs and `--json` keep engine keys. Nothing says "better" before the verdict does; "settled" is a fact about the search, never about the result.
- No vendor or model names in code, docs or tests. Write files with the Write/Edit tools only.
- Every commit message ends with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit on `algo/4-allocation`; never push from a task.

## Review Focus

1. **A resumed or legacy run.** `round_records` over events written before A4 (no `round_best`, `breed_s`, `tpe_ratio`, `noise_sd`) must not converge a run on missing data, and a resumed run must rebuild the same `rounds_left` and the same convergence answer as the uninterrupted one. Pinned in A4.1a (`test_round_records_read_legacy_events_without_inventing_progress`) and A4.1b (`test_convergence_is_rebuilt_from_events_on_resume`).
2. **An expert's plan.** Rounds or generations set by hand (`plan.adjusted`) are a decision: the run re-estimates `rounds_left` but never ends early on convergence. Pinned in A4.1b (`test_hand_set_rounds_never_end_early`).
3. **A held-back case the rule cannot read** (a feature missing or not finite), or one far from every group. It is assigned to the nearest training centroid when it has the clustering features, else to the first group, and the note says so; nothing raises. Pinned in A4.2 (`test_a_held_back_case_the_rule_cannot_read_goes_to_the_nearest_group_and_says_so`).
4. **A defaults study that is not comparable** — same case names with other content, no shared check runs, a per-group or code study, a pre-A1 comparison. The line says "not measured" with the reason, never a number. Pinned in A4.3c (`test_against_the_defaults_is_not_measured_when_the_cases_differ`, parametrised).
5. **A status document on a long run.** 999 shuffles per setting must not make the app's page slow: precomputed ranks, one Pearson per shuffle, `p = None` below `IMPORTANCE_MIN_N`. Pinned in A4.3a (`test_the_permutation_p_on_300_rows_and_8_settings_takes_under_a_second`).

---

### Task A4.1a: Rounds measured, the plan re-estimated, `plan.rounds_left` (P3)

**Files:**
- Modify: `evolvekit/search/driver.py` (`_run`, `_generation_started`, `_generation_finished`, `RunSummary`, `Driver.__init__`: the `on_round` hook; the round's `breed_s`, `round_best`, `noise_sd`, `breadth_at_ceiling` on the event)
- Modify: `evolvekit/harness/plan.py` (module docstring; `RoundRecord`, `Measured`, `round_records`, `replan`; `Plan` fields and `to_json`)
- Modify: `evolvekit/harness/execute.py` (`run_study`, `resume_study`, `run_per_group`: one helper `_round_hook(run_dir, job, plan)` passed as `on_round`)
- Modify: `evolvekit/app/jobs.py` `pace` (reads `plan.rounds_left` when present); `evolvekit/cli.py` unchanged (prints `pace.line`)
- Docs: `docs/app.md` (`job.json`), `docs/reference.md` (the events table: the four new `generation_finished` fields; `job.json`)
- Test: `tests/test_study_plan.py`, `tests/test_study_run.py`, `tests/test_app_results.py` (the two pace tests), `tests/test_driver_events.py` if it exists, else the driver test that asserts `generation_finished`'s fields (grep `generation_finished` in `tests/`)

**Interfaces:**
- Produces, in `driver.py`:
  - `Driver(config, *, run_dir, log=print, on_round: Callable[[int], str | None] | None = None)`. After a round is recorded, logged and the stop checks of that round have not already broken the loop, `reason = on_round(generation)`; a non-empty `reason` sets `summary.stop_reason = reason`, `summary.converged = True` and breaks. Never called for generation 0 or a cut-short round.
  - `RunSummary.converged: bool = False`.
  - `generation_finished` gains: `round_best` (the archive best's fitness as measured by the round, taken where `measured` is taken today, before `_reevaluate_best`), `breed_s` (wall seconds of `_adopt_pending`/`_breed` for the round, `round(…, 3)`; 0.0 for an adopted round), `noise_sd` (`self.stop_policy.noise_sd`), `tpe_ratio` (A4.1b fills it; `None` here), `breadth_at_ceiling` (`self.breadth.current >= self.breadth.config.max` after the round's update, `None` without adaptive breadth). `best_fitness` keeps its meaning (after a re-evaluation).
- Produces, in `plan.py`:

```python
@dataclass(frozen=True)
class RoundRecord:
    generation: int
    duration_s: float
    breed_s: float
    children: int
    best: float | None          # round_best (legacy: best_fitness)
    rebased: float | None       # best_fitness when it differs from round_best (a re-evaluation pooled it), else None
    noise_sd: float             # legacy: 0.0
    tpe_ratio: float | None
    breadth_at_ceiling: bool | None
    legacy: bool                # the event lacks round_best

def round_records(events: Iterable[Mapping[str, Any]]) -> list[RoundRecord]
@dataclass(frozen=True)
class Measured:
    records: tuple[RoundRecord, ...]   # generation 0 included when present
    used_s: float                      # stopping.active_seconds(events): what budget.max_hours counts
def replan(plan: Plan, measured: Measured) -> Plan
```

  - `round_records`: one record per generation from its **last** `generation_finished` event (a resumed generation counts once), ordered by generation.
  - `replan`: with no record of generation ≥ 1, returns `plan` unchanged. Else, over rounds ≥ 1: `eval_s = median(duration_s − breed_s)`, `ai_child_s = Σ breed_s / Σ children` (0 when no child), `round_s = eval_s + plan.children · ai_child_s`, `rounds_done = max generation`, `rounds_left = max(0, floor((plan.search_s − used_s) / round_s))` (0 when `round_s <= 0`). Returns `replace(plan, rounds_done=…, rounds_left=…, measured_round_s=round_s, measured_ai_child_s=ai_child_s)`; every planned field (`rounds`, `round_s`, `ai_child_s`, …) unchanged.
  - `Plan` gains `rounds_done: int = 0`, `rounds_left: int | None = None`, `measured_round_s: float | None = None`, `measured_ai_child_s: float | None = None`. `to_json` adds `"rounds_done"`, `"rounds_left"` (null until the first round) and `"measured": {"round_s", "ai_child_s"}` (null until then).
- Produces, in `execute.py`: `_round_hook(run_dir: Path, job: Job, plan: Plan, *, uses_tpe: bool) -> Callable[[int], str | None]` (`uses_tpe` = `config.search.operators.get("param_tpe", 0) > 0`, for A4.1b) — reads `events.jsonl` (`evolvekit.events.read_events`), `replan`s, `job.update(plan={**job.state["plan"], **replanned.to_json()})`, returns `None` (A4.1b adds the convergence answer). `run_study`, `resume_study` and each group of `run_per_group` (the `_GroupJob`) pass it. `continue_study` does not (its pace is not shown, `jobs.pace` returns None for it).
- Produces, in `jobs.py`: `pace(series, plan, job)` — when `plan.rounds_left` is an int, `rounds = plan.rounds_done + plan.rounds_left` replaces the series estimate; same guards, same line ("At this pace about {rounds} of the {planned} planned rounds will run."), None when `rounds >= planned`. Without the key (a run from before A4) the series estimate stays.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_study_plan.py
def test_replan_reads_the_median_round_and_the_measured_ai_seconds():
    plan = replace(Plan.fixed(children=4), search_s=3600.0)
    records = [RoundRecord(0, 50.0, 0.0, 0, -10.0, None, 0.0, None, None, False)] + [
        RoundRecord(g, d, 10.0, 4, -9.0, None, 0.0, None, None, False) for g, d in ((1, 100.0), (2, 120.0), (3, 300.0))]
    got = replan(plan, Measured(tuple(records), used_s=600.0))
    # eval median(90, 110, 290) = 110; AI 30 s over 12 children = 2.5 s; a round 110 + 4 * 2.5 = 120 s
    assert got.measured_round_s == 120.0 and got.measured_ai_child_s == 2.5
    assert got.rounds_done == 3 and got.rounds_left == 25 and got.rounds == plan.rounds
    assert got.to_json()["rounds_left"] == 25 and got.to_json()["measured"] == {"round_s": 120.0, "ai_child_s": 2.5}

def test_replan_before_the_first_round_changes_nothing():
    plan = Plan.fixed()
    assert replan(plan, Measured((), used_s=0.0)) == plan and plan.to_json()["rounds_left"] is None

def test_round_records_read_legacy_events_without_inventing_progress():
    events = [{"type": "generation_finished", "generation": 1, "duration_s": 5.0, "children": 4, "best_fitness": -3.0}]
    (r,) = round_records(events)
    assert r.legacy and r.best == -3.0 and r.breed_s == 0.0 and r.noise_sd == 0.0 and r.tpe_ratio is None
```

```python
# tests/test_study_run.py
def test_a_study_run_writes_rounds_left_after_every_round(tmp_path): ...
    # the toy study of test_a_study_runs_its_search_and_then_the_final_check; on_round sees every round:
    # job.json's plan has rounds_done == search.generations and an int rounds_left; plan.rounds unchanged
def test_the_driver_calls_on_round_after_each_recorded_round_and_stops_on_a_reason(tmp_path): ...
    # on_round returns None twice, then "converged: test" -> summary.generations == 3, stop_reason, converged True;
    # generation_finished events carry round_best, breed_s >= 0, noise_sd, tpe_ratio, breadth_at_ceiling
```

```python
# tests/test_app_results.py (beside the two pace tests)
def test_the_pace_reads_rounds_left_from_job_json(): ...
    # plan {"rounds": 11, "rounds_done": 2, "rounds_left": 3, "max_hours": 1} -> "At this pace about 5 of the 11 planned rounds will run."
    # rounds_left 9 -> None; no rounds_left key -> the series estimate as today
```

- [ ] **Step 2: Run them to verify they fail** — `PYTEST tests/test_study_plan.py tests/test_study_run.py tests/test_app_results.py`; expected: ImportError on `RoundRecord`, then failures on the missing keys.
- [ ] **Step 3: Implement** the interfaces above. `breed_s` brackets `_adopt_pending` + `_breed` + `_write_pending` with `time.perf_counter()`.
- [ ] **Step 4: Run the tests** — same command; expected PASS; then the driver's existing event tests and `tests/test_resume_random_stream.py` (the hook draws nothing from `self.rng`).
- [ ] **Step 5: Docs** — `docs/app.md` `job.json` keys; `docs/reference.md` events table.
- [ ] **Step 6: Commit** — `feat(plan): re-plan from the measured rounds; plan.rounds_left in job.json (A4.1, P3)`.

---

### Task A4.1b: A settled search ends early; the saved time goes to the one extension (P1, P2)

**Files:**
- Modify: `evolvekit/search/tuning.py` (`propose_tpe_scored`; `propose_tpe` returns its values)
- Modify: `evolvekit/search/operators.py` `param_tpe` (`meta["tpe_ratio"]`)
- Modify: `evolvekit/search/driver.py` (collect the round's TPE ratios where `outcome.meta["params"]` is read; `tpe_ratio` on `generation_finished` = the round's maximum, `None` without one)
- Modify: `evolvekit/harness/plan.py` (`Convergence`, `converged`, `CONVERGE_MIN_ROUNDS = 3`)
- Modify: `evolvekit/harness/execute.py` (`_round_hook` answers convergence; `_final_check` records `final.saved`)
- Modify: `evolvekit/app/verdict.py` (`how_it_ran`'s settled branch; the verdict key `converged`)
- Docs: `docs/app.md`, `docs/reference.md` (`converged`, `final.saved`, "Studies" / the plan paragraph), `evolvekit/skills/shared/evolvekit.md` (`converged` key)
- Test: `tests/test_study_plan.py`, `tests/test_study_run.py`, `tests/test_tuning.py` (or wherever `propose_tpe` is tested; grep), `tests/test_verdict.py`

**Interfaces:**
- Consumes: `RoundRecord`, `round_records`, `_round_hook` (A4.1a); `confirm.runs_can_help`, `LIMITED_BY_CASES`; `execute._extend_check`, `_runs_to_add`, `_time_left`.
- Produces:
  - `tuning.propose_tpe_scored(space, observations, rng, *, taken=()) -> tuple[dict[str, Any], float] | None` — the proposal and its log density ratio `Σ_p log l(x_p) − log g(x_p)` (the `ratio` the loop already maximises); `propose_tpe` = `[0]` of it, same draws, same RNG stream.
  - `plan.CONVERGE_MIN_ROUNDS = 3`.
  - `@dataclass(frozen=True) class Convergence: converged: bool; flat_rounds: int; window: int; second: bool; reason: str`.
  - `plan.converged(records: Sequence[RoundRecord], *, planned_rounds: int, uses_tpe: bool) -> Convergence`:
    - `window = max(CONVERGE_MIN_ROUNDS, ceil(planned_rounds / 3))` (the plan's original `rounds`).
    - **First leg** (no improvement beyond σ̂): `bar` = generation 0's `best`; for each round ≥ 1 in order: `margin = max(1e-6, 1e-9·|bar|, r.noise_sd)`; if `r.best is not None and (bar is None or r.best > bar + margin)`: `bar = r.best`, `flat = 0`; else `flat += 1`; then if `r.rebased is not None`: `bar = r.rebased` (a re-evaluation moves the bar, never the count: `StopPolicy.rebase`'s semantics). Leg holds when `flat >= window`.
    - **Second leg** — with `uses_tpe`: the rounds whose `tpe_ratio` is not None, in order; holds when there are at least 5 and each of the last two is below `median` of all the earlier ones. Without TPE: holds when the last two records have `breadth_at_ceiling` True, or every record has `breadth_at_ceiling` None (a fixed breadth is its own ceiling).
    - Any `legacy` record among the last `window` rounds → not converged (no progress is inferred from missing data).
    - `reason` (engine words, the log and `search.stop_reason`): `f"converged: no round gained beyond the noise for {flat} rounds"`.
  - `_round_hook` returns `Convergence.reason` when `converged` and the plan is automatic (`not {"rounds", "generations"} & set(plan.adjusted)`); then `job.update(search={**job.state.get("search", {}), "converged": True, "flat_rounds": flat, "window": window})`. `run_study` keeps `converged` when it writes `search` after the driver returns (merge, not overwrite).
  - `_final_check`: when `search.converged`, `saved_s = max(0.0, plan.search_s − used_s at convergence)` (from `Measured.used_s` as the hook last saw it, stored by the hook in `search.used_s`), and `final.saved = {"s": round(saved_s), "spent": added > 0, "why": "cases" | "answered" | "extended"}`: `"extended"` when the extension added runs, `"cases"` when `check.limited_by == "cases"`, `"answered"` when the first read was not `keep`. The extension itself is unchanged: `_time_left` already counts from the run's start, so a settled search's unspent hours are the allowance `_runs_to_add` sees, capped at 12 runs per case.
  - Verdict document key `converged`: `{"rounds": done, "planned": planned, "saved_s": s, "spent": bool, "line": str}`, absent unless `search.converged`. `how_it_ran` for a stop reason starting `"converged"`: `f"{rounds}{counts}: the search settled (no round gained beyond the noise for {window} rounds in a row), so it ended early."`. The key's `line`, exact copy:
    - extended: `"The time the search saved went to the final check: more runs per held-back case, read once more."`
    - cases: `"The time the search saved (about {m}) was not spent: only more held-back cases could firm up this answer, not more runs."`
    - answered: `"The time the search saved (about {m}) was not spent: the final check's first read already gave the answer."`
    (`{m}` by `plan._minutes`.)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_study_plan.py
def _rec(g, best, *, sd=1.0, tpe=None, ceiling=None, rebased=None, legacy=False):
    return RoundRecord(g, 10.0, 1.0, 8, best, rebased, sd, tpe, ceiling, legacy)

def test_convergence_needs_window_flat_rounds_and_the_second_leg():
    flat = [_rec(0, 0.0)] + [_rec(g, 0.5) for g in range(1, 4)]          # 0.5 < bar + 1 sd: flat
    assert converged(flat, planned_rounds=6, uses_tpe=False).converged      # window max(3, 2) = 3
    assert not converged(flat[:3], planned_rounds=6, uses_tpe=False).converged
    assert converged(flat, planned_rounds=12, uses_tpe=False).window == 4
    assert not converged(flat, planned_rounds=12, uses_tpe=False).converged
    gained = flat[:2] + [_rec(2, 1.6)] + flat[3:]
    assert converged(gained, planned_rounds=6, uses_tpe=False).flat_rounds == 1

def test_a_sub_noise_step_never_moves_the_bar():
    steps = [_rec(0, 0.0)] + [_rec(g, 0.4 * g) for g in range(1, 5)]   # 0.4, 0.8 flat; 1.2 > 0 + 1: counted
    assert converged(steps, planned_rounds=6, uses_tpe=False).flat_rounds == 1

def test_the_tpe_leg_reads_the_last_two_proposals_against_the_median_of_the_earlier():
    base = [_rec(0, 0.0)] + [_rec(g, 0.0, tpe=t) for g, t in enumerate([5, 4, 6, 3, 2], start=1)]
    assert converged(base, planned_rounds=3, uses_tpe=True).converged
    rising = base[:4] + [_rec(4, 0.0, tpe=7), _rec(5, 0.0, tpe=2)]
    assert not converged(rising, planned_rounds=3, uses_tpe=True).converged

def test_without_tpe_the_breadth_must_sit_at_its_ceiling_for_two_rounds(): ...
    # ceiling [None]*n -> holds; [.., False, True] -> not; [.., True, True] -> holds

def test_a_legacy_round_in_the_window_never_converges(): ...

def test_a_flat_search_is_called_converged_within_its_rounds_in_over_90_percent():
    """Every child exactly as good as the best (true values 0, one run's measured sd 1), 8 children
    a round, 6 planned rounds, no second leg to wait for. Estimate 95.5 % (4000 trials,
    random.Random(20261114)); asserts > 90 %, prints the rate."""

def test_a_search_gaining_one_noise_sd_a_round_is_called_converged_in_under_10_percent():
    """The best child's true value rises 1 sd a round, the other seven sit |N(0, 1)| below it;
    measured with sd 1; 6 planned rounds. Estimate 3.5 % (0.4 % at 12 planned rounds); asserts < 10 %."""

def test_a_search_gaining_half_a_noise_sd_a_round_is_called_converged_in_about_half():
    """The same at 0.5 sd a round: estimate 50 % (44-56 % over 4-12 children, 6 or 12 planned
    rounds). Asserts the band [40, 60] % and prints it: the documented cost of a one-sd bar."""
```

```python
# tests/test_tuning.py
def test_propose_tpe_scored_returns_the_proposal_and_its_ratio_on_the_same_stream(): ...
    # same seed -> propose_tpe(...) == propose_tpe_scored(...)[0]; the ratio is a finite float
```

```python
# tests/test_study_run.py
def test_hand_set_rounds_never_end_early(tmp_path): ...
    # _round_hook on a plan with adjusted ("rounds",) and flat records -> None, but rounds_left is written
def test_convergence_is_rebuilt_from_events_on_resume(tmp_path): ...
    # write events of 4 flat rounds; a fresh _round_hook answers the same reason as the first
def test_a_settled_search_gives_the_check_its_saved_time(tmp_path): ...
    # _extend_check with _confirm_recording(calls, 99): time_left 400 s -> [3, 6]; the converged run's
    # time_left (400 s + the saved 1200 s) -> [3, 12]; never a third call
def test_the_saved_time_is_not_spent_when_the_cases_set_the_answer(tmp_path): ...
    # limited_by cases -> final.saved == {"s": ..., "spent": False, "why": "cases"}; one confirm call

def test_a_settled_null_study_says_use_in_under_10_percent_through_the_real_extension(setting, capsys):
    """Handoff item 3: the product's procedure, not a copy -- execute._extend_check itself, with a
    `confirm` that analyses synthetic runs (`confirm._analyse` on the shape of test_confirm's
    `_a_study`), 4 held-back cases x 3 runs, the time allowance of a settled search (enough for
    all 12 runs per case). Settings: runs noisier than cases (case sd 0.005, run sd 0.02;
    estimate 8.5 %), pure run noise (every case 0, run sd 0.002; estimate 9.0 %). 1000 seeded
    studies each; asserts < 10 %, prints the rate. The recheck is not part of this path."""

@pytest.mark.slow
def test_a_settled_null_study_says_use_in_under_10_percent_through_the_real_extension_two_standard_errors_clear(...):
    """The same on 10000 studies per setting: rate + 2 SE < 10 % (estimate 9.0 % +- 0.29 % on pure run noise)."""
```

```python
# tests/test_verdict.py
def test_a_settled_search_says_so_and_where_its_time_went(tmp_path): ...
    # each of the three `why` values gives its exact line; how_it_ran's settled sentence; no "better"
```

- [ ] **Step 2: Run them to verify they fail** — `PYTEST tests/test_study_plan.py tests/test_tuning.py tests/test_study_run.py tests/test_verdict.py`.
- [ ] **Step 3: Implement** the interfaces. The simulation helpers live in the test module; the null test's `confirm` builds a `Comparison` exactly as `test_confirm._a_study` does (import it rather than copy it if the import is clean).
- [ ] **Step 4: Run the tests** — fast set PASS; then the slow test alone: `PYTEST tests/test_study_run.py -m slow -k two_standard_errors_clear`; record both measured rates for the build log.
- [ ] **Step 5: Docs** — `converged` and `final.saved` in the three docs; `docs/reference.md`'s plan paragraph: the search ends when it has settled (the rule in one sentence, the measured rates of the three convergence tests), and the saved time is the final check's allowance for its one extension.
- [ ] **Step 6: Commit** — `feat(plan): end a settled search early; its time is the check's allowance (A4.1, P1, P2)`.

---

### Task A4.2: Grouping that can say "one group" (S10)

**Files:**
- Modify: `evolvekit/harness/groups.py` (module docstring; `MIN_SILHOUETTE`, `COUNT_PREFIX`, `transformed`, `assign`, `compute`, `read_groups`, `merge_small_groups`, `_centroids`, `rule_lines`)
- Modify: `evolvekit/cli.py` `study groups` printing only if a new note needs a line (grep `study groups`)
- Docs: `docs/reference.md` (groups and `rule.md`), `docs/app.md` (`groups.json` format 2)
- Test: `tests/test_groups.py`, `tests/test_per_group.py`

**Interfaces:**
- Produces, in `groups.py`:
  - `MIN_SILHOUETTE = 0.5`; `COUNT_PREFIX = "rows_"`.
  - `transformed(features: dict[str, float]) -> dict[str, float]` — `log1p` of every finite value of a name starting `COUNT_PREFIX` (a value below 0 stays raw); others unchanged. Used for clustering, centroids and nearest-group only; the stump and its thresholds read raw values (a monotone transform changes neither the split nor the misplaced count, and the rule speaks in the case's own units).
  - `assign(stump: dict, features: dict[str, float]) -> str | None` — the group the one-split rule gives a case (`sides[j]` for the interval its value falls in, thresholds strict `>` as `rule_lines` says "above"), `None` when the feature is missing or not finite.
  - `compute(root, *, k=None, tables=case_tables, features=True) -> dict` (same signature). Without tags:
    1. Features of every case as today (held-back too: the rule must read them).
    2. Points: training cases only, `transformed`, then `standardise` over the training cases.
    3. For each `count` in `KS` (or `[k]`): `kmeans`, `_fits`, silhouette, as today; keep the best.
    4. No `k` given and best silhouette `< MIN_SILHOUETTE` → one group, note: `f"the cases do not fall into clear kinds (how well they separate: {s:.2f} of 1, at least 0.5 needed); all cases are one group"`, `silhouette` = the best value tried.
    5. Else `stump = rule_stump(raw training features, kmeans labels)`; `None` → one group, note `"no single measure tells the kinds apart, so no rule could be checked; all cases are one group"`.
    6. Held-back cases: `assign(stump, …)`; when `None`, the nearest training centroid (transformed, the training standardisation) if the case has the clustering features, else the first group; note per such case: `f"{case}: the rule cannot read it, so it goes to {group}, the nearest"` (or `", the first"`).
    7. `accuracy = {"training": {"right": n − stump["misplaced"], "of": n}, "held_back": {"right": r, "of": m}}`, `r` = held-back cases whose stump group equals their nearest training centroid's group.
  - The doc: `"format": 2`, plus `"assigned": "stump"` (features method), `"stump"`, `"accuracy"`, `"min_silhouette": 0.5`; tags and one group: `"assigned": "tags"` / `"one"`, no stump. Tags behave exactly as today.
  - `read_groups`: `format != 2` → `None` (the study's groups are computed again). A run's own copy (`_run_groups`, finished runs) is read whatever its format.
  - `merge_small_groups(doc)`: for `assigned == "stump"`, after each merge the stump is fitted again on the merged training labels (`rule_stump`; with one group left, none), the held-back cases are assigned again by it (step 6's fallback), and `accuracy` recomputed; the loop goes on while a group has fewer than `MIN_HELD_BACK`. Other docs merge as today.
  - `_centroids(doc)`: for format 2, the training cases only, `transformed`.
  - `rule_lines(doc, decisions)`: when `doc` has `accuracy`, the "misplaced" sentence is replaced by: `f"The rule puts {a} of the {n} cases the search learned from in the group they were found in, and {b} of the {m} held-back cases in the group they are nearest to."` (`m == 0`: the first clause alone). A format-1 doc keeps today's sentence.
- `cache_stump`: a format-2 doc already holds its stump; unchanged otherwise.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_groups.py
def test_the_demo_tour_samples_make_one_group(tmp_path):
    """Acceptance (§7 A4.2): the four demo-tour samples (30, 40, 50, 60 stops, all learned from).
    Fast: fake_tables with the samples' own stop counts read from harnesses/demo-tour/samples/*.json
    (the fake's features equal the real tables': rows_stops, mean_stops_stop_id, mean_stops_weight).
    Best silhouette 0.455 (k = 2) < 0.5 -> method "one"."""
    # also with one held back: one group (two training groups of two cannot be made from three)

@pytest.mark.slow
def test_the_demo_tour_samples_make_one_group_from_a_real_solve(tmp_path):
    """The same through `Home.use_samples` and `case_tables` (the cli-solver, the preview's time limit)."""

def test_the_pyvrp_samples_keep_their_tag_groups(tmp_path):
    """Acceptance: the ten pyvrp samples, tagged by the test from each file's meta.scenario.depots
    ("two depots": city, lake, suburbs, towns, valley; "three depots": the other five), two of each
    held back. compute(features=False) solves nothing (pyvrp is not installed here): method "tags",
    groups exactly the tags, held-back cases by their tags, assigned "tags", no stump, no accuracy;
    merge_small_groups leaves it as it is."""

def test_clustering_uses_the_training_cases_only(tmp_path): ...
    # sizes training [20, 22, 24, 80, 82, 84], held back [500, 21]: the outlier 500 changes neither the
    # training groups nor the silhouette; 500 goes to group 2 by the stump (rows_stops above 52)

def test_counts_enter_the_clustering_as_log1p(): ...
    # transformed({"rows_a": 99.0, "mean_a_x": 99.0}) == {"rows_a": math.log1p(99), "mean_a_x": 99.0}

def test_held_back_cases_follow_the_stump_and_the_rule_reports_both_accuracies(tmp_path): ...
    # test_features_make_two_groups_named_by_their_first_feature's sizes: accuracy training 6/6,
    # held back 2/2; rule_lines' exact sentence; doc["format"] == 2 and doc["assigned"] == "stump"

def test_a_held_back_case_the_rule_cannot_read_goes_to_the_nearest_group_and_says_so(tmp_path): ...

def test_a_format_1_groups_json_is_computed_again(tmp_path): ...

def test_two_clear_kinds_of_case_split_in_over_95_percent():
    """Eight training cases, one feature, two kinds 4 sd apart (random.Random(20261114), 300 trials),
    through compute's own steps 2-4 (a helper `_choose(points)` the task factors out of compute).
    Estimate 100 %; asserts > 95 %."""

def test_cases_of_one_kind_split_in_under_10_percent_on_three_features():
    """Eight training cases from one Gaussian, three features. Estimate 1.3 %; asserts < 10 %.
    (On ONE feature the same cases split in about 80 % -- open question 1.)"""
```

```python
# tests/test_per_group.py
def test_a_merge_fits_the_rule_again_and_reassigns_the_held_back_cases(): ...
def test_rule_lines_of_a_format_1_doc_keep_their_sentence(): ...
```

- [ ] **Step 2: Run them to verify they fail** — `PYTEST tests/test_groups.py tests/test_per_group.py`.
- [ ] **Step 3: Implement.** Existing tests that encode clustering over all cases or format 1 are updated only where A4.2 changes the answer, each change named in the commit body (expected: `test_small_group_collapses_to_one` unchanged; `test_features_make_two_groups_named_by_their_first_feature` gains `format`/`assigned`; `test_cli_groups_json` unchanged in its counts).
- [ ] **Step 4: Run the tests** — PASS; then the slow acceptance alone.
- [ ] **Step 5: Docs** — `docs/reference.md`: one group unless the kinds separate clearly (0.5), counts on a log scale, held-back cases by the rule, the two accuracies in `rule.md`; `docs/app.md`: `groups.json` format 2 keys.
- [ ] **Step 6: Commit** — `feat(groups): one group unless the cases separate; held-back cases by the rule (A4.2, S10)`.

---

### Task A4.3a: Importance against a permutation null, pooled on it (S9)

**Files:**
- Modify: `evolvekit/status.py` (`_parameter`; `PERMUTATIONS = 999`; a helper `_permutation_p`)
- Modify: `evolvekit/harness/memory.py` (`IMPORTANCE_P = 0.1`; `_items` reads `p`; `learned` pools on it)
- Modify: `evolvekit/app/static/js/pages/harness.js` (`importanceCell`)
- Docs: `docs/app.md` (`learned`), `docs/reference.md` (`parameters` row; "Studies over time"), `evolvekit/skills/shared/evolvekit.md` (`importance.p`)
- Test: `tests/test_status.py`, `tests/test_memory.py`, `tests/test_app_learned.py`

**Interfaces:**
- Produces, in `status.py`:
  - `PERMUTATIONS = 999`; `IMPORTANCE_MIN_N` is memory's (5): `status` uses its own `PERMUTATION_MIN_N = 5`.
  - `_permutation_p(xs: Sequence[Any], ys: Sequence[float], statistic: Callable[[Sequence[Any], Sequence[float]], float | None]) -> float | None` — `None` when `len(ys) < PERMUTATION_MIN_N` or the statistic is `None`; else `rng = random.Random(0)` (fresh per call, so each setting's p is independent of the others' order), shuffle a copy of `ys` `PERMUTATIONS` times, count shuffles whose statistic is `>= observed − 1e-12`, `p = (1 + count) / (1 + PERMUTATIONS)`. For Spearman, the statistic is `|Pearson|` of precomputed rank vectors (one `ranks` call each, outside the loop); for `> 2` categories, `_correlation_ratio` over the shuffled fitnesses.
  - `importance` gains `"p"` and `"shuffles": 999` (`p` null with no value). `value`, `sign`, `n`, `method` unchanged.
- Produces, in `memory.py`:
  - `IMPORTANCE_P = 0.1`.
  - `_items` adds `"p": importance.get("p")`.
  - `learned`: a study's `|ρ|` joins `_values` only when `n >= IMPORTANCE_MIN_N` **and** `p` is a number `< IMPORTANCE_P` (an item without `p` counts as not shown: status is rebuilt from the run's files, so only a document injected by a test lacks it). `importance` = mean of those (None without one), `importance_n` = their count, new `importance_of` = studies with `n >= IMPORTANCE_MIN_N`. `_ranked` and `warm_start` read `importance` as today, so `recommended` is re-ranked only on settings with at least one such study.
- `harness.js` `importanceCell`: `importance == null` → `"no study could tell it from chance"` when `importance_of > 0`, else `"not enough tries to tell"`; else `"{value} · from {importance_n} of {importance_of} studies (the others could not tell it from chance)"` (the parenthesis only when they differ).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_status.py
def test_importance_carries_a_permutation_p_with_999_shuffles(tmp_path): ...
    # a run where the value orders the ranking exactly over 8 rows: p == 1/1000 * (1 + count) <= 0.01;
    # under 5 ranked rows: p is None; a three-way choice gets a p too
def test_the_permutation_p_is_the_same_whatever_order_the_settings_come_in(): ...
def test_the_permutation_p_is_below_0_1_in_about_10_percent_of_null_studies():
    """Independent value and score, 8 ranked rows, 2000 seeded trials (random.Random(20261114)).
    Estimate 10.3 % (+- 0.7); asserts [8.0, 12.5] %. At 5 rows the discreteness gives 8.9 %:
    asserts <= 11 %."""
def test_the_permutation_p_on_300_rows_and_8_settings_takes_under_a_second(): ...
```

```python
# tests/test_memory.py  (the fixture's importance tuples become (value, n, p))
def test_importance_pools_only_studies_whose_correlation_beat_chance(tmp_path): ...
    # (0.6, 20, 0.01), (0.2, 20, 0.4), (0.9, 3, 0.001) -> importance 0.6, importance_n 1, importance_of 2
def test_a_setting_no_study_could_tell_from_chance_is_not_reranked(tmp_path): ...
    # three studies, the template tunes `recommended`; a setting with p >= 0.1 everywhere keeps the
    # harness's rank order behind the ones with a pooled importance
def test_a_setting_that_does_nothing_enters_the_reranked_set_in_about_27_percent_after_three_studies():
    """The design's rule, measured: three null studies of 8 rows each through status._permutation_p and
    learned's pooling; 2000 seeded trials. Estimate 1 - 0.897^3 = 27.8 %; asserts [24, 32] %.
    (Open question 2 proposes a combined p, which would make this about 10 %.)"""
```

```python
# tests/test_app_learned.py
def test_harness_page_says_how_many_studies_could_tell_a_setting_from_chance(): ...
```

- [ ] **Step 2: Run them to verify they fail** — `PYTEST tests/test_status.py tests/test_memory.py tests/test_app_learned.py`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests** — PASS.
- [ ] **Step 5: Docs** — the three docs; "What we learned" in `docs/reference.md`: a setting's strength counts a study only when its correlation beat 999 shuffles at p under 0.1.
- [ ] **Step 6: Commit** — `feat(memory): importance against a permutation null, pooled on it (A4.3, S9)`.

---

### Task A4.3b: Priors that decay for a reason, shrink for unlike cases, and reach TPE (S11)

**Files:**
- Modify: `evolvekit/harness/memory.py` (`priors_for`; `_studies_since`)
- Modify: `evolvekit/harness/groups.py` (`study_features`, `similarity`)
- Modify: `evolvekit/harness/compile.py` `write_run` only to pass the new study's root to `priors_for` (if it does not reach it already)
- Docs: `docs/reference.md` "Studies over time" (the `--warm` paragraph, design's "README 'A study from the command line'" since #77)
- Test: `tests/test_memory.py`, `tests/test_groups.py`, `tests/test_warm_start.py` or the test that covers `write_run`'s priors (grep `priors_for` in `tests/`)

**Interfaces:**
- Produces, in `groups.py`:
  - `study_features(root: Path, study: Study) -> dict[str, dict[str, float]] | None` — each case's (training and held-back) features from the **cached** tables only (`<study>/groups/tables/<file>.sqlite` whose stamp matches, as `case_tables` checks it); `None` when any case has none cached. Never solves.
  - `similarity(a: dict[str, dict[str, float]] | None, b: dict[str, dict[str, float]] | None) -> float` — 1.0 when either is `None` or empty; else over the feature names every case of both has: `transformed`, the pooled mean and population sd of each feature over both studies' cases (features with no spread dropped; none left → 1.0); `z_j = (centroid_a_j − centroid_b_j) / sd_j`; `d² = mean_j z_j²` (the RMS, so the factor does not fall with the number of features; open question 15); returns `exp(−d²/2)`.
- Produces, in `memory.py`:
  - `priors_for(home, study, *, slug=None, document_of=None, features: Callable[[Path, Study], dict | None] = groups.study_features) -> dict[str, dict]` — for each tuned setting with a `use` winner: `k` = the finished studies after the winner's that are `kind in ("settings", "both")`, not `not_run`, and have `name in f.study.tuned_settings()`, excluding the winner's and `slug`; `factor = similarity(features(winner_root, winner_study), features(new_root, study))` (`new_root = home.studies_dir / slug`, `None` without a slug); `weight = 0.5 ** (1 + k) * factor`; dropped below `MIN_PRIOR_WEIGHT` or outside the range. Returns `{"value", "weight", "studies_since": k, "similarity": round(factor, 3)}` (compile reads `value` and `weight`; `study-run.json` keeps all four for engineers).
- TPE: `compile_study` already writes `problem.parameters[name].prior`; `tuning._Density` reads it (`extra = 4·w`, A2.5b). This task pins the path with a test; no change there.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_memory.py
def test_the_prior_decays_only_for_settings_studies_that_tuned_the_setting(tmp_path): ...
    # winner A (use, tunes neighbours and restart_after); then B tunes restart_after only, C a code study,
    # D a bake-off, E a study whose search did not run -> neighbours 0.5, restart_after 0.25
def test_the_prior_shrinks_for_unlike_cases(tmp_path): ...
    # features= a fake: identical centroids -> weight 0.5; a one-sd shift in every feature -> 0.5 * exp(-0.5);
    # no cached features on either side -> 0.5; a factor that takes it under 0.05 -> dropped
def test_a_prior_reaches_tpe_as_its_pseudo_count(tmp_path): ...
    # priors_for -> compile_study -> load_config: the parameter's prior is (value, weight) and
    # tuning._Density(param, []).extra == 4 * weight
```

```python
# tests/test_groups.py
def test_similarity_is_one_for_alike_cases_and_falls_with_the_rms_distance():
    a = {"c1": {"rows_s": 10.0}, "c2": {"rows_s": 20.0}}
    assert groups.similarity(a, a) == 1.0 and groups.similarity(a, None) == 1.0
    # a shift of one pooled sd in every feature -> exp(-0.5) within 1e-9, with one feature and with three
def test_study_features_never_solve_and_need_every_case_cached(tmp_path): ...
```

- [ ] **Step 2: Run them to verify they fail** — `PYTEST tests/test_memory.py tests/test_groups.py` (plus the compile test file).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests** — PASS.
- [ ] **Step 5: Docs** — the `--warm` paragraph: the prior halves with every later settings study that tuned that setting, and shrinks when the new study's cases differ from the winner's (from the case features `study groups` keeps; without them it does not shrink); TPE and the local step both read it.
- [ ] **Step 6: Commit** — `feat(memory): priors decay for studies that tuned the setting and shrink for unlike cases (A4.3, S11)`.

---

### Task A4.3c: "Against the harness defaults": a second comparison, labelled (S11)

**Files:**
- Modify: `evolvekit/harness/memory.py` (`defaults_study`)
- Modify: `evolvekit/harness/execute.py` (`_final_check`: after the main answer of a warm-started study, `_against_defaults`)
- Modify: `evolvekit/confirm.py` only if a public builder is needed to analyse a `Comparison` made of given runs (`analysed(comparison) -> Comparison` around `_analyse`); no rule changes
- Modify: `evolvekit/app/verdict.py` (the key `against_defaults`; rendered by `_final_words` on that comparison)
- Modify: `evolvekit/app/static/js/pages/study.js` and `evolvekit/app/report.py`: one line under the verdict's line when the key is present (the words the document drives, §11)
- Docs: `docs/app.md`, `docs/reference.md`, `evolvekit/skills/shared/evolvekit.md`
- Test: `tests/test_memory.py`, `tests/test_verdict.py`, `tests/test_study_run.py`

**Interfaces:**
- Produces, in `memory.py`: `defaults_study(home, study: Study, cases: Sequence[Path]) -> tuple[Finished, Path] | None` — the latest finished study on the harness of kind `settings`/`both`, not `not_run`, with `study.warm is None` and no tuned setting with a hand-set `start`, whose `confirm/final/results.json` exists; returns it and that file. `None` when there is none.
- Produces, in `execute.py`: `_against_defaults(root, run_dir, study, comparison, decision, log) -> None` — only when `study.warm is not None` and the study lives in a library home (`root.parent.name == "studies"`). The side compared: the candidate when `decision == "use"`, else the starting point (the warm study's own baseline runs). Pairs: the defaults study's `BASELINE` runs and the warm study's runs of that side on the same **case content** (sha256 of the case file) and the same check seed. With at least 2 shared cases: a `Comparison(label="against the harness defaults", baseline_id="defaults", candidates=[side], …)` analysed by `confirm._analyse`, written to `<run>/confirm/final/against_defaults.json` with `numbers_from: {"study": slug, "run": run, "cases": n, "seeds": s}`. Otherwise the file holds `{"measured": false, "why": <reason>}`, the reasons: `"no finished study on this application started from the harness defaults"`, `"it held back other cases"`, `"its final check has no runs on the same cases and runs"`. Read once; never extended; no Holm; its decision decides nothing.
- Verdict key `against_defaults`: `{"measured": true, "decision", "chance", "range90_pct", "mean_pct", "line", "from": {...}}` with `line = "Against the harness defaults: " + <_final_words' line of that comparison, first letter lowered>`; or `{"measured": false, "line": "Against the harness defaults: not measured (<why>)."}`. Absent for a study that is not warm-started. A pre-A1 defaults comparison never occurs (the file is written by A4 code); the defaults study's **runs** may be pre-A1 — they are raw runs and are analysed by today's rule.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_verdict.py
def test_a_warm_started_verdict_adds_the_line_against_the_defaults(tmp_path): ...
    # two finished studies in one home (finished_run fixtures): a defaults study and a warm one with the
    # same case files; the warm verdict's `against_defaults.line` starts "Against the harness defaults: "
    # and its chance comes from verdict_summary of the paired log gains; the main `line` is unchanged
@pytest.mark.parametrize("why", ["no defaults study", "other cases", "same names other content", "no shared runs"])
def test_against_the_defaults_is_not_measured_when_the_cases_differ(tmp_path, why): ...
def test_a_study_that_is_not_warm_started_has_no_line_against_the_defaults(tmp_path): ...
def test_against_the_defaults_says_use_for_no_difference_in_about_5_percent():
    """One read of the reference-prior rule (no extension): 4 shared cases x 3 runs, no true difference,
    2000 seeded studies through confirm._analyse. Estimate 5.0 %; asserts [3.5, 6.5] %."""
```

- [ ] **Step 2: Run them to verify they fail** — `PYTEST tests/test_verdict.py tests/test_memory.py tests/test_study_run.py`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests** — PASS; the A4.1b null tests still pass (the main comparison's reads are untouched).
- [ ] **Step 5: Docs** — the key in the three docs; the `--warm` paragraph: the starting point is the previous winner, so the verdict also says how the result stands against the harness defaults when a study that started from them held back the same cases.
- [ ] **Step 6: Commit** — `feat(verdict): a warm study's second line, against the harness defaults (A4.3, S11)`.

---

### Task A4.D: The docs sweep

**Files:** `docs/reference.md`, `docs/app.md`, `evolvekit/skills/shared/evolvekit.md`, `evolvekit/skills/evolvekit-study/SKILL.md` (grep), README only if a sentence there is now false.

- [ ] **Step 1:** `grep -rn "patience\|15 %\|CHECK_SHARE\|silhouette\|k = 2 and 3\|halves with every study\|importance\|rounds will run" docs evolvekit/skills README.md` — every hit is true of A4 (the plan still sizes the check at up to 15 %; the search may end early; groups; priors; importance).
- [ ] **Step 2:** Every new key (`importance.p`, `importance.shuffles`, `converged`, `against_defaults`, `plan.rounds_left`, `plan.rounds_done`, `plan.measured`, `search.converged`, `final.saved`, `groups.json` format 2) appears in each document it must (Global Constraints); `SCHEMA` stays 2 in `status.py` and `app/verdict.py`.
- [ ] **Step 3:** Commit — `docs(a4): the sweep`.

---

### Task A4.C: The gate, the build log, the PR (controller)

**Files:** `docs/superpowers/reviews/2026-11-08-algorithms-build-log.md` (the A4 entry)

- [ ] **Step 1: Statistician review** (a fresh subagent on §7, this plan's Decisions, the changed modules and their tests; asked to simulate the product's real procedure — `converged` over real `generation_finished` events of a toy run, `_extend_check` itself, `compute`'s own steps), repeated until a round finds nothing Critical or Important.
- [ ] **Step 2: The gate**, with nothing else running: `PYTHONPATH="$PWD" /c/Users/norri/studio/evolvekit/.venv/Scripts/python.exe tasks.py check`. Expected: lint clean; every test passes but the known worktree-path failure (build log, step 0).
- [ ] **Step 3: The build log's A4 entry** (same format as A2's): tasks, the decisions ruled, the measured rates of every simulation test (convergence ×3, the settled null ×2 settings fast and slow, grouping ×2, permutation, pooling, defaults line), the acceptance (demo-tour one group; pyvrp tags kept), limits logged, review rounds, gate counts.
- [ ] **Step 4: Commit, push, PR**

```bash
git add docs/superpowers/reviews/2026-11-08-algorithms-build-log.md
git commit -m "docs(a4): the build log's A4 entry

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin algo/4-allocation
gh pr create --base master --title "A4: allocation and memory (S9-S11, P1-P3)" --body "<goal; review items closed; decisions; measured rates; gate counts; statistician rounds; plan path; stacked on #83 (A2) and A3's PR until they merge>

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

- [ ] **Step 5:** Bind the PR with the app's PR tools.

---

## Decisions

Taken by the controller after the builder check (a fresh statistician read §7 and this plan
against the code and simulated the product's procedures — the real `_extend_check`, `_analyse`,
the groups k-means/silhouette, `tuning._Density`, `stats.ranks`; report and scripts kept with the
build log's A4 entry). These override any task text above that disagrees. Departures from the
design's text are marked **(departs)** and listed in the PR for the owner.

1. **Grouping keeps the design's silhouette ≥ 0.5 and no reference test** (Q1, N1). The proposed
   Gaussian-reference test cut false splits on one feature from 81 % to 4–6 % but left two kinds
   4 sd apart split in only 27 %; no single threshold does both jobs at 6–12 cases, and the design
   chose 0.5 on purpose (§8 decision 6: deterministic, explainable). The docs state the measured
   rates by number of cases and features (false splits of shapeless cases; real structure found),
   and the demo tour's 0.455 (one group, 0.045 below the line). The test bands use the measured
   rates (the 3-feature docstring says 2.2 %). The trade-off is put to the owner in the PR.
2. **Pooling importance across studies uses Fisher's combined p < 0.1, then ranks by the mean |ρ|
   over all studies** (Q2) **(departs)**: under the design's "pool over studies with p < 0.1" a null
   setting enters 25.7 % ± 1.0 after three studies and pools to a mean |ρ| (0.75) equal to a real
   ρ = 0.5 setting's; Fisher: 8.5 %.
3. **The permutation p shuffles within each round** (N2, issue 5) **(departs in detail)**: a setting
   that does nothing but drifts along the lineage gets p < 0.1 in 72.5 % of studies with a global
   shuffle (the round-to-round confounding S9 names), 9.8 % within rounds. 999 shuffles,
   `random.Random(0)`, p = (b + 1)/(B + 1), dot-product form; the timing test is `slow`.
4. **Convergence is the first leg alone** (Q3, Q4) **(departs)**: no improvement beyond σ̂ for
   `max(3, ⌈rounds/3⌉)` rounds. The TPE leg cannot be read in a 6-round study (TPE first proposes in
   round 2) and barely separates settled from improving (43 % vs 38 % at round 6); studies have no
   adaptive breadth, so the breadth leg has nothing to read. Convergence also waits until
   `min_big_steps` big steps have been tried, as `StopPolicy` does (N3). Measured: a flat search is
   called settled in 95.4 %; one gaining 1 sd a round in 4.5 %; 0.5 sd a round 50.5 %; **0.25 sd a
   round 81–96 % — documented as the cost**. The 4-children test is pinned at 8 children.
5. **The similarity distance is standardised by the within-study pooled sd** (issue 4, Q15): the
   sd pooled over both studies includes the shift itself and capped the distance at 2 sd (factor
   never below 0.135). RMS form kept.
6. **The "against the harness defaults" line is information about the same runs, never an action**
   (Q16, Q17, issue 6): it goes through the same verdict functions, is labelled, and never changes
   what the study recommends; it is worded as "on the same held-back cases, against the harness
   defaults: …". Its test simulates the real, selected path (the line read after the main verdict)
   and the docs state the measured rates: 6.2 % ± 0.24 "use" under the null overall, 14.9 % after a
   main "use". It is not counted as a third read of the main comparison.
7. **The final check**: the larger extension allowance keeps the 12-run cap (Q7). Measured through
   the real `_extend_check` (40 000 null studies): 8.83 % ± 0.14 / 8.78 % ± 0.14 false "use"; the
   slow test (10 000 studies) asserts rate + 2 SE < 10 %. **Fast guards obey the 2-SE rule too**
   (N5): the fast test uses enough studies, or a looser bound, so its bound is ≥ 2 SE from the
   measured rate.
8. **`final.saved.why` gains a value for "keep, runs could help, nothing added"** (N4, issue 8):
   `at_cap` (the case already has 12 runs, or a stop) — documented with the other values.
9. **Replan** leaves adopted and legacy rounds out of the AI-seconds ratio (Q8 as proposed otherwise).
10. **Format-1 `groups.json`** (Q14): a tags document means the same thing under A4 and is read as
    is; a features document is treated as stale and recomputed.
11. **Test bands** as the builder check measured: permutation p < 0.1 at 8 rows [7.5, 11.5] %;
    pooling, null setting, 3 studies (under the design's rule, kept as a documented comparison)
    [23, 30] %; and every other band ≥ 2 SE from its measured rate.
12. **The PR goes against `master`**, stacked on A2 (#83) and A3, and says so.
13. The other open questions as proposed (5, 6, 9–13, 18, 20, 21), with the builder check's notes.
14. **The similarity factor, after A4.3b's review** (refines decision 5) **(departs)**: (a) a feature
    with no spread inside either study cannot be standardised and is left out of the distance, with
    a note naming it (a fixed fleet size must not drop every prior); (b) each feature's z² is capped
    at 9, so one nearly fixed feature cannot zero the factor; (c) the distance is bias-corrected,
    `d² = max(0, mean z² − c)` with c the exact expected z² of two studies of the same kind from
    sampling alone under the population-form pooled sd, `c = (1/n_a + 1/n_b)·N/(N − 4)`
    (N = n_a + n_b; the plain (1/n_a + 1/n_b) left a bias: 0.82 at four cases). Measured mean factor
    for alike studies: 0.89 / 0.95 / 0.97 at 4 / 8 / 12 cases a study (the clip at 0 keeps it just
    under 1: a pair apart by chance shrinks, a close pair cannot grow); uncorrected it was 0.67 /
    0.86. The docs state the factor's measured mean for alike
    studies and at 1 and 3 sd. `study-run.json` records each prior's value, weight, studies_since,
    similarity and left-out features.
15. **The "against the harness defaults" line, after A4.3c's review** (refines decision 6):
    (a) nothing in it can cost the main answer — any failure, the file write included, is logged
    and becomes "not measured", and `_final_check` is tested for that and for the per-group skip;
    (b) the defaults study must have run at the same time per case and harness version, else "not
    measured (it ran at another time per case / harness version)"; (c) when the defaults study is in
    the warm study's own chain on the same held-back case content (the usual path: a "use" winner
    of that very study), the comparison reuses cases that chose what is compared and leans toward
    "use". *Revised after measurement:* through the product's procedure that path read "use" for no
    true difference in 91.9 % ± 0.7 (the line mostly replays the defaults study's own "use"), so a
    note cannot make it information. On that path the line is **"not measured"** with the reason
    in plain words (the harness defaults' study chose this study's starting point on the same
    held-back cases, so they cannot tell); the docs state the measured 92 % as the reason; (d) people's
    text names studies by their display name and never shows a raw exception.

## Open questions for the builder check

1. **Silhouette ≥ 0.5 does not control a false split in one dimension.** Measured (`groups.kmeans`/`silhouette` as built, cases from ONE Gaussian, k ∈ {2, 3} with two cases per group): one feature, 6 / 8 / 12 training cases → 62 / 80 / 94 % split; two features 14–20 %; three 0.5–4 %. The default features are often one dimension in effect (demo-tour: `rows_stops` and `mean_stops_stop_id` are the same number). The acceptance holds (the evenly spaced samples give 0.455), but a study with eight Gaussian-sized cases would usually split. *Proposed:* keep 0.5 and add a seeded reference: 99 draws (`random.Random(0)`) of the same number of points from a Gaussian with the training points' mean and covariance; split only when the best silhouette is ≥ 0.5 **and** above the 95th percentile of the draws' best silhouettes — about 5 % false splits in any dimension, still stdlib and explainable ("clearer than 95 % of shapeless sets of cases"). The test `test_cases_of_one_kind_split_in_under_10_percent_on_one_feature` is then added. Alternative: accept and document the measured rates.
2. **"Pool |ρ| only over studies with p < 0.1" is a multiple test and a biased mean.** A null setting has at least one p < 0.1 in 1 − 0.9^k of k studies (27 % at three, 41 % at five), and its pooled mean is then of selected values (≈ 0.6 at n = 8), above a real setting's average. *Proposed:* a setting enters the re-ranking when Fisher's combined p over its studies (−2 Σ ln p against χ² with 2k degrees of freedom, closed form for even df) is < 0.1 (about 10 % for a null setting at any k); it is ranked by the mean |ρ| over **all** its studies with n ≥ 5. The pooling test's band then becomes about 10 %. Alternative: the design's rule as written in A4.3a.
3. **"TPE's best proposal score below its median" is undefined.** `propose_tpe` keeps no score, and the best of 48 is always above their median. *Proposed (in the plan):* the round's maximum log l/g over its TPE proposals; the leg holds when the last two are each below the median of all earlier rounds' values, with at least five rounds that have one.
4. **"The breadth rule at its ceiling"** — studies compile no `adaptive_children`, and code studies have no TPE. *Proposed (in the plan):* a fixed breadth is its own ceiling, so without TPE and without adaptive breadth the first leg alone decides.
5. **Which σ̂ and which bar.** *Proposed (in the plan):* `StopPolicy.noise_sd` (the final stage's `Noise.score_sd`, one candidate's score), floored at `max(1e-6, 1e-9·|bar|)`, no Student-t widening (the one-sd bar is not a quantile with a promised rate; the three convergence tests state the rates at a known σ); the bar moves only on a counted improvement (no ratchet) and follows a re-evaluation's pooled best. Known cost: a search gaining half a noise sd a round is called settled in about half the runs (pinned as a band); a longer window or a 0.5·σ̂ bar would trade it against later stops.
6. **"rounds" in max(3, ⌈rounds/3⌉), and hand-set plans.** *Proposed (in the plan):* the original plan's `rounds` (the window must not move as the estimate does); a plan whose rounds or generations were set by hand never ends early, but still gets `rounds_left`.
7. **"Extra seeds" against "read at most twice" and the 12-seed cap.** `_time_left` already counts from the run's start, so a settled search's unspent hours reach `_runs_to_add` with no code change. *Proposed (in the plan):* the cap stays `len(CHECK_SEEDS) = 12`. With more seeds the second read decorrelates from the first and the false "use" climbs toward 1 − 0.95² ≈ 9.75 %, too close to 10 % for any slow test to clear by 2 SE. The procedure is then A1's measured one (8.5–9.0 %), which the A4.1b null test re-measures through `_extend_check` itself.
8. **"The AI seconds measured so far"** — the driver times nothing of the model. *Proposed (in the plan):* `breed_s`, the wall time a round spends writing its children (model-free proposals included, which take milliseconds), per child; the round estimate is `median(duration − breed_s) + children · Σbreed_s/Σchildren`.
9. **The pyvrp "tagged samples" carry no case tags.** Their `tags` are on tasks inside the JSON; no `.tags` file exists, and pyvrp is not installed in this venv (no features can be solved). *Proposed (in the plan):* the acceptance test tags the ten samples from their own `meta.scenario.depots` (five and five), holds two of each back, and asserts the tag groups survive A4.2 untouched (no solve, `features=False`). Alternative reading: a features grouping of the pyvrp samples, which needs the PyVRP venv and a slow test.
10. **Accuracy on held-back cases needs a truth the design does not give.** *Proposed (in the plan):* the nearest training centroid in the training standardisation — how often the deployed rule agrees with the clustering it summarises.
11. **The stump on raw or transformed values.** *Proposed (in the plan):* raw — log1p is monotone, so the split and the misplaced count are the same, and `rule.md`'s threshold stays in the case's own units (a midpoint differs: raw 51 rows vs log1p's ≈ 44).
12. **Merging after the stump assigned the held-back cases.** `merge_small_groups` would leave held-back cases placed by a stump that no longer matches the groups. *Proposed (in the plan):* refit and reassign after every merge.
13. **A forced `k`.** *Proposed (in the plan):* an explicit `--k` (an expert's choice) still needs each group's two training cases but not the 0.5; the note gives the silhouette. Alternative: 0.5 for a forced k too.
14. **`groups.json` changes meaning.** *Proposed (in the plan):* format 2; a study's format-1 file is stale (computed again on the next look or run); a finished run's copy is read as it is, and `rule_lines` keeps the old sentence for it.
15. **"The standardised feature distance".** Euclidean distance grows with the number of features (two samples of the same kind, eight cases each, three features: d² ≈ 0.75, factor 0.69). *Proposed (in the plan):* the RMS over features of the centroid difference in pooled sd, so one pooled sd in every feature gives exp(−½) ≈ 0.61 however many features there are; features from cached tables only (a warm start never solves cases), so the factor is 1.0 until `study groups` or a per-group run has measured both studies' cases. Alternative: solve the new study's cases at `write_run` (≤ the preview's 10 s each).
16. **"Computed from the warm study's own baseline runs".** The warm study's baseline is the previous winner; what is set against the defaults is not said. *Proposed (in the plan):* the configuration the verdict tells a person to run — the candidate when the decision is use, else the starting point (then the warm study's own baseline runs, as the design says) — paired run by run with the defaults study's baseline runs on the same case content and check seed. Logged limit: the two sides ran in different sessions, so for a wall-clock solver machine drift is in the difference, not only noise.
17. **Is the defaults line a third read?** *Proposed (in the plan):* no — another question (another baseline), read once, never extended, no Holm, deciding nothing; its one-read false "use" is about 5 % (asserted). The docs say the line is information beside the answer.
18. **"The latest study whose starting point was the defaults".** *Proposed (in the plan):* no `warm`, no hand-set `start` on a tuned setting, a settings study (not per-group, code or bake-off), with a final check's `results.json`. An input settings file also moves the starting point; *proposed:* such a study does not count either (`_given_settings` non-empty).
19. **The permutation's cost.** Measured: ~15 ms per setting at 60 rows, ~75 ms at 300, pure Python. *Proposed (in the plan):* precomputed ranks and a Pearson per shuffle, asserted under 1 s for 300 rows × 8 settings; if it fails, `build_status` computes p only for a finished run (the app's live page shows the value without p while searching).
20. **Per-group runs.** *Proposed (in the plan):* each group's search re-plans and may settle within its own share; its saved time stays its own final check's (each group's `_time_left`).
21. **P1 (the largest children count) is not touched.** *Proposed:* accept — the convergence stop removes the cost the review named (a converged search burning its hours); re-choosing breadth mid-run is a search change that needs the benchmark.
