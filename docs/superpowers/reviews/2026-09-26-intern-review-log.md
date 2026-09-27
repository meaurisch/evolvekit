# Intern review log — the evolvekit app

The gate of design §12: a fresh reviewer each round, one at a time, who never
sees the code, the design or earlier reviews, tunes PyVRP through the real app
with real PyVRP runs, and scores it. The loop stops at an overall score of 8
or more with no blocker; after that, one extra review (not gating) of a data
study through the assistant.

## The set-up of every round

- **The reviewer:** a fresh general-purpose agent, told only the persona, the
  materials and the note below. It drives the app with the in-app browser and
  starts it with the command from the note.
- **Persona:** an intern at a logistics company who has just learned what
  PyVRP is, what an executable is, and what PyVRP's configuration looks like.
  Not a programmer; takes instructions literally; gets stuck on jargon.
- **Materials:** a clean library home, and a handover folder with
  `pyvrp-1.0.0.zip` (`evolvekit harness pack harnesses/pyvrp`) and `cases/`,
  eight delivery requests of 78 to 192 tasks — made with the benchmark's
  generator from seeds that are not the harness's samples, named like daily
  requests (`requests-2026-03-02.json`, …).
- **The manager's note:** start evolvekit with the given command (it also
  says to open `http://localhost:8791/`); PyVRP is installed; find the PyVRP
  settings with the lowest cost on these requests, at most 20 s per request
  and 30 minutes in total; send the settings file (its contents) and say
  whether the new settings are really better than PyVRP's defaults.
- **The report:** an overall score from 1 to 10 for "How easy was it to tune a
  configuration to get the most out of the objective?"; sub-scores for getting
  started, knowing what to do next, setting up, confidence while it ran,
  understanding the result, and handing it over; the worst frictions (where,
  what happened, why it hurt, what would fix it, severity blocker / major /
  minor); what would make it a 10.

## Before the first round

Walking a PyVRP study through the app in the in-app browser myself found and
fixed: runs dying with the app (a host that kills the app's process tree);
`study.yaml` refused on Windows while being read; no way to add cases or a
harness without the browser's file picker; a stray "null" in several
screens; "tune" counted as "fixed" in the review sentences (strings have a
`.fixed()` method); Start disabled without a reason; a goal shown as "100"
(per-case normalisation) instead of its value; settings studies listed after
data studies; a 27-setting page without folding; guardrails starting at 0.

## Round 1 — 6/10, no blocker

| Getting started | Knowing what to do next | Setting up | Confidence while it ran | Understanding the result | Handing it over |
|---|---|---|---|---|---|
| 8 | 7 | 6 | 6 | 5 | 7 |

**The run.** The eight recommended settings; 3 of the 8 requests held back;
the goal changed to real cost with a "Feasible at least 1" guardrail after
asking the assistant ($0.08); 20 s per request, 30 minutes. 5 rounds, 36
combinations, 13 runs timed out. 2.0 % lower real cost on the requests the
search learned from; the final check "not distinguishable" (−10.1 % to
+11.5 %). The reviewer's answer to the manager: not proven better, keep the
defaults.

**Frictions, and what changed.**

- **(a, major) The final check.** Held-back columns empty, "−10.1 % better",
  no advice. Now: the held-back numbers from the check's own runs, the goal
  case by case (held back and learned from), the interval in words
  ("somewhere between 10.1 % worse and 11.5 % better"), a plain advice line,
  and "Check again with 6 runs per case instead of 3 (about 2 min)" — the
  check keeps the runs it did, so it only adds.
- **(b, major) "PyVRP's objective" or "Real cost"?** Every measure's meaning
  now sits under it (measures, goal, guardrails, results); the default is
  called "Cost as PyVRP counts it"; a harness KPI can name the guardrail it
  wants as a goal (`guard:`), and the app offers it in one click.
- **(c, major) "Will not learn much", and no way out; 9 "generations" against
  "about 6 rounds".** The warning now comes with fixes: tune only the N that
  matter most (recommended settings are ranked now) or allow the time it
  needs. The expert box sets rounds, as the plan counts them.
- **(d, major) Timeouts against a 20 s limit.** The cause was PyVRP 0.14's
  local search never returning for some settings (reproduced: max_penalty
  3773 and 30000 hung on requests-2026-03-03). PyVRP now runs in a process of
  its own that is stopped after the limit with its best plan
  (`summary.stopped`); both combinations come back at 22.5 s with a feasible
  plan. The running page explains a timeout against the limit.
- **(e, major) A five-minute preview without a clock.** The preview solves at
  most 10 s (it measures overhead and shows today's values), shows its time so
  far and the usual time, and redraws the step when it lands.
- **(f) Stale "Still to do".** The checklist and the rail redraw whenever the
  study changes.
- **(g) "I changed the goal".** Cards say "Proposed … nothing changes until
  you apply it", the button says Apply, and the prompt says "I propose".
- **(h) "1270,478" beside "1,270.5".** One number format; number boxes show
  1270.5 and read 1270,5 too; no "1.00e+7".
- **(i) Dashboard jargon.** What was only there — per-case wins and losses,
  failures — is on the simple pages; the dashboard is marked "for experts".
- **(j) Jargon and five names for the test set.** Plain case description and
  application help, "PyVRP 0.14.x", one name each: "held-back cases" and
  "cases the search learned from".
- **(k) The hand-over.** "Copy a summary" (set-up, result, final check,
  advice, changes); the report opens in the browser and carries the settings
  file; export labels without "(the benchmark's solve.py)".
- **(l) Small things.** The terminal prints the address at once; a zip that is
  already built in says so; question 2 shows before an application is chosen;
  a finished run says "Finished" and its time used stops.
