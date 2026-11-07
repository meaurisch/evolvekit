# Agent-path persona gate log

The gate of the agent-path design, §8: for each project, a fresh reviewer
agent that never sees the code, the design or earlier reviews, given only a
persona, materials and a note; it drives the real thing and scores it out of
10 with any blocker named; the loop repeats until 8 or more with no blocker.
The protocol is the intern review log's
([2026-09-26-intern-review-log.md](2026-09-26-intern-review-log.md)).

## Project 1 — One world

**Persona:** an engineer who reads the README only.
**Does:** from a terminal alone, makes, starts, watches and reads a demo-tour
study (the solver is `examples/cli-solver/solver.py`), then finds it in the
app through `study open` and the in-app browser.
**Materials:** the checkout on the project's branch, a clean library home
per round, the project's venv.
**The note:** the task above; score 1–10 overall and six sub-scores (getting
started, knowing what to do next, setting up, confidence while it ran,
understanding the result, finding it in the app); the worst frictions with
severity; every command and what it printed.

### Round 1 — 8/10, no blocker: the gate passes (2026-11-07)

| Getting started | Knowing what to do next | Setting up | Confidence while it ran | Understanding the result | Finding it in the app |
|---|---|---|---|---|---|
| 9 | 8 | 8 | 6 | 7 | 9 |

**The run.** A library home whose path has a space (no trouble anywhere).
`doctor` first, every line ✓. `study new "Depot A" --harness demo-tour
--template tune --app examples/cli-solver/solver.py`; the four demo-tour
samples through a quoted glob with one held back; the plan set to 2 rounds ×
2 combinations and 0.05 h by editing `study.yaml`; `study check` ready;
`study start`, `study watch`, `study wait`; 31 s. The verdict: "On the one
held-back case: 4.8 % lower tour length. Not sure: one held-back case is too
few to tell." The reviewer's answer: not proven; the app, opened through
`study open`, showed the same study, run and verdict and "Nothing to change:
keep today's settings".

**Frictions, and what changed.**

- **(major) The state line said `running · 3 min left` throughout** and
  never a round number, though the README promises `running · round 2 of
  6`; the run took 31 s. Now: the CLI passes the status document, so the
  line carries the round; a manual plan's time comes from its rounds.
- **(minor) One improvement, three numbers** (4.8 % in the verdict, 5.0 %
  in the report's table, 4.78 % in the app). Now: the table uses the
  verdict's own per-case statistic and its columns say what they are.
- **(minor) The CLI's advice was weaker than the app's** ("A study that
  holds back three or more can." against "Nothing to change: keep today's
  settings"). Now: every advice for a decision other than `use` starts with
  "Keep today's settings", on the page too (one document); the report's
  "What changed" says "found, not recommended" or "recommended".
- **(minor) "Combinations tried: 5" for a 2 × 2 plan.** Now: "4, plus
  today's settings".
- **(minor) No next step after a command succeeds.** Now: a `next: …` hint
  on stderr after `study new`, `cases`, `check`, `start` and a finished
  `wait`; stdout and `--json` unchanged.
- **(minor) The README's `study.yaml` snippet had no `plan:`.** Now it does,
  with what `children` and `generations` mean.
- **Not changed.** The app's tries chart counts search time while the
  report counts the whole run; "Continue for 3 min more" is the budget.

### Round 2 — 8/10, no blocker (2026-11-07)

| Getting started | Knowing what to do next | Setting up | Confidence while it ran | Understanding the result | Finding it in the app |
|---|---|---|---|---|---|
| 9 | 9 | 8 | 6 | 8 | 8 |

**The run.** A fresh engineer, a fresh home with a space in its path; ten
commands and 32 s of running. The verdict: "On the one held-back case: 4.8 %
lower tour length. Keep today's settings (Demo tour's own defaults): one
held-back case is too few to tell; a study that holds back three or more
can." The reviewer's answer: not proven, keep today's settings — the tool's
own words. The app showed the same study, run, settings and "Nothing to
change: keep today's settings".

**Frictions, and what changed.**

- **(major) The app's headline said "4.78 % better on one case"** while
  the terminal never says "better" and gives 4.8 %. The design (§3.4) wants
  the page's headline and advice to be the document's `line` and `advice`;
  the results document carried them but the page composed its own words.
  Now: the Study page's headline is the verdict's `line`, the sentence
  under it the `advice`, verbatim; the held-back table and the chosen
  combination's chip use the document's figures, rounded the document's
  way, with the measure's direction word.
- **(minor) 7.43 % against 7.5 % for the gain on the study cases.** One
  figure now, "on the cases it learned from, not checked".
- **(minor) The state line right after `start` said only `running`.** Now
  `running · starting round 1 of 2`.
- **(minor) The `next:` hint came before the command's result.** Now the
  result first, the hint last; every alternative in the hint carries its
  `evolvekit` prefix.
- **(minor) The plan had to be edited by hand and nothing echoed it.**
  `study check` now prints the plan and the budget it will run.

### Round 3 — 9/10, no blocker (2026-11-07)

| Getting started | Knowing what to do next | Setting up | Confidence while it ran | Understanding the result | Finding it in the app |
|---|---|---|---|---|---|
| 9 | 10 | 9 | 7 | 9 | 8 |

**The run.** A third engineer, a fresh home; the `next:` hints carried them
from `doctor` to `study open` without opening the README again after the
first read; `study check` echoed `plan: 2 rounds × 2 combinations, budget
0.05 h`. The verdict and the study page agreed word for word.

**Frictions, and what changed.**

- **(major) The Home card used its own words** ("NO ANSWER · 1 HELD-BACK
  CASE", "4.78 % better on tour length (lower) on one case") while the
  study page and the CLI said "4.8 % lower tour length", "Keep today's
  settings", `finished · not proven on the held-back cases`. Now: the
  finished card renders the verdict's own line and advice from the same
  document, and its label is the state line's decision word.
- **(minor) `study wait` printed nothing while it waited.** Now it prints
  the state line on stderr whenever it changes; stdout is still the verdict
  at the end.
- **(minor) The single-case verdict led with the gain** and only its second
  line said to keep today's settings. Now the line leads with its decision
  like the others ("Not proven, one held-back case: …"), and the advice says
  "too few cases to tell".
- **(minor) The report's file paths did not say what they were relative
  to.** Now the Files section names the study folder.
- **Not changed.** Flags for the plan on `study new` (a later project);
  the app's address-bar routing and the mascot pop-up (the redesign's).

The gate passed at round 1 (8/10, no blocker) and held at rounds 2 and 3;
the loop stopped after round 3's fixes, which change only words the
engineer reads, not the path they walk.
