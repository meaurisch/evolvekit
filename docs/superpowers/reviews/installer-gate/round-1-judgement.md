# Gate p5 — round 1 — the intern (Zoë Intern), evolvekit-app 0.3.0 win64

**Score: 5/10**
**Blocker: the first Start "finished" in 37 s without searching, and said "Keep today's settings … the search found nothing better."** The plan had promised "Search · 4 rounds" in 10 min. The run measured the starting point and stopped. The page gave no reason. Only the run's own `job.json` in my library said why: `time limit: another round would not finish within 0.124 h (0.01 h used; a round has taken up to 0.12 h)`, although the round had taken 36 s. If I had gone home then, I would have told my manager "evolvekit tried and PyVRP's settings can't be improved", which was false. Pressing *Continue for 10 min more* got a real search and an honest answer, but nothing on screen told me to do that.

## Ask 1: install and start

- **What I did:** Expanded the app zip into `Downloads\evolvekit-app-0.3.0-win64` (Extract All's default). That makes `…\evolvekit-app\` with `evolvekit-app.exe`, `evolvekit-cli.exe` and `_internal`. I double-clicked `evolvekit-app.exe` with `start ""` and the process started. No browser reached me, so I followed the README: from Documents I ran `"…\evolvekit-cli.exe" --no-browser`.
- **What I saw:** `the app is already running for this library: http://127.0.0.1:8780/?token=REDACTED`. I opened that address in my browser and got Home: "No studies yet … Start one in a few steps. Pepper, the butterfly, follows it." `app.log` said: `evolvekit app : http://127.0.0.1:8780/  (the key is in app.json)` and `library home  : C:\gate\p5\Users\Zoë Intern\evolvekit`.
- **Windows:** I saw no SmartScreen, antivirus or console window (I cannot see the desktop, so a SmartScreen prompt may simply not have reached me).
- **Friction (worst first):**
  - When no browser opens, the way back is a terminal command. It worked first time, but an intern has to find it in the README.
  - The address carries a long key, which the terminal prints in plain view.
  - Extracting gives a nested folder (`evolvekit-app-0.3.0-win64\evolvekit-app\`). It is harmless, but the exe is one level deeper than I expected.

## Ask 2: add PyVRP from `pyvrp-1.0.0.zip`

- **What I did:** Opened *Add a harness or zip*. It offers "Or drop a harness or study zip / Or choose a zip / Or a harness folder on this computer". My browser cannot drop or pick files, so I pasted the zip's full path into the box marked as a harness folder (`C:\…\my-harness`) and pressed *Install*.
- **What I saw:** The drawer closed with no sentence at all. The only sign it worked was "Applications (2)" turning into "Applications (3)". The list then said "PyVRP 1.0.0 · Vehicle routing with PyVRP 0.14 … installed".
- **Friction (worst first):**
  - There was no "installed" confirmation; I had to notice the count change.
  - The path box says it takes a *folder*, yet it accepted a zip. I only tried it on a hunch.

## Ask 3: make, start and wait for a study (samples, held back, about 10 min)

- **What I did, step by step:**
  - New study → PyVRP. The app said "Python with PyVRP installed not found / PyVRP 0.13.4 found, and this harness needs PyVRP 0.14.x. Point to it on this computer." *Find it* listed `C:\Users\norri\AppData\Local\Programs\Python\Python312\python.exe`, a Python outside my user folder.
  - I pasted the path IT gave me and pressed *Check this path*. It said "PyVRP 0.14.0 · Ready on this computer".
  - Kind: *Tune solver settings*. After the Python check the list reordered (Route cost sets came first) but my choice stayed.
  - Cases: *Use the sample cases* gave "10 cases · 17 KB to 50 KB … 4 held back: a fair answer for a clear, consistent gain." and "Held back: kept aside, never searched on. The final check tries the result on them." I kept the name "PyVRP · tune solver settings".
  - Set up: the Goal defaulted to "Cost as PyVRP counts it". "Less driving" meant *Distance* (lower is better, km, today 298.1), so I chose that.
  - Time: I set 0 h 10 min. The app said "Not ready: the plan cannot work … With these choices the search gets fewer than 3 rounds, too few to learn anything. Give each case at most 19 s. Allow at least 30 minutes in total." (that sentence is printed twice). I set 15 s a case, and it said "15 s a case · 10 min in total … Search · 4 rounds … About 6 tries each."
  - *Test run* said "Test run passed on cases/city-60.json · Distance 287.4 · Feasible 1.00 …".
  - Start (03:04): the page read "SEARCHING · ROUND 1 OF 4 … Measuring the starting point on 6 cases".
- **The false finish:** About 40 s later the page said "FINISHED · There is no final check for this run. Keep today's settings (PyVRP's own defaults): there is no final check yet to tell."
  - Details: "COMBINATIONS 0 tried".
  - The report: "Combinations tried 1 · It took 37 s · No settings found beat your starting point … Keep today's settings …: the search found nothing better."
  - *Past runs*: "finished · the starting point stayed the best".
  - Nowhere did it say the search had been cut short.
- **Continuing:** I pressed *Continue for 10 min more*.
  - The strip said "SEARCHING AGAIN · ROUND 1 OF 4 … Writing changes: 12 of 12." (AI help was off) and "Your computer is busy. Tries may run slower." Its *Is it still fair?* explains: "The answer stays fair: the final check runs today's settings and the change side by side, under the same load."
  - Home's card said "Continued after a restart · Nothing lost", but there had been no restart.
  - It went through rounds 2–4 with "Best so far: 0.5 % lower distance on the cases it learned from · Not checked yet".
  - Then "FINAL CHECK · 0 OF 48 RUNS · No answer yet · Adding runs on 4 held-back cases while time remains."
  - The verdict came at about 03:16–03:17. The report says "It took 12 min" for a 10 min study.
- **Friction (worst first):**
  - The false finish: the plan promised 4 rounds and delivered none, and the page called that a result.
  - Finding PyVRP: the app chose a different Python, said "not found", and I needed the path from IT.
  - The time fix ("Give each case at most 19 s") is plain text, not a button, so I had to open Time again myself. The reason sentence appears twice.
  - "Writing changes: 12 of 12" with AI off, and "Continued after a restart" when nothing restarted. Both made me wonder what else was wrong.
  - 12 min for a 10 min study, plus the 37 s false start.
  - Home briefly said "No studies yet" while the study was running, before it loaded.

## Ask 4: what it found, should I use it, stop the app

- **What I saw:** The strip read "KEEP · FINAL CHECK ON 4 HELD-BACK CASES".
  - Verdict: "Not proven on 4 held-back cases: a chance of about 4 in 20 of a gain; at worst 0.8 % worse (somewhere between 0.8 % worse and 0.4 % better)."
  - Advice: "Keep today's settings (PyVRP's own defaults) for now: the gain is not proven."
  - Rule: "The rule: use it at 9 in 10 or better, worst within 1 %."
  - Under it: "Settings to keep · Nothing to change … Settings found, not recommended", and the one changed setting, *Share of plans that keep the rules* 0.65 → 0.720445.
- **The report, case by case:**
  - Held-back cases: county-240, suburbs-180 and towns-80 the same; lake-140 581.6 → 586.3 km, "0.8 % higher".
  - Learned-from cases: only metro-100 changed, "3.7 % lower". The other five were the same.
- **My answer to the manager:** No. Keep PyVRP's settings. In 10 minutes evolvekit found nothing that holds up on routes it had not seen.
- **Stop:** Settings → *This app* → *Stop the app*. It said: "The app has stopped. Studies that are running carry on. To open it again, double-click evolvekit-app.exe. To update the app, extract the new version over its folder only when no study is running …". `app.log` said `03:17:57 stopped`. `app.json` was removed, and no evolvekit or PyVRP process was left.
- **Friction (worst first):**
  - The report's headline is the search figure: "The best settings found give 0.6 % lower distance than your starting point … on the cases the search learned from". Only the next line says keep. A manager skimming it sees "0.6 % lower" first.
  - The figure differs: the strip said 0.5 %, the report 0.6 %. The manual explains the averaging; I didn't know that on the day.
  - The report's "PyVRP settings (JSON)" block holds the *not-recommended* settings (`target_feasible: 0.720445`). It does not repeat the page's "not recommended" label, so someone could copy it into PyVRP.
  - Stopping was easy and its message was clear.

## What worried me

- The 37 s "FINISHED … Keep today's settings" with "the search found nothing better", when nothing had been searched.
- A Python in another user's folder (`C:\Users\norri\…\Python312`) was offered as mine.
- "Your computer is busy" on a new computer with nothing else open. The explanation calmed me.
- The "Writing changes" and "after a restart" messages that were not true.
- Windows itself showed me nothing worrying (no SmartScreen or antivirus prompt reached me).

## Did anything claim "better" before the final check did?

Mostly no.
- The strip said "Best so far … on the cases it learned from · Not checked yet".
- The Cases view said "ahead of today … (not checked)".
- The verdict never said use it.
- Exceptions:
  - The Settings view's legend: "The band: values in tries over 1 % better."
  - The first, false run's report claimed an absence: "the search found nothing better", which it had not looked for.
  - The final report leads with "0.6 % lower distance" (labelled as on the learned-from cases) above the keep advice.

## Where the numbers came from

- All of them come from real PyVRP 0.14.0 solves, run on this computer by the Python I pointed to, 15 s per solve.
- The search figures are from the 6 study cases (city-60, metro-100, valley-120, cities-160, market-200, region-220).
- The verdict is from the 4 held-back cases (towns-80, lake-140, suburbs-180, county-240), "each run 4 times on every held-back case", best settings against today's settings side by side.
- No AI was used and nothing was spent.

## What would raise the score

1. Never let a run end before its first round without saying so. Fix the round-time estimate (36 s counted as 0.12 h), or say "The search did not run: …" with *Continue*. Never write "the search found nothing better" for a search of 0 combinations.
2. Confirm an install ("PyVRP 1.0.0 installed"), and say the path box takes a zip too.
3. Only offer Pythons I can use, and say "found PyVRP 0.13.4 in <path>, needs 0.14", not "not found".
4. Make the plan's fixes ("Give each case at most 19 s") buttons, and print the reason once.
5. Lead the report with the verdict, not the search figure. Label the settings JSON "not recommended" when the answer is keep.
6. Remove the "Writing changes" (AI off) and "after a restart" (no restart) messages, and the "better" in the Settings legend.
