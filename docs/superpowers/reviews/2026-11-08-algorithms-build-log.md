# Algorithms programme — build log

The running log of the session that builds the five projects of
[the algorithms design](../specs/2026-11-08-algorithms-design.md): one entry
per project with the decisions taken, the gate counts, the benchmark tables,
the statistician review's rounds and the PR link. The session prompt is
[docs/superpowers/design/2026-11-08-algorithms-session-prompt.md](../design/2026-11-08-algorithms-session-prompt.md).

## Step 0 — a clean start (2026-11-09)

- The clone was on `agent/5-installer`. `git status` listed
  `tests/test_app_store.py` as modified, but `git hash-object` of the working
  file equalled the committed blob (9bb1be8): the content was unchanged and
  only git's cached file stats were stale (`core.autocrlf=true`, file on disk
  with LF). Decision: no session's work could be in a byte-identical file, so
  the index entry was refreshed with `git add` of the identical file (the file
  itself was not touched) and the tree read clean. Cost if wrong: none; the
  bytes are the committed bytes.
- Base branch (design §8 decision 1): PRs #72–#76 and the README #77 are all
  merged, so the base is **`master` at 68b579e** ("Merge pull request #77"),
  fast-forwarded to `origin/master`.
- `user.email` and `user.name` as required.
- As in the agent-path build, "python" on this machine is the project's
  `.venv/Scripts/python.exe`; every gate and test in this log ran with it.
- `python tasks.py check` on master: **2711 passed, 5 skipped** in 27:35.

## Project A0 — correctness (2026-11-09)

Branch `algo/0-correctness` from master 68b579e. Plan:
[2026-11-08-algo-0-correctness.md](../plans/2026-11-08-algo-0-correctness.md), written to
step level from design §3 and checked by a fresh builder-check reviewer; its eleven answers are
the plan's "Decisions" section. Seven tasks (A0.1–A0.5, A0.6 split in two), each built by a
fresh implementer and reviewed by a fresh reviewer; then a whole-branch review and one fix wave.

| Task | Closes | Review rounds | Commits |
|---|---|---|---|
| A0.1 bound names only | D1 | 1, clean | 7507d74 |
| A0.2 hold-out veto | D2 | 3 (two fix rounds) | eafc215 deeb963 2882a7e |
| A0.3 one paired comparison, `stats.py` | D3 | 1, clean | b25178c |
| A0.4 economics without big steps | D4 | 1, clean | 129ddb9 |
| A0.5 near gate on the change | D5 | 1, clean | d464b67 |
| A0.6a sweep, config and scoring | D6 | 1, clean | b1d4054 |
| A0.6b sweep, big steps | D6 | 1, clean | 822d4d9 |
| Whole-branch review + fix wave | — | 1 + scoped re-review, clean | cac48e3 3482966 dcef23e 95471e8 |

### Decisions

Taken where the design was silent or wrong against the code; each with what it costs if wrong.
The plan's "Decisions" section holds the eleven taken before building; these are the ones that
changed what was built.

- **"Empty delta" means "only constants changed".** The design says the exact hash catches a
  child whose 3-gram delta is empty; it does not, because node-type 3-grams cannot see `max` →
  `min` (D1's own example) or `18` → `20`. A constants-only child (its canonical dump with
  constant values blanked equals the parent's) is near its parent at 1.0, so the hint "change the
  decision rule, not the constants" is true; a name swap with an empty delta is near nothing.
  When the twin is another candidate's matching change, the hint now says "`<id>` already made
  this change — try a different one". Cost: a constants-only child costs one re-prompt, as today.
- **The hold-out reference is the last seed row with a private score, or none.** An aborted seed
  is evaluated again; with no reference nothing is vetoed. Cost: no veto in a run whose re-run
  seed's hold-out failed.
- **On resume the ranking score is recomputed with the run's current λ** from the stored public
  and private scores, and the veto is replayed — in the archive, the economics series and every
  view that ranks through `leaderboard.rank` (status, `confirm --candidates top:N`, the harness,
  the app's verdict). Cost: a resumed pre-A0 run's order changes at resume, which is the fix.
- **The veto is the literal `private < seed_private`**, as design §3 and §8 decision 4 say: no
  noise margin, so on a stochastic hold-out about half the candidates as good as the seed are
  kept out, and the docs say so. **Project A2 must add the margin**
  (`private < seed_private − 2·σ̂/√seeds`). Cost: A0 alone is stricter than intended on noisy
  hold-outs.
- **Vetoed candidates are counted apart** — "kept out by the hold-out" in the run summary, the
  leaderboard (with the `!` flag), `status`, and a dashboard filter and progress-strip mark —
  never as "unfinished" or "not promoted". Checked in the browser on the demo run: 20 candidates
  = 8 ranked + 0 unfinished + 2 kept out + 10 rejected.
- **Two big-step counts.** Attempts (counted at planning) give patience its `min_big_steps`
  relief, so a strong model that keeps failing cannot hold patience open forever; applied big
  steps feed the economics guard. Replay recognises a failed apply by its row shape (rejected,
  no novelty, block equal to its parent's). The hint names its trigger, "scheduled" when both
  fire, and keeps it on the novelty re-prompt.
- **The status document's schema is 2**: `progress.improvement` lost `t`, and `n` now counts
  cases (per-instance means) instead of runs; the manual promises a bump when a key changes
  meaning.
- **The private score fails, with a sentence, when the hold-out run lacks a measure the score
  needs** (a weight, or a level when levels are set; a missing penalty counts 0). With levels
  the stage's own required-measure check fires first; the guard behind it is kept and commented.
- **`search.parent_top_k` is now a config error** naming `archive.top_k`; every config made by
  the old `init` template has it, so old configs fail at load with that sentence. The examples
  and the template no longer write it.
- **The design's engine sentences stay verbatim** ("holdout below the seed's …", "seed 1
  reported 20 values of …"): they are failure texts the design gives word for word and engine
  data.

### Limits logged, not fixed

- Read-only views of a pre-A0 run resumed after the upgrade show the stored λ = 1 ranking scores
  (they have no config to recompute with); they do apply the veto, to pre-A0 rows whatever
  `holdout_veto` says.
- `status` and `confirm` agree only when no run failed: `status` does not impute failed pairs as
  `confirm` does. The agreement test says so in its name.
- The canonicaliser collects bound names per block, not per scope: a lambda argument named like
  a free helper also renames the outer read (over-collapses only, rarely).

### Acceptance

- `python tasks.py run`: the quickstart counts moved and `docs/reference.md` "Quickstart" says
  so: ten rejected of twenty (one no-op, four duplicates instead of six — A0.1 keeps builtins
  apart — two behavioural twins, the `import os` block, one response in neither format, one that
  gave back its parent), four archive cells instead of five, one near-duplicate, and two
  finished candidates kept out by the hold-out. The best path is unchanged (−5.871 → −5.620 →
  −5.370).
- The live verdict's error rate is simulated: null gains on 3 cases × 2 runs call "clear" in
  2.5 % (asserted within [1.5 %, 3.5 %], 20 000 seeded trials); the old `t ≥ 2` on runs did so
  in about 5 %.
- Gate: the first `python tasks.py check` failed one slow test,
  `test_the_archive_spreads_across_cells`: it assumed every candidate that is not rejected
  carries an archive cell, and a candidate the hold-out keeps out finishes without one. The rule
  is right; the test now checks candidates that compete (commit after 81fa543). The rerun:
  **2785 passed, 5 skipped** in 29:11.
- PR: [meaurisch/evolvekit#78](https://github.com/meaurisch/evolvekit/pull/78), `algo/0-correctness`
  against `master`. **PASS.**

## Project A1 — the verdict (2026-11-09)

Branch `algo/1-verdict` from `algo/0-correctness` (1386757). Plan:
[2026-11-08-algo-1-verdict.md](../plans/2026-11-08-algo-1-verdict.md). Six tasks (A1.1–A1.3, A1.4a,
A1.4b, A1.5), each built by a fresh implementer and reviewed by a fresh reviewer; then the docs, a
whole-branch review, five statistician rounds, two persona rounds and five fix waves.

| Task | Closes | Review rounds |
|---|---|---|
| A1.1 log-ratio effects | S4 | 2 (the imputed loss was said as the wrong number) |
| A1.2 one decision | S1, S2, S8 | 4 (decisions 11–13 taken during it, after measurement) |
| A1.3 extend only while runs help | S2 | 2 |
| A1.4a Holm for bake-off and `top:N` | S3 | 3 |
| A1.4b merge small kinds of case, Holm over them | S3 | 2 |
| A1.5 levels judged with intervals | S7 | 2 (levels joined Holm) |

### The design's statistics, re-derived

The design's rule did not survive measurement. Each change below is a numbered decision in the
plan, with the simulation that forced it; the owner should read decisions 11–15.

- **The prior** (decisions 1, 11). The design's conjugate prior with κ0 = 1 capped the posterior
  t at √(n(n+1)): two held-back cases could never exceed a chance of 0.954, so Holm over groups was
  unreachable. A weak κ0 then counted the data twice ("from the data" prior: 22 % false "use") or
  understated the case spread (a run-noise prior: 13–14.5 %). The posterior is now the reference
  prior, μ | d ~ t(n − 1, mean, sd/√n): the chance is stated as a posterior under a flat prior.
- **The threshold** (decision 2). At "9 in 10" the design's own null procedure gave 11.5–13 %
  false "use" against its "< 10 %". "Use" needs a chance of 19 in 20.
- **The reads** (decision 12). Adding a run at a time and stopping on "use" took false "use" to
  16 %: the posterior is not a licence to peek against a point null. The final check reads its
  answer, adds all its extra runs at once, and reads once more; the app refuses a third read.
- **The rule said as it works** (decision 13). With both thresholds at 0.95 the 1 % margin leg can
  never bind; the rule is one sentence: "Use when the chance of a gain is at least 19 in 20."
- **Failures and "worse"** (decision 14 and statistician rounds 3–4). A candidate that failed
  more runs than the starting point is never "use" (a program crashing on a third of its runs read
  "use" at 0.99996); "worse" is symmetric with "use" (≤ 1 in 20; at 1 in 10 a change with no
  effect read "worse" in 17.8 % of null studies); a failure on either side is sized alike once one
  is floored at 1 % (an asymmetric floor made equal crashes read "worse" in up to 42 %).
- **The range a person sees** (decision 15, persona round 1). The page shows the 90 % range, whose
  low end is "at least"/"at worst" — the number the rule reads; the 95 % interval stays in
  `comparison.json` for engineers.

Measured false rates with no true gain (seeded, under the product's reads; slow tests assert
rate + 2 SE < 10 %):

| Setting | False "use" | False "worse" |
|---|--:|--:|
| runs noisier than cases (4 cases × 3 runs) | 8.5 % | — |
| pure run noise, the worst case | 8.9–9.0 % | 4.9 % |
| cases noisier than runs | 5.0 % | — |
| no repeated runs | 4.6 % | — |
| time stops the extension at 4 runs | 7.6 % | — |
| both programs crash alike, 5–30 % of runs | 0.1–5.1 % | 4.0–6.0 % |
| 3 kinds of case × 2 cases, with Holm (family-wise) | 8.8 % | — |
| 3 kinds of case × 4 cases, with Holm | 8.5 % | — |
| 4 branches of a bake-off, with Holm | 3.7 % (18 % without) | — |
| a level at its tolerance edge called equal | 2.7 % per side (49 % before) | — |

A 3 % true gain on four held-back cases reads "use" in about 96 % of studies.

### Statistician review

| Round | Findings | What it found |
|---|---|---|
| 1 | 6 (1 Important) | per-group Holm ran after two reads; its test simulated one |
| 2 | 5 (2 Important) | the engineers' file under levels said the objective's answer; a crashing program read "use"; false "worse" 17.8 % |
| 3 | 5 (1 Important) | a do-nothing change read "use" when the starting point timed out |
| 4 | 1 (Important) | equal crashes on both sides read "worse" |
| 5 | **clean** | one docstring |

### Persona round (the Study page)

A planner read three finished studies (use; keep limited by its cases; a bake-off "use" among two
branches) through the in-app browser, in a fresh home whose path has a space, built with the real
`run_study`/`run_bakeoff`.

| Round | Score | Blocker | What it found |
|---|---|---|---|
| 1 | 6/10 | none | "at least 4.5 %" beside a bar whose low end was 4.0 % (two bounds for one idea); no chance in figures on "use"; two names for the range |
| 2 | **8/10** | none | **pass**; the bake-off caveat beside "at least" (reworded), a keep without a middle figure (added) |

### Limits logged, not fixed

- The app's groups preview reads the study's un-merged `groups.json`; the run's own copy records
  `merged_from` (A4.2 reworks grouping).
- Levels results written on this branch before the 90 % range fall back to a "95 % range" label.
- A Keep page still shows a "Settings to keep" table with the new settings' column (the page's
  shape is out of programme A's scope).
- Campaign rechecks and Continue ask a new question; they are outside the measured rates.

### Gate and PR

- Gate: `python tasks.py check` on 5f04e66, nothing else running: **2914 passed, 5 skipped** in 35:58.
- PR: [meaurisch/evolvekit#79](https://github.com/meaurisch/evolvekit/pull/79), `algo/1-verdict`
  against `master`, stacked on #78. **PASS.** The session stopped here at the owner's request; A2–A4
  start from [the handoff prompt](../design/2026-11-09-algorithms-a2-a4-session-prompt.md).

### The owner's review (2026-11-10)

The owner accepted the rule and its measured rates, and asked for chances in percent instead of
"19 in 20" (plan decision 16): "Use when the chance of a gain is at least 95 %.", "about 96 %",
"under 95 %", "over 5 %". Words only; no decision changes. Gate after the change: 2913 passed, 1 failed, 5 skipped in 43:03;
the one failure was a report test that asserted no "95 %" anywhere (the rule now says it), narrowed
to "no 95 % range"; that file then passed (25 tests).

## Step 0 for A2–A4 — a clean start (2026-11-11)

The second session runs from [the handoff prompt](../design/2026-11-09-algorithms-a2-a4-session-prompt.md),
started by another session, in the git worktree `.claude/worktrees/app-redesign-build-17432c` (the
main checkout holds `algo/1-verdict`).

- `git status --porcelain` empty. Base: A0 (#78) and A1 (#79) are merged, so **`master` at 90c8c38**
  ("Merge pull request #79"); `algo/2-noise` is cut from it.
- `user.email` and `user.name` as required.
- Python is the main checkout's `.venv/Scripts/python.exe` with `PYTHONPATH` set to the worktree
  (the venv's editable install points at the main checkout; `PYTHONPATH` makes every test and
  subprocess import the worktree's code). The git-ignored `benchmarks/pyvrp_hard/instances/` was
  copied into the worktree.
- `python tasks.py check` on master: **2905 passed, 1 failed, 13 skipped** in 30:47. The failure is
  the worktree's path, not the code: `test_check_a_sample_that_fails_names_the_engineer_s_program`
  asserts that the fix sentence does not contain "trees" (a removed checkout's path), and this
  worktree's path is `…\.claude\worktrees\…`. It passes in the main checkout; every later gate in
  this log carries the same one failure for the same reason. Eight more tests skip than in the
  first session (environmental; the later gates print their reasons).
- `python benchmarks/operator_mixes.py --seeds 5` on master (the "before" of A2):

  | Regime | lhs only | local only | mix |
  |---|--:|--:|--:|
  | far (defaults 1225.00) | 1031.56 | 1106.35 | 1034.30 |
  | mature (defaults 102.57) | 102.20 | 100.05 | 100.30 |

## Project A2 — noise-aware search (2026-11-11 to 2026-11-14)

Branch `algo/2-noise` from master 90c8c38. Plan:
[2026-11-10-algo-2-noise.md](../plans/2026-11-10-algo-2-noise.md), written to step level from design
§5 and checked by a fresh builder check that re-derived every formula and simulated every rate the
plan asserted; nineteen decisions in the plan record what was ruled before, during and after the
build. Fourteen tasks, each built by a fresh implementer and reviewed by a fresh reviewer; then four
statistician rounds, a benchmark diagnosis and a docs sweep.

| Task | Closes | Review rounds | Commits |
|---|---|---|---|
| A2.1a the noise estimate (`work/noise.json`) | — | 1 + minors | 1a094bc 7beb926 |
| A2.1b repeatability, calibration, `progress.noise` | — | 1 + minors | 177ac67 c1d4d0d |
| A2.1c the hold-out veto's noise margin | A0 decision 7 | 1 + minors | 521aa95 be84127 |
| A2.2 behaviour twins by noise | Q7 | 1 + minors | cc19ed0 9423299 |
| A2.3a the race at fixed looks | S5 | 1 + minors (a ruling mid-task: decision 14) | 85d7d49 5f9027d |
| A2.3b promotion band, percentiles of like with like | Q9 | 2 (decision 15) | ebaff0e 595826f 4a1b2ea |
| A2.3c screening as a controller | S6 | 1 + minors | 3023c94 1674eac |
| A2.4a improvement and elites on the noise | Q5 | 2 (three Important) | f82ba85 eaf2a24 |
| A2.4b re-evaluating the best | Q5 | 2 (two Critical) | 75cbbc0 b7dd9fc |
| A2.5a one run-wide hypercube, ranked TPE, constant liar | Q3, Q4 | 1 (one Important) | b45d99d 5f9412f |
| A2.5b TPE density, priors that act | Q4, S11 in part | 1 + minors | 32bb49b cf66895 |
| A2.6a quantile and parameter axes | Q2 | 3 (two Important) | 099a0de 5471c9f 5c35ee6 |
| A2.6b a dial on selection | Q1 | 1 + minors | 65930a5 f60b524 |
| A2.6c reflected steps, the 1/5 rule | Q6 | 1 (decision 16) | d572f39 6303f2a |
| Statistician fixes (decisions 17, 18) | — | rounds 1–4 | 74e3467 c27f25f c53df5d |
| Benchmark fix (decision 19) | — | — | 027d550 |

### What measurement changed

The design's rules did not all survive measurement; each change is a numbered decision in the plan.

- **The noise a comparison means** (decisions 1, 17). Every candidate runs on the same seeds, so for
  an evaluator that repeats itself on a seed the across-seed spread is the wrong yardstick: the run
  now runs the seed once more on seed 0 per stage, and a repeatable stage compares candidates by the
  paired noise (two-way residuals over candidates × seeds). At `seeds: 1` that is no noise — today's
  behaviour.
- **Twins** (decisions 2, 3, 13, 15). "Within the noise of anything" rejected 97–100 % of children
  once the index filled; a child is now a twin only of a candidate of an earlier round that scored
  at least as well, compared on the evaluator's own numbers. A real gain is never a twin.
- **The race** (decisions 5, 14, 17a, 18a). A spread term for cases that trade, read on the stage's
  own cases; same-seed comparison with the incumbent; a fixed per-look tail. Equal candidates are
  raced out in about 0.5 % (36.5 % before A2, measured).
- **Few degrees of freedom** (decision 17b, superseding the design's "no minimum evidence"). A
  one-run study's noise comes from three runs: every margin now uses the Student-t quantile at the
  estimate's degrees of freedom, so the documented rates hold at 2 df as at 200. The price is power:
  at `seeds: 1` the race stops 11 / 36 / 82 % of candidates 5 / 10 / 20 % behind (97 % at 5 % when
  the noise is well measured), and the docs say so.
- **A re-evaluation is never progress** (A2.4b review). A re-measured best moves the bar both ways
  without touching patience; the economics series is read as of each round.
- **The 1/5 rule per trial** (decision 16): the design's per-round threshold settles at 30–50 %
  success with one or two local children a round.
- **The benchmark** (decision 19). After A2.6a the "mature" mix read 100.72 against 100.27 (15
  seeds). Diagnosis: on a typed space the grid does not steer the search; the larger grid only made
  every child draw inspirations that model-free operators never read, shifting the random stream.
  Model-free operators now draw none.

Measured rates (seeded, through the product's code, with the noise estimated as the product
estimates it; slow tests assert the tight bands):

| Rule | Rate |
|---|--:|
| hold-out veto: an equal candidate kept out (any df, any correlation of weighted measures) | 7.6–8.3 % (50 % before) |
| twin: a true twin caught, one number / ten numbers at 2 df | 84.5 % / 93.8 % |
| twin: a real gain of 4 sd called a twin | 0 % |
| race: an equal candidate raced out, fresh / best of 8 / best of 50 (well measured) | 0.5 / 2.7 / 6.6 % |
| race: a candidate trading cases, not behind on the stage's cases | 3.1 % |
| promotion: a candidate level with the k-th left out | 7.9 % |
| promotion: one noise sd better left out (well measured) | 18 % (41 % before) |
| epsilon: an equal candidate read as an improvement over one draw | 36.2 % |
| elite: an equal challenger takes the cell | 24.2 % |
| screening: a good screen switched off / an unrelated one grows k | 1.1 % / 90.6 % |

### Statistician review

| Round | Findings | What it found |
|---|---|---|
| 1 | 1 Critical, 3 Important, 1 Minor | the race compared a seed-0 prefix with an all-seed mean under the paired noise; every rate assumed a known noise (veto 14.7 %, twins 70 %); the paired estimate never passed the seed's 2 df; an unmeasured screen stage rejected distinct children as twins |
| 2 | 2 Important, 2 Minor | the race lost its power at 2 df (5 % behind stopped 6 %); `score_sd` ignored the covariance of weighted measures (veto 14.8 % at correlation 0.8) |
| 3 | 1 Important | the calibration path still ignored that covariance; one docs sentence described the wrong case |
| 4 | **clean** | one Minor (calibration across cases assumes proportional profiles) |

### Benchmark

`python benchmarks/operator_mixes.py --seeds 5`, before (master 90c8c38) and after (027d550):

| Regime | lhs only | local only | mix |
|---|--:|--:|--:|
| far, before | 1031.56 | 1106.35 | 1034.30 |
| far, after | 1043.33 | 1098.02 | 1035.44 |
| mature, before | 102.20 | 100.05 | 100.30 |
| mature, after | 102.52 | 100.30 | **99.89** |

The acceptance holds: the "mature" mix's mean true cost of the reported best did not rise. The
benchmark's solvers have no `{seed}`, so it cannot see A2.1–A2.4 (σ̂ = 0 there); a seed-deterministic
`{seed}` regime is the follow-up that would.

### Limits logged, not fixed

- A non-final stage is checked for repeatability only in the seed's generation; a run resumed from
  before A2 keeps the exact hash at its screen.
- The calibration's per-case covariance assumes the measures' levels are proportional across cases
  (a negatively correlated, mismatched pair reads 8.6 %).
- Two true noise twins of one round both go deeper (decision 15's cost).
- `Noise.pool` (re-evaluation at `seeds > 1`) inflates the best's variance by about 11 %.
- An interrupted re-evaluation is not retried and not counted against the daily cap on resume; the
  as-of economics series costs O(re-evaluations × rows).
- The parameter grid does not yet hold configurations apart in the search (typed parents come from
  the flat top-4); drawing them from grid cells needs its own benchmark.
- On resume the stop policy and the archive replay with today's noise (documented).
- A run dying while it breeds, before `pending.json` exists, skips the hypercube rows that round took.

### Whole-branch review and gate

- A whole-branch review (read-only, across the seams between tasks) found one Important — two
  `promote.adapt` stages shared one `work/screening.json` and overwrote each other on resume — and
  nine Minor (resume windows for the noise, screening and hypercube files; the replayed epsilon;
  λ recomputed on every row in the views; `promote.adapt` in places it cannot act now an error; the
  `init` template writes `epsilon: null`; dead code). Fixed in 1141625.
- The first gate (493b019) failed two slow tests the task implementers had not run: a fake
  `evaluate_generation` without A2.3b's `stage_scores`, and a run count that did not include the
  seed's repeatability runs (by design; the concurrency check now covers them too). Fixed in 1141625.
- Gate: `python tasks.py check` on 1141625, nothing else running, the PyVRP harness tests pointed at
  the main checkout's `.venv-pyvrp`: **3243 passed, 1 failed, 5 skipped** in 36:35. The one failure is
  the worktree's path (step 0).
- The benchmark re-run on 1141625 gives the "after" table above to the digit.
- PR: [meaurisch/evolvekit#83](https://github.com/meaurisch/evolvekit/pull/83), `algo/2-noise` against
  `master`. **PASS.**

## Project A3 — what the model is told (2026-11-14)

Branch `algo/3-prompt` from `algo/2-noise` (1141625). Plan:
[2026-11-14-algo-3-prompt.md](../plans/2026-11-14-algo-3-prompt.md), checked by a fresh builder check
(read-only; it found eight concrete fixes and five questions, ruled as the plan's eleven decisions).
Two tasks, each built by a fresh implementer and reviewed by a fresh reviewer.

| Task | Closes | Review rounds | Commits |
|---|---|---|---|
| A3.1 a per-parent ledger, one local change summary | Q8 | 2 (one Important: "no reply" for errors where a reply came back) | b8767d3 d23f1c0 |
| A3.2 big steps with spacing and a true hint | Q8, D6 in part | 1 + minors | 02836df 527c331 |

### Decisions

- **The ledger's words are measured against the rows the engine writes**: a twin of an earlier
  candidate that scored at least as well reads "same behaviour as" (exact) or "within the noise
  of"; "raced out after N instances"; "kept out by the hold-out"; "not ranked (score X)" for a
  finished row that does not compete; "not applied" only where a reply came back and could not be
  applied, "no reply" where none did. The score change is on the score the parent's breakdown
  shows, and a float-error difference prints `+0`. A function changed only in its numbers reads
  `f (numbers only)` (A0 decision 3's definition). The docstring is no longer used in any summary.
- **Bounded**: five lines of at most 160 characters each including the bullet, every kind of line
  break collapsed, children ordered by their integer counter; the ledger is built only for operators
  that call a model, and replaces the old failure section in both prompt builders.
- **Big steps**: after a plateau big step that applied, none for `big_step_every` generations unless
  something improved (scheduled steps keep their schedule); the hint names the trigger and the
  previous big step as a ledger line. One helper keeps A0's two counts and the spacing for the live
  path, the replay and adopted generations — closing A0's adoption gap. `generation_started` records
  `plateau`, so the replay is exact when the noise moved since.
- **Prompts keep the engine's words** (they are read by the model); the people's vocabulary binds the
  app, the `study` CLI and docs for people.

### Limits logged

- Siblings of the current generation never appear in the ledger.
- A scheduled big step inside a plateau window still runs (at most two strong-model calls per window).
