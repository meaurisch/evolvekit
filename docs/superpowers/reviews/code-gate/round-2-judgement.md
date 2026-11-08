# Gate p3, round 2: an engineer judges evolvekit through a coding agent

Score: 7/10. Blocker: none. Nothing stopped me, and I would trust every figure I was shown. But I did not get what I came for: no answer that says "use it", and so no PR to review. The cause is honest, not a fault: two held-back cases cannot clear the rule, and 10 minutes paid for only 2 rounds of AI changes. That should have been said before the money was spent, not after.

## Ask 1: "Use evolvekit to make the demo-tour solver in solver-repo find shorter tours in the same time, by changing its code; spend at most 10 minutes and 1 dollar, and hold back two cases for the final check."

**What the agent did and said.** It asked no questions, made the study `shorter-demo-tours` and started it within about a minute. The study:
- uses the `faster` template;
- changes the code in C:\gate\p3\solver-repo, starting from HEAD 0629c1f;
- uses my sentence as its brief;
- gives 2 s per case, with 0.167 h in all and AI capped at $1;
- uses all 7 cases from `data/` and `fresh/`, with b-60 and e-150 held back (evolvekit picked them).

It gave me the app address and the plan: "about 132 combinations in 11 rounds, ends around 05:48".

When I asked how it went, it gave the verdict word for word:
> Not proven on 2 held-back cases: a chance of about 18 in 20 of a gain; at worst 11.5 % worse (somewhere between 11.5 % worse and 20.4 % better). Keep today's code for now: the gain is not proven.

It added "Ran 2 of the 11 planned rounds … the AI's replies took longer than the plan assumed", plus a one-line reading of its own. It also wrote a report to C:\gate\p3\evolvekit-report.md.

**What I saw in the app.**
- **During the search:**
  - Round 1 sat at "Searching · round 1 of 11 · Nothing better yet / Getting ready: preparing the runs." for about 3.5 minutes. Meanwhile the AI wrote 12 changes and spent about $0.53 (I read this in usage.jsonl).
  - Then: "Best so far: 3.9 % lower tour length on the cases it learned from [Not checked yet]".
  - Later the final check, with "No answer yet · Adding runs on 2 held-back cases", and the chip "Combination 17 · 7.4 % lower on the cases it learned from, not checked".
- **After:**
  - The KEEP strip carried the verdict's line and advice word for word, with a range band and the rule.
  - "Code to keep: Nothing to change… Below it is the change that came closest: not proven… so not recommended."
  - *Show the change* opened a clean, readable unified diff (+10 −4 in solver.py). The diff makes `--init nearest` the default, evaluates 2-opt moves by their change in length, and accepts moves slightly worse than the current tour.
  - Details holds the held-back table: b-60 3.2 % lower and e-150 5.7 % lower, 12 paired runs each. It also holds the learned-from table, where every case had one run, and "Ran 2 of the 11 planned rounds".
- `study verdict` and `study export --diff` (which gives best.patch) agree with the page exactly.

**Friction, worst first.**
- The plan promised about 132 combinations in 11 rounds, and only 25 in 2 rounds ran. The AI's time per round was not in the estimate. This is disclosed honestly, but only after the money and time were spent.
- Nobody warned me at the start that 2 held-back cases can practically never meet "worst within 1 %". The README itself says this is likely with two. The agent should have said so before starting, or offered to hold back more.
- The AI spend ($0.87 of my $1, summed from usage.jsonl) is not shown anywhere: not by the agent, not on the Study page or in its Details, not in the report.
- The agent pooled `fresh/` (f-70, g-130) into the training cases and let evolvekit hold back b-60 and e-150. `fresh/` was the obvious hold-out set. It did say what it did.
- During the 3.5-minute AI phase, "Getting ready: preparing the runs" does not say that the AI is writing changes, or how many have been written.
- The Tries/Code tab counts stayed at "0" while dots and a best-so-far were already on the plot.
- After the verdict, the chip still reads "7.4 % lower on the cases it learned from, not checked". Details ends with a stale "So far … The results say whether it held".

## Ask 2: "Now compare my branches nearest-first and restart-sooner against main the same way."

**What the agent did and said.** It ran the bake-off on the same two held-back cases, with 12 runs each, and it took 57 s. It reported the run's line word for word:
- "None of the 2 branches is shown to beat the starting point."
- nearest-first: "Not proven on 2 held-back cases: a chance of about 19 in 20 of a gain; at worst 2.6 % worse (… 12.1 % better)".
- restart-sooner: "No difference in any run: every run of this change gave exactly today's result…".

Its own reading of restart-sooner was the real cause, and a good catch. With accept-worse at 0, the current tour is always the best tour, so a restart to the best changes nothing. It wrote C:\gate\p3\evolvekit-bakeoff-report.md.

**What I saw in the app.**
- The Study page followed the new run (Past runs (2)), with the run's line and each branch's line, and *See the branches side by side*.
- The Compare page shows these columns, each with its commit:
  - Today's code;
  - nearest-first (46a98f6), with a band;
  - restart-sooner (61782a1), with no band.
- The rule is stated per branch.
- Layout was fine. The pane is narrower than the 1024 px page, but the page does not overflow.

**Friction, worst first.**
- The app's hint for restart-sooner, "a fixed setting can override changed code", points the wrong way here: the cause is algorithmic. Only the agent's own reading got it right.
- Neither the bake-off report nor the Compare page gives each branch's per-case figures (b-60 and e-150), only the summary line and band.
- No suggestion that nearest-first and the AI's best change share the same `--init nearest` edit. The two are worth about the same, about 4.5 % mean.

## Ask 3: "If the verdict says to use a change, open a pull request for it into origin — yes, you may push."

**What the agent did and said.** Correctly, it did nothing: "I didn't open a pull request, because the verdict doesn't say to use either change… Nothing was pushed." It proposed the next step: add more cases and hold more back, then open the PR if the verdict says use.

I checked: no new branch locally or in remote.git, a clean working tree, and no leftover worktrees.

**What I saw in the app.** Nothing new. The Keep page has no PR action, which is right.

**Friction, worst first.**
- I could not judge the PR path at all, because the earlier asks never produced a "use".
- I tried to see what a PR would contain with a dry-run of `study pr`. My environment blocked the command, so I don't know what it would show.

## Where the numbers came from

- **Search figures** (3.9 %, then 7.4 % lower): one run per case on the 5 learned-from cases, labelled "on the cases it learned from, not checked" everywhere.
- **Verdicts**: the final check. Each candidate was paired against HEAD on the 2 held-back cases, 12 runs per case, giving the mean, the 95 % interval, the chance and the worst case.
- **Bake-off**: the same pairing, with each branch's `git diff main...branch` applied to HEAD.
- **App, CLI, agent and reports** all quote the same `study verdict` line word for word.

## Did anything claim "better" before the final check?

No.
- The search's best was always "lower … on the cases it learned from" with "Not checked yet" or "not checked".
- During the final check the strip said "No answer yet".
- The agent quoted only verdicts.

The only "better" before the check is the Tries axis label "% BETTER THAN TODAY", which is a label, not a claim.

## What would raise the score

- Size the plan by the AI's real speed. Or warn before starting: "10 minutes buys about 2 rounds of AI changes".
- Warn before starting that 2 held-back cases cannot reach "use". Offer to hold back `fresh/`, or more cases.
- Show the AI spend against the cap: live on the Study page, in Details, in the report, and in the agent's summary.
- Show the AI-writing phase as such ("writing changes: 7 of 12").
- Fix the stale tab counts and the "not checked" chip after the verdict.
- Give branch comparisons per-case figures, and a hint that does not blame fixed settings when the code path is simply a no-op.
