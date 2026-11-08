# Gate p4, round 1: a weekend study (transport planner)

**Score: 7/10**
**Blocker: none.** All three asks were done in the app. Nothing stopped me, and I found nothing I would distrust. What holds the score down is how little the weekend bought (17 minutes of search in a 48-hour window) and a Monday answer that is hard to read in one place.

## Ask 1: run "Weekend depot" over the weekend only, start it, close the browser

**What I did.** I opened Home (rehearsal clock *Fri 13 Nov 17:38*). The card said *DRAFT · Weekend depot · Set up not finished*, and opening it went to Set up. I pressed *When it runs → Change* and chose *Weekends, all day* (the other choices were *As soon as I start it*, *Nights, Monday to Friday, 01:00 to 06:00* and *Nights … and weekends*, plus an optional *Until* date). I pressed *Save* and then *Schedule it*. Home then said *CAMPAIGN · NEXT WINDOW SAT 00:00 — Waits for its first window*. I closed the tab.

**What I saw.** The summary changed to *Weekends, all day — It runs only inside these hours, and only while the app is running then.* The Start button became *Schedule it*. That was clear.

**Friction (worst first):**
- The plan bar still said *18 min in total · done around 17:34* after I chose weekends. 17:34 is not when a weekend study finishes, so for a moment I thought it would run tonight.
- It never says or offers *use the weekend*. The study's 18 minutes is all a 48-hour window gets (see Ask 2), and nothing warned that the weekend would be mostly idle.
- *Set up not finished* on the Home card was alarming, but the only thing missing turned out to be the schedule. It does not say what is missing.
- The page is a little wider than the window at 800 px: the right edge (*Final check* label, the note) is cut off. My first click on *Schedule it* seemed not to register, and a second one did.

## Ask 2: on Monday after 08:00, what did the weekend study find, and should I use it?

**What I did.** I opened the app at rehearsal *Mon 16 Nov 08:39* and read the Home card, then the Study page, *Details*, *Past runs* and *Why*.

**What I saw.**
- Home and the Study strip: *KEEP · FINAL CHECK ON 2 HELD-BACK CASES — Not proven on 2 held-back cases: a chance of about 17 in 20 of a gain; at worst 26.7 % worse (somewhere between 26.7 % worse and 37.9 % better). Keep today's settings (Demo tour's own defaults) for now: the gain is not proven. · Campaign finished*
- *Settings to keep — Nothing to change: keep today's settings … Below them is what the search found: not proven on held-back cases, so not recommended.* The settings: Candidate moves 8 → 4; Stalled moves 2,000 → 9,053; Chance to accept a worse move 0 → 0.0224; Starting tour random → nearest.
- In *Details*:
  - *Held-back cases: depot-20-2, 12 runs, 3.1 % lower; depot-80-8, 12 runs, 8.2 % lower.*
  - *The search's best was 4.8 % lower tour length … on the 6 cases the search learned from (one run each). The final check's verdict is the answer on the cases the search never saw.*
  - *For a firmer answer: bring more cases …*
- *Past runs*: one run, *today 17:19 · finished · not proven on the held-back cases*.

**My answer as a planner:** do not use it; keep today's settings. The app says so plainly, and *Why* gives the fixed rule (*use it at 9 in 10 or better, worst within 1 %*).

**Friction (worst first):**
- Both held-back cases came out *lower* (3.1 % and 8.2 %), yet the headline says *at worst 26.7 % worse*. Nothing explains in plain words that two cases are too few to pin the range down. *Why* only restates the rule. Set up did warn me (*With 2 held-back cases a 'use' is unlikely*), but on Monday the two numbers look like a contradiction.
- The weekend was barely used. The campaign finished at rehearsal Sun 09:27 after 17 minutes of search (*18 min · took 17 min*), and the computer then sat idle for the rest of the weekend. *Continue for 18 min more* is offered on Monday morning, which is exactly when I do not want it running. Nothing says whether it would run now or wait for next weekend.
- The times are on the real clock, not the rehearsal clock: *STUDY · TODAY 17:19* and *Past runs: today 17:19* next to *rehearsal clock: Mon 16 Nov 08:39*. From the app alone I could not tell *when in the weekend* it ran. I needed `app.log` for that.
- The plan promised *20 rounds · 240 combinations*. Details says *176 tried* (the Tries tab says 175), and nothing says why fewer ran.

## Ask 3: what have all the studies on this solver learned?

**What I did.** I opened *What we learned* from Home (it opens *What we learned · Demo tour*).

**What I saw.** *From 3 finished studies on this application. A winning value comes only from a study whose final check said use.* It lists the three studies with their verdict lines:
- Depot spring: *Not proven, one held-back case: 8.7 % …*
- Depot summer: *… 7.7 % …*
- Weekend depot: the line above.

The table:

| Setting | How strongly it went with the measure | Winning values | Winners moved it |
|---|---|---|---|
| Candidate moves per stop | 0.41 · from 3 studies | none yet | — |
| Starting tour | 0.35 · from 3 studies | none yet | — |
| Chance to accept a worse move | 0.12 · from 3 studies | none yet | — |
| Stalled moves before a restart | 0.11 · from 3 studies | none yet | — |

**My answer as a planner:** two settings seem to matter (candidate moves per stop, starting tour). No value has won yet, because no study has passed its final check.

**Friction (worst first):**
- *0.41* has no explanation on the page: no scale, no *higher is better or worse*, no direction, and no visible *this is not proof*. Without the docs I would not know what it means. *Winners moved it — —* gives me nothing.
- *Winning values: none yet* is honest, but I cannot see which values the searches leaned towards (for example, did all three prefer the *nearest* starting tour?). After three studies I still have nothing to try.
- All three studies say *finished 8 Nov* during a rehearsal dated 16 Nov. I was told two of them were done this autumn, so the date confuses rather than informs.

## Did anything run outside the weekend?

No. The app's only sign was *Campaign finished* and a run dated *today 17:19* on the real clock. I checked `C:\gate\p4\home\app.log`, which has two lines:
- *2026-11-14T00:04:10 rehearsal … weekend-depot: started a run (window Sat 00:00 – Sun 23:59)*
- *2026-11-15T09:27:13 rehearsal … weekend-depot: the campaign is complete: the campaign's search time is used*

So it started at Sat 00:04 and ended Sun 09:27, inside the weekend. Nothing ran on Friday evening or on Monday.

## Did anything claim "better" before the final check did?

No, in everything I saw. I was away during the run (the browser was closed, as on a real Friday), so I did not see the live strip. Afterwards every search figure is labelled:
- *% change vs today, on the cases it learned from (not checked)*
- *Settings found, not recommended*
- *The search's best was 4.8 % … The final check's verdict is the answer*

Home never said *use* or *better*, and *What we learned* shows *none yet* for winning values.

## Where the numbers came from

- **The verdict:** the final check on 2 held-back cases (depot-20-2 and depot-80-8, 12 runs each, paired against today's settings). Those cases gave 3.1 % and 8.2 % lower, and the range was 26.7 % worse to 37.9 % better, chance about 17 in 20.
- **The 4.8 %:** the search on the 6 cases it learned from, one run each. The page labels it as not checked.
- **The *What we learned* strengths:** the searches of the 3 studies.
- **Winning values:** only from studies whose final check said use. There were none.

## What would raise the score

1. On a weekend schedule, offer to spend the window: *the weekend has 48 h; your study uses 18 min, so use the weekend?* Say in the plan bar when the campaign is expected to finish, instead of *done around 17:34*.
2. Under a *keep* with few held-back cases, add one plain sentence explaining why it is still *keep* when both cases improved, for example *with only 2 cases the range is too wide to rule out a loss*.
3. Show when a campaign ran, by window, on the Study page and in Past runs, on the clock the app is using (*ran Sat 00:04 – Sun 09:27*).
4. On *What we learned*, say what the strength figure means and that it is not proof. Say which way the searches leaned for each setting, even when no value has won yet.
5. Say what *Continue for 18 min more* does for a campaign (now, or at the next window).
