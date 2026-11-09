
## You

Use evolvekit to make the demo-tour solver in solver-repo find shorter tours in the same time, by changing its code; spend at most 10 minutes and 1 dollar, and hold back two cases for the final check.

## Agent

I've checked the setup: evolvekit is ready, and the `demo-tour` harness fits your solver. Its `faster` template changes the solver's code and has tour length as the measure, where lower is the goal. From your request I have:

- **Application:** `solver-repo/solver.py`, starting from the commit checked out now (`ead9b18`).
- **What changes:** the code in `solver.py`, with your brief "find shorter tours in the same time".
- **Time:** 10 minutes in total (`0.167` hours), and the harness's default of 2 seconds per case.
- **Money for the AI help:** $1.
- **Held-back cases:** two.

I've made nothing yet because one thing is missing: **which cases should the study use?** `solver-repo` has two sets:

- `data/`: `a-40`, `b-60`, `c-80`, `d-110` and `e-150`
- `fresh/`: `f-70` and `g-130`

Should it use all seven and hold back two of them, or only the five in `data/` with two of those held back? Or are the cases somewhere else?

## You

Use all seven, and hold back the two in fresh/ for the final check.

## Agent

The study is ready and nothing has started yet. I set it up as you asked:

- **Cases:** all seven. The five in `data/` are the ones it learns from, and `f-70` and `g-130` from `fresh/` are held back for the final check.
- **Measure:** tour length, where lower is the goal.
- **Limits:** 2 seconds per case (the harness's default), 10 minutes in total, and at most $1 for the AI help.
- **Starting point:** the code at commit `ead9b18` in `solver-repo`.

evolvekit first picked `b-60` and `e-150` to hold back, so I switched them in `study.yaml` to the two in `fresh/`. The check passed after that change. A trial solve of `a-40` with today's code ran without errors in 2.3 seconds.

The plan:

> compiled into C:\gate\p3\home\studies\shorter-tours-by-changing-the-solver\runs\20261108-080807: about 3 rounds, about 21 combinations tried (assumes about 20 s for each AI change), a final check on 2 held-back cases with 3 runs each (≈ 9 s); done around 08:18. With 2 held-back cases a 'use' is unlikely: the worst case stays wide. Hold back more cases for a firmer answer.

**Two held-back cases:** as that note says, a final check on only two cases will rarely give a firm "use". I can hold back more of the seven if you want a firmer answer, but that leaves fewer cases to learn from.

**What the AI help can do:** the coding agent runs shell commands in a separate git working copy (a worktree) of `solver-repo`. From there git can reach your branches and remotes. The commands that would change them are blocked, but that block isn't watertight. The repository's own agent settings and hooks also apply. Each AI call is limited to 1800 seconds and $0.50.

**Shall I start it with this plan, or hold back more cases first?**

## You

Start it with this plan.

## Agent

I compiled the plan again just before starting, and it hadn't changed: about 3 rounds and about 21 combinations, with the final check on `f-70` and `g-130`, done around 08:18.

Your study is running: http://127.0.0.1:8780/?token=REDACTED#/study/shorter-tours-by-changing-the-solver. It tries about 21 combinations in 3 rounds and ends around 08:18; the answer is on the Study page.

Ask me when it has finished and I'll give you the final check's verdict word for word.

## You

How did it go?

## Agent

The study has finished. The final check's verdict, word for word:

> Probably better: 9.1 % lower tour length on 2 held-back cases, a chance of about 20 in 20; at least 2.0 % better than your starting point (the code at HEAD).
> Use the new code change: it is probably better on cases the search never saw.

How it ran: "The AI budget was used up in round 3 (16 combinations, about 21 planned)." AI: $0.89 of $1.00.

The full report is in `C:\gate\p3\evolvekit-report.md`.

My own reading: the verdict rests on only the two held-back cases in `fresh/`.

I haven't changed `solver-repo` or made a branch or pull request. If you want the change, I can first show what a pull request would do and make it only after your yes.

## You

Now compare my branches nearest-first and restart-sooner against main the same way.

## Agent

I've started a comparison of your two branches, `nearest-first` and `restart-sooner` in that order, against `main`. It runs on the same held-back cases, `f-70` and `g-130`. It doesn't search for changes or use the AI help, so nothing is being spent.

You can follow it here: http://127.0.0.1:8780/?token=REDACTED#/study/shorter-tours-by-changing-the-solver. The answer will be on the Study page.

Ask me when it's done and I'll give you each branch's verdict word for word.

## You

How did the comparison go?

## Agent

The comparison has finished. Its verdict, word for word:

> None of the 2 branches is shown to beat the starting point.
> Keep today's code for now: the gain is not proven.

Each branch, word for word:

- **nearest-first:** Not proven on 2 held-back cases: a chance of under 9 in 10 of a gain; at worst 12.6 % worse (somewhere between 12.6 % worse and 18.8 % better).
- **restart-sooner:** No difference in any run: every run of this change gave exactly today's result, so the study may not exercise it: a fixed setting, or a code path these cases never reach, can leave a change without effect.

It ran against `main` on the two held-back cases in `fresh/`, with no AI help. The full report is in `C:\gate\p3\evolvekit-bakeoff-report.md`. The report from the first study is still in `evolvekit-report.md`.

My own reading: `restart-sooner` may change something that these two cases never reach in 2 seconds.

## You

If the verdict says to use a change, open a pull request for it into origin — yes, you may push.

## Agent

I made the branch and pushed it to `origin`, but no pull request exists yet. `origin` is `C:/gate/p3/remote.git`, which isn't on GitHub, so evolvekit couldn't open one.

- **Branch:** `evolvekit/shorter-tours-by-changing-the-solver-20261108-080830`, at commit `3c8413824130`. It adds the change from the first study (+35 −12 lines in `solver.py`) on top of `main` at `ead9b18`.
- **Pull request text:** written to `C:\gate\p3\home\studies\shorter-tours-by-changing-the-solver\runs\20261108-080830\pr.md`, for whatever place you open the pull request from.

The branch's change comes from the first study, whose verdict, word for word:

> Probably better: 9.1 % lower tour length on 2 held-back cases, a chance of about 20 in 20; at least 2.0 % better than your starting point (the code at HEAD).
> Use the new code change: it is probably better on cases the search never saw.

evolvekit added this note: "The final check had only 2 held-back cases: its worst case stays wide, so this answer is thin. Hold back more cases for a firmer answer."

Your own branches are unchanged. The comparison said to keep today's code, so I made nothing from `nearest-first` or `restart-sooner`.
