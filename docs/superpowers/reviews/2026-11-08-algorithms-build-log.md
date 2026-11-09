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
- PR: PR_A0_PENDING.
