# Studies-over-time persona gate (agent path, project 4)

The gate of design §8 for project 4: a transport planner, not a programmer, sets a study to run over the weekend only
on Friday 17:30, closes the browser, and on Monday morning reads what it found and what all the studies on the
solver have learned. A rehearsal clock (`evolvekit app --service --clock 2026-11-13T17:30 --clock-speed 120`) makes
the weekend pass in about half an hour. Pass: 8/10 or more with no blocker.

Materials (made by the controller, outside the checkout, from the same template each round): `C:\gate\p4\home` with
demo-tour; two finished studies made beforehand with real short runs from the CLI ("Depot spring", "Depot summer":
the tune template, four sample cases with one held back, 0.1 h, no AI — both "not proven, one held-back case");
a draft "Weekend depot" (eight cases, four with 20 cities and four with 80, two held back, 0.3 h, everything set but
*When it runs*); `rehearse.cmd` and `open.cmd` (with `--no-browser`, so the gate never opens the owner's own
browser). The reviewer is a fresh agent told only the persona, the materials and the note; it reads only README.md
and docs/app.md and uses the in-app browser. Neither it nor the controller runs `install-startup` (D51).

## Round 1 -- 7/10, no blocker

[Judgement](over-time-gate/round-1-judgement.md). Code at dde6ff9.

- Scheduled for weekends from Set up's *When it runs* in a few clicks; Home said "Campaign · next window Sat 00:00".
  The run started at rehearsal Sat 00:04 and finished Sun 09:27; nothing ran outside the weekend; nothing claimed
  "better" before the final check. Monday's verdict: keep (2 held-back cases, both lower, range from 26.7 % worse
  to 37.9 % better).
- Worst: the weekend was barely used (18 minutes of search in a 48-hour window, and the plan bar still said "done
  around 17:34"); a keep whose two cases both improved read as a contradiction; the app showed real-clock times,
  so when it ran was only in `app.log`; *What we learned*'s strength figure had no explanation and no leaning;
  Monday's "Continue for 18 min more" did not say when it would run; the draft card did not say what was missing;
  fewer combinations than planned, unexplained; Set up cut at 800 px and a lost first click.
- Controller checks: every `app.log` campaign line carries its rehearsal and real time and falls inside the
  weekend; one run folder; `evolvekit app stop` exit 0, `app.json` and `clock.json` gone; no scheduled task.

Fixes after round 1 (each wave reviewed): a keep whose held-back cases all improved says why the range still
allows a loss; the campaign keeps its sessions on the window clock and the Study page and Past runs say "Ran in its
windows: Sat 00:04 – Sun 09:27"; a scheduled study's plan line says when it would be done on the window clock;
Set up offers to search through the whole window; Continue on a scheduled study goes to the next window; *What we
learned* explains its strength figure and says which values the searches leaned to (not checked); a draft card names
what is missing; how much of the plan ran is said whenever less ran; Set up moves aside for its drawer.

## Round 2 -- 8/10, no blocker: PASS

[Judgement](over-time-gate/round-2-judgement.md). Materials restored from the same template; code at ffb59c5.

- Scheduled for weekends; the run started at rehearsal Sat 00:04 and finished Sun 12:49, and the Study page said
  so; nothing ran outside the weekend; nothing claimed "better" before the final check; Monday's keep said why
  ("Every held-back case was lower …, but with only 2 cases the range is too wide to rule out a loss").
- Near-blocker: "Search through the whole window" sized the search to the whole 48 hours, so the plan's final
  check moved to the next weekend; the planner saw it on the plan line and set 20 minutes by hand.
- Smaller: the *When it runs* drawer was cut at 1024 px and Save gave no confirmation; a radio click landed on the
  next choice; real-time dates beside the rehearsal clock on the Study page.
- Controller checks: both `app.log` lines (start, complete) carry the rehearsal and the real time and fall inside the
  weekend; one run folder; `evolvekit app stop` exit 0, `app.json` and `clock.json` gone; no scheduled task.

Fixed after the pass, before the gate: the whole-window offer leaves room for the final check and says when it would
be done; Save confirms; the drawer fits at 1024 px; whole-row radio targets; the rehearsal note on the Study page.
