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
