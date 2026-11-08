
## You

Use evolvekit to make the demo-tour solver in solver-repo find shorter tours in the same time, by changing its code; spend at most 10 minutes and 1 dollar, and hold back two cases for the final check.

## Agent

Your study is running: http://127.0.0.1:8780/?token=REDACTED#/study/shorter-demo-tours. It tries about 21 combinations in 3 rounds and ends around 07:05; the answer is on the Study page.

This is the plan as evolvekit compiled it:

> about 3 rounds, about 21 combinations tried (assumes about 20 s for each AI change), a final check on 2 held-back cases with 3 runs each (≈ 9 s); done around 07:05. With 2 held-back cases a 'use' is unlikely: the worst case stays wide. Hold back more cases for a firmer answer.

**How it's set up:**
- **Measure:** tour length, and lower is the goal. The study also reports the longest leg.
- **Time per case:** 2 s, the harness's default, since you didn't name one.
- **Budget:** 10 minutes in total, and AI help may spend at most $1.00.
- **What changes:** the AI may only edit `solver.py`, starting from the commit checked out in `solver-repo` (`d9cdf00`).
- **Cases:** I used all seven cases in `solver-repo` (`data/` and `fresh/`). evolvekit chose two to hold back for the final check: `b-60` and `e-150`.

**Before it gets far:**
- **More held-back cases:** evolvekit warns that two is few for a firm answer, as quoted above. I can hold back more if you want; that means stopping and recompiling.
- **What the AI agent can do:** it runs shell commands in a git worktree of `solver-repo`. From there git can reach your branches and remotes. The commands that write to them are denied, but that block is not airtight. The repository's own agent settings and hooks also apply. Each AI call is limited to 1800 s and $0.50.

Nothing has been pushed and no pull request has been opened. If you want the verdict reported when it finishes, ask me and I'll wait for it.

## You

Now compare my branches nearest-first and restart-sooner against main the same way.

## Agent

I've started the bake-off. It compares 2 branches, nearest-first and restart-sooner, against main (d9cdf00), each on its own. It uses the same 2 held-back cases, `b-60` and `e-150`, and does no search and no AI. Follow it here: http://127.0.0.1:8780/?token=REDACTED#/study/shorter-demo-tours. When it's done, the Study page shows how each branch did against main.

Your first study, the code search, has finished. Its verdict:

> Probably better: 8.0 % lower tour length on 2 held-back cases, a chance of about 19 in 20; at worst 0.6 % worse than your starting point (the code at HEAD).
> Use the new code change: it is probably better on cases the search never saw.

AI: $1.03 of $1.00. That is $0.03 more than the $1 you set.

Nothing has been pushed and no pull request has been opened. If you want either branch's result reported when it finishes, ask me and I'll wait for it.

## You

If the verdict says to use a change, open a pull request for it into origin — yes, you may push.

## Agent

I pushed the code search's change to origin as a branch, but no pull request was opened, because origin isn't on GitHub. evolvekit says: "the remote is not on GitHub: open the PR there from evolvekit/shorter-demo-tours-20261108-065534; the body is at C:\gate\p3\home\studies\shorter-demo-tours\runs\20261108-065534\pr.md".

- **Pushed:** the branch `evolvekit/shorter-demo-tours-20261108-065534` (commit `3beea9d`) is now on `C:/gate/p3/remote.git`. It's main (`d9cdf00`) plus a +151 −124 line change to `solver.py`. The commit uses your own git identity and hooks.
- **Why this change:** its verdict is "Probably better: 8.0 % lower tour length on 2 held-back cases, a chance of about 19 in 20; at worst 0.6 % worse than your starting point (the code at HEAD)." The advice: "Use the new code change: it is probably better on cases the search never saw."
- **Your branches:** the bake-off doesn't recommend either, so nothing was pushed for them. Its verdict:
  > None of the 2 branches is shown to beat the starting point.
  >   nearest-first: Not proven on 2 held-back cases: a chance of about 19 in 20 of a gain; at worst 2.6 % worse (somewhere between 2.6 % worse and 12.1 % better).
  >   restart-sooner: No difference in any run: every run of this change gave exactly today's result, so the study may not exercise it: a fixed setting, or a code path these cases never reach, can leave a change without effect.
  >
  > Keep today's code for now: the gain is not proven.

To finish, open the pull request wherever origin is hosted, from that branch, and use `pr.md` as its description.
