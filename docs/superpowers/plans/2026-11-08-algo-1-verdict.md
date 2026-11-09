# A1 The Verdict Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One decision rule, said in one language, valid under the looks the product actually takes. The final check compares log ratios. One posterior rule decides, and every page, command and report renders from its document. The check stops adding runs when only more held-back cases could help. A choice among branches, candidates or groups pays for its multiplicity. Goals in order of importance are judged with intervals.

**Architecture:** `evolvekit/stats.py` gains the log-gain helpers, the conjugate posterior (`posterior_mean_t`) and Holm. `evolvekit/confirm.py` gains `verdict_summary`, the only function that turns per-case effects into the numbers and the decision. `decide` and `decision_of` read that summary and nothing else. `app/verdict.py`, `harness/execute.py`, `app/tries.py` and `cli.py` stop computing their own statistics and read the summary. The frequentist interval and the Wilcoxon test stay in `comparison.json`/`.md` under `frequentist`. A1.1–A1.5 are one task each, except A1.4, which is two (bake-off/top:N, and per-group).

**Tech Stack:** Python 3.11 standard library only; pytest; ruff (`python tasks.py lint`).

**Spec:** `docs/superpowers/specs/2026-11-08-algorithms-design.md` §4 (A1.1–A1.5 and the statistician review at its end). It closes §2.2 S1–S4, S7 and S8. Read it with §8 (decisions 3, 7 and 8), §9 and §10. Vocabulary and testing rules come from `docs/superpowers/specs/2026-11-07-agent-path-design.md` §2 and §9. Sections 3 and 5–7 and programme B are out of scope. A0's plan (`docs/superpowers/plans/2026-11-08-algo-0-correctness.md`) and its Decisions are the base this plan builds on.

**Base branch:** `algo/1-verdict`, cut from the tip of `algo/0-correctness` (A0 finished: `stats.py` exists, the hold-out is a veto, and status SCHEMA 2 judges a per-case 95 % interval).

```
git switch -c algo/1-verdict algo/0-correctness
```

## Global Constraints

- Windows first (the dev box); Linux in CI. Use `pathlib`. Write files with `encoding="utf-8"`.
- Stdlib only. No scipy, no new dependency (§8.7, §11). `t_cdf`, `_t95` and `_betainc` in `stats.py` are the only distribution functions.
- TDD: write the failing test first, run it, watch it fail, then write the code.
- The gate is `python tasks.py check` (lint, then every test). The only entry points are `python tasks.py test | full | run | check | lint | dist`. Never run a demo study or `tasks.py run` beside a full gate: the gate has timing-sensitive tests.
- Per-task test command: `.venv/Scripts/python.exe -m pytest <files> -q`.
- **The verdict is the only claim.** `confirm.verdict_summary` (A1.2) is the one place that decides. `decide`/`decision_of` read only its output. Every page, command and report renders from the document that holds it (`comparison.json` → `app/verdict.py`'s document). Nothing recomputes a chance, an interval or a decision anywhere else.
- **Every statistical rule gets a seeded simulation test.** It uses a seeded `random.Random`, its name states the error rate it asserts, and it asserts that rate (§10).
- **The words.** Anything meant for people uses the app's vocabulary: held-back case, starting point, run, measure, goal, rule, kind of case, branch. Never seed, instance, baseline or KPI. `--json` documents and `comparison.json` keep the engine's keys. The `confirm` CLI and `comparison.md` are engineers' tools and keep their present terms.
- No vendor or model names in code, prompts, docs or tests.
- Write files with the Write/Edit tools only, never with PowerShell `Set-Content`/`Out-File`.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Docs ship in the same PR as the behaviour (§10). `docs/reference.md` is the manual. `README.md` is the front page and changes only if its example line goes wrong.
- Never push to `master`. Push the branch, open a PR, and let a maintainer merge.

## Review Focus

1. **A comparison with no spread at all** (every per-case effect equal, including all zero; `identical` can be false when only another measure differed). The prior from the data is 0, so the posterior scale can be 0. It must not divide by zero. Zero effects give `chance` 0.5 and `keep`. Equal non-zero effects give a finite chance below 1 (`T_{n+1}(√(n(n+1)))`, ≈ 0.954 at n = 2). Pinned in A1.2 (`test_no_spread_is_no_division_and_no_certainty`).
2. **An objective that reaches 0 or goes negative** (a count of late deliveries, a profit). On the log scale such a pair is dropped and counted as `nonpositive_pairs`. The sentence must name the dropped pairs, and a case whose every pair is dropped leaves `n`. Pinned in A1.1 (`test_a_pair_with_a_value_of_zero_on_either_side_is_dropped_and_counted`). Whether dropping is right at all is open question 3.
3. **A comparison written before A1** (a `summary` without `chance_margin`, read by `app/verdict.py`, `jobs.run_line` and `decide`). It must still give the same decision as its `job.json` and the same line. Pinned in A1.2 (`test_a_comparison_written_before_the_posterior_decides_as_it_did`).
4. **A bake-off with one branch, and a per-group run with one group after merging.** Holm over k = 1 changes nothing. "Chosen among 1 branches" and "with 1 kinds of case compared" must never be said. Pinned in A1.4a and A1.4b.
5. **A per-group study grouped by tags, so `groups.json` has no features.** The merge of a group with fewer than two held-back cases needs a "nearest" group without feature distances. The rule: the group with the most held-back cases, ties to the first in order. If every group is too small, the result is one group, not a crash. Pinned in A1.4b.

---

### Task A1.1: Log-ratio effects (S4)

**Files:**
- Modify: `evolvekit/stats.py`: add `log_gain`, `pct_from_log`, `margin_log`, and update `__all__` and the module docstring.
- Modify: `evolvekit/confirm.py`: `_analyse` and `_PAIR_COUNTS` (`zero_baseline_pairs` → `nonpositive_pairs`), `_counted_as`, `render_markdown`, `_dropped`, the module docstring ("Per instance, the log ratio to the baseline …").
- Modify: `evolvekit/status.py`: `_verdict_per_instance` judges log ratios, as S4 names it.
- Modify: `evolvekit/app/tries.py`: `_gain` becomes the log ratio (the live check view averages the same quantity the verdict does).
- Test: `tests/test_confirm.py`, `tests/test_status.py`

**Interfaces:**
- Produces: `stats.log_gain(base: float, mine: float, direction: str) -> float | None`. It returns `s·(log base − log mine)` with s = +1 for `"minimize"` and −1 for `"maximize"`, and `None` when either value is ≤ 0.
- Produces: `stats.pct_from_log(d: float, direction: str) -> float`. This is the percentage of a log gain, relative to the starting point: `100·(exp(d) − 1)` for `"maximize"`, `100·(1 − exp(−d))` for `"minimize"`. **Deviation from the design, on purpose.** The design writes `100·(exp(d) − 1)` for both directions. For a minimised measure that is the change in percent of the *candidate's* value: a cost cut from 100 to 90 would read "11.1 % lower" instead of 10 %. See open question 1.
- Produces: `stats.margin_log(margin_pct: float, direction: str) -> float`. It is the gain-scale threshold `x_m` with `pct_from_log(x_m) == −margin_pct`: `log(1 − m/100)` for `"maximize"` (the design's formula) and `−log(1 + m/100)` for `"minimize"`.
- Produces: in `comparison.json`, each `per_instance` row gains `log_gain` (the mean over the case's pairs, failures imputed), `seed_var` (the sample variance of those pair gains, `None` below 2 pairs) and `nonpositive_pairs`. `improvement_pct = pct_from_log(log_gain)`. Each candidate gains `log_gains` (the per-case `log_gain` list, in instance order) and `failure_counted_as_log`. `failure_counted_as_pct` becomes `pct_from_log(largest)`. The summary keys `mean`, `ci95`, `worst` stay in percent (`pct_from_log` of the log-scale quantity), and `chance` is unchanged. `zero_baseline_pairs` is gone everywhere.

- [ ] **Step 1: Write the failing tests** (`tests/test_confirm.py`, statistics section; add `import math` and `import random` at the top):

```python
def test_a_gain_is_a_log_ratio_and_its_percentage_is_of_the_starting_point():
    from evolvekit.stats import log_gain, margin_log, pct_from_log

    assert log_gain(100.0, 90.0, "minimize") == pytest.approx(math.log(100 / 90))
    assert log_gain(100.0, 110.0, "maximize") == pytest.approx(math.log(1.1))
    assert log_gain(0.0, 90.0, "minimize") is None and log_gain(100.0, -1.0, "maximize") is None
    # Half as much and twice as much are one step either way: no -50 % against +100 %.
    assert log_gain(100.0, 50.0, "minimize") == pytest.approx(-log_gain(100.0, 200.0, "minimize"))
    assert pct_from_log(log_gain(100.0, 90.0, "minimize"), "minimize") == pytest.approx(10.0), "10 % lower"
    assert pct_from_log(log_gain(100.0, 110.0, "maximize"), "maximize") == pytest.approx(10.0), "10 % higher"
    for direction in ("minimize", "maximize"):
        assert pct_from_log(margin_log(1.0, direction), direction) == pytest.approx(-1.0)
    assert margin_log(1.0, "maximize") == pytest.approx(math.log(0.99)), "the design's x_m"


def test_the_comparison_averages_log_ratios_not_percentages_of_a_noisy_run():
    """S4: halving on one case and doubling on another is no change at all on the log
    scale. In percent of the starting point's run it was +50 % and -100 %: a mean of -25 %."""
    from evolvekit.confirm import BASELINE, Comparison, _analyse

    comparison = Comparison(label="t", objective="cost", direction="minimize", stage="full", seeds=[1],
                            instances=["a", "b"], baseline_id="g000-c0001", candidates=["c1"])
    for instance, mine in (("a", 50.0), ("b", 200.0)):
        comparison.runs += [
            {"configuration": BASELINE, "instance": instance, "seed": 1, "ok": True, "kpis": {"cost": 100.0}},
            {"configuration": "c1", "instance": instance, "seed": 1, "ok": True, "kpis": {"cost": mine}},
        ]
    _analyse(comparison)
    result = comparison.per_candidate["c1"]
    a, b = result["per_instance"]
    assert a["log_gain"] == pytest.approx(math.log(2)) and b["log_gain"] == pytest.approx(-math.log(2))
    assert (a["improvement_pct"], b["improvement_pct"]) == (pytest.approx(50.0), pytest.approx(-100.0))
    assert result["log_gains"] == [pytest.approx(math.log(2)), pytest.approx(-math.log(2))]
    assert result["summary"]["mean"] == pytest.approx(0.0, abs=1e-12)
```

Replace `test_a_pair_with_a_baseline_of_zero_is_counted_too` with this (Review Focus 2):

```python
def test_a_pair_with_a_value_of_zero_on_either_side_is_dropped_and_counted(tmp_path):
    """A log ratio of 0 is no number. Such a pair is dropped and the verdict says
    how many; a case whose every pair is dropped is not a case of the comparison."""
    config_path = _write_config(tmp_path)
    solver = (tmp_path / "solver.py").read_text(encoding="utf-8")
    (tmp_path / "solver.py").write_text(
        solver.replace("print(json.dumps({\"cost\": scale",
                       "print(json.dumps({\"cost\": 0.0 if (a.instance, a.x) == (\"small\", 0.0) else scale"),
        encoding="utf-8",
    )
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001, 1003], log=lambda m: None)
    result = comparison.per_candidate["g003-c0012"]
    assert result["nonpositive_pairs"] == 2 and result["failed_pairs"] == 0 and result["pairs"] == 4
    assert result["per_instance"][0]["nonpositive_pairs"] == 2 and result["per_instance"][0]["log_gain"] is None
    assert result["summary"]["n"] == 2, "small has no comparable pair: it is not a case"
    assert "2 pair(s) had a value of 0 or below and are not in this comparison" in result["summary"]["verdict"]
    assert "zero_baseline_pairs" not in json.dumps(comparison.to_json())
```

In `test_a_pair_only_the_candidate_failed_counts_as_its_largest_loss`, add after the `worst` line:
`assert result["failure_counted_as_log"] == pytest.approx(-math.log(0.95), abs=0.01)`.

In `tests/test_status.py`, add:

```python
def test_the_live_verdict_per_instance_averages_log_ratios(tmp_path):
    """S4 in status too: halving on one instance and doubling on another is no gain."""
    run = RunDir(tmp_path)
    stages = [STAGES[0], {**STAGES[1], "instances": ["a", "b"]}]
    run.event("run_started", 1000, objective="cost", direction="minimize", stages=stages,
              first_generation=1, generations_planned=5, budget={}, stop={})
    run.row("g000-c0001", 0, 100.0)
    run.row("g001-c0002", 1, 90.0, parent_id="g000-c0001")
    for seed in range(2):
        for instance, mine in (("a", 50.0), ("b", 200.0)):
            for cid, cost in (("g000-c0001", 100.0), ("g001-c0002", mine)):
                run.event("eval_finished", 900, candidate_id=cid, stage="full", seed=seed, private=False,
                          instance=instance, ok=True, duration_s=1.0, kpis={"cost": cost})
    improvement = build_status(tmp_path, now=NOW)["progress"]["improvement"]
    assert improvement["n"] == 2 and improvement["mean_gain"] == pytest.approx(0.0, abs=1e-9)
```

- [ ] **Step 2: Run the tests and check that they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_confirm.py tests/test_status.py -q`
Expected: FAIL (`log_gain` is not defined, there is no `log_gain` key, the status mean is −25).

- [ ] **Step 3: Implement**
  - **`stats.py`.** Add the three helpers exactly as the Interfaces block says. Use `math.log`/`math.exp`. Raise `ValueError` on any direction other than `"minimize"`/`"maximize"`.
  - **`_analyse`.** The per-pair gain is `log_gain(base[key], mine[key], comparison.direction)`. A pair that both sides finished but whose gain is `None` counts toward `nonpositive_pairs` and is skipped. `largest = max(|g|)` over the candidate's finished, comparable pairs. Imputation keeps its rule on the log scale: `−largest` when only the candidate failed, `+largest` when only the baseline did. A one-sided failure is imputed whatever the finished side's value (open question 4). Per case, `log_gain = fmean(gains)` and `seed_var = statistics.variance(gains)` when there are 2 or more gains. The case's `baseline`/`candidate` means are unchanged. `improvement_pct = pct_from_log(log_gain, direction)`. The candidate's `log_gains` is the list of case `log_gain` values that are not `None`. `summary` = `_pct_summary(paired_summary(log_gains), direction)` and `finished_only` = `_pct_summary(paired_summary(finished_only_logs), direction)`.
  - **`_pct_summary(summary, direction) -> dict`** (private, in `confirm.py`). A copy of a log-scale `paired_summary` with `mean`, `ci95` and `worst` passed through `pct_from_log`, `chance` kept, plus `"log": {"mean", "sd", "ci95"}` holding the log values. A0.3 decision 10 still holds: status does not impute failures, so the status/confirm agreement test still uses a run with no failed pair.
  - **`_counted_as`.** Replace the zero-baseline note with `f"{n} pair(s) had a value of 0 or below and are not in this comparison"`. The failure sizes say `f"{pct_from_log(largest):.3g} %"`, which needs `direction`: pass it in.
  - **`render_markdown` and `_dropped`.** Replace "baseline of 0" with "a value of 0 or below" (`_dropped`: `"N not positive"`). The header sentence becomes: "Improvement is the log ratio of the baseline's and the candidate's value on the same instance and seed, averaged over the seeds of an instance, and shown in percent of the baseline; positive is better."
  - **`status._verdict_per_instance`.** Compute `gains = {run: log_gain(a[run], b[run], direction) …}` and skip `None`. Then `summary = paired_summary(per_instance_means(gains))`. `mean_gain` and `ci95` are reported through `pct_from_log`; `sd_gain` stays the log sd, and `unit` becomes `"percent of the baseline (from log ratios), per instance"`. `_judged` reads the log interval, so the verdict is unchanged: `clear` iff it lies above 0.
  - **`tries._gain(start, value, minimize)`.** Return `pct_from_log(log_gain(start, value, d), d)` with `d = "minimize" if minimize else "maximize"`, or `None`. The rest of `tries._check` changes in A1.2.

- [ ] **Step 4: Run the tests and check that they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_confirm.py tests/test_status.py tests/test_levels.py tests/test_app_tries.py tests/test_verdict.py tests/test_app_results.py -q`
Expected: PASS. `test_a_real_improvement…` still finds ≈ 5.0 % (x = −0.05 is a 5.0 % cut). `test_against_names…` still finds 5.26 % (0.90 against 0.95).

- [ ] **Step 5: Commit**

```bash
git add evolvekit/stats.py evolvekit/confirm.py evolvekit/status.py evolvekit/app/tries.py tests/test_confirm.py tests/test_status.py
git commit -m "feat(a1): the final check compares log ratios (S4)

Per pair the gain is s*(log base - log mine); per case the mean over
the runs; every percentage is of the starting point, from the log
quantity. Pairs with a value of 0 or below are dropped and counted
(nonpositive_pairs replaces zero_baseline_pairs). Failure imputation
keeps its rule on the log scale. status per instance does the same.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A1.2: One decision, in the words the page already uses (S1, S2, S8)

**Files:**
- Modify: `evolvekit/stats.py`: add `Posterior` and `posterior_mean_t`.
- Modify: `evolvekit/confirm.py`: add `USE_MARGIN_CHANCE`, the new `RULE_WORDS`, `verdict_summary`, `_prior_of` and `decision_of`; rewrite `decide`; add the `Comparison` fields `prior_scale`, `prior` and `looks`; `_analyse` builds its summaries through `verdict_summary`; `confirm` reads and writes `looks`; update `render_markdown`.
- Modify: `evolvekit/harness/execute.py`: `_decision = decision_of` (kept as a name for the importers), plus the "read k times" log line in `_extend_check`.
- Modify: `evolvekit/app/verdict.py`: `SCHEMA = 2`; `_final_words` reads `summary` only; `_chance_words` is unchanged; `_outcome` is unchanged (it reads `change_pct`, which is now `pct_from_log` of the case's log gain); the module docstring says that `chance` is the posterior P(gain > 0) and `ci95_pct` a 95 % credible interval.
- Modify: `evolvekit/app/tries.py`: `_check` builds its summary with `verdict_summary` (prior from the data).
- Modify: `evolvekit/cli.py`: `cmd_confirm` exits 0 iff `decision_of(result) == "use"` for every candidate.
- Modify: `evolvekit/app/static/js/pages/study.js:346` and `:1016`: the rule sentence (the page states the rule, `RULE_WORDS` being its long form).
- Test: `tests/test_confirm.py`, `tests/test_status.py`, `tests/test_verdict.py`, `tests/test_app_results.py`, `tests/test_study_run.py`

**Interfaces:**
- Produces: `stats.Posterior`, a frozen dataclass `(n: int, mu: float, scale: float, df: int, prior_scale: float)`, with:
  - `p_above(x: float) -> float`: `1 − t_cdf((x − mu)/scale, df)`. When `scale == 0` it is 1.0 if `mu > x`, 0.0 if `mu < x`, 0.5 if equal.
  - `interval95() -> tuple[float, float]`: `mu ∓ _t95(df)·scale`.
- Produces: `stats.posterior_mean_t(d: Sequence[float], prior_scale: float) -> Posterior`. It raises `ValueError` on an empty `d` or a negative `prior_scale`. The algorithm (the design's, re-derived: the standard NIG update, prior μ|σ² ~ N(μ0, σ²/κ0), σ² ~ Scaled-Inv-χ²(ν0, σ0²)):
  ```
  μ0 = 0, κ0 = 1, ν0 = 1, σ0² = prior_scale²
  dbar = mean(d);  S = Σ (d_i − dbar)²
  κn = κ0 + n;  μn = (κ0·μ0 + n·dbar) / κn;  νn = ν0 + n
  νn·σn² = ν0·σ0² + S + κ0·n/κn · (dbar − μ0)²
  μ | data ~ Student-t(νn, location μn, scale σn/√κn)
  P(gain > x) = 1 − T_νn((x − μn) / (σn/√κn))      (t_cdf from stats.py; νn is an int)
  ```
  So `Posterior(n, mu=μn, scale=σn/√κn, df=νn, prior_scale)`.
- Produces: `confirm.USE_CHANCE = 0.9`, `confirm.USE_MARGIN_CHANCE = 0.95` (new) and `confirm.WORSE_CHANCE = 0.1`. "worse" means P(gain < 0) ≥ 0.90, that is `chance ≤ WORSE_CHANCE`.
- Produces: `confirm.RULE_WORDS = "Use when the chance of a gain is at least 9 in 10 and the chance of losing more than 1 % is under 1 in 20."`
- Produces: `confirm.verdict_summary(d: Sequence[float], *, prior_scale: float | None, direction: str, prior: str | None = None) -> dict`. For `n ≥ 2` it returns:
  - `n`, `mean` (`pct_from_log(dbar)`, the paired mean, see open question 6), `posterior_mean_pct` (`pct_from_log(μn)`).
  - `ci95` (the `pct_from_log` of `interval95()`), `chance` (`p_above(0)`), `chance_margin` (`p_above(margin_log(USE_MARGIN_PCT, direction))`), `worst` (`pct_from_log(interval95()[0])`, i.e. `exp(q_0.025) − 1` in the design's words).
  - `sd` (the log sd of `d`), `wins`, `losses`.
  - `prior_scale` and `prior`. With `prior_scale=None` the prior scale is `statistics.stdev(d)` and the label is `"from the data"`; otherwise it is the given value and the label is `prior` (default `"from the search's repeated runs"`).
  - `posterior: {"mu", "scale", "df"}` (log units).
  - `frequentist`: `_pct_summary(paired_summary(d), direction)`, labelled `"label": "frequentist 95 % confidence interval and Wilcoxon test, for engineers"`.
  - `decision` (`decide(self)`) and `verdict` (the sentence, Step 3).

  For `n == 1` it returns `mean` and `n`, `None` for `ci95`, `chance`, `chance_margin` and `worst`, decision `single`, and the verdict "one instance: no chance can be given". For `n == 0`, `n: 0`, decision `none`, verdict "nothing to compare".
- Produces: `confirm.decide(summary: dict) -> str`: `n == 0 → none; n == 1 → single; chance_margin ≥ 0.95 and chance ≥ 0.90 → use; chance ≤ 0.10 → worse; otherwise keep`. A summary without `chance_margin` (written before A1) is decided by the old rule (`chance ≥ 0.9 and worst ≥ −1 → use; chance ≤ 0.1 → worse`), so a stored comparison keeps its answer.
- Produces: `confirm.decision_of(result: dict) -> str`: `identical → keep`; `"levels" in result → use iff result["levels"]["confirmed"] else keep`; otherwise `decide(result["summary"])`. A1.4 adds the Holm step here. `execute._decision` becomes an alias (`_decision = decision_of`).
- Produces: `confirm._prior_of(seed_row: dict, objective: str) -> tuple[float | None, str]`. It returns `(kpi_cv[objective], "from the search's repeated runs")` when the seed row recorded a positive `kpi_cv` for the objective (to first order the sd of log objective is its coefficient of variation), else `(None, "from the data")`.
- Produces: `Comparison` fields `prior_scale: float | None = None`, `prior: str = "from the data"` and `looks: int = 1`. `to_json` adds `"prior"`, `"prior_scale"` and `"looks"`. `confirm` sets `looks` to the previous `comparison.json`'s `looks` (1 if the file exists without the key) + 1, or 1 when there is no file.

- [ ] **Step 1: Write the failing tests** (`tests/test_confirm.py`; import `verdict_summary`, `decision_of`, `BASELINE`, `Comparison` and `_analyse` from `evolvekit.confirm`):

```python
def test_the_posterior_is_the_normal_inverse_gamma_update():
    from evolvekit.stats import posterior_mean_t

    d, prior = [0.02, 0.01, 0.03, -0.01], 0.02
    n, dbar = 4, 0.0125
    S = sum((x - dbar) ** 2 for x in d)
    post = posterior_mean_t(d, prior)
    assert post.n == 4 and post.df == 5 and post.mu == pytest.approx(n * dbar / 5)
    sigma2 = (prior ** 2 + S + (1 * n / 5) * dbar ** 2) / 5
    assert post.scale == pytest.approx(math.sqrt(sigma2) / math.sqrt(5))
    assert post.p_above(0.0) == pytest.approx(1 - t_cdf((0.0 - post.mu) / post.scale, 5))
    low, high = post.interval95()
    assert (low + high) / 2 == pytest.approx(post.mu) and high - low == pytest.approx(2 * 2.571 * post.scale, rel=1e-3)
    with pytest.raises(ValueError):
        posterior_mean_t([], 0.1)


def test_the_chance_shown_is_the_chance_the_rule_reads():
    s = verdict_summary([0.013, 0.006, 0.019], prior_scale=None, direction="minimize")
    assert s["prior"] == "from the data" and s["prior_scale"] == pytest.approx(statistics.stdev([0.013, 0.006, 0.019]))
    assert decide(s) == s["decision"] == "use"
    assert s["chance"] >= 0.9 and s["chance_margin"] >= 0.95
    assert s["ci95"][0] == pytest.approx(s["worst"]), "worst is the low end of the range shown"
    assert s["frequentist"]["label"].startswith("frequentist")
    assert s["ci95"][0] <= s["mean"] <= s["ci95"][1], "the paired mean lies in the range"


def test_no_spread_is_no_division_and_no_certainty():
    """Review Focus 1. With every effect equal the prior from the data is 0: no
    division by zero, and equal gains still leave the doubt three cases leave."""
    zero = verdict_summary([0.0, 0.0, 0.0], prior_scale=None, direction="minimize")
    assert zero["chance"] == 0.5 and zero["decision"] == "keep"
    same = verdict_summary([0.02, 0.02, 0.02], prior_scale=None, direction="minimize")
    assert same["chance"] == pytest.approx(t_cdf(math.sqrt(12), 4)) and same["decision"] == "use"
    two = verdict_summary([0.5, 0.5], prior_scale=None, direction="minimize")
    assert two["chance"] == pytest.approx(t_cdf(math.sqrt(6), 3)), "two cases never say more than about 19 in 20"


def test_the_rule_decides_on_the_posterior():
    assert decide(verdict_summary([-0.09, 0.115, -0.004], prior_scale=None, direction="minimize")) == "keep"
    assert decide(verdict_summary([-0.02, -0.02, -0.02], prior_scale=None, direction="minimize")) == "worse"
    assert decide(verdict_summary([-0.015, -0.010, -0.012, -0.008], prior_scale=None, direction="minimize")) == "worse"
    assert decide(verdict_summary([0.05, 0.052, 0.048], prior_scale=None, direction="minimize")) == "use"
    assert decide(verdict_summary([-0.08, 0.01, 0.012], prior_scale=None, direction="minimize")) == "keep"
    assert decide(verdict_summary([0.004], prior_scale=None, direction="minimize")) == "single"
    assert decide(verdict_summary([], prior_scale=None, direction="minimize")) == "none"


def test_a_comparison_written_before_the_posterior_decides_as_it_did():
    """Review Focus 3: a stored summary without `chance_margin` keeps the rule it was decided by."""
    assert decide({"n": 3, "chance": 0.96, "worst": -0.5}) == "use"
    assert decide({"n": 3, "chance": 0.96, "worst": -1.5}) == "keep"
    assert decide({"n": 3, "chance": 0.05, "worst": -4.0}) == "worse"


def _spread_of(n: int, *, mean: float, sd: float) -> list[float]:
    """`n` values with exactly this mean and sample sd."""
    z = [float(i) for i in range(n)]
    m, s = statistics.fmean(z), statistics.stdev(z)
    return [mean + sd * (v - m) / s for v in z]


def test_the_decision_is_monotone_in_the_mean_gain():
    order = {"worse": 0, "keep": 1, "use": 2}
    shape = [0.01, -0.02, 0.015, 0.0]
    seen = [order[decide(verdict_summary([x + k / 1000 for x in shape], prior_scale=0.02, direction="minimize"))]
            for k in range(-80, 81)]
    assert seen == sorted(seen) and seen[0] == 0 and seen[-1] == 2


def test_the_chance_grows_with_the_cases_for_a_fixed_mean_and_spread():
    chances = [verdict_summary(_spread_of(n, mean=0.01, sd=0.02), prior_scale=0.02, direction="minimize")["chance"]
               for n in range(2, 31)]
    assert all(a < b for a, b in zip(chances, chances[1:]))


def _a_study(rng: random.Random, *, gains: list[float], run_sd: float, prior_scale: float):
    """A synthetic final check of len(gains) held-back cases with these true log gains: each
    (case, run) has its own draw, fixed up front, so adding runs extends the same study.
    Returns `look(runs_per_case) -> result`, the real `_analyse` of the first runs."""
    draws = [[(rng.gauss(0.0, run_sd), rng.gauss(0.0, run_sd)) for _ in range(12)] for _ in gains]

    def look(runs: int) -> dict:
        comparison = Comparison(label="sim", objective="cost", direction="minimize", stage="full",
                                seeds=list(range(runs)), instances=[f"case{i}" for i in range(len(gains))],
                                baseline_id="seed", candidates=["c"], prior_scale=prior_scale,
                                prior="from the search's repeated runs")
        for i, gain in enumerate(gains):
            for s in range(runs):
                e_base, e_mine = draws[i][s]
                comparison.runs += [
                    {"configuration": BASELINE, "instance": f"case{i}", "seed": s, "ok": True,
                     "kpis": {"cost": 100.0 * math.exp(e_base)}},
                    {"configuration": "c", "instance": f"case{i}", "seed": s, "ok": True,
                     "kpis": {"cost": 100.0 * math.exp(-gain + e_mine)}},
                ]
        _analyse(comparison)
        return comparison.per_candidate["c"]

    return look


def _final_answer(look, *, runs: int = 3) -> str:
    """The product's looks: the check adds a run per case while the answer is open (up to 12),
    then one recheck with twice the runs when the answer is not use."""
    from evolvekit.confirm import runs_can_help  # A1.3; until then: lambda result: True

    result = look(runs)
    while decision_of(result) == "keep" and runs < 12 and runs_can_help(result):
        runs += 1
        result = look(runs)
    if decision_of(result) != "use" and runs < 12:
        result = look(min(2 * runs, 12))
    return decision_of(result)


def test_a_null_study_says_use_in_under_one_in_ten_with_extension_and_a_recheck():
    rng = random.Random(20261108)
    uses = sum(_final_answer(_a_study(rng, gains=[rng.gauss(0.0, 0.005) for _ in range(4)],
                                      run_sd=0.02, prior_scale=0.02)) == "use" for _ in range(200))
    assert uses < 20, f"{uses} of 200 studies with no true gain said use"


def test_a_null_study_with_quiet_runs_reports_its_false_use_rate(capsys):
    """Not asserted: with runs much quieter than the 1 % margin the margin leg does not
    bind, and only P(gain > 0) >= 0.9 stands between the null and use (open question 5)."""
    rng = random.Random(11)
    uses = sum(_final_answer(_a_study(rng, gains=[0.0] * 4, run_sd=0.002, prior_scale=0.002)) == "use"
               for _ in range(200))
    with capsys.disabled():
        print(f"\nfalse use with quiet runs (0.2 %), 4 cases x 3 runs, extension and a recheck: {uses / 200:.3f}")


def test_a_three_percent_gain_on_four_cases_reports_its_power(capsys):
    rng = random.Random(3)
    uses = sum(_final_answer(_a_study(rng, gains=[rng.gauss(0.03, 0.005) for _ in range(4)],
                                      run_sd=0.02, prior_scale=0.02)) == "use" for _ in range(200))
    with capsys.disabled():
        print(f"\npower of a 3 % gain on 4 held-back cases x 3 runs, with extension and a recheck: {uses / 200:.2f}")
    assert 0 <= uses <= 200


def test_comparison_json_records_the_prior_and_how_often_the_answer_was_read(tmp_path):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    confirm(load_config(config_path), run_dir, seeds=[1001], label="final", log=lambda m: None)
    again = confirm(load_config(config_path), run_dir, seeds=[1001, 1003], label="final", log=lambda m: None)
    written = json.loads((run_dir / "confirm" / "final" / "comparison.json").read_text(encoding="utf-8"))
    assert again.looks == 2 and written["looks"] == 2
    assert written["prior"] == "from the data", "the hand-written seed row has no kpi_cv"
    summary = written["per_candidate"]["g003-c0012"]["summary"]
    assert {"chance", "chance_margin", "worst", "ci95", "frequentist", "posterior", "decision"} <= set(summary)
```

Add `import statistics` at the top of `tests/test_confirm.py`. Until A1.3 lands, `_final_answer` imports `runs_can_help` from `evolvekit.confirm`, so A1.2 ships a stub `runs_can_help(result) -> bool` that returns `True`, and A1.3 replaces it.

Update the existing tests:
- `test_a_summary_gives_the_chance_of_a_gain_and_the_worst_case`: delete it. `test_the_rule_decides_on_the_posterior` replaces it. (Its `[3.0, 3.1, 2.9, -1.4]` example says `use` under the posterior: chance 0.92, P(losing > 1 %) 0.02. The old rule said `keep` because its worst case was a two-sided 95 % bound. See open question 5.)
- `test_an_improvement_that_was_luck_is_reported_as_not_distinguishable`: replace the last line with `assert summary["chance"] == 0.5 and summary["decision"] == "keep"`.
- `test_the_exit_code_says_whether_the_improvement_is_real`: `"Probably better" in printed` instead of `"Better than the baseline"`.
- `test_a_pair_only_the_candidate_failed…`: `result["summary"]["mean"] < result["finished_only"]["mean"]` still holds, since both are paired means.
- `tests/test_status.py::test_the_live_verdict_and_the_final_check_are_one_test`: compare with `summary["frequentist"]`. That means `summary["frequentist"]["n"]`, `["mean"]` and `["ci95"]` (pct) in place of `summary[...]`. The docstring gains: "the live verdict is the frequentist interval; the final check's decision is the posterior's, and its frequentist block is the same test."
- `tests/test_verdict.py::test_verdict_keys_and_schema`: `v["schema"] == 2`.
- `tests/test_app_results.py:55`: `use["rule"].startswith("Use when the chance of a gain")`.
- `tests/test_study_run.py::_confirm_recording`: build `summary = verdict_summary([-0.09, 0.115, -0.004] if len(seeds) < settled_at else [0.013, 0.006, 0.019], prior_scale=None, direction="minimize")`. Import `verdict_summary` from `evolvekit.confirm` instead of `paired_summary`.

In `tests/test_study_run.py`, add:

```python
def test_the_check_says_how_often_the_answer_was_read(tmp_path):
    lines: list[str] = []
    calls: list[int] = []
    fake = _confirm_recording(calls)

    def counting(*args, **kwargs):
        comparison = fake(*args, **kwargs)
        comparison.looks = len(calls)
        return comparison

    job = Job(tmp_path, run_id="r", study="s", plan={})
    _extend_check(_three_held_back(tmp_path), _plan_with(), None, tmp_path, job, lines.append,
                  stop=threading.Event(), time_left_s=lambda: 1e9, confirm=counting)
    assert lines[-1] == "final check: the answer was read 4 times"
```

- [ ] **Step 2: Run the tests and check that they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_confirm.py tests/test_study_run.py tests/test_status.py tests/test_verdict.py tests/test_app_results.py -q`
Expected: FAIL (`posterior_mean_t` and `verdict_summary` are not defined).

- [ ] **Step 3: Implement**
  - **`stats.py`.** Add `Posterior` and `posterior_mean_t` as the Interfaces block says. When `σn == 0` (which happens only when all `d_i` are 0 and `prior_scale` is 0), `scale = 0.0`.
  - **`confirm.verdict_summary`.** It is the only place that decides. It builds the posterior from `d`, the prior scale and its label, the keys above, and the `verdict` sentence:
    - `use`: `f"probably better: a chance of a gain of {chance:.2f}, and of losing more than {USE_MARGIN_PCT:g} % of {1 - chance_margin:.2f} (prior {prior})"`
    - `worse`: `f"probably WORSE: a chance of a gain of {chance:.2f} (prior {prior})"`
    - `keep`: `f"not proven: a chance of a gain of {chance:.2f}, and of losing more than {USE_MARGIN_PCT:g} % of {1 - chance_margin:.2f} (prior {prior})"`
  - **`_analyse`.** `summary = verdict_summary(log_gains, prior_scale=comparison.prior_scale, direction=…, prior=comparison.prior)`, then `summary["verdict"] += _counted_as(...)`. `finished_only` is built by the same call over the finished-only logs. The `_pct_summary` from A1.1 now lives only under `summary["frequentist"]`.
  - **`confirm`.** Pass `prior_scale, prior = _prior_of(seed_row, config.evaluate.score.objective)` into `Comparison`. Read the old `out_dir / "comparison.json"` (if any) for `looks`, before `_compare` writes it. A stopped comparison writes nothing, so it is not a look.
  - **`render_markdown`.** The head line becomes: `**Mean improvement {mean:+.3f} %** (the paired mean), 95 % credible interval [lo, hi] over n instance(s); chance of a gain {chance:.2f}, of losing more than 1 % {1-cm:.2f}; prior {prior}. **{Verdict}.**`. Add a second line: `Frequentist, for engineers: mean {f.mean:+.3f} %, 95 % CI [..], Wilcoxon signed-rank p = ...`. The `Comparison` header line says `The answer was read {looks} time(s).`
  - **`_extend_check`.** After the loop, `looks = getattr(comparison, "looks", None)`. When it is an int, log `f"final check: the answer was read {looks} time{'s' if looks != 1 else ''}"` before returning (on every return path).
  - **`app/verdict.py`.** Set `SCHEMA = 2` and update the docstring's description of `chance`/`ci95_pct`. Delete the `t_cdf` import only if `_chance_of` no longer needs it. It does: keep `_chance_of`'s fallback for comparisons written before A1. `_final_words` is unchanged in shape: it reads `summary["chance"]`, `summary["worst"]`, `summary["ci95"]` and `summary["mean"]`, which `verdict_summary` provides, and its decision comes from `final.decision` (written by `decision_of`).
  - **`tries._check`.** Replace `paired_summary(...)` with `verdict_summary(log_means, prior_scale=None, direction=…)`. The per-case means are now log means (A1.1's `_gain` returns percent, so collect `log_gain` values here and report `per_case` through `pct_from_log`). The keys `mean`, `ci95`, `chance` and `worst` stay.
  - **`cli.cmd_confirm`.** `confirmed = all(decision_of(r) == "use" for r in comparison.per_candidate.values())`. The docstring becomes: "Exit 0 when the rule says use for every candidate".
  - **`study.js`.** Line 346 becomes `The rule: use it at 9 in 10 or better, and under 1 in 20 of losing more than 1 %.`. The fallback at 1016 becomes the new `RULE_WORDS` text.

- [ ] **Step 4: Run the tests and check that they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_confirm.py tests/test_study_run.py tests/test_status.py tests/test_verdict.py tests/test_app_results.py tests/test_bakeoff.py tests/test_per_group.py tests/test_app_tries.py tests/test_levels.py -q`
Expected: PASS. `test_page_line_equals_verdict_line` (`tests/test_verdict.py:80`, every decision) passes unchanged. If the null simulation fails, do **not** tune its parameters. Record the rate in the build log and raise it with the statistician review (open question 5).

- [ ] **Step 5: Commit**

```bash
git add evolvekit/stats.py evolvekit/confirm.py evolvekit/harness/execute.py evolvekit/app/verdict.py evolvekit/app/tries.py evolvekit/cli.py evolvekit/app/static/js/pages/study.js tests/
git commit -m "feat(a1): one posterior decides, and the page says its numbers (S1, S2, S8)

stats.posterior_mean_t: the Normal-inverse-gamma update with mu0 = 0,
kappa0 = nu0 = 1 and the starting point's run-to-run spread as the
prior scale (from the data without repeated runs). confirm.
verdict_summary is the one place that decides: use when P(gain > 0)
>= 0.9 and P(gain > -1 %) >= 0.95. chance and worst are the posterior's,
so the sentence and the decision agree. The frequentist interval and
Wilcoxon stay in comparison.json for engineers; looks are recorded.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task A1.3: Extend the check only while runs can help (S2)

**Files:**
- Modify: `evolvekit/confirm.py`: add `CASES_LIMIT_SHARE = 0.25`, `LIMITED_BY_CASES`, `within_share`, and the real `runs_can_help`.
- Modify: `evolvekit/harness/execute.py`: `_extend_check`.
- Modify: `evolvekit/app/jobs.py`: the check's `now_line` (around `:1097`).
- Modify: `evolvekit/app/verdict.py`: in `_document`, `limited_by` and the `why_keep` fallback.
- Test: `tests/test_confirm.py`, `tests/test_study_run.py`, `tests/test_verdict.py`

**Interfaces:**
- Consumes: the A1.1 `per_instance` rows with `log_gain`, `seed_var` and `pairs`.
- Produces: `confirm.within_share(per_instance: Sequence[dict]) -> float | None`. Let `D` be the cases with a `log_gain` and `V` the cases with a `seed_var`. Then `within = mean(seed_var_i / pairs_i over V)` and `between = max(0, variance(log_gain over D) − within)`, and the function returns `within / (within + between)`. It returns `None` when `len(D) < 2` or `V` is empty, and `0.0` when `within + between == 0` (a deterministic program: more runs add nothing). Re-derived: `E[var(d_i)] = τ² + mean(σ_i²/n_i)`, so `between` is the method-of-moments τ², and the posterior variance of μ scales with `(τ² + within)/n`. Only the `within` part shrinks with more runs.
- Produces: `confirm.runs_can_help(result: dict) -> bool`: `share is None or share >= CASES_LIMIT_SHARE`.
- Produces: `confirm.LIMITED_BY_CASES = "the answer is set by the held-back cases, not by the runs: hold back more cases for a firmer answer"`.
- Produces: `job.json` `check` is `{"seeds": k}`, or `{"seeds": k, "limited_by": "cases"}` when the cases set the answer.
- Produces: the verdict document's `limited_by: "cases"` (only then). On a `keep` with no `why_keep` of its own, `why_keep = LIMITED_BY_CASES[0].upper() + LIMITED_BY_CASES[1:] + "."`. The page already renders `why_keep` (`study.js:285`, `words.js:142`), so no page code changes.

`_extend_check`'s conditions after this task, in order. It stops and returns when any of these holds:
1. `decision_of(result) != "keep"`;
2. `result.get("identical")`;
3. `not runs_can_help(result)`: it logs `"final check: " + LIMITED_BY_CASES` and calls `job.update(check={"seeds": seeds, "limited_by": "cases"})`;
4. `seeds >= len(CHECK_SEEDS)`;
5. `time_left_s() < seed_cost_s`;
6. `stop.is_set()`.

Otherwise it adds a seed. Under the posterior, conditions 1, 4, 5 and 6 read as before (open question 9).

- [ ] **Step 1: Write the failing tests.** In `tests/test_confirm.py`:

```python
def test_the_share_of_the_runs_is_within_over_within_plus_between():
    from evolvekit.confirm import within_share

    rows = [{"log_gain": 0.10, "seed_var": 0.0004, "pairs": 4}, {"log_gain": -0.08, "seed_var": 0.0004, "pairs": 4},
            {"log_gain": 0.01, "seed_var": 0.0004, "pairs": 4}]
    within = 0.0001
    between = statistics.variance([0.10, -0.08, 0.01]) - within
    assert within_share(rows) == pytest.approx(within / (within + between))
    assert within_share(rows[:1]) is None, "one case has no spread between cases"
    assert within_share([{**r, "seed_var": None} for r in rows]) is None, "one run per case: unknown"
    assert within_share([{"log_gain": 0.0, "seed_var": 0.0, "pairs": 3}] * 3) == 0.0


def test_runs_dominated_by_their_own_noise_are_called_case_limited_in_under_one_in_ten_checks():
    """With no difference between cases the share test stops wrongly when var(d) > 4 x within,
    an F(3, 8) tail of about 5 %."""
    from evolvekit.confirm import runs_can_help

    rng = random.Random(25)
    limited = sum(not runs_can_help(_a_study(rng, gains=[0.0] * 4, run_sd=0.02, prior_scale=0.02)(3))
                  for _ in range(400))
    assert limited < 40, f"{limited} of 400"
```

In `tests/test_study_run.py` (add `import math, random` and `from evolvekit.confirm import BASELINE, Comparison, _analyse`):

```python
def _confirm_of_cases(calls: list[int], *, case_gains: list[float], run_sd: float):
    """A `confirm` that runs nothing: the real comparison (`confirm._analyse`) of held-back
    cases whose true log gains are `case_gains`, each run noisy by `run_sd`."""
    def fake_confirm(config, run_dir, *, seeds, instances, label, log, stop):
        calls.append(len(seeds))
        comparison = Comparison(label="final", objective="cost", direction="minimize", stage="full",
                                seeds=list(seeds), instances=list(instances), baseline_id="seed", candidates=["c1"])
        for instance, gain in zip(instances, case_gains):
            for seed in seeds:
                rng = random.Random(f"{instance}/{seed}")
                comparison.runs += [
                    {"configuration": BASELINE, "instance": instance, "seed": seed, "ok": True,
                     "kpis": {"cost": 100.0 * math.exp(rng.gauss(0.0, run_sd))}},
                    {"configuration": "c1", "instance": instance, "seed": seed, "ok": True,
                     "kpis": {"cost": 100.0 * math.exp(-gain + rng.gauss(0.0, run_sd))}},
                ]
        _analyse(comparison)
        return comparison
    return fake_confirm


def test_the_check_stops_adding_runs_when_the_cases_set_the_answer(tmp_path):
    """A toy whose cases differ by far more than its runs: more runs cannot open the answer."""
    calls: list[int] = []
    lines: list[str] = []
    job = Job(tmp_path, run_id="r", study="s", plan={})
    _, seeds = _extend_check(_three_held_back(tmp_path), _plan_with(), None, tmp_path, job, lines.append,
                             stop=threading.Event(), time_left_s=lambda: 1e9,
                             confirm=_confirm_of_cases(calls, case_gains=[0.10, -0.08, 0.01], run_sd=0.001))
    assert calls == [3] and seeds == 3
    assert job.state["check"] == {"seeds": 3, "limited_by": "cases"}
    assert "final check: the answer is set by the held-back cases, not by the runs: hold back more cases " \
           "for a firmer answer" in lines


def test_the_check_adds_runs_when_the_runs_are_what_is_unsure(tmp_path):
    calls: list[int] = []
    job = Job(tmp_path, run_id="r", study="s", plan={})
    _extend_check(_three_held_back(tmp_path), _plan_with(), None, tmp_path, job, lambda _m: None,
                  stop=threading.Event(), time_left_s=lambda: 1e9,
                  confirm=_confirm_of_cases(calls, case_gains=[0.004, 0.0, 0.002], run_sd=0.05))
    assert len(calls) > 1 and "limited_by" not in job.state["check"]
```

In `tests/test_verdict.py`:

```python
def test_a_check_limited_by_its_cases_says_so_in_place_of_more_runs(run_of):
    root, run = run_of("keep")
    job_path = root / "runs" / run / "job.json"
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job["check"] = {"seeds": 3, "limited_by": "cases"}
    job_path.write_text(json.dumps(job), encoding="utf-8")
    v = build_verdict(root, run)
    assert v["limited_by"] == "cases"
    assert v["why_keep"] == ("The answer is set by the held-back cases, not by the runs: hold back more cases "
                             "for a firmer answer.")
    assert jobs.results(root, run, document_of)["final"]["line"] == v["line"]
```

- [ ] **Step 2: Run the tests and check that they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_confirm.py tests/test_study_run.py tests/test_verdict.py -q`
Expected: FAIL (`within_share` is not defined; `_extend_check` extends to 12).

- [ ] **Step 3: Implement.** The functions are as the Interfaces block says; `_extend_check` takes the six conditions in that order. In `jobs.py`'s check `now_line`, when `check.limited_by == "cases"`, say `f"The final check: {_runs_word(on)} per case on the held-back cases; more runs would not change the answer: it is set by the held-back cases."` In `verdict._document`, set `document["limited_by"] = "cases"` from `job["check"]`, and use the fallback `why_keep` only when `why_keep(...)` returned empty and the decision is `keep`.
- [ ] **Step 4: Run the tests and check that they pass.** Run the Step 2 command plus `tests/test_app_results.py`. Expected: PASS. The existing `test_the_check_adds_runs_while_the_answer_is_open…` passes, because its fake has no `per_instance`, so `within_share` is `None` and the runs can help.
- [ ] **Step 5: Commit** (`feat(a1): the check stops adding runs when the cases set the answer (S2)`, with the attribution line).

---

### Task A1.4a: Multiplicity where a choice is made: bake-off and `top:N` (S3)

**Files:**
- Modify: `evolvekit/stats.py`: `holm`.
- Modify: `evolvekit/confirm.py`: `apply_holm`, the Holm step in `decision_of`, the `confirm(..., family=False)` parameter (`top:N` with N > 1 is always a family), and the Holm line in `render_markdown`.
- Modify: `evolvekit/harness/execute.py`: `run_bakeoff` passes `family=True`; its winner is `max(using or ids, key=mean)` on the posterior summary's `mean`.
- Modify: `evolvekit/app/verdict.py`: `_final_words` (the held line), `_bakeoff_headline` (the "chosen among" sentence), and the module docstring.
- Test: `tests/test_confirm.py`, `tests/test_bakeoff.py`

**Interfaces:**
- Produces: `stats.holm(pvalues: Mapping[str, float], alpha: float) -> dict[str, bool]`. Holm's step-down: sort ascending; the i-th smallest (1-based) passes while `p ≤ alpha / (k − i + 1)`; the first failure fails every larger one. An empty mapping gives `{}`.
- Produces: `confirm.apply_holm(results: Mapping[str, dict], *, what: str) -> None`. The family is the results that are not `identical`, have no `levels`, and whose summary has `n ≥ 2` and a `chance_margin`; `k = len(family)`. When `k ≥ 2`, each family member gets `result["holm"] = {"k": k, "what": what, "own": decide(summary), "passed": margin_ok and zero_ok}`, where `margin_ok = holm({id: 1 − chance_margin}, 1 − USE_MARGIN_CHANCE)[id]` and `zero_ok = holm({id: 1 − chance}, 1 − USE_CHANCE)[id]`. The design says Holm on `1 − P(gain > x_m)` only. This plan adds the second leg on purpose: see open question 7. `what` is `"branches"`, `"candidates"` or `"kinds of case"`.
- Produces: `decision_of(result)`: when `decide(...) == "use"` and `result.get("holm", {}).get("passed") is False`, it returns `"keep"`.
- Produces: the verdict copy. A Holm-held keep line is `f"Not proven on {cases} with {k} {what} compared: {'; '.join(parts)}."`, with `parts` as in today's keep line. The bake-off `use` headline, when it has 2 or more branches, appends `f" Chosen among {count} branches: the true gain is likely smaller than shown."`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_confirm.py`:

```python
def test_holm_steps_down():
    from evolvekit.stats import holm

    assert holm({"a": 0.01, "b": 0.03, "c": 0.04}, 0.05) == {"a": True, "b": False, "c": False}
    assert holm({"a": 0.01, "b": 0.02, "c": 0.04}, 0.05) == {"a": True, "b": True, "c": True}
    assert holm({"a": 0.02, "b": 0.03, "c": 0.04}, 0.05) == {"a": False, "b": False, "c": False}
    assert holm({"a": 0.04}, 0.05) == {"a": True} and holm({}, 0.05) == {}


def test_top_n_pays_for_choosing_among_its_candidates():
    from evolvekit.confirm import apply_holm

    strong = {"summary": {"n": 4, "chance": 0.999, "chance_margin": 0.999, "worst": 1.0, "decision": "use"}}
    marginal = {"summary": {"n": 4, "chance": 0.93, "chance_margin": 0.96, "worst": -0.8, "decision": "use"}}
    results = {"g1": dict(strong), "g2": dict(marginal), "g3": {"summary": {"n": 4, "chance": 0.5, "chance_margin": 0.8}}}
    apply_holm(results, what="candidates")
    assert decision_of(results["g1"]) == "use" and decision_of(results["g2"]) == "keep"
    assert results["g2"]["holm"] == {"k": 3, "what": "candidates", "own": "use", "passed": False}
    alone = {"g2": dict(marginal)}
    apply_holm(alone, what="candidates")
    assert "holm" not in alone["g2"] and decision_of(alone["g2"]) == "use", "one is no family"


def test_three_null_branches_say_use_in_under_one_in_ten_bakeoffs_with_holm():
    from evolvekit.confirm import apply_holm

    rng = random.Random(32)
    any_use = 0
    for _ in range(200):
        results = {f"b{i}": _a_study(rng, gains=[rng.gauss(0.0, 0.005) for _ in range(4)], run_sd=0.02,
                                     prior_scale=0.02)(3) for i in range(3)}
        apply_holm(results, what="branches")
        any_use += any(decision_of(r) == "use" for r in results.values())
    assert any_use < 20, f"{any_use} of 200 bake-offs of three equal branches named one better"
```

In `tests/test_bakeoff.py`, update `test_compare_page_lists_candidates`. The line assertion becomes:

```python
    assert verdict["decision"] == "use" and verdict["line"] == (
        f"Of 2 branches, restart: {line[0].lower()}{line[1:]} "
        "Chosen among 2 branches: the true gain is likely smaller than shown."), \
        "the run's line is its chosen candidate's, said as one of two, and what choosing costs"
```

And add:

```python
def test_one_branch_is_not_said_to_be_chosen_among_one(tmp_path):
    root, _ = study_with_runs(tmp_path)
    run_dir = root / "runs" / RUN
    finished_run(run_dir, decision="use")
    _as_bakeoff(run_dir, only="c1")
    assert "Chosen among" not in build_verdict(root, RUN)["line"]


def test_a_branch_held_back_by_its_rivals_says_why(tmp_path):
    """Its own chance passes the rule; among two branches it does not, and the line says so."""
    root, _ = study_with_runs(tmp_path)
    run_dir = root / "runs" / RUN
    finished_run(run_dir, decision="keep")
    _as_bakeoff(run_dir, {"c1": "keep", "c2": "keep"})
    path = run_dir / "confirm" / "final" / "comparison.json"
    comparison = json.loads(path.read_text(encoding="utf-8"))
    comparison["per_candidate"]["c1"]["holm"] = {"k": 2, "what": "branches", "own": "use", "passed": False}
    path.write_text(json.dumps(comparison), encoding="utf-8")
    line = next(c["line"] for c in build_verdict(root, RUN)["candidates"] if c["ref"] == "restart")
    assert line.startswith("Not proven on 1 held-back case with 2 branches compared: ")
```

- [ ] **Step 2: Run the tests and check that they fail.** Run: `.venv/Scripts/python.exe -m pytest tests/test_confirm.py tests/test_bakeoff.py -q`. Expected: FAIL (`holm` is not defined).
- [ ] **Step 3: Implement.** The functions are as the Interfaces block says. `_compare` calls `apply_holm(comparison.per_candidate, what="candidates" if top else "branches")` after `_analyse`, when `family` is set; `confirm` sets `family = family or (candidates.startswith("top:") and N > 1)`. `render_markdown` adds, per candidate with `holm`, the line `"Chosen among {k} {what} (Holm): {'passes' if passed else 'does not pass'} the rule's two chances at their stricter levels."`. In `_final_words`, the held line applies when `decision == "keep"` and `result.get("holm", {}).get("passed") is False` and `own == "use"`. `_bakeoff_headline` appends the "chosen among" sentence when `count >= 2` and the decision is `use`.
- [ ] **Step 4: Run the tests and check that they pass.** Run the Step 2 command plus `tests/test_verdict.py tests/test_app_results.py`. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(a1): Holm where a branch or a candidate is chosen (S3)`, with the attribution line).

---

### Task A1.4b: Multiplicity per kind of case: merge small groups, Holm over groups (S3)

**Files:**
- Modify: `evolvekit/harness/groups.py`: `merge_small_groups`, plus `__all__` and the docstring (a group with fewer than two held-back cases joins its nearest group).
- Modify: `evolvekit/harness/execute.py`: `run_per_group` merges before it writes the run's `groups.json`; the new `apply_group_holm` runs after the last group's check.
- Modify: `evolvekit/app/verdict.py`: `PER_GROUP_RULE`.
- Test: `tests/test_groups.py`, `tests/test_per_group.py`

**Interfaces:**
- Produces: `groups.MIN_HELD_BACK = 2`.
- Produces: `groups.merge_small_groups(doc: dict) -> dict`. It returns a new doc and never mutates its argument. While more than one group exists and some group has fewer than `MIN_HELD_BACK` held-back cases:
  - take the group with the fewest held-back cases (ties: the last in order);
  - merge it into its nearest other group, by Euclidean distance between centroids in `standardise`d features over every case in `doc["features"]` (centroid = the mean over the group's training and held-back cases). Without features (tags only), the nearest is the group with the most held-back cases, ties to the first in order;
  - the target keeps its name and its place in order, and its `training`/`test` become the target's lists followed by the merged group's;
  - the target's `merged_from` list gains the merged group's name, plus any names in that group's own `merged_from`;
  - add a note: `f"{small} has {n} held-back case{'s' if n != 1 else ''}, so it joins {target}, the nearest by its features"`, or `"… the one with the most held-back cases"` without features.

  Finally `doc["k"] = len(groups)`. A doc whose groups all qualify comes back equal.
- Produces: `execute.apply_group_holm(run_dir: Path) -> None`. For a per-group run whose groups are all `done`, it gathers each group's `per_candidate[final.candidate]` and calls `apply_holm(results, what="kinds of case")`. It writes each group's `confirm/final/comparison.json` back, and sets its `job.json` `final.decision = decision_of(result)` and `final.confirmed = (decision == "use")`. `run_per_group` calls it right before `kinds.cache_stump`.
- Produces: `PER_GROUP_RULE = RULE_WORDS + " Per kind of case, each group's settings are used only when its own answer says use; with several groups, each needs the stricter chances that comparing several asks for."`

- [ ] **Step 1: Write the failing tests.** In `tests/test_groups.py`:

```python
def _doc(groups_, features=None):
    return {"format": 1, "method": "features" if features else "tags", "k": len(groups_), "groups": groups_,
            "features": features or {}, "notes": []}


def test_a_group_with_one_held_back_case_joins_the_nearest_by_its_features():
    features = {c: {"rows": v} for c, v in {"s1": 20, "s2": 21, "s3": 22, "m1": 40, "m2": 41, "m3": 42, "m4": 43,
                                             "l1": 90, "l2": 91, "l3": 92, "l4": 93}.items()}
    doc = _doc({"small": {"training": ["s1", "s2"], "test": ["s3"]},
                "mid": {"training": ["m1", "m2"], "test": ["m3", "m4"]},
                "large": {"training": ["l1", "l2"], "test": ["l3", "l4"]}}, features)
    merged = groups.merge_small_groups(doc)
    assert list(merged["groups"]) == ["mid", "large"] and merged["k"] == 2
    assert merged["groups"]["mid"] == {"training": ["m1", "m2", "s1", "s2"], "test": ["m3", "m4", "s3"],
                                       "merged_from": ["small"]}
    assert "small has 1 held-back case, so it joins mid, the nearest by its features" in merged["notes"]
    assert doc["k"] == 3 and "merged_from" not in doc["groups"]["mid"], "the argument is not changed"
    assert groups.merge_small_groups(merged) == merged


def test_without_features_a_small_group_joins_the_one_with_most_held_back_cases():
    """Review Focus 5: tags only, so no distances."""
    doc = _doc({"north": {"training": ["a", "b"], "test": ["c"]}, "south": {"training": ["d", "e"], "test": ["f", "g"]},
                "east": {"training": ["h", "i"], "test": ["j", "k", "l"]}})
    merged = groups.merge_small_groups(doc)
    assert list(merged["groups"]) == ["south", "east"] and merged["groups"]["east"]["merged_from"] == ["north"]
    tiny = groups.merge_small_groups(_doc({"x": {"training": ["a", "b"], "test": ["c"]},
                                           "y": {"training": ["d", "e"], "test": []}}))
    assert list(tiny["groups"]) == ["x"] and tiny["k"] == 1, "every group too small: one group"
```

In `tests/test_per_group.py`:

```python
def test_a_run_merges_a_group_without_two_held_back_cases_before_it_searches(tmp_path, monkeypatch):
    root = per_group_study(tmp_path, sizes=[20] * 5 + [80] * 5, held=3, tags={"small": 5, "large": 5})

    def stop_here(_root):
        raise RuntimeError("stop here")

    monkeypatch.setattr(execute, "measured", stop_here)  # called right after the run's groups.json is written
    with pytest.raises(RuntimeError, match="stop here"):
        execute.run_per_group(root, "r1", log=lambda line: None)
    written = json.loads((root / "runs" / "r1" / groups.GROUPS).read_text(encoding="utf-8"))
    assert list(written["groups"]) == ["large"] and written["groups"]["large"]["merged_from"] == ["small"]


def _posterior(folder: Path, *, chance: float, margin: float) -> None:
    path = folder / "confirm" / "final" / "comparison.json"
    comparison = json.loads(path.read_text(encoding="utf-8"))
    comparison["per_candidate"]["c1"]["summary"].update(
        {"chance": chance, "chance_margin": margin, "decision": "use"})
    path.write_text(json.dumps(comparison), encoding="utf-8")


def test_each_groups_use_pays_for_the_number_of_groups(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("use", 2)})
    _posterior(run_dir / "groups" / "small", chance=0.99, margin=0.97)
    _posterior(run_dir / "groups" / "large", chance=0.99, margin=0.96)
    execute.apply_group_holm(run_dir)
    doc = build_verdict(root, "r1")
    assert doc["groups"]["small"]["decision"] == "keep" and doc["groups"]["large"]["decision"] == "keep"
    assert doc["groups"]["small"]["line"].startswith("Not proven on 2 held-back cases with 2 kinds of case compared: ")
    from evolvekit.app.verdict import PER_GROUP_RULE

    assert doc["decision"] == "keep" and doc["rule"] == PER_GROUP_RULE
    root, run_dir = per_group_run(tmp_path / "b", {"small": ("use", 2), "large": ("use", 2)})
    _posterior(run_dir / "groups" / "small", chance=0.999, margin=0.99)
    _posterior(run_dir / "groups" / "large", chance=0.99, margin=0.96)
    execute.apply_group_holm(run_dir)
    assert build_verdict(root, "r1")["decision"] == "use", "0.01 <= 0.05/2 and 0.04 <= 0.05"
```

- [ ] **Step 2: Run the tests and check that they fail.** Run: `.venv/Scripts/python.exe -m pytest tests/test_groups.py tests/test_per_group.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** The functions are as the Interfaces block says. In `run_per_group`, set `doc = kinds.merge_small_groups(doc)` right after the doc is read or computed, before the `order` log line and the run's `_atomic_write(run_dir / kinds.GROUPS, …)`. The study's own `<study>/groups.json` is left as `compute` wrote it (open question 8). `per_group_decision` is unchanged: after merging every group has two or more held-back cases, so `checked` is every group. Its `single` branch stays for runs made before A1, and for a study with fewer than two held-back cases in all.
- [ ] **Step 4: Run the tests and check that they pass.** Run the Step 2 command plus `tests/test_verdict.py`. Expected: PASS. The existing `test_per_group_single_case_group` and `test_no_group_with_two_held_back_cases_is_single` pass unchanged: their fixtures write runs by hand, as a pre-A1 run would be.
- [ ] **Step 5: Commit** (`feat(a1): every kind of case is checked on two held-back cases, and pays for the others (S3)`, with the attribution line).

---

### Task A1.5: Levels judged with intervals (S7)

**Files:**
- Modify: `evolvekit/confirm.py`: `_analyse_levels` (the states and the verdict words) and `_levels_markdown` (the explanation sentence).
- Modify: `evolvekit/evaluate/levels.py`: `make_anchor(levels, values, per_instance=None)` and the docstring formula for `scale_L`.
- Modify: `evolvekit/evaluate/cascade.py`: `_level_score(key, values, vectors=None)`; the call at `:791` passes `outcome.vector_kpis`.
- Modify: `evolvekit/app/verdict.py`: `_levels` maps the state `undecided` (it passes `state` through, so only check that nothing assumes three states).
- Test: `tests/test_levels.py`

Penalties with levels are **already** a config error (`config._check_levels`, `"evaluate.penalties: cannot be combined with evaluate.score.levels … Make the penalised KPI a level of its own, or a gate (evaluate.gates)"`), pinned by `tests/test_levels.py:90`. This task leaves that as it is (open question 10).

**Interfaces:**
- Produces: the `_analyse_levels` row `state` for a level with a tolerance (every level but the last) is decided by the 95 % interval `[lo, hi]` against `±w` (`tolerance_in_unit`):
  - `equal` iff `lo ≥ −w and hi ≤ w`;
  - `better` iff `lo > w`;
  - `worse` iff `hi < −w`;
  - `undecided` otherwise, which includes a level with no interval (n < 2).

  The last level is unchanged: its state by the mean's sign, and `clear` by its interval. An `undecided` level stops the descent: `deciding = level`, `confirmed = False`, and its verdict part is `f"level {i} ({kpi}) is not yet decided within {within}"`, with `within` as the equal-part spells it (`"1 %"` or `"0.5"`). `better`/`worse` on a non-last level are clear by construction, so `clear = True` there.
- Produces: `make_anchor(levels: Sequence[LevelConfig], values: Mapping[str, float], per_instance: Mapping[str, Sequence[float]] | None = None) -> Anchor`. Let `b_L` be the seed's last value and `rng_L = max − min` of `per_instance[last.kpi]` when it is given with 2 or more values. Then `scale = max(0.1·|b_L|, rng_L)`, falling back to `1.0` when both are 0. Without per-instance values, `scale` is as today. `level_score` is unchanged: `x_L = 0.5·tanh(s_L·(v_L − b_L)/scale_L)`.
- Produces: `Cascade._level_score(key: str, values: dict[str, float], vectors: Mapping[str, Sequence[float]] | None = None) -> float`. On the first candidate through a stage it passes `{kpi: vectors[per_instance_key(kpi)]}` for the last level's KPI when present. The anchor is persisted as before, so a resumed run keeps its scale.

- [ ] **Step 1: Write the failing tests** (`tests/test_levels.py`):

```python
def test_the_last_level_is_scaled_by_the_seeds_own_range_over_instances():
    anchor = make_anchor(TWO, {"dph": 10.0, "wait": 100.0}, per_instance={"wait": [50.0, 100.0, 150.0]})
    assert anchor.scale == pytest.approx(100.0)
    forty, thirty = (level_score(TWO, anchor, {"dph": 10.0, "wait": 100.0 * (1 - g)}) for g in (0.4, 0.3))
    assert forty - thirty > 0.04, "a 40 % and a 30 % gain are different numbers (0.002 apart at a tenth)"
    narrow = make_anchor(TWO, {"dph": 10.0, "wait": 100.0}, per_instance={"wait": [99.0, 101.0]})
    assert narrow.scale == pytest.approx(10.0), "never below a tenth of the seed's value"
    assert make_anchor(TWO, {"dph": 10.0, "wait": 5.0}).scale == pytest.approx(0.5), "without instances, as before"


FANOUT_SOLVER = (
    "import argparse, json, sys\n"
    "p = argparse.ArgumentParser(); p.add_argument('instance'); p.add_argument('--x', type=float)\n"
    "a = p.parse_args()\n"
    "size = {'a': 50.0, 'b': 100.0, 'c': 150.0}[a.instance]\n"
    "print(json.dumps({'dph': 10 + 0.01 * a.x, 'wait': size * (1 + a.x)}))\n"
)


@pytest.mark.slow
def test_a_run_anchors_its_last_level_on_the_seeds_spread_over_instances(tmp_path):
    (tmp_path / "solver.py").write_text(FANOUT_SOLVER, encoding="utf-8")
    raw = _raw(stages=[{"id": "static", "kind": "builtin-static"}, FANOUT])
    raw["search"] = {"operators": {"param_lhs": 1.0}, "children_per_generation": 2, "generations": 1, "seed": 5}
    Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run").run()
    anchors = json.loads((tmp_path / "run" / "work" / "levels.json").read_text(encoding="utf-8"))
    assert anchors["full"]["scale"] == pytest.approx(150.0), "the seed (x = 0.5): 75, 150, 225"


def test_a_level_whose_interval_straddles_its_tolerance_is_not_yet_decided():
    """About 1 % more deliveries per hour, give or take: the interval crosses the 1 % edge.
    (`_comparison` gives the candidate's dph no noise of its own, so it cannot straddle.)"""
    rng = _random.Random(8)
    comparison = Comparison(label="t", objective="dph", direction="maximize", stage="full", seeds=[1, 2],
                            instances=["a", "b", "c"], baseline_id="g000-c0001", candidates=["c"], levels=DESCRIBED)
    for instance, size in (("a", 10.0), ("b", 20.0), ("c", 40.0)):
        for seed in (1, 2):
            comparison.runs += [
                {"configuration": BASELINE, "instance": instance, "seed": seed, "ok": True,
                 "kpis": {"dph": size, "wait": 5.0}},
                {"configuration": "c", "instance": instance, "seed": seed, "ok": True,
                 "kpis": {"dph": size * (1.01 + rng.uniform(-0.004, 0.004)), "wait": 4.0}},
            ]
    _analyse(comparison)
    levels = comparison.per_candidate["c"]["levels"]
    assert levels["levels"][0]["ci95"][0] < 1.0 < levels["levels"][0]["ci95"][1], "the fixture straddles the edge"
    assert levels["levels"][0]["state"] == "undecided"
    assert levels["verdict"] == "level 1 (dph) is not yet decided within 1 %"
    assert levels["deciding"] == 1 and levels["confirmed"] is False
    assert decision_of(comparison.per_candidate["c"]) == "keep", "level 2 is better, and does not speak"


def test_a_level_at_its_tolerance_edge_is_called_equal_in_under_one_in_twenty():
    """TOST on the 95 % interval: a difference exactly at the tolerance is called equal at most
    about 2.5 % of the time."""
    rng = _random.Random(15)
    equal = 0
    for _ in range(200):
        comparison = Comparison(label="t", objective="dph", direction="maximize", stage="full", seeds=[1, 2],
                                instances=["a", "b", "c", "d"], baseline_id="g000-c0001", candidates=["c"],
                                levels=DESCRIBED)
        for instance, size in (("a", 10.0), ("b", 20.0), ("c", 40.0), ("d", 80.0)):
            for seed in (1, 2):
                dph = size * math.exp(rng.gauss(0, 0.004))
                comparison.runs += [
                    {"configuration": BASELINE, "instance": instance, "seed": seed, "ok": True,
                     "kpis": {"dph": dph, "wait": 5.0}},
                    {"configuration": "c", "instance": instance, "seed": seed, "ok": True,
                     "kpis": {"dph": dph * 1.01 * math.exp(rng.gauss(0, 0.004)), "wait": 5.0}},
                ]
        _analyse(comparison)
        equal += comparison.per_candidate["c"]["levels"]["levels"][0]["state"] == "equal"
    assert equal < 10, f"{equal} of 200"
```

Add `import math` to `tests/test_levels.py`, and `decision_of` to its `evolvekit.confirm` import. The three existing `_comparison` tests keep their assertions: 1.002 is equal (interval within ±1 %); 0.95 is worse and clear; the last-level `wait` shift of 0.01 is still "worse on level 2 (wait)".

- [ ] **Step 2: Run the tests and check that they fail.** Run: `.venv/Scripts/python.exe -m pytest tests/test_levels.py -q -m "not slow"`, then `… -m slow -k anchors`. Expected: FAIL.
- [ ] **Step 3: Implement** as the Interfaces block says. The `_levels_markdown` sentence becomes: "A level is equal when its 95 % interval lies within its tolerance, better or worse when the interval lies wholly beyond it, and not yet decided otherwise; the first level that is not equal decides, and an undecided one keeps today's. The last level has no tolerance: it is confirmed when its interval lies above zero."
- [ ] **Step 4: Run the tests and check that they pass.** Run `.venv/Scripts/python.exe -m pytest tests/test_levels.py tests/test_confirm.py tests/test_verdict.py -q`, then `tests/test_levels.py -m slow`. Expected: PASS.
- [ ] **Step 5: Commit** (`feat(a1): goals in order of importance are judged with intervals (S7)`, with the attribution line).

---

### Final task: docs, the statistician review, the persona round, the gate, the PR

**Files:**
- Modify: `docs/reference.md`, "After the run: is the improvement real? `confirm`, and `export`". Rewrite these bullets:
  - "The instance is the unit": the log ratio per pair, averaged over the seeds; percentages of the starting point.
  - Add a bullet, "The rule is one posterior": the prior, P(gain > 0) ≥ 0.9 and P(losing > 1 %) < 0.05, the frequentist interval and Wilcoxon labelled as the engineers' view, `looks`.
  - "Exit code 0 only when the rule says use for every candidate".
  - "A failure is a result": the log scale, `nonpositive_pairs`.
  - The `top:N` paragraph: Holm.
  - The levels paragraph: TOST and "not yet decided".
- Modify: `docs/reference.md`, "Goals in order of importance: `evaluate.score.levels`": the `scale_L` rule (the seed's range over instances, never below a tenth of its value), and what an undecided level does in `confirm`.
- Modify: `docs/app.md`, "The Study page":
  - the rule sentence (*use it at 9 in 10 or better, and under 1 in 20 of losing more than 1 %*);
  - "the chance shown is the chance the rule reads";
  - the `limited_by` sentence in the Keep bullet;
  - the bake-off "Chosen among N branches" sentence;
  - the "with N kinds of case compared" line.
- Modify: `docs/superpowers/reviews/2026-11-08-algorithms-build-log.md`: the A1 rounds, the simulation rates printed by the two report-only tests, the statistician round, and the persona round.

- [ ] **Step 1: Write the docs** in the manual's voice. Any number quoted comes from a test or from the build log.
- [ ] **Step 2 (controller): The statistician review.** Dispatch a fresh general-purpose subagent. Give it only §4 of the design, `evolvekit/stats.py`, `evolvekit/confirm.py`, and the tests `tests/test_confirm.py`, `tests/test_levels.py`, `tests/test_study_run.py`, `tests/test_bakeoff.py` and `tests/test_per_group.py`. Ask it to find any place where the decision, the sentence and the numbers can disagree, or where the posterior is misused (prior scale against the noise of a case mean, κ0 = 1 shrinkage, Holm on posterior tails, optional stopping under a point null, the `worst` quantile against the rule's). Fix what it finds, each with a test, and log the round.
- [ ] **Step 3 (controller): The persona round on the Study page** (§8 decision 8, the one persona exception). Give a planner persona only the Study page of three finished studies: `use`, `keep` with `limited_by`, and a bake-off `use` among 2. The persona reads only the page. Ask it: is the sentence the same quantity as the number (the chance in twentieths, "at worst", the range, the rule's sentence)? Fix what it finds and log the round.
- [ ] **Step 4 (controller): Run the gate.** Make sure no demo study is running, then run `python tasks.py check`. Expected: lint is clean and every test passes, slow ones included.
- [ ] **Step 5 (controller): Commit, push, open the PR**

```bash
git add docs/reference.md docs/app.md docs/superpowers/reviews/2026-11-08-algorithms-build-log.md
git commit -m "docs(a1): the verdict, one rule in one language

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin algo/1-verdict
gh pr create --base algo/0-correctness --title "A1: the verdict (S1-S4, S7, S8)" --body "<S1..S8 closed, each with its test; the simulation rates; the statistician and persona rounds>

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Decisions

Taken by the controller after the builder check (a fresh statistician read §4 and this plan
against the code and ran small pure-Python checks). These override any task text above that
disagrees. The session prompt says: when the design's formula and its own test disagree,
re-derive the formula; these are the re-derivations.

1. **The prior on the effect is weak: κ0 = σ0² / τ0², τ0 = log(1.5)** (design said κ0 = 1).
   Re-derived: with κ0 = 1 the prior says μ ~ N(0, σ²), i.e. effects larger than the noise are
   rare, and the posterior t statistic can never exceed √(n(n+1)) however large the gain (no
   spread: chance ≤ 0.954 at n = 2, ≤ 0.987 at n = 3). Holm over k ≥ 2 groups of two held-back
   cases, a 4-branch bake-off on 3 cases, and A1.4b's own test values are then unreachable — the
   design contradicts itself. With κ0 = σ0²/τ0² the prior on μ has sd ≈ τ0 (a 50 % change is a
   large effect) whatever the noise, μn ≈ dbar, and the chance grows with the evidence. Floor
   κ0 at 1e-6. When σ0 = 0 and S = 0 (no spread at all) the posterior is degenerate and is
   handled explicitly, never a divide by zero: P(gain > x) is 1 if μn > x, 0 if μn < x, and 0.5
   if μn == x (so all-zero effects give chance 0.5 and `keep`, and equal non-zero effects with no
   noise anywhere decide). This supersedes Review Focus 1's "≈ 0.954 at n = 2": the test
   `test_no_spread_is_no_division_and_no_certainty` is renamed for what it pins
   (`test_no_spread_is_no_division`) and asserts these three cases plus a finite chance < 1 when
   σ0 > 0. ν0 = 1, μ0 = 0 and the rest of
   the NIG update stay as the design writes them. Test: the chance is not capped (three cases
   each 5 % better give > 0.99; n = 2 with a 30 % gain and small spread can exceed 0.975).
   Cost if wrong: a weaker prior makes small-n verdicts bolder; decision 2 pays for it.
2. **"Use" needs a chance of a gain of at least 19 in 20, not 9 in 10.** Re-derived: the
   builder check simulated the design's null (2000 studies, 4 cases × 3 runs, extension and one
   recheck): 11.5–13 % false "use" at 0.90 even with κ0 = 1, against the design's "< 10 %"; with
   a weak prior (decision 1) it is higher still. At 0.95 it measured 3–5 % (κ0 = 1).
   `USE_CHANCE = 0.95`; `RULE_WORDS`: "Use when the chance of a gain is at least 19 in 20 and the
   chance of losing more than 1 % is under 1 in 20."; `study.js` and every place that renders
   the rule follow. "Worse" stays at P(gain < 0) ≥ 0.90. The null test asserts the design's
   bound (< 10 % "use" over 200 synthetic studies, 4 cases × 3 runs, extension and one recheck,
   seeded) and prints the measured rate. **If the pair (decision 1, 0.95) does not meet < 10 %,
   the implementer stops with NEEDS_CONTEXT and the measured rates; the controller decides.**
   The docs never claim the looks are free against a point null: they say the rule reads the
   same posterior however many times the answer is read, and that its false-"use" rate under
   the product's looks was measured at X % (the number the test prints).
   Cost if wrong: a real 2–3 % gain needs more cases to read "use".
3. **`worst` is the 5 % quantile** (the one-sided 95 % bound the rule reads), shown with the
   direction's percent conversion. "Use" then holds exactly when chance ≥ 0.95 and worst ≥ −1 %,
   and the "at worst" number on the page is the number the rule read. The range shown stays the
   95 % interval. Tests assert `use ⇒ worst ≥ −1 %` and drop any `ci95[0] == worst`.
4. **The mean shown is the posterior mean μn** (≈ the paired mean once κ0 is weak), so the
   number and the decision are the same quantity; per-case rows are labelled as measured. The
   raw paired mean stays in `comparison.json` for engineers.
5. **Percent and margin per direction** (open question 1, as proposed): maximise
   `pct = 100·(exp(d) − 1)`, `x_m = log(1 − m/100)`; minimise `pct = 100·(1 − exp(−d))` (the
   percent of the starting point's value saved), `x_m = −log(1 + m/100)`. Monotone, so quantiles
   carry over.
6. **Levels move to log ratios and the posterior too.** `_analyse_levels` uses the per-pair log
   gain (A1.1) on every level; equal / undecided / better / worse per level use the 95 %
   interval as A1.5 says (two one-sided tests at 2.5 % each — the test's name says "1 in 40 per
   side"); when the descent reaches a level that decides, "use" / "worse" on that level are read
   by decision 2's posterior rule, so "use" means the same thing with and without levels and
   `_extend_check`'s looks are covered the same way. Cost: one more code path in A1.5.
7. **`within_share` counts the gains actually used** per case (after non-positive pairs are
   dropped and failures imputed), not the case's total pairs.
8. **Holm on both legs** (open question 7, as proposed): a candidate, branch or group keeps
   "use" only when its own decision is use and Holm passes on `1 − P(gain > x_m)` at 0.05 and on
   `1 − P(gain > 0)` at 0.05 (decision 2's threshold).
9. **"From the data" prior** (open question 2): σ0 = stdev(d) when there are no replicate runs,
   named `prior: "from the data"` in `comparison.json`; the statistician review weighs it.
10. Open questions 3, 4, 8, 9, 10 and 12 as proposed.
11. **Re-ruling during A1.2 (supersedes decisions 1 and 9 on the variance): the posterior uses
    the reference prior.** The implementer measured, under the design's null procedure, 22–23 %
    false "use" with the "from the data" prior and 13–14.5 % when cases differ more than runs.
    Re-derived: σ0 = sd(d) adds sd² as a pseudo-observation, so νn·σn² = n·sd², νn = n + 1 and
    the scale is sd/√(n+1) with n + 1 df instead of sd/√n with n − 1 — the data counted twice;
    and a σ0 taken from run-to-run noise understates the case-to-case spread of the effects with
    ν0 = 1 pseudo-observation behind it. The prior that is honest whatever the spread's source
    is the reference prior p(μ, σ²) ∝ 1/σ² (the NIG limit κ0 → 0, ν0 → −1, σ0 → 0):
    `μ | d ~ Student-t(n − 1, location dbar, scale s/√n)`, s the sample sd of the per-case
    effects. `posterior_mean_t` computes that (it keeps its name and a docstring that states the
    prior); the degenerate s = 0 case stays as decision 1 says (1 / 0 / 0.5); n = 1 is `single`.
    The run-to-run noise (`kpi_cv`, the seed's pooled sd of log objective) is no longer a prior:
    it is recorded in `comparison.json` as `run_noise` for engineers and A1.3 still uses the
    per-case run variances. `prior` in `comparison.json` says `"reference"`. The mean shown is
    dbar (decision 4 holds: it is the posterior mean). The null simulation asserts < 10 % "use"
    under the design's procedure in **three** settings — runs noisier than cases, cases noisier
    than runs, and no replicate runs — with enough seeded studies that the asserted bound is not
    within two standard errors of the measured rate (use 1000 per setting if the test stays under
    ~10 s, else mark it `slow`), printing each rate. If a setting fails, stop with the rates.
    Cost if wrong: the chance is the frequentist one-sided t level read as a posterior under a
    stated flat prior — exactly what S1 asked to be stated, now stated in `comparison.json` and
    the docs.

## Open questions for the builder check

1. **The percentage of a log gain for a minimised measure.** The design gives `100·(exp(d) − 1)` for both directions. For a minimised measure, `d = log(base/mine)`, so that is `(base − mine)/mine`: a cut from 100 to 90 would read "11.1 % lower" where it is 10 %. Likewise `x_m = log(1 − m/100)` is exact only for maximise. *Proposed (in the plan):* use `pct_from_log` and `margin_log` per direction, so every shown percentage is of the starting point, as today. The design's formulas are the maximise branch.
2. **`prior_scale` when the search had no replicate runs** (the app's default is one run per case, and a bake-off's seed has no evaluation). *Proposed (in the plan):* `prior: "from the data"`, with `stdev(d)` as the scale (n ≥ 2; n = 1 is `single` and no posterior is used). Separately, the design's scale is the sd of one run's log objective, while the noise of a case mean `d_i` is about `√(2/r)·σ_run` (two noisy sides, r runs) plus the spread between cases. At r = 3 that is 0.82 σ, close enough that the plan keeps the design's value. With `kpi_cv` as the source (a mean CV over instances), it is the first-order sd of log. *Proposed:* keep, and ask the statistician review.
3. **Objectives that reach 0 or go negative.** The design drops and counts such pairs. A minimised count (late deliveries) that a candidate brings to 0 then loses exactly its best pairs, and a profit can be negative. *Proposed:* keep the design's rule in A1 (pinned by Review Focus 2). As a follow-up, fall back per candidate to today's percent of the starting point (`scale: "percent"`, prior from the data) when any finished value is ≤ 0, rather than dropping.
4. **How `n == 1 → single` and failure imputation on the log scale interact with the posterior.** *Proposed (in the plan):*
   - A one-sided failure is imputed (±max |g| over the candidate's finished, comparable pairs) whatever the other side's value, since the failure, not the ratio, is the information.
   - `nonpositive_pairs` applies only to pairs both sides finished.
   - A case with no gain at all (both failed, or all non-positive) is not a case, so `n` counts only cases with a `log_gain`, and a two-case study that loses one becomes `single`.
   - Imputed gains enter `d_i`, `S`, `seed_var` and the prior from the data, as they enter today's interval.
   - When no pair finished, `largest` is `None`, the failures are not in the comparison, `n = 0` and the answer is `none`, as today. A candidate that fails everywhere should arguably be `worse`: that is out of A1's scope, and the build log should note it.
5. **The null rate, and the per-look leg that does not bind.** With runs much quieter than the 1 % margin, `P(gain > x_m) ≥ 0.95` is nearly automatic under a zero gain, and only `P(gain > 0) ≥ 0.90` stands between the null and `use`. That is a one-sided 10 % test per look, and at n = 4 with ν0 adding a degree of freedom the per-look rate is about 12 %, before the extension and the recheck add looks. The design asserts "under 10 %" for 4 × 3 with extension and a recheck. Whether that holds depends on the noise: the plan runs it at 2 % runs (where the margin leg binds) and reports, without asserting, the rate at 0.2 % runs. Also, the posterior rule is less strict than the old frequentist one at small n: `[3.0, 3.1, 2.9, −1.4] %` is now `use`. The likelihood principle makes the posterior coherent under optional stopping, but it does not bound a frequentist false-use rate against a point null. *Proposed:* build as designed, record both rates in the build log, and let the statistician review decide whether `USE_CHANCE` should be 0.95.
6. **The mean shown.** With κ0 = 1, μ0 = 0 the posterior mean is `n/(n+1)·dbar` whatever the noise: three held-back cases each 5 % better would headline 3.75 %, beside held-back rows that each say 5 %, and existing tests would break. *Proposed (in the plan):* `mean` (→ `mean_pct`) is the paired mean `pct_from_log(dbar)`, consistent with `by_case`, and `posterior_mean_pct` is kept beside it. `chance`, `worst`, the range and the decision are the posterior's. The paired mean always lies inside the 95 % credible interval: its upper end is ≈ `(1 + 1/n)·dbar` at zero spread. The statistician review should weigh κ0 itself.
7. **How Holm on `1 − P(gain > x_m)` maps to a "use" label.** *Proposed (in the plan):*
   - A member is `use` iff its own decision is `use` **and** it passes Holm.
   - The family is the results with a posterior (n ≥ 2; not identical; no levels; not a branch equal to today's commit). k counts the family, not only the `use` ones.
   - Holm runs on **both** legs: `1 − chance_margin` at α = 0.05 and `1 − chance` at α = 0.10. Holm on the margin leg alone does not touch the winner's curse among equal branches (under a zero gain the margin p is small when runs are quiet), and the 3-null-branch simulation would fail.
   - Posterior tail probabilities are treated as p-values, which is close to the frequentist one-sided p under this weak prior. The review should confirm.
   - The bake-off's winner is still the largest paired mean among `use`.
   - A Holm-held branch says why in its own line.
   - Levels studies are left out of the family (their decision is the lexicographic one).
8. **Where the per-group merge is written.** "Before the run … `groups.json` records `merged_from`." *Proposed (in the plan):* merge in `run_per_group` and record it in the run's own `runs/<id>/groups.json`. Leave `<study>/groups.json` (written by `groups.compute`, shown on the Cases page) unmerged, so the Cases page still shows the grouping the features found, and the existing fixtures that write single-case groups by hand stay valid as pre-A1 runs. The alternative is to merge inside `compute`, so the planner sees the run's groups before starting. That is one line, but it changes `test_per_group_single_case_group`'s fixture. Holm over groups runs once at the end of `run_per_group`, because the groups are checked one after another, and it rewrites each group's `final.decision`.
9. **Whether `looks: k` is counted per check or per study, and how `_extend_check`'s other conditions read now.** *Proposed (in the plan):*
   - **Looks** are per comparison folder (`confirm/<label>/`): every completed `confirm` call on it adds one, whether it comes from the extension, a by-hand recheck (2× runs), a campaign recheck, `continue_study`'s check of a new best on the same held-back cases, or a resume. A stopped call adds none. A per-group run counts per group folder.
   - **The other conditions:**
     - "decision ≠ keep" still ends the loop. Stopping on `use` or `worse` is what the posterior licenses, with the caveat in question 5.
     - `identical` stops, as before.
     - The 12-seed cap and the time-left test are budget limits, unchanged.
     - `stop` is unchanged.
     - The new cases-limited test comes after `identical` and before the budget tests, so its log line and `limited_by` are written only when the cases are the reason.
   - **The recheck offer.** The app's `_recheck` offer ("check again with twice the runs") is still made after a `limited_by: "cases"` answer. Its own `why` already says that "with a few cases, it is mostly how much they differ that sets it". Suppressing it would be a page change beyond §11's one line, so it is left for the persona round.
10. **"Penalties with levels become a config error"** is already true: `config._check_levels` raises with exactly the alternative the design names, and `tests/test_levels.py:90` pins it. The cascade's `penalty_total = 0.0` under levels is then unreachable. *Proposed:* no change. The build log says so.
11. **`worst` is the 2.5 % quantile while the rule reads the 5 % one.** The design fixes `worst = exp(q_0.025) − 1` (the low end of the 95 % range shown) and the rule `P(gain > x_m) ≥ 0.95` (the 5 % quantile). A `use` line can therefore say "at worst 1.3 % worse than your starting point" above a rule that says "the chance of losing more than 1 % is under 1 in 20". That is consistent (the loss chance lies between 2.5 % and 5 %), but it reads as a contradiction. *Proposed:* build as designed, with the test `ci95[0] == worst`, and put this case to the persona round explicitly. If the persona trips, make `worst` the 5 % quantile and the range the 90 % credible interval (a verdict-schema change).
12. **The verdict document's schema.** `chance` and `ci95_pct` change meaning (a posterior probability and a credible interval), so the plan bumps `app/verdict.SCHEMA` to 2. *Proposed:* bump it. Nothing outside the tests reads `schema == 1`.
