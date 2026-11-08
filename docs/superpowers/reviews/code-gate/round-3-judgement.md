# Gate p3, round 3: engineer persona (code study, bake-off, PR)

**Score: 7/10. Blocker: none.** Nothing stopped me. One thing I would not trust as shown: evolvekit's own diff and size line for the winning change. The app, the report, best.patch, the PR body and the agent all said "+151 −124 lines in solver.py". The commit that actually landed is **+35 −8**. The recorded diff is a whole-file CRLF rewrite.

Times are WEST, 2026-11-08. App: http://127.0.0.1:8780/?token=REDACTED The agent started it; I started no app.

## Ask 1: the code search

**Asked:** "Use evolvekit to make the demo-tour solver in solver-repo find shorter tours in the same time, by changing its code; spend at most 10 minutes and 1 dollar, and hold back two cases for the final check."

**Agent did and said (≈1 min):**
- Made study `shorter-demo-tours` (template `faster`, `from: HEAD` d9cdf00, open file `solver.py`, $1 AI cap, 0.167 h) and started it.
- Gave the app link and quoted the compiled plan word for word: about 3 rounds, about 21 combinations, about 20 s per AI change, a final check on 2 held-back cases, done around 07:05, and "With 2 held-back cases a 'use' is unlikely…".
- Stated what it chose: tour length lower; time per case 2 s (the harness default); all 7 cases from `data/` and `fresh/`; evolvekit held back `b-60` and `e-150`.
- Warned what the AI agent can reach in a worktree.
- Said nothing was pushed.

**In the app:**
- The Study page followed the run live.
- Round 1 showed "Nothing better yet · Writing changes: 2 of 7 · AI: $0.20 of $1.00".
- Later: "Best so far: 3.9 % lower tour length on the cases it learned from · Not checked yet".
- The Code tab listed each combination with its size. Details showed the diff with a green/red tint and the result case by case. Combination 3 was "+1 −1": the default `--init` changed to `nearest`, the same idea as my nearest-first branch.
- Finished 07:03:44, after 8 min: 4 rounds, 25 combinations.
- Verdict, word for word in the app, `study status` and `study verdict`: "Probably better: 8.0 % lower tour length on 2 held-back cases, a chance of about 19 in 20; at worst 0.6 % worse than your starting point (the code at HEAD)." Advice: "Use the new code change." Decision: `use`. Interval [−0.6 %, +16.5 %]. 7 runs per held-back case. b-60 7.3 % lower, e-150 8.6 % lower.
- Just before the check the search's own figure was 12.3 % on the learned cases. The checked figure is 8.0 %, so the optimism of the search was visible and discounted.

**Friction (worst first):**
- The size and diff are wrong everywhere evolvekit shows them. The winner reads "+151 −124 lines in solver.py": the recorded blob has CRLF endings and the base has LF. Half of the round-3/4 combinations show the same whole-file rewrite. In the app's Details and in best.patch the change cannot be reviewed; the real change is about 45 lines.
- "Use" came on only 2 held-back cases. The interval is t with df=1 and its worst end is −0.6 %, just inside the 1 % rule. The plan had said a 'use' is unlikely with 2. The agent stated the warning in ask 1, but did not raise it again before the PR.
- The AI spend went over the cap: "AI: $1.03 of $1.00". The agent said so plainly in ask 2.
- The agent chose the case pool itself: `fresh/` went into training and two `data/` files were held back. I had kept `fresh/` apart for a reason. It told me, but did not ask.
- The Tries plot's y-axis reads "% BETTER THAN TODAY" while every point on it is unchecked. The strip and the chips are careful ("not checked"), so this is minor.
- Planned 3 rounds and 21 combinations; it ran 4 rounds and 25. That is harmless but unexplained.

## Ask 2: compare my branches

**Asked:** "Now compare my branches nearest-first and restart-sooner against main the same way."

**Agent did and said (≈1 min):**
- Started `study bakeoff` on the same study and the same held-back b-60 and e-150, with no search and no AI.
- Gave the link.
- Repeated ask 1's verdict and flagged the $0.03 overspend.
- Finished in 57 s, 12 runs per case.

**Verdict:**
- "None of the 2 branches is shown to beat the starting point." / "Keep today's code for now: the gain is not proven."
- **nearest-first:** "Not proven on 2 held-back cases: a chance of about 19 in 20 of a gain; at worst 2.6 % worse (somewhere between 2.6 % worse and 12.1 % better)." b-60 4.1 % lower, e-150 5.3 % lower.
- **restart-sooner:** "No difference in any run … a fixed setting, or a code path these cases never reach, can leave a change without effect."

I checked restart-sooner myself. Its diff is `restart_after // 4`. With accept-worse 0, a restart resets the tour to the best tour it already is, so a no-op is genuinely right. This explanation is the best moment of the session.

**In the app:**
- The Study page switched to the new run by itself: "Comparing branches · No answer yet · Running the starting point and 2 branches…".
- Then the KEEP strip with each branch's line, and "See the branches side by side".
- The Compare page gave the starting-point column and one column per branch: commit, verdict line, per-case change and range band. restart-sooner had no band and read "the same". The page was clean, with no horizontal overflow (scrollWidth 1009 ≤ 1024).

**Friction (worst first):**
- After the bake-off the study as a whole reads "NOT PROVEN … Keep today's code" on Home and in `study status`. The AI change from run 1, which said *use* and was pushed in ask 3, is now only on "An older run of this study". Anyone glancing at Home would think the study says keep.
- The bake-off did not include the AI's winner as a third column, so I cannot see "AI change vs my branches" on one page. I did not ask for it, but it is the comparison I actually want.

## Ask 3: the pull request

**Asked:** "If the verdict says to use a change, open a pull request for it into origin — yes, you may push."

**Agent did and said (≈2 min):**
- Chose the right verdict: run 1's *use*, not the bake-off's *keep*. It said why and quoted both.
- Pushed branch `evolvekit/shorter-demo-tours-20261108-065534` (commit 3beea9d) to origin.
- Opened no PR, because origin is not on GitHub. It quoted evolvekit's message and pointed to `runs/20261108-065534/pr.md` as the body.
- Pushed nothing for my branches.

**What I verified in `remote.git`:**
- The commit is on d9cdf00, authored by my own identity (Delivery Engineer).
- The diff is **+35 −8**, LF, clean: a position index, a 2-opt variant, and a double-bridge kick on restart.
- The commit message carries the verdict line.
- solver-repo is clean: still on main, no leftover worktrees, no `refs/evolvekit`.

**pr.md:** verdict, rule, held-back table, how it ran. It also says "+151 −124 lines in solver.py" (wrong) and ends with "## Files — No files yet." (noise in a PR).

**Friction (worst first):**
- The PR body states the wrong change size (+151 −124 against the real +35 −8). A reviewer opening the PR sees a contradiction at once.
- No PR was opened: origin is a bare local repository. That is expected and clearly explained, but the ask was not completed end to end.
- The app does not show that a branch or PR was made for the run.

## Where the numbers came from

Every figure I was told came from evolvekit's own output, quoted word for word by the agent:

- `study status`, `study verdict --json`, `study report`, `study pr`.
- The final check reran the starting code and the chosen change paired on the held-back b-60 and e-150 (7 runs each for the search's winner, 12 for the bake-off). It took the per-case mean change and an instance-level 95 % interval: 2 cases, so df=1. Checked by hand: mean 7.97 ± 12.71 × 0.675 gives [−0.6, 16.5], which matches.

The agent composed no numbers of its own.

**Did anything claim "better" before the final check?** No headline did. Before the verdict:

- The strip said "Best so far: … lower … on the cases it learned from · Not checked yet".
- `study status` said "… so far, not checked yet".
- The chips said "… not checked".

"Probably better" appeared only after the final check. The one word before it was the Tries plot's axis label "% BETTER THAN TODAY", over unchecked points.

## What would raise the score

1. Make the recorded diff from what git would commit. Normalise line endings, or diff with the repository's eol and autocrlf rules, so that the app's Details, best.patch, the size line, the report and the PR body all show the real +35 −8. **This alone is worth +1.5.**
2. Before `study pr --yes` on a 2-case *use*, have the agent (or `study pr`) restate the thin-check note. Offer a re-check on more held-back cases, for example `fresh/`.
3. Keep the AI spend within the cap. Or say in the plan that the cap can be exceeded by up to one call.
4. On Home and in `study status`, do not let a bake-off's *keep* hide an earlier run's *use* of a different change. Show both, or the newest run per kind.
5. Optionally put the search's winner into the bake-off as one more column.
6. Drop "Files: No files yet" from the PR body.
