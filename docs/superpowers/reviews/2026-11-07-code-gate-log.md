# Code-study persona gate (agent path, project 3)

The gate of design §8 for project 3: an engineer with a slow route solver asks a coding agent, one sentence each,
to improve the solver's code with evolvekit, to compare two prepared branches against `main`, and to open a pull
request if a verdict says to use a change. Pass: 8/10 or more with no blocker.

Materials (made fresh by the controller, outside the checkout): `C:\gate\p3\solver-repo` (a git copy of
`examples/cli-solver` on `main`, branches `nearest-first` -- the starting tour defaults to `nearest` -- and
`restart-sooner` -- `restart_after // 4`), `C:\gate\p3\remote.git` (a bare clone of `main`, the repository's
`origin`), a library home `C:\gate\p3\home` (demo-tour built in, provider `claude-cli`, models
`claude-sonnet-5-5`), evolvekit's skills installed in `C:\gate\p3`. The reviewer is a fresh agent told only the
persona, the materials and the note; it reads only README.md and docs/app.md, and talks to a fresh, isolated
Claude Code agent in `C:\gate\p3` (project settings only, no MCP, nothing that prompts; `ANTHROPIC_MODEL` set).
Transcripts and judgements are under [code-gate/](code-gate/), tokens redacted.

## Round 1 -- 5/10, blocker

[Judgement](code-gate/round-1-judgement.md), [transcript](code-gate/round-1-transcript.md).

- **Blocker:** the bake-off measured `nearest-first` with its change switched off. The study passed the harness's
  default settings as flags (`--init random`), which overrode the branch's new default; all 24 paired runs equal
  `main`'s to the digit, yet the verdict read as an ordinary "not proven" ("about 10 in 20 ... between -0.0 % worse
  and 0.0 % better"). Run with its own defaults, the branch's tours were 2-6 % shorter.
- The plan said 11 rounds and about 132 combinations; 2 rounds and 25 ran (the AI's replies were slow) and nothing
  said so afterwards.
- During the search the app said "Best so far: 8.99 % better" while `study status` said "0.4 % lower"; the app's
  figure fell between reads.
- The Study page and the past-runs drawer stayed stale after the bake-off until the engineer went Home and back.
- A code study's pages use settings words ("Settings 0", "TODAY'S SETTINGS"); the verdict drawer is cut off in a
  narrower window.
- The coding agent read evolvekit's source and reported that `study compile` fails on AI studies.
- Held up: both verdicts KEEP and nothing said "use it" before the final check; no PR, nothing pushed, `origin`
  unchanged, no worktree or `refs/evolvekit` left in the repository; $0.935 of $1 spent; every figure traced to the
  run's files.

Fixes (rulings in the SDD ledger): in a code study only the settings the study fixes reach the program, so each
candidate runs with its own code's defaults; a candidate whose every paired run equals the base gets "No difference
in any run ..." and no chance or range; a run that ran fewer rounds than planned says so; one "best so far" figure
for the app and `study status`; the Study page follows new runs; code words on a code study; the drawer fits;
`study compile` on AI studies checked.

## Round 2 -- 7/10, no blocker

[Judgement](code-gate/round-2-judgement.md), [transcript](code-gate/round-2-transcript.md). Materials rebuilt from
nothing; the code at 808e305.

- The code study ran 2 of its 11 planned rounds and kept today's code (about 18 in 20 of a gain, at worst 11.5 %
  worse); the bake-off said `nearest-first` is not proven (about 19 in 20, at worst 2.6 % worse) and
  `restart-sooner` gave exactly today's result in every run; no PR was opened, nothing pushed. Every figure traced;
  nothing claimed "better" before the final check.
- The plan still ignores the AI's time: the shortfall was told only after time and money were spent.
- No warning before the run that two held-back cases can almost never reach "use it".
- The AI spend ($0.87 of $1) was shown nowhere.
- The no-difference hint blamed a fixed setting; the cause was algorithmic (no worse moves accepted, so a restart
  changes nothing).
- Smaller: "Getting ready" while the AI writes changes; stale tab counts and a "not checked" chip after the
  verdict; no per-case figures for branches.

Fixes: the plan counts an assumed time per AI change and re-estimates after the first round; a note before the run
when the held-back cases are too few for a firm answer; the AI spend in the verdict document, report, status and
app; a neutral no-difference hint; the app follows the AI-writing phase, the counts and the finished state; per-case
figures for each branch.

## Round 3 -- 7/10, no blocker

[Judgement](code-gate/round-3-judgement.md), [transcript](code-gate/round-3-transcript.md). Materials rebuilt; the
code at f6c8d5b.

- The search said **use** (8.0 % lower on 2 held-back cases, about 19 in 20, at worst 0.6 % worse); the bake-off
  kept today's code (`nearest-first` not proven; `restart-sooner` no difference, which the engineer confirmed is
  right: with no worse moves accepted, a restart changes nothing). `study pr --yes` pushed
  `evolvekit/shorter-demo-tours-20261108-065534`, a clean +35 -8 commit in the engineer's own name; no PR, since
  `origin` is not on GitHub, and the command said so. The plan's thin-check note and the AI spend were shown live.
- Checked afterwards: `solver-repo` clean on `main`, `git worktree list` the main checkout only, no
  `refs/evolvekit`, `remote.git` holds `main` and the pushed branch only.
- Worst: the recorded change read "+151 -124 lines" everywhere evolvekit showed it (a whole-file line-ending
  rewrite), against the real +35 -8; the review surface could not be trusted.
- The AI spend went over its cap: $1.03 of $1.00.
- The thin `use` was not said again before pushing; after the bake-off, Home and `study status` showed only its
  "keep", hiding the earlier "use"; the Tries axis said "better" over unchecked points; the PR body ended with an
  empty "Files" section.

Fixes: the recorded change follows the repository's own line-ending rules (operators keep a file's endings); the cap
holds (every call gets the budget left as its limit, and no call starts when less is left than the largest call so
far); the thin note again at `study pr` and in its body; an earlier `use` shown beside a later run; the pushed branch
shown; a neutral axis label; no empty sections in the PR body.

## Round 4 -- 8/10, no blocker: PASS

[Judgement](code-gate/round-4-judgement.md), [transcript](code-gate/round-4-transcript.md). Materials rebuilt; the
code at 52d7649.

- The agent asked which cases to hold back; the engineer chose `fresh/`. The plan showed about 3 rounds with the
  AI's time counted, and the thin-check note. The search said **use** (9.1 % lower tour length on the 2 held-back
  cases, at least 2.0 % better); the run stopped at 6 of its 10 minutes when the AI budget was used up ($0.89 of
  $1.00, shown live and in the report). The change it recorded is the change `study pr` pushed (+35 -12), a real
  algorithmic change the engineer could review in the app.
- The bake-off kept today's code: `nearest-first` not proven, `restart-sooner` "no difference in any run"; the
  Study page showed the earlier run's `use` beside it. `study pr --yes` pushed
  `evolvekit/shorter-tours-by-changing-the-solver-20261108-080830` in the engineer's name, repeated the thin note,
  and said no PR was opened because `origin` is not on GitHub; the run's page names the pushed branch.
- Nothing claimed "better" before the final check. Checked afterwards: `solver-repo` clean on `main`, the main
  checkout the only worktree, no `refs/evolvekit`, `remote.git` holds `main` and the pushed branch only.
- Remaining frictions: "a chance of about 20 in 20" sounds certain on two cases; two compile-only run folders were
  left in the study; the Compare page stayed drawn after going back to the Study page by its link; no PR text offered
  from the app for a non-GitHub remote; the bake-off does not include the search's change.
- The harness flagged the reviewer for a possible permission bypass. Asked, it listed every command it ran: a shell
  heredoc failed to parse and it wrote its judgement with the file tool instead; everything else only read. The
  repositories' state matched.

Fixed after the pass, before the gate: chance wording past 19 in 20 says "better than 19 in 20"; compiling leaves no
run folder; the Compare-to-Study redraw.
