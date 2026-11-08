# Gate p3, round 1: the engineer's judgement

**Score: 5/10. Blocker: the branch comparison measured `nearest-first` with its change switched off and gave it an ordinary "not proven" verdict.** The study passes `--init random` to the solver explicitly, which cancels the branch's only change (`--init` default `random` → `nearest`). Every one of the 24 paired runs came out identical to `main`, down to the last digit. evolvekit still reported "a chance of about 10 in 20 of a gain … between -0.0 % worse and 0.0 % better" and gave no warning that the branch changed nothing in what was run. I ran the branch's own `solver.py` with its own defaults on the same held-back cases and seeds (4 seeds each). Its tours were about 2–4 % shorter on b-60 and about 5–6 % shorter on e-150. If I had trusted the comparison, I would have thrown away a good change.

(Token in the app URL redacted as `token=REDACTED` throughout.)

## Ask 1: the code study

**Asked:** "Use evolvekit to make the demo-tour solver in solver-repo find shorter tours in the same time, by changing its code; spend at most 10 minutes and 1 dollar, and hold back two cases for the final check."

**What the agent did and said:** It asked no questions. It made the study `shorter-demo-tours` (template `faster`, `from: HEAD` = e973d92, `budget.hours 0.167`, `ai.max_usd 1.0`, 2 s per case) and added all 7 cases (`data/*` and `fresh/*`). evolvekit held back `b-60` and `e-150`. The agent said so and offered to hold back `fresh/` instead. It started the run, gave me the app address (`http://127.0.0.1:8780/?token=REDACTED#/study/shorter-demo-tours`) and quoted the plan: "About 11 rounds, about 132 combinations tried … done around 04:13". It warned me that the coding agent gets a worktree of my repo and that the git block is not airtight. It also reported an evolvekit bug it had found by reading evolvekit's source (`study compile` fails on AI studies, citing `cli.py:1509-1510`). It did not wait for the result: "ask me and I'll report the final check's verdict."

**What actually ran:** 2 rounds and 25 combinations, not the 11 rounds and 132 combinations in the plan. The run stopped on time: "another round would not finish… a round has taken up to 0.07 h". It spent $0.935 of $1 on 24 AI calls. The final check ran 2 held-back cases × 12 runs each. The verdict was KEEP: "Not proven on 2 held-back cases: a chance of under 9 in 10 of a gain; at worst 4.4 % worse (somewhere between 4.4 % worse and 6.8 % better)." Both held-back cases did improve (b-60 +0.8 %, e-150 +1.7 %). With only two cases the range is wide, and the verdict says so honestly.

**In the app:** The status strip showed "SEARCHING · ROUND n OF 11 · Best so far: X % better · Not checked yet". The terminal and the app disagreed during the run: at the same moment `study status` said "0.4 % lower tour length so far" while the app said "Best so far: 8.99 % better", and seconds later "7.97 % better". The final best was 6.1 % (learned-from). The verdict strip, its range band, the rule ("use it at 9 in 10 or better, worst within 1 %"), and "Code to keep: Nothing to change… keep today's code" were all clear. *Details* on combination 15 showed the diff, `+18 −9 lines in solver.py`, with red and green lines, which is reviewable. A code study still shows "Settings 0", "TODAY'S SETTINGS" and a "SETTING / TODAY / NEW" table.

**Friction (worst first):**
- The plan promised 11 rounds and 132 combinations. 2 rounds and 25 ran, because the plan does not count the AI's own time. Nobody told me afterwards.
- The app and the terminal gave different "best so far" numbers at the same time (8.99 % / 7.97 % vs 0.4 %), and the app's number jumped down between reads.
- The agent went into evolvekit's source and filed a bug report at me mid-answer. That is noise for me and a sign the CLI path was not smooth (`study compile` failed).
- The agent did not wait for the verdict. I had to come back and ask; it reported the verdict only inside its answer to ask 2.
- A code study is labelled with "settings" words ("Settings 0", "TODAY'S SETTINGS", "SETTING" column).
- The verdict drawer and strip are cut off at the right in a narrower window.

## Ask 2: comparing my branches

**Asked:** "Now compare my branches nearest-first and restart-sooner against main the same way."

**What the agent did and said:** It ran `study bakeoff` on the same study with the same two held-back cases and 12 runs each. That took 58 s and no AI. The verdict, word for word: "None of the 2 branches is shown to beat the starting point. Keep today's code for now: the gain is not proven." Each branch got "a chance of about 10 in 20 of a gain; at worst 0.0 % worse (somewhere between -0.0 % worse and 0.0 % better)."

**What I found:** The comparison file shows every candidate run identical to the baseline (b-60 6.57923 = 6.57923, e-150 10.3476 = 10.3476; "0 of 12 pairs won"). The cause: the study passes the base settings (`init: random`, `restart_after: 2000`) as flags, so `nearest-first`'s new default never takes effect. Run with its own defaults, nearest-first gives b-60 6.23–6.32 vs main 6.04–6.96, and e-150 9.66–9.98 vs main 9.84–10.85. That is a real gain that evolvekit hid. `restart-sooner` also came out bit-identical; with these runs its change seems to make no difference to the tour. evolvekit never said "this branch's runs are identical to the starting point: its change is not exercised".

**In the app:** The Compare branches page is good: a "Today's code" column, one column per branch with its commit, its own line and its band, and the rule. The Study page did not show the comparison at first. It kept the first run's verdict and "Past runs (1)", and the runs drawer listed the 04:03 run as "running · 10 min left". The new verdict appeared only after I went Home and came back through the Study tab. The Study page then read "Below it is the change that came closest… No changes."

**Friction (worst first):**
- **Blocker:** the branch change was cancelled by the study's fixed flags, and the result was reported as an ordinary "not proven" with no warning, so the answer about my branch is wrong.
- The awkward "-0.0 % worse and 0.0 % better" and "about 10 in 20" make the identical result read like a measured coin-flip instead of "nothing changed".
- The Study page and the past-runs drawer stayed stale after the comparison; I had to navigate Home and back.
- The agent noticed the exact 0.0 % but told me to check whether my branches differ from main. They do, and the fault is in how they were run.

## Ask 3: the pull request

**Asked:** "If the verdict says to use a change, open a pull request for it into origin — yes, you may push."

**What the agent did and said:** "I didn't open a pull request and pushed nothing, because neither verdict says to use a change." It quoted both verdicts word for word and wrote the report to `C:\gate\p3\evolvekit-report.md`. I checked: `origin` still has only `main` at e973d92, no new branch, my working tree is clean, and no worktrees or `refs/evolvekit` are left behind.

**In the app:** Nothing to open; "Code to keep: Nothing to change". The *Details* diff for the closest change was the only reviewable artifact.

**Friction (worst first):**
- The correct "no PR" outcome rests partly on the broken bakeoff. A fair measurement of nearest-first might well have said "use it".
- The report file was written to `C:\gate\p3\` unasked (harmless, but I didn't ask for it).

## Where the numbers came from

- **Tour lengths:** the solver's own last JSON line (`length`), read through the demo-tour harness (`SELECT length FROM summary`), with the solver run from a git worktree of my repo per combination at a 2 s limit.
- **Search ("learned from") figures:** the 5 training cases (a-40, f-70, c-80, d-110, g-130), one run per case.
- **Verdicts:** the final check on the 2 held-back cases (b-60, e-150) × 12 seeds (1001–1012). Each candidate is paired with the starting commit on the same case and seed, summarised as a mean, a 95 % range, a "chance of a gain", and the fixed rule (chance ≥ 9 in 10, worst within 1 %). I could trace every figure to `runs/<id>/confirm/final/comparison.md`, `events.jsonl` and `usage.jsonl`. The AI cost ($0.935) is from claude-cli's own reports.

**Did anything claim "better" before the final check?** Yes, in wording only. The app's strip said "Best so far: 8.99 % better / 7.97 % better" during the search, always next to a "Not checked yet" badge. The chip said "6.1 % lower on the cases it learned from, not checked". Nothing said *use it* before the final check, and the final check overruled the search: KEEP. Two problems remain: the in-flight "better" figure was higher than the final learned-from figure (8.99 % vs 6.1 %), and it disagreed with the terminal (0.4 %).

## What would raise the score

1. The comparison detects a branch whose runs are identical to the starting point (or whose change the study's fixed flags override) and says so plainly, instead of "not proven". This alone would lift it to about 7.
2. A plan that counts the AI's time, so "11 rounds, 132 combinations" is not promised when 2 rounds and 25 will run. Or the verdict says how much of the plan actually ran.
3. One "best so far" number shared by the app and `study status`, which does not jump down.
4. The Study page and the past-runs list refresh when a new run (bakeoff) starts and ends.
5. Code-study wording throughout the app ("code", not "settings"). Report numbers like "0.0 %" as "no difference in any run".
6. The agent waits for (or offers to wait for) the verdict, and does not dig into evolvekit's source unasked.
