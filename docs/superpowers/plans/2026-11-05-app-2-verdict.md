# App improvement 2: a verdict that decides — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The final check tells a planner what to do — *use it* or *keep today's settings* — with the chance that the new settings are better and the worst plausible case, by one fixed rule the app applies itself; the check spends budget on its own while the answer is open; a run always reads the study it was started with.

**Architecture:** The statistics stay in `evolvekit/confirm.py` (paired differences per held-back case, in % of the starting point): it gains a Student-t probability that the true mean gain is above zero and one `decide()` rule. `evolvekit/harness/execute.py` runs the check, extends it seed by seed while time remains, and reads a copy of `study.yaml` pinned in the run folder. `evolvekit/harness/plan.py` reserves a share of the total time for the check. `evolvekit/app/jobs.py` and `store.py` turn that into words and fields the page shows; `app.html` changes only where a new field appears (the page is redesigned separately).

**Tech stack:** Python ≥ 3.11 standard library (`statistics`, `math`), PyYAML; tests with pytest; the demo harness for end-to-end runs.

**Spec:** `~/.claude/plans/find-ways-to-significantly-hidden-sutherland.md`, proposal 2; the handoff `docs/superpowers/design/2026-09-27-app-redesign-handoff.md`, *What the app will do by then* §1.

## Global constraints

- The user sets nothing: one fixed rule, stated on the page in one sentence.
- Honesty: nothing says "use it" unless the rule holds; the 95 % interval stays visible; every estimate keeps its range.
- Words follow the handoff: *held-back case*, *starting point*, *final check*; never "confidence interval", "p-value", "t-test" on the page (the report may say "95 % interval").
- No new dependency; no SciPy.
- `python tasks.py check` green; branch `app/verdict` from `harness/3-app`, PR against `master`, stacked on #49.

## Review focus

1. Zero spread (every held-back case improves by exactly the same %): `chance` must be 1.0 and `decide` *use*, never a division by zero. Test in Task 1.
2. A held-back case where the candidate failed: `confirm` already counts it as its largest loss; `decide` must then not say *use*. Test in Task 1 (`decide` on `[-8.0, 1.0, 1.2]`).
3. A run whose held-back cases were changed after it ran: results and a re-check must still use the run's own cases. Test in Task 3.
4. The check extension must stop on a stop request and on the time budget, never run past 12 runs per case, and keep the comparison it has when stopped. Test in Task 4.
5. A plan with 1 held-back case: `check_seeds` may grow, but the verdict stays *single* (no interval). Test in Task 2 (`decide` on one difference).

---

## Task 1: the chance of a gain and the rule (`evolvekit/confirm.py`)

**Files:**
- Modify: `evolvekit/confirm.py:81-156` (`_t95`, `paired_summary`)
- Test: `tests/test_confirm.py`

**Interfaces:**
- Produces: `t_cdf(x: float, df: int) -> float`; `paired_summary(differences)` gains keys `"chance"` (float in [0, 1] or `None` when n < 2) and `"worst"` (= `ci95[0]`, or `None`); `decide(summary: dict[str, Any]) -> str` returning `"use" | "keep" | "worse" | "single" | "none"`; constants `USE_CHANCE = 0.9`, `USE_MARGIN_PCT = 1.0`, `WORSE_CHANCE = 0.1`, `RULE_WORDS = "The app says use it when the chance of a gain is at least 9 in 10 and the worst plausible case is within 1 % of today."`

- [ ] **Step 1: Write the failing tests**

```python
def test_the_t_distribution_is_right():
    assert t_cdf(0.0, 5) == pytest.approx(0.5)
    assert t_cdf(4.303, 2) == pytest.approx(0.975, abs=0.001)
    assert t_cdf(-2.920, 2) == pytest.approx(0.05, abs=0.001)
    assert t_cdf(1.96, 2000) == pytest.approx(0.975, abs=0.001)
    assert t_cdf(1.0, 1) == pytest.approx(0.75, abs=0.001)


def test_a_summary_gives_the_chance_of_a_gain_and_the_worst_case():
    handoff = paired_summary([1.3, 0.6, 1.9])            # the design handoff's own study
    assert handoff["chance"] == pytest.approx(0.96, abs=0.01)
    assert handoff["worst"] == pytest.approx(handoff["ci95"][0])
    assert decide(handoff) == "use", "all three cheaper, worst case within 1 %"
    luck = paired_summary([-9.0, 11.5, -0.4])          # review round 1: -10.1 % .. 11.5 %
    assert 0.4 < luck["chance"] < 0.7 and decide(luck) == "keep"
    assert paired_summary([2.0, 2.0, 2.0])["chance"] == 1.0, "no spread, no division"
    assert decide(paired_summary([-2.0, -2.0, -2.0])) == "worse"
    assert decide(paired_summary([-1.5, -1.0, -1.2, -0.8])) == "worse"
    assert decide(paired_summary([5.0, 5.2, 4.8])) == "use"
    assert decide(paired_summary([0.5, 0.5, 0.5, 0.5, 0.5])) == "use"
    assert decide(paired_summary([-8.0, 1.0, 1.2])) == "keep", "one bad held-back case holds it back"
    assert decide(paired_summary([0.4])) == "single" and paired_summary([0.4])["chance"] is None
    assert decide(paired_summary([])) == "none"
    assert decide(paired_summary([3.0, 3.1, 2.9, -1.4])) == "keep", "chance is high but the worst case is beyond 1 %"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_confirm.py -k "t_distribution or chance_of_a_gain" -v`
Expected: FAIL, `ImportError: cannot import name 't_cdf'`.

- [ ] **Step 3: Implement `t_cdf`, the two summary keys and `decide` in `evolvekit/confirm.py`**

`t_cdf(x, df)`: Student's t cumulative distribution through the regularised incomplete beta function `I_z(df/2, 1/2)` with `z = df / (df + x²)`: `cdf = 1 - I/2` for `x ≥ 0`, `I/2` for `x < 0`. Evaluate `I` with the continued fraction (modified Lentz, as in Numerical Recipes `betacf`), 200 iterations, tolerance 1e-12, `math.lgamma` for the prefactor, the symmetry `I_z(a,b) = 1 - I_{1-z}(b,a)` when `z > (a+1)/(a+b+2)`; no table, no SciPy.

`paired_summary`: `chance = t_cdf(mean / (sd / sqrt(n)), n - 1)` when `sd > 0`; `1.0 / 0.0 / 0.5` for `sd == 0` and mean `> 0 / < 0 / == 0`; `worst = ci95[0]`. Both `None` when `n < 2`.

`decide(summary)`: `"none"` when `n == 0`; `"single"` when `n == 1`; `"worse"` when `chance <= WORSE_CHANCE`; `"use"` when `chance >= USE_CHANCE and worst >= -USE_MARGIN_PCT`; else `"keep"`. The verdict sentence in `paired_summary["verdict"]` stays as it is (the CLI prints it).

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_confirm.py -v`
Expected: all PASS (the existing summary test keeps passing: the interval is unchanged).

- [ ] **Step 5: Commit**

```bash
git add evolvekit/confirm.py tests/test_confirm.py
git commit -m "confirm: the chance of a gain, the worst case, and one rule that decides"
```

## Task 2: the decision in the run and in words (`execute.py`, `jobs.py`, the page)

**Files:**
- Modify: `evolvekit/harness/execute.py:379-404` (`_final_check`)
- Modify: `evolvekit/app/jobs.py:573-630` (`_final_words`), `:1096-1110` (`state_line`), the summary text near `:733` and `report_html` near `:1049`
- Modify: `evolvekit/app/static/app.html:1827-1890` (the final-check card)
- Modify: `docs/app.md` (results §1–2)
- Test: `tests/test_app_results.py`

**Interfaces:**
- Consumes: `decide`, `RULE_WORDS` from Task 1.
- Produces: `job.final` gains `"decision": str` (the `decide` value); `_final_words` returns, besides today's keys, `"decision"`, `"chance"` (float or None), `"chance_words"` (str), `"worst_pct"` (float or None), `"rule"` (`RULE_WORDS`); `state` values stay `confirmed | worse | unclear | single | skipped | stopped | missing`, with `confirmed` meaning *use it*; `_chance_words(p: float | None) -> str`.

- [ ] **Step 1: Write the failing tests** (extend `tests/test_app_results.py`; add a helper `words_for(differences, **kw)` that builds the comparison from `paired_summary(differences)` so the test uses the real statistics)

```python
def test_the_final_check_decides_with_the_chance_and_the_worst_case():
    use = words_for([1.3, 0.6, 1.9], what="settings", baseline="PyVRP's own defaults")
    assert use["state"] == "confirmed" and use["decision"] == "use"
    assert use["chance_words"] == "about 19 in 20"
    assert use["line"] == ("Probably better: 1.3 % lower real cost on 3 held-back cases, a chance of about 19 in 20; "
                           "at worst 0.3 % worse than your starting point (PyVRP's own defaults).")
    assert use["advice"] == "Use the new settings: they are probably better on cases the search never saw."
    assert use["rule"].startswith("The app says use it when")
    keep = words_for([-9.0, 11.5, -0.4])
    assert keep["state"] == "unclear" and keep["decision"] == "keep"
    assert keep["line"].startswith("Not proven on 3 held-back cases: a chance of about 12 in 20 of a gain; at worst 10.1 % worse")
    assert keep["advice"] == "Keep your current settings for now: the gain is not proven."
    worse = words_for([-1.5, -1.0, -1.2, -0.8])
    assert worse["state"] == "worse" and worse["line"].startswith("Worse on 4 held-back cases")
    assert words_for([0.4])["state"] == "single"
    assert jobs._chance_words(0.999) == "almost certain" and jobs._chance_words(0.02) == "1 in 20 or less"
    assert jobs._chance_words(0.5) == "about 10 in 20"
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_results.py -k decides -v` — Expected: FAIL on `decision`.

- [ ] **Step 3: Implement**

`execute._final_check`: `decision = decide(result["summary"])` (levels keep their own branch: `decision = "use" if levels confirmed else "keep"`); `final={"candidate", "confirmed": decision == "use", "decision": decision, "comparison"}`.

`jobs._chance_words(p)`: `None → ""`; `p ≥ 0.995 → "almost certain"`; `p ≤ 0.05 → "1 in 20 or less"`; else `f"about {round(p * 20)} in 20"`.

`jobs._final_words`: read `summary["chance"]`, `summary["worst"]`, and decide with `decide(summary)` when `final` has no `decision` (older runs). Lines, with `_pct` and the goal words as today:
- use: `Probably better: {pct} {better} {goal} on {cases}, a chance of {chance_words}; at worst {|worst|} % worse than your starting point{ (baseline)}.` When `worst > 0`: `…; at least {worst} % better than …`.
- keep: `Not proven on {cases}: a chance of {chance_words} of a gain; at worst {|worst|} % worse ({_span(low, high)}).`
- worse: today's line, plus `, a chance of {chance_words} of a gain` before the span.
- advice for use: `Use the new {what}: {they} probably better on cases the search never saw.`
- every branch with a comparison returns `rule: RULE_WORDS`, `chance`, `chance_words`, `worst_pct`, `decision`.

`state_line` (home card): `finished · probably better: 1.3 % lower real cost` for use; `finished · not proven on the held-back cases` (tone warn) for keep; `finished · worse on the held-back cases` (tone bad).

The summary text (`Answer:` line) and `report_html` use `final["line"]` and `final["advice"]` already; add the rule as one line under the advice in both.

`app.html` final-check card: under `r.final.line` add `h("p", {class: "faint"}, r.final.rule)` when present; `finalWord` for `confirmed` becomes "Probably better". Nothing else on the page changes.

`docs/app.md` results §1–2: replace *Confirmed* / *Not distinguishable* with the new sentences and the rule in one line.

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_results.py tests/test_app_jobs.py -v` — Expected: PASS (the slow end-to-end test asserts `final["decision"] in ("use", "keep", "worse", "single")`: add that assertion).

- [ ] **Step 5: Commit**

```bash
git add evolvekit/harness/execute.py evolvekit/app/jobs.py evolvekit/app/static/app.html docs/app.md tests/test_app_results.py tests/test_app_jobs.py
git commit -m "app: the final check says use it or keep, with the chance and the worst case"
```

## Task 3: a run reads the study it was started with

**Files:**
- Modify: `evolvekit/harness/compile.py:329-350` (`write_run`), `evolvekit/harness/study.py` (`load_study`), `evolvekit/harness/execute.py:350-376` (`final_check`), `evolvekit/app/jobs.py:377-380, 768-771, 1072-1083` and `resume_final_check`
- Test: `tests/test_study_run.py`, `tests/test_app_jobs.py`

**Interfaces:**
- Produces: `write_run` copies `<root>/study.yaml` to `<run_dir>/study.yaml`; `study.load_study_file(path: Path) -> Study` (and `load_study(root)` calls it); `execute.run_study_of(root: Path, run_dir: Path) -> Study` (the pinned copy, else the study root's — for runs made before this change); `jobs.status`, `jobs.results`, `jobs.state_line` and `resume_final_check` load the study through it.

- [ ] **Step 1: Write the failing test** (in `tests/test_study_run.py`, slow; `_fix_plan(folder)` is the fixed-plan helper the CI fix introduced — lift it out of the end-to-end test into a helper)

```python
@pytest.mark.slow
def test_a_rerun_final_check_uses_the_cases_the_run_held_back(tmp_path):
    folder = _study(tmp_path)
    _fix_plan(folder)
    run_study(folder, "r1", log=lambda _m: None)
    assert (folder / "runs" / "r1" / "study.yaml").is_file()
    study = load_study(folder)
    study.training, study.test = study.training[1:] + study.test, [study.training[0]]   # swap after the run
    save_study(study, folder)
    job = final_check(folder, "r1", seeds=2, log=lambda _m: None)
    comparison = json.loads((folder / "runs" / "r1" / job["final"]["comparison"]).read_text(encoding="utf-8"))
    assert comparison["instances"] == ["region-60"], "the run's own held-back case, not today's"
```

- [ ] **Step 2: Run to see it fail** — `pytest tests/test_study_run.py -k held_back -v`; Expected: FAIL at the `study.yaml` assertion.

- [ ] **Step 3: Implement** `write_run` copies the file with `shutil.copyfile`; `run_study_of` reads `run_dir / "study.yaml"` with `load_study_file` when present; every caller listed above loads through `run_study_of(root, run_dir)`. `status`'s `can_check` uses the pinned study's `test`.

- [ ] **Step 4: Run** `pytest tests/test_study_run.py tests/test_app_jobs.py tests/test_app_store.py -v` — Expected: PASS.

- [ ] **Step 5: Commit** — `git commit -m "runs: a run keeps the study it was started with, and reads only that"`

## Task 4: the check keeps adding runs while time remains

**Files:**
- Modify: `evolvekit/harness/execute.py:379-404` (`_final_check`), `evolvekit/app/jobs.py:377-472` (`status`: the `check` phase line) and `:636-659` (`_recheck`)
- Test: `tests/test_study_run.py`

**Interfaces:**
- Produces: `execute._extend_check(study, plan, config, run_dir, job, log, *, stop: threading.Event, time_left_s: Callable[[], float], confirm=confirm) -> tuple[Any, int]`: runs the check with `plan.check_seeds`, then one more seed at a time while `decide(summary) == "keep"`, `seeds < len(CHECK_SEEDS)`, `time_left_s() >= seed_cost_s` and `not stop.is_set()`; `seed_cost_s = ceil(2 * m / plan.workers) * plan.run_s`; `job.update(check={"seeds": k})` before each round. `job.final` gains `"seeds": k`. `_recheck` returns `None` when `seeds >= 12` (as today) and its label says `instead of {k}`.

- [ ] **Step 1: Write the failing tests** (monkeypatched `confirm`, so no solver time; `plan_with(...)` = `replace(Plan.fixed(), check_seeds=3, workers=1, run_s=20.0, test_cases=3)`)

```python
def test_the_check_adds_runs_while_the_answer_is_open_and_time_remains(tmp_path):
    calls = []
    def fake_confirm(config, run_dir, *, seeds, instances, label, log, stop):
        calls.append(len(seeds))
        summary = paired_summary([-9.0, 11.5, -0.4] if len(seeds) < 6 else [1.3, 0.6, 1.9])
        return SimpleNamespace(per_candidate={"c1": {"summary": summary}})
    clock = iter([400.0, 300.0, 200.0, 100.0, 10.0, 10.0])
    job = Job(tmp_path, run_id="r", study="s", plan={})
    comparison, seeds = _extend_check(study_with_three_held_back, plan_with(), None, tmp_path, job, lambda _m: None,
                                      stop=threading.Event(), time_left_s=lambda: next(clock), confirm=fake_confirm)
    assert calls == [3, 4, 5, 6], "open at 3, 4 and 5 runs per case; settled at 6"
    assert seeds == 6 and job.state["check"] == {"seeds": 6}


def test_the_check_stops_adding_runs_when_time_the_seeds_or_a_stop_say_so(tmp_path):
    # time_left_s always 30 with a seed cost of 120 -> calls == [3]
    # plan check_seeds=12 -> calls == [12]
    # stop already set -> calls == [3]
```

- [ ] **Step 2: Run to see it fail** — Expected: `ImportError: _extend_check`.

- [ ] **Step 3: Implement** `_extend_check` as specified; `_final_check` calls it with `time_left_s = lambda: plan.hours * 3600 - (now - started_at)` where `started_at` is `job.state["started_at"]` (the run's start, so the extension stays inside the study's total time) and `stop = RunStop(run_dir).event`. The `check` phase line in `jobs.status`: `The final check: {k} runs per case so far on the held-back cases; it adds runs while time remains and the answer is open.`

- [ ] **Step 4: Run** `pytest tests/test_study_run.py tests/test_app_results.py -v` — Expected: PASS; the end-to-end slow test still passes.

- [ ] **Step 5: Commit** — `git commit -m "final check: adds runs on its own while the answer is open and time remains"`

## Task 5: the plan reserves time for the check (`evolvekit/harness/plan.py`)

**Files:**
- Modify: `evolvekit/harness/plan.py:35-40, 146-230` (`CHECK_SEEDS`, `_layout`, `make_plan`, `Plan.words`)
- Test: the file that tests `make_plan` today (`grep -l make_plan tests/`)

**Interfaces:**
- Produces: `CHECK_SHARE = 0.15`; `make_plan` tries `check_seeds` from `kmax` down to 3 at `GOOD_ROUNDS`, then 3 at `MIN_ROUNDS`, then 2 at `GOOD_ROUNDS` and `MIN_ROUNDS`, where `kmax = max(3, min(len(CHECK_SEEDS), floor(CHECK_SHARE * hours * 3600 / seed_cost_s)))` and `seed_cost_s = ceil(2 * m / workers) * run_s`; `Plan.words` says `a final check on {m} held-back cases with {k} runs each`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_long_study_gives_its_check_more_runs_per_case():
    plan = make_plan(training=5, test=3, runs_per_case=1, time_limit_s=20, hours=2.0, workers=4)
    assert plan.check_seeds == 12 and plan.rounds >= 6
    assert "with 12 runs each" in plan.words()


def test_a_short_study_keeps_three_runs_per_case():
    assert make_plan(training=5, test=3, runs_per_case=1, time_limit_s=20, hours=0.5, workers=4).check_seeds == 3
```

Compute the exact `check_seeds` for `hours=0.5` before pinning it; the test pins behaviour, not a guess.

- [ ] **Step 2: Run to see them fail.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Run the plan tests and `tests/test_app_jobs.py -k plan`.** — [ ] **Step 5: Commit** `plan: the final check gets up to 15 % of the time, more runs per case`.

## Task 6: hold back more, and say how firm the answer can be (`store.py`, the page)

**Files:**
- Modify: `evolvekit/app/store.py:478-486` (`suggested_test_count`), `:519` (`document`)
- Modify: `evolvekit/app/static/app.html` (step 3, under the held-back count)
- Test: `tests/test_app_store.py`

**Interfaces:**
- Produces: `suggested_test_count`: `n < 2 → 0`; `n < 6 → round(test_share * n)`; `6 ≤ n < 10 → max(that, 3)`; `n ≥ 10 → max(3, round(0.4 * n))`, always `≤ n - 1`. `split_words(held: int, total: int) -> str` (module level); `document["split_words"]`.

- [ ] **Step 1: Failing tests**

```python
def test_more_cases_hold_more_back(home_with_cases):
    assert counts_for(8) == 3 and counts_for(10) == 4 and counts_for(20) == 8 and counts_for(3) == 1


def test_the_split_says_how_firm_the_answer_can_be():
    assert split_words(0, 5) == "Nothing held back: there will be no final check, so no answer about cases the search never saw."
    assert split_words(1, 5) == "1 held back: the final check can give a number, but no range and no verdict."
    assert split_words(2, 6) == "2 held back: a rough answer; the range will be wide."
    assert split_words(3, 8) == "3 held back: a fair answer for a clear, consistent gain."
    assert split_words(5, 12) == "5 held back: a firm answer."
```

- [ ] **Step 2: Run to fail.** — [ ] **Step 3: Implement** (`split_words`: 0, 1, 2, 3–4, ≥ 5 sentences as pinned; page: one `faint` line under the held-back number box showing `doc.split_words`, redrawn with the step). — [ ] **Step 4: Run `tests/test_app_store.py`.** — [ ] **Step 5: Commit** `app: hold more back when there is more, and say how firm the answer can be`.

## Task 7: the gate, the docs and the PR

- [ ] **Step 1:** `.venv/Scripts/python.exe tasks.py check` — Expected: lint clean, every test passes.
- [ ] **Step 2:** Walk one demo study through the app (`preview_start` with `evolvekit-app`): results show the new sentence, the rule, and the home card's new words; take `docs/app/results-light.png` again with `docs/app/make_screenshots.py`.
- [ ] **Step 3:** `git push -u origin app/verdict`; `gh pr create --base harness/3-app` with the decisions above in the body (the rule's two numbers, the 15 % share, the 0.4 held-back share), ending with the attribution line.
