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
