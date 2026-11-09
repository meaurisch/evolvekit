# Installer persona gate (agent path, project 5)

The gate of design §8 for project 5: an intern, not a programmer, on a "new Windows computer" installs evolvekit from
its zip, adds PyVRP from `pyvrp-1.0.0.zip`, makes and runs a study of PyVRP's settings, reads its answer and stops
the app. Pass: 8/10 or more with no blocker.

**No Windows account was created** (a persistent system change the owner has not approved): the intern works under a
simulated fresh profile, `C:\gate\p5\Users\Zoë Intern` (a space and a non-ASCII letter), through
`C:\gate\p5\as-intern.cmd`, which sets USERPROFILE, HOME, APPDATA, LOCALAPPDATA and TEMP into it, keeps only
`C:\Windows\System32` (and Wbem, PowerShell) on PATH, so no Python is found, and clears EVOLVEKIT_HOME, VIRTUAL_ENV,
PYTHONPATH and PYTHONHOME. A defect only a real second account would show (its registry, its profile's ACLs, a real
SmartScreen prompt) is not covered. The reviewer is a fresh agent told only the persona, the materials and the note;
it reads only README.md and docs/app.md and uses the in-app browser.

## Round 1 -- 5/10, blocker

[Judgement](installer-gate/round-1-judgement.md). Code at b563b89; `evolvekit-app-0.3.0-win64.zip` 37.9 MB (59 MB
unpacked), `pyvrp-1.0.0.zip` 104 KB.

- Installed with Extract All, started with a double-click (the README's way back to the address worked when no
  browser reached the intern), PyVRP added from its zip, a study of 10 sample cases with 4 held back, 15 s a case,
  10 min.
- **Blocker:** the first Start "finished" after 37 s with 0 combinations ("another round would not finish …; a round
  has taken up to 0.12 h", after 36 s) and said "the search found nothing better". *Continue for 10 min more* then
  searched, and the final check said keep (about 4 in 20, at worst 0.8 % worse).
- Smaller: no confirmation after installing the zip; "PyVRP not found" when 0.13.4 was found; the plan's fixes as
  text, not buttons; the report led with the search figure; "Writing changes" with AI off; "Continued after a
  restart" with no restart; "better" in the Settings legend; "No studies yet" while Home loaded.
- Controller checks: the runs were `evolvekit-cli.exe -m evolvekit.app.job` from the extracted folder and the solves
  ran under the PyVRP Python; the home is under the simulated profile with `harnesses\pyvrp-1.0.0` and the study;
  the bundle folder unchanged (1088 of 1088 files, size and CRC-32); the app stopped (no `app.json`, no process); no
  scheduled task; the real home unchanged (0 files).

Fixes after round 1 (each wave reviewed): the blocker's root cause was in the engine's time rule, not in the installer
(present since 2026-09-26): the first round was priced as the starting point's time times every child, ignoring that
only the best children run every case; it is now priced stage by stage as the plan counts a round, and the intern's run
then searched all four planned rounds. A search that does not run now says "The search did not run: …" everywhere and
offers Continue, never "found nothing better". Also: a zip install is confirmed; the Python finder says what version it
found; the plan's fixes are buttons; the report leads with the verdict; no "Writing changes" without AI and no "after a
restart" without one; no "better" in the Settings legend; Home says "Loading…".

## Round 2 -- 8/10, no blocker: PASS

[Judgement](installer-gate/round-2-judgement.md). Code at 506a9d2; `evolvekit-app-0.3.0-win64.zip` 37.9 MB; a fresh
simulated profile.

- All four asks alone: Extract All, a double-click, PyVRP added from its zip, a study of the sample cases with some held
  back in about ten minutes, the answer read, the app stopped from Settings. The answer was an honest keep (2 of 4
  held-back cases better, 2 worse, about 9 in 20); nothing claimed "better" before the final check.
- Frictions: the report's search figure (3.8 %) differed from the Study page's (1.8 %); the Python finder offered the
  owner's own PyVRP 0.13.4 (an artefact of the simulated profile on the owner's machine) and the intern pasted the
  path IT gave; the default goal is PyVRP's cost, not distance; settings tuned at 5 s a case against PyVRP's 1 min with
  no warning.
- Controller checks: the runs were `evolvekit-cli.exe -m evolvekit.app.job` from the extracted folder and the solves
  ran under the PyVRP Python; the home under the simulated profile with `harnesses\pyvrp-1.0.0` and the study done;
  the bundle unchanged (1088 of 1088); the app stopped; no scheduled task; the real home unchanged.

Fixed after the pass, before the gate: one search figure on every surface; a note when the time per case is below the
harness's own.
