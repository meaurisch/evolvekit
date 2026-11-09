# Persona p4, round 2: the transport planner's weekend study

**Score: 8/10**
**Blocker: none.** One near-blocker: *Search through the whole window (48 h on the rehearsal clock, 24 min of real time)* is the app's own one-click offer for a weekend. Clicking it moved the plan to *done around Sat 21 Nov 00:00*. That is next weekend, not Monday morning. I noticed it only because I read the plan line, and I undid it by typing 20 min by hand.

Rehearsal: rehearse.cmd started at 19:38:00 real time (clock Fri 13 Nov 17:30, 120 ×). I scheduled the study at 19:39:49 real (clock Fri 21:07) and closed the tab. I opened the app again at 20:09 real (clock Mon 07:26 on the first load, Mon 09:02 after a reload).

## Ask 1: run "Weekend depot" over the weekend only, and start it

**What I did:** Home → the *Weekend depot* draft card → Set up → *When it runs* · Change → *Weekends, all day* → Save. Then I clicked *Search through the whole window*, saw where that left the plan, set Time to 20 min instead, clicked **Schedule it** and closed the browser tab.

**What I saw:**
- Draft: *When it runs: As soon as you start it. Or only at night, or at weekends.*
- After Save: *Weekends, all day. It runs only inside these hours, and only while the app is running then.* Plan: *Starts Sat 00:00; about 18 min of search; done around Sun 12:00.*
- After *Search through the whole window*: *2 s a case · 24 min in total. Starts Sat 00:00; about 24 min of search; done around Sat 21 Nov 00:00.*
- After I set 20 min by hand: *Starts Sat 00:00; about 20 min of search; done around Sun 15:57.*
- After Schedule it, Home: *CAMPAIGN · NEXT WINDOW SAT 00:00 · Weekend depot · Waits for its first window.* The button says "Schedule it" rather than "Start", so it is clear that nothing runs before Saturday. Good.

**Friction (worst first):**
1. The whole-window offer, made for exactly this case, pushes the answer to next weekend (*done around Sat 21 Nov 00:00*). The final check does not fit after 48 h of search, so the offer should leave room for it or say so. A planner who trusts the button gets no answer on Monday.
2. The *When it runs* drawer is cut off on the right at a 1024-px window: its subtitle and the option labels (*Nights, Monday to Frida…*) are clipped.
3. Save in the drawer gave no visible confirmation. The card on the left still said *As soon as you start it* for a few seconds and the drawer stayed open, so I clicked Save twice before it took.
4. My first click on *Weekends, all day* selected *Nights, Monday to Friday, and weekends*. This may have been my misclick, but the four options sit close together in the clipped drawer.
5. *About 18 min of search; done around Sun 12:00* reads oddly: 18 minutes of search that end 36 hours later. Under the rehearsal clock this is correct, but a planner has to stop and think about it.
6. The plan bar and the cases warning were already there (*With 2 held-back cases a 'use' is unlikely: the worst case stays wide.*). This is fair and honest, but a planner only setting *when* it runs cannot act on it on Friday at 17:30.

## Ask 2: on Monday, what did the weekend study find, and should I use it?

**What I did:** Opened the app with open.cmd, read the Home card, then opened the Study page, its Details and Past runs.

**What I saw:**
- Home card and status strip: *KEEP · FINAL CHECK ON 2 HELD-BACK CASES — Not proven on 2 held-back cases: a chance of about 17 in 20 of a gain; at worst 25.3 % worse (somewhere between 25.3 % worse and 36.2 % better).* Under it: *Every held-back case was lower (3.1 % and 7.9 %), but with only 2 cases the range is too wide to rule out a loss: hold back more cases for a firmer answer.* Then: *Keep today's settings (Demo tour's own defaults) for now: the gain is not proven.*
- *Campaign finished · Ran in its windows: Sat 00:04 – Sun 12:39*
- *The rule: use it at 9 in 10 or better, worst within 1 %.* The band runs from worst to best through *today*, with *← worse / better →*.
- Settings to keep: *Nothing to change: keep today's settings… Below them is what the search found: not proven on held-back cases, so not recommended.* The table follows: candidate moves 8→4, stalled moves 2,000→10,905, accept-worse 0→0.019, start random→nearest.
- Details: *Ran all 19 planned rounds (199 combinations, about 228 planned); 30 of the combinations tried repeated one already tried and were not measured again.* Held-back cases: depot-20-2, 12 runs, 3.1 % lower; depot-80-8, 12 runs, 7.9 % lower. *The search's best was 4.8 % lower tour length… on the 6 cases the search learned from (one run each). The final check's verdict is the answer on the cases the search never saw.*
- What next: *Search 20 min more in the next window (Sat 00:00)* and *Search now instead*.
- My answer as the planner: no, don't use it. Keep today's settings. To get a firmer answer next time, bring more cases and hold more of them back.

**Friction (worst first):**
1. The page header says *STUDY · TODAY 19:41* and Past runs says *today 19:41*, while the rehearsal clock says Monday. This is real time and documented, but on Monday morning "today 19:41" looks wrong.
2. *Every held-back case was lower* next to *at worst 25.3 % worse* puzzles a non-statistician at first. The sentence explains it (only 2 cases), but I needed to read it twice.
3. The plan at setup said *22 rounds · 264 combinations*. Details says *19 planned rounds… about 228 planned*. The plan changed and nothing says why (perhaps the timing after the first round).
4. The rehearsal banner on Home did not move on a hash navigation (it still said Mon 07:26). It updated only after a full reload.
5. *Search 20 min more in the next window (Sat 00:00)* does not say which Saturday.

## Ask 3: what have all the studies on this solver learned so far?

**What I did:** Home → *What we learned* (it goes to *What we learned · Demo tour*).

**What I saw:**
- *From 3 finished studies on this application. A winning value comes only from a study whose final check said use.*
- It lists the three studies, each with its verdict line: Depot spring (*8.7 % lower*, not proven), Depot summer (*7.7 % lower*, not proven) and Weekend depot.
- Table:
  - Candidate moves per stop: 0.41 from 3 studies, leaned to *5 (search, not checked)*, winning values *none yet*
  - Starting tour: 0.35, leaned to *nearest*, *none yet*
  - Stalled moves before a restart: 0.15, leaned to *2,380*, *none yet*
  - Chance to accept a worse move: 0.13, leaned to *0.0162*, *none yet*
- *…It comes from the searches, not from final checks: it says which settings are worth tuning, not which value wins.*
- My answer: candidate moves per stop and the starting tour matter most. No value has won yet, because no study has passed its final check. The searches lean to about 5 candidate moves and a nearest-neighbour start.

**Friction (worst first):**
1. Each study shows *finished 8 Nov* (the real date) while the rehearsal says Mon 16 Nov. Spring and summer also show 8 Nov, so I cannot tell from this page when each was really done.
2. The table cuts off at 1024 px wide: the *Winners moved it* column sits under the butterfly mascot.
3. The page does not say what to do next. The cases advice appears per study, but not as one line such as "all 3 were held back on too few cases."

## Did anything run outside the weekend?

No. The app says *Ran in its windows: Sat 00:04 – Sun 12:39*. I checked app.log, which the app's docs point to: *2026-11-14T00:04:15 rehearsal · weekend-depot: started a run (window Sat 00:00 – Sun 23:59)* and *2026-11-15T12:49:20 rehearsal · the campaign is complete: the campaign's search time is used*. There is nothing on Friday evening or on Monday. Between Friday and Saturday 00:00 the card said *Waits for its first window*.

## Did anything claim "better" before the final check?

Not in anything I saw. I closed the browser over the weekend, so I did not see the live strip mid-run. Afterwards, every pre-check figure was labelled as such:
- the Tries axis: *% change vs today, on the cases it learned from (not checked)*
- What we learned: *(search, not checked)*
- the search's 4.8 % comes with *The final check's verdict is the answer*
- the found settings are listed as *not proven… so not recommended*

The only "better" is the band's *better →* direction label.

## Where the numbers came from

- The verdict comes from the final check on 2 held-back cases the search never saw: depot-20-2 and depot-80-8, 12 paired runs each, mean of the paired runs. Their files are in C:\gate\p4\weekend-cases.
- The search figure (4.8 %) comes from the other 6 cases (depot-20-1/3/4, depot-80-5/6/7), one run each.
- What we learned comes from the three studies' searches, not their final checks, and says so.

## What would raise the score

- Make *Search through the whole window* end before the window does, with the final check inside it, or have it warn: "the final check would run next weekend; use 22 min to finish Sunday night". Today it silently moves the answer a week.
- Keep the setup drawer inside a 1024-px window, and make Save close it or show *Saved*.
- On a rehearsal, give the study page header, Past runs and What we learned dates on the window clock as well (*Sun 12:39*), not only *today 19:41* / *8 Nov*.
- Explain when the planned rounds change (22 → 19).
- Add a one-line summary on What we learned for a planner: "no winner yet: every study held back too few cases; hold back 3+ next time."
