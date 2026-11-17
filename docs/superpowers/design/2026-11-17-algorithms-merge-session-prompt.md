# The algorithms programme, A2–A4: the test-and-merge session prompt

Paste everything below the line into a new Claude Code session opened in the evolvekit checkout
(`C:\Users\norri\studio\evolvekit`), once no other session is working in it.

What exists when you start:

| Project | Branch | PR | State |
|---|---|---|---|
| A0 correctness | `algo/0-correctness` | #78 | merged (master 90c8c38) |
| A1 the verdict | `algo/1-verdict` | #79 | merged |
| A2 noise-aware search | `algo/2-noise` | #83 | open, against `master` |
| A3 what the model is told | `algo/3-prompt` | #84 | open, against `master`, stacked on #83 |
| A4 allocation and memory | `algo/4-allocation` | #85 | open, against `master`, stacked on #84 |

---

Test PRs #83, #84 and #85 of meaurisch/evolvekit thoroughly, fix the bugs you find, and merge them —
in order, #83 then #84 then #85, with merge commits (`gh pr merge <n> --merge`) — once each is fully
correct. The owner asked for this and authorises the merges; do not merge anything that is not green
and correct. You run autonomously; take decisions the way a careful colleague would and write them
down.

## Read first

- `AGENTS.md`: never push to `master`; `python tasks.py test | full | run | check | lint` are the only
  entry points; `check` is the gate.
- `docs/superpowers/reviews/2026-11-08-algorithms-build-log.md`: the A2, A3 and A4 entries and the
  final entry (gates, the benchmark, the limits logged, which decisions marked "(departs)" leave the
  design's text on measured grounds).
- The three plans' "Decisions": `docs/superpowers/plans/2026-11-10-algo-2-noise.md` (19),
  `2026-11-14-algo-3-prompt.md` (11), `2026-11-14-algo-4-allocation.md` (17). Treat them, including the
  "(departs)" ones, as settled rulings: reopen one only if you find it factually wrong or broken in
  the code, and then say why in the PR before changing it.
- The A1 rule is owner-accepted and fixed (reference prior, use at a chance of a gain ≥ 95 %, worse
  ≤ 5 %, Holm, at most two reads of a comparison). Chances for people are said in percent, never
  "19 in 20".

## Environment facts (learned the hard way)

- Python is `.venv/Scripts/python.exe`. In a git worktree its editable install still points at the
  main checkout, so there ALWAYS set `PYTHONPATH` to the worktree (subprocess tests inherit it).
- Set `EVOLVEKIT_PYVRP_PYTHON` to `.venv-pyvrp\Scripts\python.exe`, and in a worktree copy the
  git-ignored `benchmarks/pyvrp_hard/instances/` from the main checkout, or 8 PyVRP tests skip.
  `PYTEST_ADDOPTS=-rs` prints skip reasons; 5 skips are expected.
- The full gate takes 35–50 min: run it in the background and never beside a demo study, the app, a
  benchmark or another heavy test run (it has timing-sensitive tests).
- Known false failure: `tests/test_harness_code.py::test_check_a_sample_that_fails_names_the_engineer_s_program`
  fails ONLY when the checkout path contains "trees" (e.g. `.claude\worktrees\…`); it passes in CI
  and in the main checkout. Never weaken it.
- Write files with the Edit/Write tools (no shell heredocs, no PowerShell `Set-Content`). Never
  `git stash` (the stack is shared); use a WIP commit. Commit trailer:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Subagents one or two at a time; they cannot write report files, so ask for reports inline.

## What "test" means here (do all of it and report what you ran)

1. CI on each PR (`gh pr checks <n>`) green on Linux and Windows.
2. `python tasks.py check` on `algo/4-allocation`'s tip (it carries all three projects).
3. The product end to end, as a person and as an engineer would use it:
   - `python tasks.py run`.
   - A demo-tour study through `evolvekit study …` in a fresh home whose path has a space: plan,
     run, final check, verdict, report, `study status`, `study verdict`.
   - A second, warm-started study (`--warm`): the priors recorded in `study-run.json`; the
     "against the harness defaults" line (on the usual warm chain it must say "not measured").
   - `study groups` on the demo tour (must give ONE group) and on tagged cases; a per-group study.
   - Stop a study mid-round and resume it.
   - The Study page and the "What we learned" page in the app, through the browser (start the app,
     check the pages and the console, stop it before any gate run).
   - The new run files look as `docs/reference.md` says: `work/noise.json`, `work/screening.json`,
     `work/perturb.json`, `work/lhs.json`, `reevaluations.jsonl`, `job.json`'s `plan.rounds_left`,
     `search.converged`, `final.saved`.
   - Every sentence meant for people uses the app's vocabulary (study, case, held-back case,
     starting point, round, final check) and percent.
4. `python benchmarks/operator_mixes.py --seeds 5`: the "mature" mix must not rise above master's
   100.30 (A2's after-figure was 99.89).
5. A focused review of anything that looks wrong; for any statistical doubt, a fresh reviewer
   subagent that simulates the product's real procedure (every earlier review round found a test
   that simulated a simpler one).

## Fixing

Fix each bug on the LOWEST branch it belongs to (A2's code on `algo/2-noise`, A3's on
`algo/3-prompt`, A4's on `algo/4-allocation`), with a failing test first; then merge that branch
forward into the higher ones (`git merge`, never rebase — the stack uses merge commits) and push
every affected branch. Re-run the gate on a branch after fixing it. Record each fix in the build log
under a new "Pre-merge testing" entry: what was found, the fix, the commit.

## Merging

When #83 is fully correct (CI green, gate green, its part of the end-to-end checks passed), merge it.
#84 and #85 keep `master` as their base and their diffs shrink on their own: check that, let CI run
again, then merge #84, then #85, each only when green and correct. If something should block a
merge and you cannot fix it correctly, do not merge that PR or the ones after it; report instead.

After the last merge, update the local `master` (`git switch master && git pull`) if no other session
is working in the checkout, and update the project memory note `algorithms-build.md` to say A2–A4 are
merged.

## When you are done

All three merged to `master` with merge commits; a "Pre-merge testing" entry in the build log listing
what was tested and fixed; one short report: what you ran, what you found and fixed (with commits),
anything you chose not to fix and why. Then stop.
