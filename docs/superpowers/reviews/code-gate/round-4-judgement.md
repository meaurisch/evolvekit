# Gate p3, round 4 — the engineer's judgement

**Score: 8/10. Blocker: none.**

I got a trustworthy answer and a change I can review. Every step finished: the AI search with a final check, the comparison of my two branches, and a pushed branch with a PR body. There was no PR because `origin` is a plain local bare repo, and the agent and evolvekit both said so plainly. The deductions are for wording that oversells a thin final check and for small loose ends.

Timeline (2026-11-08): ask 1 08:06, study started 08:08:30, ended 08:14:06 (6 min, AI budget hit); bakeoff 08:15–08:16; branch pushed 08:17.

---

## Ask 1 — "Use evolvekit to make the demo-tour solver in solver-repo find shorter tours in the same time, by changing its code; spend at most 10 minutes and 1 dollar, and hold back two cases for the final check."

**What the agent did and said.**
- First it summed up what it understood: the `faster` template, `solver-repo/solver.py` at `ead9b18`, the brief, 10 min, $1, two held-back cases. Then it asked one fair question: which cases should be used (`data/` has five, `fresh/` has two).
- I answered "all seven, hold back the two in fresh/". It told me evolvekit had first picked b-60 and e-150 to hold back, and that it had switched them in `study.yaml` to f-70 and g-130.
- It then showed the plan verbatim: about 3 rounds and about 21 combinations, done around 08:18, plus evolvekit's own warning: "With 2 held-back cases a 'use' is unlikely … Hold back more cases for a firmer answer."
- It also told me what the AI agent can reach. git can reach my branches and remotes, and the block on git writes "isn't watertight". It asked before starting. I said start.
- It gave me the app link (token redacted here) and said "ask me when it has finished".
- When I asked, it quoted the verdict word for word, said the run used up its AI budget ($0.89 of $1.00), and added its own sober note: the verdict rests on only two cases. It did not touch the repo.

**What I saw in the app.**
- While running: "SEARCHING · ROUND 2 OF 3 — Best so far: 2.7 % lower tour length on the cases it learned from — Not checked yet". The AI spend was shown live ($0.81 of $1.00). The plot axis read "% change vs today, on the cases it learned from (not checked)".
- The Code tab listed the combinations as "+19 −10 lines in solver.py". The current best was tagged "best so far, not checked".
- At the end: "USE IT · FINAL CHECK ON 2 CASES. Probably better: 9.1 % lower tour length on 2 held-back cases, a chance of about 20 in 20; at least 2.0 % better than your starting point (the code at HEAD)." It also showed a likely-range bar with the 1 % limit dashed, and the rule.
- "Show the change" opens a side panel with the full unified diff and the change per learned-from case (a-40 9.5 %, b-60 6.9 %, c-80 11.1 %, d-110 11.7 %, e-150 9.0 % lower).
- The diff is a real, reviewable algorithmic change. It does an O(1) 2-opt delta with an in-place reversal and a position index instead of `tour.index`, applies a double-bridge kick on restart, and makes `--init` default to `nearest`. It still honours the same `--time-limit` deadline.
- git worktrees were cleaned up afterwards, and my working tree was untouched.

**Friction (worst first).**
- "a chance of about 20 in 20" rounds 0.98 into words that sound certain, on 2 cases whose interval spans 2 %–16 %. It sits awkwardly beside "Probably better" and the thin-answer note.
- The run stopped at 6 of the 10 minutes because the AI dollar ran out: 16 of the about 21 planned combinations, $0.81 spent by minute 5. This was said honestly, but $1 buys little search.
- Two leftover compile-only run folders (`20261108-080807` and `-080826`, from the agent compiling the plan twice) sit beside the real run in `runs/`.
- It cost two extra round trips before anything ran (which cases, then go ahead). Both questions were fair, but an engineer who said "hold back two" might expect the `fresh/` folder to be guessed.
- The agent's `C:\gate\p3\evolvekit-report.md` keeps an empty "Files … No files yet." section.

## Ask 2 — "Now compare my branches nearest-first and restart-sooner against main the same way."

**What the agent did and said.**
- It started a bakeoff with no search and no AI spend, on the same held-back f-70 and g-130, and gave the app link.
- When asked, it quoted:
  - The overall line: "None of the 2 branches is shown to beat the starting point. Keep today's code for now: the gain is not proven."
  - nearest-first: "Not proven … at worst 12.6 % worse (somewhere between 12.6 % worse and 18.8 % better)".
  - restart-sooner: "No difference in any run: every run of this change gave exactly today's result, so the study may not exercise it…"
- It added its own reading: restart-sooner "may change something that these two cases never reach in 2 seconds".

**What I saw in the app.**
- The Study page flipped to "KEEP · FINAL CHECK ON 2 HELD-BACK CASES", with one line per branch. The first run's USE is now a one-line "An earlier run (20261108-080830) says use: …" above it.
- "See the branches side by side" opens the Compare page: the starting point ("2 held-back cases, 12 runs each"), then nearest-first (4895275) with f-70 1.9 % lower and g-130 4.3 % lower plus a range bar, then restart-sooner (c62f032) with "the same" on both.
- I checked the diffs myself and both results are right:
  - nearest-first only flips the `--init` default, which is real but small and noisy.
  - restart-sooner (`restart_after // 4`) restarts to the best tour with accept-worse 0, which is a no-op. The "exactly today's result" detection is a genuinely useful catch.

**Friction (worst first).**
- The agent's explanation for restart-sooner was a guess, and the wrong one. The change is a no-op by construction (restarting to `best` when nothing ever accepts a worse move), not a path the cases fail to reach. evolvekit's own line was correctly hedged.
- "The same way" is not quite the same: the final check ran 3 runs per held-back case, while the bakeoff ran 12 runs for the starting point (6 per case). Nothing says why they differ.
- The AI's own change was not compared alongside my branches. I have two separate verdicts against main rather than one table (I did not ask for it, but an engineer would want it).
- App: after jumping between Compare and Study with the hash link, the page kept showing the Compare content until a full reload.

## Ask 3 — "If the verdict says to use a change, open a pull request for it into origin — yes, you may push."

**What the agent did and said.**
- It picked correctly. The AI change from run 20261108-080830 (verdict "use") went ahead; nothing was made from nearest-first or restart-sooner, and it said why.
- It ran the PR step. That made one commit on `main@ead9b18` with my own git identity, made branch `evolvekit/shorter-tours-by-changing-the-solver-20261108-080830` (commit 3c84138), and pushed it to origin.
- It said "no pull request exists yet" because origin is not on GitHub, and gave the path of the PR text (`home\studies\shorter-tours-by-changing-the-solver\runs\20261108-080830\pr.md`).
- It repeated the verdict and evolvekit's thin-answer note.

**What I saw / checked.**
- The remote has the branch. The diff is the same +35 −12 in `solver.py`, and `main`, my branches and my working tree are untouched.
- `pr.md` is a good review body: the verdict, the thin-answer note, the 95 % interval, the per-held-back-case table (means of 3 runs), what changed, and how it ran (AI $0.89, 16 of about 21 combinations, 6 min). It names no local folders.
- The app's run page now says "Branch evolvekit/shorter-tours-by-changing-the-solver-20261108-080830 pushed to origin".

**Friction (worst first).**
- There is no PR to review. That is correct for a non-GitHub remote and honestly reported, but I am left to copy `pr.md` from deep inside the library home myself.
- The PR body names the change "combination g002-c0009", while the app calls it "No. 9 · round 2". It also repeats "about 20 in 20".
- The PR body does not mention that my two hand branches were compared and came out "keep". A reviewer would want that context.
- The held-back table has no unit (km) on the Start/Best columns.

---

## Where the numbers came from

- **The measure.** `tour_length` is the demo-tour harness's KPI, read from the JSON the solver prints. Each case is solved with a 2 s time limit.
- **During the search.** The figures were on the 5 learned-from cases (a-40 … e-150), each expressed as a % change against today's code on that case and then averaged. They were always labelled "on the cases it learned from" and "not checked".
- **The final check.** It used the 2 held-back cases (f-70, g-130) that the search never saw, with 3 paired runs per case: today's code against the change.
  - f-70: 7.035 → 6.430 (8.5 % lower).
  - g-130: 9.588 → 8.658 (9.6 % lower).
  - The interval [1.97 %, 16.2 %] is exactly a t-interval with 1 degree of freedom over the two case means (mean 9.09, half-width 12.71 × 0.56). I recomputed it. So the "at least 2.0 %" is honest, and wide for the right reason.
- **The bakeoff.** The same 2 held-back cases, paired against `main`. For nearest-first the mean is 3.1 %, the 95 % interval is −12.6 % to +18.8 %, and the chance of a gain is 0.88. For restart-sooner the result was identical in every run.
- **Spend.** $0.89 of $1.00 AI, 335 s wall time for the study, and $0 for the bakeoff.

## Did anything claim "better" before the final check?

No.
- **Terminal while running:** "2.7 % lower … so far, not checked yet".
- **App while running:** "Best so far … Not checked yet", "(not checked)" on the plot axis, and "best so far, not checked" in the Code list.
- **The agent:** it never reported a number before the verdict.
- "Probably better" appeared only after the final check, on the held-back cases.
- The one overstatement is after the check: "a chance of about 20 in 20".

## What would raise the score

1. Say the chance honestly near certainty: "about 49 in 50" or "98 in 100", or cap the words at "19 in 20" for 2-case checks.
2. Make $1 go further, or say up front that "about 21 combinations" may stop at about 16 when the AI budget runs out.
3. When the remote is not on GitHub, say in the app and the CLI exactly how to open the PR, and offer `pr.md` from the app.
4. Let a bakeoff include the AI's best change beside the hand branches (one table, one verdict). Have the PR body mention the branches that were compared and kept.
5. Use the same number of runs per held-back case in the final check and the bakeoff, or say why they differ.
6. Don't leave compile-only run folders behind, and fix the stale render when navigating between Compare and Study.
