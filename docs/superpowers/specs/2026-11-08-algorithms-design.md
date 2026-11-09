# The algorithms programme: a critical review of evolvekit's methods, and what to build — design

Date: 2026-11-08. Reviewed on the working tree of `agent/4-over-time` (projects 1–4 of the
agent path present). Companion: the session prompt in
`docs/superpowers/design/2026-11-08-algorithms-session-prompt.md`; the build log the session
keeps is `docs/superpowers/reviews/2026-11-08-algorithms-build-log.md`.

## 1. Why

evolvekit's design is sound where it counts: cost is first-class, `confirm` is a paired,
interleaved, per-instance comparison whose failure imputation refuses to let a crashing
configuration be judged on its lucky runs, every view is built from one status document, and
"the verdict is the only claim" governs the app. This review went underneath that philosophy
and read the algorithms themselves: how a child is judged novel, how a parent is chosen, how a
proxy stage promotes, how the race and the final check decide, how the hold-out is used, how
TPE proposes, how groups and priors are formed, how the plan spends the hours.

The finding, in one sentence: the search and the verdict carry a number of small statistical
and algorithmic shortcuts that each cost the user something real — a semantic change rejected
unseen, a hold-out that is secretly a training set, a "use" that the same page's sentence
calls "not distinguishable", a filter that discards improvements under one percent, a plan
that cannot stop early. None is a redesign. Together they are the difference between a tool
that is careful and a tool that is right.

Section 2 is the review, ranked by cost to the user. Sections 3–7 are five projects, each a
stacked PR, that fix what the review found. Section 8 holds decisions taken here so the builder
does not have to; section 9 what is left to the builder; section 10 testing standards; section
11 what is out of scope.

## 2. The review

Line numbers are from the working tree of `agent/4-over-time` on 2026-11-08. Every claim below
was verified by hand against the code; the two that are easiest to doubt were also executed
(2.1 and 2.5: `python -B -c` against `evolvekit.search.novelty`).

### 2.1 Defects

| # | Finding | Where | Why it matters |
|---|---|---|---|
| D1 | **The novelty canonicaliser renames builtins and skeleton-provided names.** `_protected_names` protects only top-level defs and imports, so `max`, `min`, `abs`, `sorted` and every helper the skeleton prefix exposes become `v0, v1…`. Verified: `return max(item, bins)` and `return min(item, bins)` have the same `novelty_hash`. | `evolvekit/search/novelty.py` `_protected_names`, `_Canonicaliser` | A semantically different child is written off as `no_op` or `duplicate` **without evaluation**. On bin packing, tightest-first versus emptiest-first is literally max versus min. |
| D2 | **The hold-out feeds selection.** `Candidate.fitness` returns `ranking_score = public − λ·max(0, public − private)`, λ = 1 by default, i.e. `min(public, private)`. That fitness drives the archive, parent sampling, `best`, patience, TPE observations and promotion thresholds. The docstring on `_run_private` says "Never feeds the search signal". | `candidate.py:174-176`, `evaluate/scoring.py:59-85`, `evaluate/cascade.py:777` | Over generations the private set is selected on and stops being a hold-out; the dashboard's `generalization_gap` stops meaning what it says. |
| D3 | **`status` uses a fixed `t ≥ 2` whatever the degrees of freedom, and on per-instance stages treats every (instance, seed) run as an independent sample.** `confirm` does it right (per-instance means, Student t with df). | `status.py:940-1009` | The live verdict is a different test from the final one; at n = 2 (df = 1) `t ≥ 2` is about a 15 % one-sided error, not 2.3 %. |
| D4 | **The economics stop rules are dead in an LLM run with `big_step_every: 0`.** Both require `big_steps_in_window ≥ 1`, hard-wired; the `min_big_steps` relief applies only to patience. | `budget.py:199-229`, `search/driver.py:170-174` | A documented stop rule that cannot fire. |
| D5 | **The near-duplicate gate flags nearly every `diff` child as near its own parent.** Cosine over node-type 3-grams scales with block size: adding a whole `if` branch to a 30-line block scores 0.9998 against the 0.97 threshold, and the parent is in the index. | `search/novelty.py` `LocalNearIndex`; threshold `config.py:1525` | One extra LLM call per child for exactly what the diff instruction asked for, and a retry hint ("change the decision rule, not the constants") that contradicts "change as little as possible". |
| D6 | Smaller: `search.parent_top_k` is parsed and never read; `search.inspirations > 2` is silently capped at 2 (`driver.py:1171-1187`); the private score merges public KPIs the private run did not emit (`cascade.py:829-830`); the big-step prompt always says "scheduled … plateaued" (`prompts.py:107-112`); big steps are counted at planning time, before the call succeeds (`driver.py:1051-1078`); `_cv` switches units when the mean is 0 (`stages.py:665-678`); ragged seed vectors fall back to the first run's (`stages.py:681-715`). | various | Each a small lie in a tool whose brand is not lying. |

### 2.2 The statistics that decide the verdict

| # | Finding | Where | Why it matters |
|---|---|---|---|
| S1 | **The decision rule mixes confidence levels and the words disagree with the decision.** `decide`: "use" iff one-sided `chance ≥ 0.9` **and** 95 % lower bound ≥ −1 %. A study can say *use* while its own verdict sentence says "not distinguishable: the 95 % interval includes zero". `chance` (a one-sided t cdf) is rendered as "19 in 20", a posterior-probability reading correct only under a flat prior nobody stated. | `confirm.py:245-262`, `paired_summary` (213-242), `app/verdict.py:98-114` | The one claim the product makes has two rules inside it. |
| S2 | **Optional stopping in the final check.** `_extend_check` adds a seed while the answer is `keep` and stops the moment it becomes `use` or `worse`; the app offers "check again with 2× runs"; campaigns recheck; `continue_study` re-tests new bests on the **same** held-back cases. No α is spent across these looks. Adding seeds also does not raise n (the held-back cases): when between-case spread dominates, more seeds cannot open the answer, and the loop spends time proving it. | `harness/execute.py:1007-1033`, `app/jobs.py:1274-1297`, `app/scheduler.py:702-713` | The real false-"use" rate is above the nominal one, precisely for the marginal studies where it matters. |
| S3 | **No multiplicity handling.** `top:N`, bake-off branches (winner = max mean among "use") and per-group verdicts are each tested at nominal level; groups with fewer than two held-back cases are left out of the run-level rule. | `confirm._select` (351-359), `execute.py:748-751`, `app/verdict.py:808-829` | Winner's curse on the bake-off; k groups are k shots. |
| S4 | **Percent-of-baseline per pair uses the noisy baseline run as the denominator.** Asymmetric (−50 % vs +100 %) and biased upward. | `confirm._analyse` (647-730), `status._verdict_per_instance` | A systematic optimism in every reported percentage. |
| S5 | **The race is a repeated, unadjusted test against an optimistically biased incumbent.** Checked after every run past `after`; fixed `margin_pct` (1 %) with no noise estimate; the incumbent is the max-so-far, whose per-instance values were selected on. | `evaluate/fanout.py:71-95, 293-313`, `cascade.py:551-570` | Equally good candidates are raced out more often than the margin suggests, and the search believes they were worse. |
| S6 | **Screening is a fixed top-2 at a quarter of the time limit, uncalibrated.** The run computes screen-versus-full Spearman agreement, but only as a diagnostic. | `harness/plan.py:292, 337`, `harness/compile.py:322-331`, `status.py:1203-1242` | On an anytime solver a quarter-time ranking can be unrelated to the full one; the search then explores the proxy's favourites. |
| S7 | **Levels: "equal within tolerance" is judged on the point estimate; the last level saturates near ±30 % of the seed's value (`0.5·tanh(Δ/(0.1·|b|))`); penalties are silently dropped; levels force `normalize: none`, so large instances dominate.** | `evaluate/levels.py:51-118`, `cascade.py:754-755`, `confirm._analyse_levels` (733-830) | A wide interval on level 1 can hide a large loss while level 2 "confirms". |
| S8 | **The uncertainty shown is heterogeneity, and one number on the verdict has none.** On fan-out stages `sd`/`sem` are across instances; `learned_from.pct` is the ratio of training means on the selection runs, no interval. | `status.py:895-920, 1244-1262`, `app/verdict.py:722-732` | Numbers that look like precision but measure spread. |
| S9 | **Importance is mean |Spearman| across studies on operator-clustered, generation-confounded samples, with no null.** At n = 5, E|ρ| under the null is about 0.4; after three studies this average chooses which settings get tuned. | `status.py:1394-1489`, `harness/memory.py:211-260, 301-357` | Noise can decide the tuned set of every later warm study. |
| S10 | **Grouping always splits when it can.** k ∈ {2, 3} only, never compared with one group, no minimum silhouette; z-scores of raw counts and means; held-back cases take part in clustering; the deployed `rule.md` stump is fit on training cases and is **not** the assignment the final check evaluated. | `harness/groups.py:60, 220-320, 431-453, 502-575` | Planners get a rule the check never validated. |
| S11 | **Warm-start priors are thin and mis-anchored.** Only `param_lhs` (15 % of draws) uses them; TPE ignores them; the decay counts unrelated studies (code studies, bake-offs); nothing checks case similarity; the final-check baseline becomes the previous winner, so chained "use" verdicts never re-anchor to the defaults. | `harness/memory.py:372-398`, `search/operators.py:293-374`, `search/tuning.py:109-145` | The "directional oracle as a decaying prior" decision of September is right; the implementation barely acts on it. |

### 2.3 Search quality

| # | Finding | Where |
|---|---|---|
| Q1 | Selection pressure is weak and fixed: `sigmoid(z)` with slope 1 spans about 0.12–0.88; the lifetime `1/(1+children)` penalty counts failed applies and duplicates, never decays, and is applied to inspirations too. | `search/archive.py:330-375`, `driver._breed_one` |
| Q2 | Auto-range axes are equal-width over the observed min–max: one outlier squashes the grid; every member is bin 0 until a second distinct value; the default single `complexity` axis rewards bloat; typed parameter spaces collapse to one cell (rendered `configure()` blocks have the same AST size), so MAP-Elites does nothing for tuning studies. | `archive.py:55-103`, `config.py:1616-1650` |
| Q3 | "LHS" is iid random across children: each child builds its own 6-point hypercube and keeps one point. | `search/operators.py:293-339`, `space.py:519-527` |
| Q4 | TPE: independent marginals; a fixed 25 % split; one global Scott bandwidth clipped to [0.05, 0.33]; the truncated Gaussian is not renormalised in `pdf` (inconsistent with `sample`); 48 candidates drawn from l(x) only; no constant liar (`taken` is in the signature and never passed, so siblings propose from identical state); observations on an ad hoc scale (failures at `floor − 1.0` absolute, raced-out at `floor`, screened candidates with unpenalised proxy scores beside finished ones with penalised scores). | `search/tuning.py:39-159`, `driver._observations` (1105-1144) |
| Q5 | Improvement is `> best + 1e-6` absolute with a ratchet: sub-ε gains raise `best` without resetting patience, so steady slow progress reads as stagnation; elite replacement is strict `>` with no noise tolerance; elites are never re-evaluated; the cache freezes a lucky draw forever. | `budget.py:147-251`, `archive.add` (221-266), `evaluate/cache.py:170-172` |
| Q6 | `perturb` clamps at the bounds (mass piles up), σ = 0.15 fixed; constraint loops return an infeasible point after 100 tries. | `space.py:304-317, 473-485, 529-570` |
| Q7 | Behaviour twins at 3 significant digits on stochastic stages discard real improvements under about 1 % and suffer bucket-edge effects; "stochastic" means only `seeds > 1`, so the app's default (`runs_per_case = 1`) on a wall-clock solver hashes at 9 digits and the filter is inert. | `evaluate/signature.py:63-120`, `config.py:565-566, 1098` |
| Q8 | The model never sees failures (the failure section shows the *parent's* failure, and parents compete), nor its earlier children on the same parent, nor any score history; inspiration summaries are a diff stat plus the LLM's own, possibly stale, docstring. On a plateau every generation spends a strong-model call. | `prompts.py:234-366`, `driver.py:1051-1078, 1208-1222`, `deltas.py:22, 60-73` |
| Q9 | Promotion by `archive_percentile` compares a proxy-stage score with final-stage *ranking* scores; `top_k_per_generation` is a fixed k that ignores breadth. | `cascade.py:115-152` |

### 2.4 Planning and allocation

| # | Finding | Where |
|---|---|---|
| P1 | The plan takes the **largest** children count (12 down to 4) that still buys 6 rounds: breadth over depth by construction; `stop.patience = generations`, so a converged search burns its hours. | `harness/plan.py:54-55, 296-301` |
| P2 | The final check gets a fixed 15 % share, sized before anything is known about the noise. | `plan.py:51, 243-256` |
| P3 | AI seconds per change are constants (10 s / 30 s) measured once; the plan does not re-plan from measured round durations, only reports "about 2 of 11 rounds". | `plan.py:57-66` |

## 3. Project A0: correctness

Branch `algo/0-correctness` from the base branch (section 8, decision 1). Goal in one line:
the six defects fixed, one shared statistics module, no behaviour change beyond the fixes.

### A0.1 Canonicalise only the names the block binds
In `evolvekit/search/novelty.py`, `_Canonicaliser` renames a name only if the block **binds**
it: function and lambda arguments, assignment / augmented / annotated assignment targets, `for`,
`with … as`, `except … as`, comprehension targets, walrus targets, nested `def` and `class`
names, `global` / `nonlocal` declarations. Every free name (builtins, helpers from the skeleton
prefix, imported names) keeps its text. Top-level defs stay protected as today (the block's
interface). Tests in `tests/test_novelty.py`: `max` vs `min` differ; `sorted` vs `reversed`
differ; `v = x` vs `w = x` collide; a block calling skeleton helper `repair` vs one calling
`deadline` differ; the existing no-op and duplicate tests still pass. Re-run
`python tasks.py run` and update the README quickstart counts (the "rejects eleven of its
twenty" paragraph) if they move; say so in the build log.

### A0.2 The hold-out as a veto, not a fitness term
- `evaluate.holdout_penalty` default becomes **0.0**. `ranking_score` is still computed and
  reported; with λ = 0 it equals the public score, so the archive orders by the public score.
- New `evaluate.holdout_veto: bool`, default **true**: in `finished_final_stage`
  (`cascade.py:55-93`) a candidate whose `private_score` is below the **seed's** private score
  at the same stage does not compete (`competes: false`, `veto: "holdout below the seed's
  <value>"` on the row). The seed's private score is read from the first seed row's
  `private_score`; on a resume it is rebuilt from `runs.jsonl` like everything else.
- The leaderboard's `!` flag stays (it is the same condition, shown).
- Fix the `_run_private` docstring: "Scores the hold-out, records the gap, and with
  `holdout_veto` keeps a candidate below the seed's hold-out from competing. With a non-zero
  `holdout_penalty` it also enters the ranking score."
- README "The hold-out penalty": rewrite to say what the default does and why (the 2026-08-22
  failure is caught by the veto with one bit of leakage instead of a continuous channel).
- Tests (`tests/test_ranking.py`, `tests/test_evaluate.py`): with λ = 0 the archive order is the
  public order; a candidate below the seed's private does not compete and `status.best` skips
  it; λ = 1 behaves as before when set explicitly.

### A0.3 One paired comparison
New module `evolvekit/stats.py`: move `_t95`, `t_cdf`, `_betainc` (or whatever the incomplete
beta is called), `wilcoxon_signed_rank`, `paired_summary` out of `confirm.py` (re-exported there
so nothing else moves), and add `per_instance_means(pairs) -> list[float]` that averages
(instance, seed) gains within an instance. `status._verdict` and `_verdict_per_instance` call
`paired_summary(per_instance_means(...))` and report `clear` iff the 95 % interval excludes
zero, `worse` iff it lies below, else `within noise`; delete `_paired_t`. The `why` sentence
keeps its "these are the seeds the search selected on" caveat. Test: on one run directory
`status --json` `progress.verdict` and `confirm` with the same seeds agree
(`tests/test_status.py`, `tests/test_confirm.py`).

### A0.4 Economics rules without big steps
`budget.py:199-229`: the `big_steps_in_window ≥ 1` guard applies only when the run takes big
steps (`takes_big_steps`, the same relief `driver.py:170-174` gives `min_big_steps`). Test
(`tests/test_economics.py`): `min_gain_per_usd` stops a `big_step_every: 0` LLM run.

### A0.5 The near gate compares the change, not the block
`LocalNearIndex` indexes, per candidate, the **delta multiset** of 3-grams: child grams minus
parent grams (Counter subtraction both ways, concatenated with a sign tag). A new child is
`near` when the cosine of its delta against any earlier candidate's delta is ≥ threshold, with
the parent itself never a match. The seed (no parent) indexes its full grams and matches only
children whose delta is empty — which the exact hash already catches, so in practice the seed
is never a near twin. Tests (`tests/test_near_novelty.py`): adding a branch to a 30-line parent
is not near; applying an earlier child's change to the same parent is near (≥ 0.97); the
re-prompt hint is sent once and then the child is evaluated anyway, as today. README "The
novelty filters": the `local` row says "cosine over the 3-grams the change added and removed".

### A0.6 The sweep of small lies
- `search.parent_top_k`: remove from the schema with a config error that names `archive.top_k`
  (it never did anything; an error is more honest than silence).
- `search.inspirations > 2`: lift the cap in `driver.py:1171-1187` (draw `randint(1, n)`) and
  document the cost; crossover stays at 1.
- `cascade.py:829-830`: the private score is computed on the private run's KPIs only; a private
  run that lacks the objective fails the hold-out with a sentence.
- `prompts.py:107-112`: the big-step hint says which trigger fired ("scheduled every N" or
  "the search has not improved for k generations").
- `driver.py:1051-1078`: `note_big_step()` moves to after a big-step response was applied.
- `stages.py:665-678`: `_cv` is `None` when the mean is 0; views print "n/a".
- `stages.py:681-715`: ragged vectors fail the stage: "seed 1 reported 20 values of
  `bins_per_instance`, seed 0 reported 19".

Gate: `python tasks.py check`. PR body: the six defects by letter, each with its test.

## 4. Project A1: the verdict

Branch `algo/1-verdict` from `algo/0-correctness`. Goal: one decision rule, said in one
language, valid under the looks the product actually takes.

### A1.1 Log-ratio effects
In `confirm._analyse` the per-pair gain becomes `g = s·(log base − log mine)` with s = +1 to
minimise and −1 to maximise (pairs with a non-positive value on either side are dropped and
counted as `nonpositive_pairs`; this replaces the `zero_baseline_pairs` count). Failure
imputation keeps its rule on the log scale (±max |g| over the candidate's finished pairs). Per
instance: the mean over seeds. Every percentage shown to a person is `100·(exp(d) − 1)` of the
relevant posterior or interval quantity, so it is symmetric and the noisy baseline run is no
longer a denominator. `comparison.json` keeps both: `log_gain` fields and the derived `pct`.

### A1.2 One decision, in the words the page already uses
`stats.posterior_mean_t(d, prior_scale)`: with the per-instance effects `d_1..d_n`, the
Normal–inverse-gamma conjugate update with μ0 = 0, κ0 = 1, ν0 = 1, σ0² = `prior_scale²`:
```
dbar = mean(d);  S = Σ (d_i − dbar)²
κn = κ0 + n;  μn = (κ0·μ0 + n·dbar) / κn;  νn = ν0 + n
νn·σn² = ν0·σ0² + S + κ0·n/κn · (dbar − μ0)²
μ | data ~ Student-t(νn, location μn, scale σn/√κn)
P(gain > x) = 1 − T_νn((x − μn) / (σn/√κn))          (t_cdf from stats.py)
```
`prior_scale` is the seed's own seed-to-seed sd of `log(objective)` at the final stage, pooled
over instances, from the search's replicate runs (`kpi_cv` is already recorded; with one run
per case use the sample sd of `d` itself, and say so in `comparison.json` as
`prior: "from the data"`). The rule, with the planner's margin m (`USE_MARGIN_PCT`, 1 %,
as `x_m = log(1 − m/100)` on the gain scale):
```
n == 0 → none;   n == 1 → single
P(gain > x_m) ≥ 0.95 and P(gain > 0) ≥ 0.90   → use
P(gain < 0) ≥ 0.90                            → worse
otherwise                                     → keep
```
`chance = P(gain > 0)` and `worst = exp(q_0.025 of μ) − 1`, so `_chance_words`' twentieths are
literally the number shown and `RULE_WORDS` says: "Use when the chance of a gain is at least
9 in 10 and the chance of losing more than 1 % is under 1 in 20." The frequentist 95 % interval
and the Wilcoxon stay in `comparison.json` and `comparison.md` for engineers, labelled as such;
the verdict sentence is built from the posterior, so the sentence and the decision cannot
disagree. Because the posterior does not depend on when one stopped looking, `_extend_check`,
rechecks and campaign rechecks are sound under this rule; `comparison.json` still records
`looks: k` and the final check's log says "the answer was read k times".

Files: `confirm.py`, `stats.py`, `app/verdict.py` (`_final_words`, `_chance_words`,
`_outcome`), `harness/execute.py` (`_decision`), README "After the run", `docs/app.md`
"The Study page". The existing test that the page's headline equals the document's `line`
for every decision stays; add property tests in `tests/test_confirm.py`: the decision is
monotone in `dbar` and the chance is monotone in n for fixed `dbar` and sd; a null simulation
(200 synthetic studies, true gain 0, 4 cases × 3 seeds, extension and one recheck enabled)
yields "use" in under 10 % of them; a 3 % true gain on 4 cases reports its power in the test's
output without asserting it.

### A1.3 Extend the check only while runs can help
In `_extend_check`, after each `confirm`, split the posterior variance of μ into the share
explained by within-case (seed) variance: with per-case seed variances v_i and n_i seeds,
`within = mean(v_i / n_i)`, `between = max(0, var(d_i) − within)`. Add a seed only while
`within / (within + between) ≥ 0.25` and the other conditions hold; otherwise stop and log
"the answer is set by the held-back cases, not by the runs: hold back more cases for a firmer
answer" and set `check.limited_by: "cases"` in `job.json`, which the Study page shows in place
of "adding a run per case". Test in `tests/test_study_run.py` with a toy whose cases differ by
far more than its seeds.

### A1.4 Multiplicity where a choice is made
- Bake-off (`execute.py:748-751`): the "use" label per branch uses Holm over the k branches on
  `1 − P(gain > x_m)`; the winner is still the max posterior mean among "use" branches, and the
  verdict adds "chosen among k branches: the true gain is likely smaller than shown".
- `confirm --candidates top:N`: the same Holm step over N, reported per candidate.
- Per-group (`harness/groups.py`, `execute.run_per_group`): before the run, a group with fewer
  than two held-back cases is merged into the nearest group by standardised feature distance
  (centroid to centroid), and `groups.json` records `merged_from`; the run-level rule then
  covers every group. Each group's own "use" uses Holm over the groups.
Tests: `tests/test_bakeoff.py`, `tests/test_per_group.py`, `tests/test_groups.py`.

### A1.5 Levels judged with intervals
`confirm._analyse_levels`: a level is **equal** when its 95 % interval lies within ±tolerance
(two one-sided tests); **undecided** when the interval straddles a tolerance edge; better or
worse when it lies wholly outside on one side. An undecided level stops the descent: the result
is `keep` with "level 1 (deliveries per hour) is not yet decided within 1 %". In
`evaluate/levels.py` the last level's map becomes `x_L = 0.5·tanh(s_L·(v_L − b_L) / scale_L)`
with `scale_L = max(0.1·|b_L|, the seed's range of that KPI over instances)` when per-instance
values exist, so a 40 % gain and a 30 % gain are different numbers on the seed's own scale;
document the change under "Goals in order of importance". Penalties with levels become a
config error naming the alternative (a level or a gate) instead of being dropped silently.
Tests: `tests/test_levels.py`, `tests/test_confirm.py`.

Gate, then a **statistician review**: a fresh general-purpose subagent given only section 4 of
this document, `stats.py`, `confirm.py` and the tests, asked to find any place where the
decision, the sentence and the numbers can disagree, or where the posterior is misused. Fix
what it finds; log the round.

## 5. Project A2: noise-aware search

Branch `algo/2-noise` from `algo/1-verdict`. Goal: every place the search compares two numbers
knows how noisy they are.

### A2.1 A noise estimate the whole run shares
`evaluate/noise.py`: `Noise` keeps, per stage and per KPI, the pooled within-candidate
seed-to-seed variance (`σ̂_k² = mean over candidates with ≥ 2 seeds of their sample variance`),
updated from every stage outcome, persisted in `work/noise.json`, rebuilt on resume. With no
replicates anywhere, `preflight` and the study's preview run the seed **twice more** at the
final stage on the smallest instance when the stage's command has a `{seed}` placeholder, and
seed the estimate from those; a run with no `{seed}` and `seeds: 1` has `σ̂ = 0` and every rule
below degrades to today's behaviour. `status` reports `noise.sd` per KPI under `progress`.

### A2.2 Behaviour twins by noise, not digits
`cascade.py:666-706`: on a stage with `σ̂_k > 0` for the objective, two candidates are twins iff
for every signature KPI k: `|a_k − b_k| ≤ 2·σ̂_k/√seeds` (per element for vectors, with the
instance's own σ̂). Rounding to `signature_digits_stochastic` goes; the 9-digit hash stays for
stages with `σ̂ = 0`. `BehaviourIndex` keeps per-stage lists of (id, KPIs) for the comparison;
a twin is still "of the first id it matches". README "Stochastic evaluators" and "The novelty
filters" updated; `tests/test_signature.py`: two programs 0.3 % apart under 2 % noise are twins
at 3 digits today and are **not** twins with the new rule; two programs 0.1 % apart are.

### A2.3 Race and promotion with margins that mean something
- Race (`fanout.py`): the incumbent's prefix is its **seed-averaged** per-instance values; the
  margin is `max(margin_pct, 200·σ̂_obj/|incumbent mean|)` percent; the verdict is read at fixed
  look points only: after `after` instances, then every `⌈n/4⌉` instances. `RaceRule` gains
  `looks` in the event log.
- Promotion (`cascade.select_promoted`): promote the top k **plus** every candidate within
  `2·σ̂_stage` of the k-th; `archive_percentile` compares like with like: when the stage is not
  the final one the threshold is computed over the archive members' **own score at that stage**
  (`stage_scores[stage.id]`), not their fitness.
- Screening as a controller (`harness/execute.py`, `status._screening_agreement`): after each
  round with ≥ 8 (screen, full) pairs, if Spearman ρ < 0.2 the study's promote `k` grows by one
  for the next round (cap: children); if ρ < 0 for two rounds, screening is switched off for
  the rest of the run and the log says why. The plan's "about N rounds" is recomputed when k
  changes (A4.1 makes that general).
Tests: `tests/test_racing.py`, `tests/test_evaluate.py`, `tests/test_study_run.py`.

### A2.4 Improvement and elites on the objective's own scale
- `budget.py`: `epsilon` defaults to `None` meaning `max(1e-9·|best|, 0.5·σ̂_obj)`; an explicit
  number still works. No ratchet: `best` moves only on an improvement.
- `archive.add`: a challenger replaces the elite only when `fitness > elite.fitness + σ̂_obj`,
  or when it has more seed replicates than the elite and `fitness > elite.fitness`.
- `search.reevaluate_best_every: N` (default 0 = off): every N generations the incumbent best is
  run once more at the final stage on a fresh seed (`1000 + k`), its KPIs become the mean over
  all its runs, and the archive, `best` and the economics series use the pooled value. The
  dashboard's "best so far" band then narrows on a real winner and a lucky one drops.
Tests: `tests/test_budget_ledger.py`, `tests/test_archive.py`, a new `tests/test_reevaluate.py`.

### A2.5 A real LHS and a real TPE
- `param_lhs`: the driver builds **one** hypercube per generation with as many points as
  `param_lhs` children in that generation and hands each child its row (`operators.py:293-339`,
  `driver._plan_operators`).
- `tuning.py`:
  - observations are **ranked** (`driver._observations`): the fitness is replaced by its rank
    among the usable observations; failed and gated candidates share the worst rank; raced-out
    ones the rank just above them; screened-out ones their rank among themselves placed below
    every finished one. The absolute `floor − 1.0` goes.
  - split: `n_good = max(2, min(25, ⌈0.1·n⌉))` (Optuna's γ).
  - bandwidth per point: the larger of the gaps to the point's two nearest neighbours on the
    unit scale, clipped to [0.02, 0.5] (Hyperopt's rule); the prior component keeps weight 1.
  - `pdf` renormalises each truncated Gaussian on [0, 1] (`Φ((1−p)/h) − Φ(−p/h)`).
  - candidates: 24 from l(x) and 24 from the uniform prior, scored by `log l − log g`.
  - the driver passes `taken` = the params of the siblings already proposed this generation
    plus the constant-liar observation (each taken point enters the bad set) so parallel TPE
    children spread out.
  - **the warm-start prior becomes the prior component**: for a parameter with
    `prior: {value, weight}`, the prior term is a Gaussian at `to_unit(value)` with bandwidth
    0.1 and weight `1 + 4·weight` instead of the uniform's 1.
  - `param_local` starts from the prior value with probability `weight` when the parent is the
    seed.
Benchmark: `python benchmarks/operator_mixes.py --seeds 5` before and after; the "mature"
regime's mean true cost of the reported best must not rise; both tables go into the PR body
and the README's operator section is updated from the script. Tests: `tests/test_tuning_operators.py`,
`tests/test_prior.py`, `tests/test_space.py`.

### A2.6 Descriptors that mean something, and a dial on selection
- Auto axes bin by **quantiles** of the observed values (equal counts), re-binned when the
  member count doubles; `cells=` can still fall, and the README's paragraph about it is kept.
- For `problem.parameters` spaces the default descriptors are the two tuned parameters the
  harness ranks first (`recommended` order; else the first two declared), 4 quantile bins
  each, so MAP-Elites holds configurations apart along the axes a planner chose to tune.
- `search.selection_temperature` (default 1.0 = today): `w = sigmoid(z/τ) / (1 + c)` where `c`
  counts **evaluated** children only and halves every generation; inspirations are drawn
  without the `1/(1 + c)` factor.
- `space.perturb` reflects at the bounds; σ adapts by the 1/5-success rule per run (×1.2 after a
  generation in which ≥ 1/5 of `param_local` children improved on their parent, ÷1.2
  otherwise, clipped to [0.03, 0.4]), persisted in `work/perturb.json`.
Tests: `tests/test_archive.py`, `tests/test_space.py`, `tests/test_breadth.py` unaffected.

Gate; benchmark tables in the PR; a statistician review as in A1 on `noise.py`, A2.2 and A2.3.

## 6. Project A3: what the model is told

Branch `algo/3-prompt` from `algo/2-noise`.

### A3.1 A per-parent ledger
`prompts.user_prompt` gains "## What was tried on this parent": the last 5 children of the
sampled parent, newest first, one line each: operator; a **locally computed** change summary —
the diff stat plus the names of changed functions and changed constants (`deltas.py`; the
docstring is no longer used anywhere in a summary); the outcome in words: "+0.21 (now the
best)", "−0.04", "failed: `<first line of stderr>`", "same behaviour as g003-c0012", "broke the
gate `missed_required = 2 > 0`", "raced out after 4 instances", "not promoted". The parent's
own failure section is replaced by this. Bounded (5 lines of at most 160 characters), so the
prompt still does not grow with the run; `tests/test_diff_prompts.py` asserts the bound.
Inspiration summaries use the same local summary.

### A3.2 Big steps with spacing and a true hint
`driver._plan_operators`: on a plateau, one big step, then no plateau-triggered big step for
`big_step_every` generations unless something improved; the hint states the trigger and what
the previous big step tried (its change summary) and scored. `tests/test_driver_tree.py` and
`tests/test_zero_llm.py` keep passing (no model: no big steps).

Gate; README "Operators" and "The meta-scratchpad" rows updated.

## 7. Project A4: allocation and memory

Branch `algo/4-allocation` from `algo/3-prompt`.

### A4.1 Re-plan from what the run measures
`harness/plan.py` gains `replan(plan, measured) -> Plan` from the median round duration and
the AI seconds measured so far; `execute.run_study` calls it after every round, and `job.json`
carries `plan.rounds_left`, which the Study page and `study status` read (they already say "at
this pace about k of N rounds"). Convergence: no improvement beyond `σ̂_obj` for
`max(3, ⌈rounds/3⌉)` rounds **and** TPE's best proposal score below its median for two rounds
(or, with no TPE, the breadth rule at its ceiling for two rounds) → the search ends early,
`job.json` says `converged: true`, and the saved time goes to the final check as extra seeds
**only while A1.3 says runs help**; otherwise it is simply saved and the report says so.
Tests: `tests/test_study_plan.py`, `tests/test_study_run.py`.

### A4.2 Grouping that can say "one group"
`harness/groups.py`: one group unless the best silhouette is ≥ **0.5** (Kaufman & Rousseeuw's
"reasonable structure"); count features (`rows_*`) enter as `log1p`; clustering runs on the
training cases only; held-back cases are assigned by the stump, so the final check validates
the rule a planner will deploy; `rule.md` reports the stump's accuracy on training and
held-back cases separately. The demo-tour samples (four cases) must produce one group; the
pyvrp tagged samples keep their tag groups. Tests: `tests/test_groups.py`, `tests/test_per_group.py`.

### A4.3 Priors that act and decay for a reason
`harness/memory.py`: the decay counts **settings studies that tuned that setting**, not every
finished study; each winner's weight is multiplied by a similarity factor
`exp(−d²/2)` where d is the standardised feature distance between the centroid of the new
study's cases and the winner's (features from `groups.py`; 1.0 when either has no features);
`priors_for` feeds A2.5's TPE prior. Importance: `status._parameter` reports `|ρ|` with a
permutation p-value (999 shuffles, `random.Random(0)`); `memory.learned` pools `|ρ|` only over
studies with p < 0.1 and re-ranks `recommended` only on settings that have at least one such
study. The verdict of a warm-started study adds a second line, "against the harness defaults:
…", computed from the warm study's own baseline runs on the same held-back cases when the
defaults' runs exist in the library (the latest study whose starting point was the defaults),
else "not measured". Tests: `tests/test_memory.py`, `tests/test_app_learned.py`, `tests/test_status.py`.

Gate; the "What we learned" page and README "A study from the command line" (`--warm`) updated.

## 8. Decisions taken here

1. **Base branch.** The review was made on `agent/4-over-time`; `groups.py`, `verdict.py`,
   `memory.py` exist only from agent project 4 on. `algo/0-correctness` branches from the tip of
   the latest `agent/*` branch present in the clone (`agent/5-installer` if it exists, else
   `agent/4-over-time`), or from `master` once PRs #72–#75 are merged; the build log says which.
2. **P0 is part of the programme.** A1 depends on A0.3 (`stats.py`) and A0 is the cheapest tier
   with the largest effect per line; it is project 0 of five.
3. **Bayesian verdict with a conjugate prior**, not a sequential frequentist test. The page
   already speaks in "19 in 20"; the posterior makes that literal and is sound under the
   extension loop and rechecks without pre-registration. The frequentist interval and Wilcoxon
   stay in the files for engineers.
4. **Hold-out as a veto** rather than removing it from selection entirely: the one documented
   failure (a winner whose hold-out went backwards) is caught; a continuous channel is closed.
5. **Noise from replicates the run already has**, with a two-run preflight fallback, rather than
   a configured noise constant.
6. **Silhouette ≥ 0.5 for k > 1** rather than a gap statistic: deterministic, stdlib, explainable
   on the page.
7. **Stdlib only.** `stats.py` needs the incomplete beta and a Student-t cdf, both already in
   `confirm.py`.
8. **No persona gate per project**; instead the quantitative acceptance in each section and a
   statistician review for A1 and A2. The planner's page is unchanged in shape; a persona round
   on the Study page after A1 (the sentence and the number are the same quantity) is the one
   exception.

## 9. Left to the builder

The exact names of new config keys beyond those given; where `Noise` is wired into `Cascade`
(constructor or `evaluate_generation`); the prose of the new README paragraphs (keep the
README's voice); the tests' toy evaluators (`tests/harness_toy.py` and `tests/campaign_helpers.py`
have the shapes); how `rule.md` words accuracy.

## 10. Testing standards

Those of `docs/superpowers/specs/2026-11-07-agent-path-design.md` §9: TDD per task; a failing
test first; unit tests for every function named here; the CLI through `main([...])`; Windows
first, Linux in CI; timing-sensitive tests marked `slow`; the full gate never beside a demo
study; docs in the same PR. Plus: every statistical rule has a simulation test with a seeded
RNG that states its false-positive rate in its name and asserts it.

## 11. Out of scope of programme A

Multi-objective fronts; a scipy dependency; changes to the harness SDK contract; the two
unattempted benchmark targets in the README; any change to the app's pages beyond the words
the verdict document already drives and the one `check.limited_by` line. Programme B below
takes some of these up.

---

# Programme B: the go-to tool

Added 2026-11-08 after a second review pass, this one about the product rather than the
formulas. Programme A makes evolvekit right; programme B makes it the tool people reach for
when they have a configuration or a change to investigate. It runs **after** programme A, in
its own session with its own prompt (to be written from this part); its branches are
`tool/1-quick` … `tool/5-fronts`, stacked from `algo/4-allocation` or from `master` once
programme A is merged.

## 12. Why: what keeps evolvekit from being the default choice

evolvekit is the most honest tool in its class: a search paired with a pre-registered, paired,
held-out final check that refuses to say "better" until it passes. What stops it being the
default is around the algorithms, not in them:

- **A harness is the price of admission.** `harnesses/pyvrp/harness.yaml` is 680 lines plus a
  runner and a SQLite table vocabulary. `harness discover` finds candidate flags, but ranges,
  cases, tables and measures are written by hand before the app, the verdict or a campaign will
  touch a program. The engine has a five-minute path (`init --template tune`); the product
  does not.
- **The judge is reachable only through a study.** The bake-off is the nearest thing to
  "compare A and B properly" and needs a code study, a harness, git and held-back cases. The
  first need of most engineers is exactly that comparison with no search: two configs, two
  commits, two prompts.
- **The result is a point, not an explanation.** A winner and a sentence. Importance is a rank
  correlation with no null (programme A fixes the null); there is no ablation, no sensitivity,
  no trade-off view, no "which of the six changes carried the gain".
- **One machine, one study, one agent.** One run per library at a time (`app/jobs.py:80,
  626`); no second machine even on a LAN; `agent_patch` compiled for `claude-cli` only
  (`providers/copilot_cli.py:44`); no Python API (`evolvekit/__init__.py` exports a version)
  and no MCP server, although the design's second user is a coding agent.
- **The plan does not know what it can see.** No noise is measured before a study starts, so
  the plan cannot say "with four held-back cases and 2 % noise you can only detect gains above
  3 %", and studies run that could never have said *use*.

## 13. Project B1: ten minutes to a verdict

Branch `tool/1-quick`. Goal: a program with a command line and a number in its output gets a
study, a verdict and a campaign with no harness written.

### B1.1 `study quick`
```
evolvekit study quick NAME --cmd "./solver --n {n} --time 30 --instance {case} --seed {seed}"
    --cases "data/*.vrp" --read "cost: ([-+0-9.eE]+)" [--read-json KEY] [--direction minimize]
    --tune "n:int:10..120[:default]" --tune "penalty:float:1e2..1e6:log" --tune "init:choice:greedy,savings"
    [--hold-back N] [--hours H] [--time-limit-flag --time] [--json]
```
It writes a **generated harness** into the library home under `harnesses/quick-<slug>/`
(`kind: program`, `harness: 1`, settings from `--tune`, one `cases:` format matching the
files' extension, one KPI from `--read` or `--read-json`, `time_limit.accepts` true when
`--time-limit-flag` is given, no tables, no levers), using `harness/skeleton.py`'s program
skeleton and the SDK as every harness does, then `study new … --harness quick-<slug>`,
`study application`, `study cases --hold-back` and `study check`, and prints the plan line. The
harness passes `harness check` (B1.3 relaxes the table requirement). Everything downstream is
unchanged: the app shows the study, the verdict is the same document, `--warm`, campaigns and
`per_group` (by tags) work. `harness new my-solver --from quick-<slug>` promotes it later.
Files: new `evolvekit/harness/quick.py`, `cli.py` (`s_quick`), `harness/skeleton.py`,
`harness/manifest.py` (tables optional, §B1.3). Tests: `tests/test_study_quick.py` end to end
on `examples/cli-solver/solver.py`, offline; `--json` prints `{"slug", "harness", "root"}`.

### B1.2 `harness discover --draft`
`harness discover` already lists candidate settings with type guesses and defaults
(`harness/discover.py`). `--draft DIR` writes a harness folder from them: every candidate as a
setting with `recommended` unset and a `TODO` range where none is known (`low`/`high` from the
default: ×0.1 and ×10 for a positive number, ±1 around 0 for others, marked `guessed: true`,
which `harness check` reports as a warning until removed), the runner skeleton, an `AGENTS.md`,
and a `cases:` section with the extension the user names (`--case-ext .json`). The skill
`evolvekit-harness` is updated to start from `--draft`. Tests: `tests/test_harness_discover.py`.

### B1.3 Tables become optional
A harness without `tables:` is valid: KPIs then come only from the runner's reported numbers
(`kpis_from`/`kpi_patterns` as the engine already supports), `features:` fall back to file
size and line count, levers and SQL KPIs are refused with a sentence naming `tables:`. Files:
`harness/manifest.py`, `harness/check.py`, `harness/groups.py` (`_defaults`),
`docs/harness-authoring.md` ("Tables: the vocabulary" becomes "optional, and when you want
them"). Tests: `tests/test_harness_manifest.py`, `tests/test_harness_check.py`.

### B1.4 The README leads with the job
The first paragraph of the README and the app's Home say what the tool does for a person
("find a better configuration or change, and prove it on cases it never saw") before the
mechanism (the evolutionary loop stays, one paragraph down). `study quick` is the quickstart
after `python tasks.py run`. Persona gate: an engineer with a command-line solver and no
harness reaches a verdict from the README alone.

## 14. Project B2: the judge, the API and the tools

Branch `tool/2-judge`. Goal: the final check usable without a study; evolvekit callable from
Python and from a coding agent's tool list.

### B2.1 `evolvekit judge`
```
evolvekit judge --baseline "cmd A {case} {seed}" --candidate "cmd B {case} {seed}" [--candidate …]
    --cases "data/*" --read PATTERN|--read-json KEY --direction minimize --runs 3 [--label L] [--json]
evolvekit judge --repo PATH --base REF --candidate REF [--candidate REF] --bench "cmd {case}" --cases …
```
The interleaved, paired comparison of `confirm.py` with programme A's statistics, run from a
temporary run directory under the library home (`judgements/<label>/`, kept), with the
verdict document as its output (`decision`, `line`, `by_case`, `levels` when `--read` is given
more than once with `--within`). The repository form checks each ref out as a worktree exactly
as the bake-off does (`tree.py`). No harness, no study, no search. A judgement is listed on
Home under "Judgements" with its line, and `study quick --from-judgement L` starts a search
from a judgement's cases and command. Files: new `evolvekit/judge.py`, `cli.py`, `confirm.py`
(a `Comparison` constructor that takes commands instead of a config), `app/api.py`,
`app/store.py`. Tests: `tests/test_judge.py` offline on `examples/cli-solver`; a null
simulation that the "use" rate stays under its nominal level.

### B2.2 A Python API
`evolvekit.api` (re-exported from `evolvekit/__init__.py`): `Study.new(name, harness, …)`,
`Study.open(slug)`, `.application(path)`, `.cases(files, hold_back=)`, `.check()`,
`.start(now=False)`, `.wait(timeout=)`, `.status()`, `.verdict()`, `.report(markdown=)`,
`judge(...)`, `Harness.discover(...)`, `Harness.check(...)`. Every method calls the same
functions the CLI calls (`harness/execute.py`, `app/store.py`, `confirm.py`); nothing is
duplicated; return values are the `--json` documents as dicts. Documented in a new
`docs/api.md` with one worked script. Tests: `tests/test_api.py` mirrors
`tests/test_cli_study.py` case by case.

### B2.3 An MCP server
`evolvekit mcp` serves, over stdio, the tools `study_new`, `study_application`,
`study_cases`, `study_check`, `study_start`, `study_status`, `study_verdict`, `study_report`,
`judge`, `harness_discover`, `harness_check`, `doctor`, each a thin wrapper over B2.2 with the
`--json` document as its result and the verdict's own sentence in `line`. Standard library
only (JSON-RPC over stdin/stdout; the MCP framing is small). `skill install` also writes the
`.mcp.json` entry when the project has one, and the two skills say "use the tools when they are
listed, the commands otherwise". Tests: `tests/test_mcp.py` drives the server through pipes
with `main([...])`-style helpers; the agent gate of the agent-path design §4.4 gains a round
with the tools installed.

## 15. Project B3: understanding and trust

Branch `tool/3-understanding`. Goal: the verdict explains itself and the plan knows what it
can see.

### B3.1 Ablation: the minimal winning change
After a final check whose decision is `use` and whose best changed ≤ 8 settings, run the
ablation: for each changed setting, the winner with that one setting reverted to the start,
on the held-back cases with the check's seeds (interleaved with the winner; cached runs are
reused), compared with the winner by the same posterior rule. The verdict document gains
`ablation: [{setting, from, to, carries: "gain" | "nothing" | "loss", mean_pct, chance}]` and
`minimal_change`: the smallest set of changes whose reverted-complement is not shown to be
worse than the full winner (greedy, drop the setting with the highest "nothing" chance first,
re-judge). The Study page shows "What carried the gain" and offers the minimal change beside
the full one; `study export` and `study pr` take `--minimal`. Budget: the ablation runs only
within the time the plan has left after the check plus 10 % of the hours, else it is skipped
and says so. Files: `harness/execute.py` (`_ablate`), `confirm.py`, `app/verdict.py`,
`app/report.py`, `app/static/js/pages/study.js`. Tests: `tests/test_ablation.py` with a toy
whose gain comes from one of three changed settings.

### B3.2 Noise calibration and the minimum detectable effect
The preview (`harness/execute.py` preview path; `app/work.py`) runs the smallest case **three**
times when the harness accepts seeds, and the plan gets `noise_pct` (the sd of log score across
the runs). `plan.py` computes the minimum detectable effect for the planned held-back cases and
runs per case under programme A's rule (the smallest true gain for which the posterior rule
says "use" with probability 0.8, by a 200-draw simulation with `random.Random(0)`), and the
plan bar says: "This plan can show a gain of about 2.4 % or more. To see 1 %: hold back 3 more
cases, or 2 more runs per case." `study check` prints the same line. Tests:
`tests/test_study_plan.py` (the MDE falls with more cases and more runs; it is `None` without
seeds and the line says so).

### B3.3 Range edges and inert settings
- Verdict: when the winner's value of a setting lies in the outer 5 % of its range (on the log
  scale where declared), `advice` gains "widen `neighbours` above 120 and run again"; the
  Study page's settings table marks the value. Source: `status.parameters.coverage` and the
  winner's params (`app/verdict.py`).
- `harness check --probe`: for each setting, run the smallest sample at the low end, the high
  end and the default; a setting whose objective moves by less than the measured noise at
  both ends is reported as "no measured effect on the samples: consider `recommended: 0`",
  and a setting that crashes the application at an end as "the range includes values the
  application refuses". Exit 1 (warnings). Files: `harness/check.py`, `app/verdict.py`,
  `status.py`. Tests: `tests/test_harness_check.py` with `examples/cli-solver` (its inert
  flags), `tests/test_verdict.py`.

### B3.4 Stratified hold-back
`app/store.py` `split` holds cases back across the feature groups of `harness/groups.py`
(proportionally, at least one per group when the count allows) instead of by size alone, and
never holds back a case whose standardised features are within 0.1 of a training case's
(a near-duplicate stays with the training cases, and the strip says so). `study cases
--hold-back` and the app's "Which ones?" use it. Tests: `tests/test_app_store.py`,
`tests/test_cli_study.py`.

## 16. Project B4: reach

Branch `tool/4-reach`. Goal: more than one machine, more than one study, more than one agent.

### B4.1 An evaluation executor
`evaluate/fanout.py` `run_instance_stage` gains an `Executor` protocol (`submit(run) ->
future`, `workers`), with `LocalExecutor` as today (pool, pinning, host load). A second
implementation, `FolderExecutor`, writes each run as a JSON job into `<run>/work/queue/` and
waits for its result file; a separate process, `evolvekit worker --queue PATH [--workers N]
[--pin …]`, started by hand on any machine that can see the folder (a LAN share, a synced
folder), takes jobs, runs them and writes results. Interleaving is preserved because the
queue is the stage's own order; `host_busy` and the log paths are recorded as today, with the
worker's host name. The design's "no server" rule stands: a worker is a client of the run
directory. `study.yaml` `limits.executor: local | folder` and `workers:` per machine. Tests:
`tests/test_executor.py` with two worker processes on one machine; the full gate stays local.

### B4.2 Studies side by side
`app/jobs.py`: up to `home.max_parallel_studies` (default 1, so nothing changes until set)
studies run at once when their plans' workers fit the machine's physical cores together
(`cpus.plan_workers` takes the cores already pinned by live runs); campaigns and the queue
honour it; Home says "2 of 2 slots in use". Tests: `tests/test_app_jobs.py`, `tests/test_scheduler.py`.

### B4.3 An agent-agnostic code operator
`agent_patch` becomes a command template: `models.strong.options.agent_command: "<cmd>
--brief {brief} --cwd {tree}"` with the contract of `search/agent_patch.py` (brief file in,
edited folder out, exit code, optional JSON cost on stdout). `claude-cli` keeps its built-in
template; `copilot-cli` and any other CLI get one in the app's Settings and in `study.yaml`.
The refusal in `providers/copilot_cli.py:44` goes. Tests: `tests/test_agent_patch.py` with a
fake agent script.

### B4.4 The performance study template
A built-in template on `demo-tour` and in `harness new --kind program`: `vary.code` with a
brief, KPI = wall time of a benchmark command over `runs_per_case` repeats (programme A's
noise estimate decides twins and margins), `guardrails` = the test suite's exit code
(`tests_failed: {max: 0}`), result = `study pr`. The skill `evolvekit-study` names it for
"make this faster". Tests: `tests/test_study_code.py`.

## 17. Project B5: fronts

Branch `tool/5-fronts`. Goal: two goals as a curve, with the verdict still one decision.

`goal.front: [kpi_a, kpi_b]` compiles to archive descriptors over the two KPIs (quantile bins,
programme A2.6) with the objective being level 1; the Study page gains a "Trade-off" card from
`archive` (one dot per elite, the seed marked, the non-dominated set drawn as a line, the knee
by the maximum-distance-to-chord rule marked). The planner picks an operating point (a dot)
before the final check; the check compares that candidate on both KPIs with programme A's
levels rule, so the verdict remains one sentence. Files: `harness/compile.py`,
`search/archive.py`, `app/verdict.py`, `app/static/js/pages/study.js`, README "Goals in order
of importance" gains "or a trade-off". Tests: `tests/test_study_compile.py`, `tests/test_archive.py`.

## 18. Decisions taken for programme B

1. Programme B runs after programme A and depends on it (A1's rule in the judge and the MDE,
   A2's noise estimate in B3 and B4.4, A2.6's quantile bins in B5).
2. The generated quick harness is a real harness in the library home, not a special case in
   the engine, so nothing downstream learns a second path.
3. The MCP server is stdlib JSON-RPC over stdio; no SDK dependency.
4. The executor is folder-based, not socket-based: no server, no SSH, works on a synced folder.
5. Ablation reverts one setting at a time (first-order); interactions are reported only as
   "the minimal change keeps k of n".
6. The MDE is simulated, not closed-form, so it follows the decision rule exactly.

## 19. Not in programme B

A GP or scipy dependency; a hosted service; a second harness format; cross-application
memory; a Pareto front with more than two goals.
