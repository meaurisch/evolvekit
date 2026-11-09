# Gate p5, round 2: intern installs evolvekit 0.3.0 + PyVRP 1.0.0 (2026-11-09)

**Score: 8/10. Blocker: none.**

All four asks were done alone using only README "For planners: the installer" and docs/app.md. Nothing stopped me. The answer was an honest **keep**. I trust it, but two places showed different numbers for the same search result, and that costs trust (below).

## 1. Install evolvekit from the zip and start it

**What I did:** I ran Windows's *Extract All* (`Expand-Archive`) from `Downloads\evolvekit-app-0.3.0-win64.zip` into `Downloads\evolvekit-app-0.3.0-win64`, which took 15 s. I double-clicked `…\evolvekit-app-0.3.0-win64\evolvekit-app\evolvekit-app.exe`. No browser appeared where I could see it, so I followed the README: from Documents I ran `"…\evolvekit-cli.exe" --no-browser`.

**What I saw:**
- The folder layout was exactly the README's drawing: `evolvekit-app\` holding `evolvekit-app.exe`, `evolvekit-cli.exe` and `_internal\`.
- Windows showed no SmartScreen dialog and no prompt at all.
- The library appeared at `C:\gate\p5\Users\Zoë Intern\evolvekit` (harnesses, studies, app.json, app.log). The ë caused no problems.
- app.log said `evolvekit app : http://127.0.0.1:8780/  (the key is in app.json)`, with no key in the log.
- The CLI printed `the app is already running for this library: http://127.0.0.1:8780/?token=REDACTED`.
- Home said "No studies yet / Start one in a few steps. Pepper, the butterfly, follows it."

**Friction (worst first):**
- With no visible browser I had to open a terminal and run an exe by its full path. The README tells you how, and it worked first time, but it is the one command-line step in an otherwise no-command-line install.
- It is not obvious that the double-click worked. Nothing appears and there is no window, so if the browser does not open there is no sign the app is running.

## 2. Add PyVRP from pyvrp-1.0.0.zip

**What I did:** Home → *Add a harness or zip*. I pasted `C:\gate\p5\Users\Zoë Intern\Downloads\pyvrp-1.0.0.zip` into the box and pressed *Install*.

**What I saw:**
- A toast said "PyVRP 1.0.0 installed", word for word as the README promised, and *Applications (2)* became *(3)*.
- On Start a study the app first chose the wrong Python. It showed "Another version of PyVRP found — PyVRP 0.13.4 found in C:\Users\norri\AppData\Local\Programs\Python\Python312\python.exe; this harness needs PyVRP 0.14.x. Point to the one it needs."
- *Find it* opened "Which Python with PyVRP installed?". I pasted the PyVRP Python path and pressed *Check this path*. The app showed "PyVRP 0.14.0 · Ready on this computer" and a toast "Python with PyVRP installed / 0.14.0 will be used".

**Friction (worst first):**
- The app's own search found only the wrong PyVRP, inside *another user's* folder (`C:\Users\norri\…`), and did not find the one the team uses. Without the path my manager gave me I would have been stuck here. The warning itself was clear and told me what to do.
- The "Kind of study" cards changed order after I picked the Python ("Tune solver settings" moved from first to the middle). It was still selected, but I had to look twice.

## 3. Make a study (sample cases, some held back, about 10 min), start it, wait

**What I did:**
- Kind: "Tune solver settings" (the default). Cases: *Use the sample cases*, which read 10 cases of 17–50 KB with 4 held back. Name: "Depot routes less driving".
- On Set up I changed **Goal** from the default "Cost as PyVRP counts it, lower" to "Distance, lower (km · today 298.1)", because my manager asked for less *driving*.
- I set **Time** to 5 s a case and 10 min in total.
- The plan said "Ready, but the plan is thin / Thin plan: 42 combinations for 8 settings / about 5 tries per setting". I pressed *Tune the top 4*, which gave "Search · 6 rounds · 42 combinations · 8 min / Final check · 4 held back · 1 min".
- *Test run* said "Test run passed on cases/city-60.json — Distance 298.1, Feasible 1.00, Real cost 904.4".
- I pressed *Start* at 05:15. The study was done at about 05:25, and the page said it "took 10 min".

**What I saw while it ran:**
- Round 1: "SEARCHING · ROUND 1 OF 6 / Nothing better yet / Measuring the starting point on 6 cases".
- Round 4: "Best so far: 3.3 % lower distance on the cases it learned from · Not checked yet".
- Final check: "FINAL CHECK · 23 OF 80 RUNS / No answer yet / Adding runs on 4 held-back cases while time remains", with the chip "Combination 40 · 1.8 % lower on the cases it learned from, not checked".
- A banner the whole time: "Your computer is busy. Tries may run slower. Is it still fair?". Clicking it explained: "The answer stays fair: the final check runs today's settings and the change side by side, under the same load."

**Friction (worst first):**
- The default goal is "Cost as PyVRP counts it", not distance. Only an intern who opens *Change* on Goal and knows that cost is not driving would pick Distance. Nothing asks "what do you want less of?".
- "Time per case" is also PyVRP's own time limit. The default was 1 min; I set 5 s to fit 10 minutes. Nothing says whether settings tuned at 5 s still hold at the time limit the team really uses.
- The "best so far" went *down*: round 4 said 3.3 % lower, then "A new best 1 min ago" came with 1.8 %. That looks like the app took back a gain, and nothing explains it.
- Distance was the goal with no guardrail. The report's own text says Real cost should be paired with "Feasible at least 1", but Set up did not offer this for Distance. (Feasible stayed 1.00 → 1.00, so no harm this time.)

## 4. What it found, should I use it; stop the app

**What I saw:**
- Study strip and Home card agreed word for word: "KEEP · FINAL CHECK ON 4 HELD-BACK CASES — Not proven on 4 held-back cases: a chance of about 9 in 20 of a gain; at worst 3.1 % worse (somewhere between 3.1 % worse and 2.9 % better). Keep today's settings (PyVRP's own defaults) for now: the gain is not proven." Under it: "The rule: use it at 9 in 10 or better, worst within 1 %."
- "Settings to keep: Nothing to change: keep today's settings… Below them is what the search found: not proven on held-back cases, so not recommended." The found values were 500→220, 100,000→70,283.9 and 0.65→0.877338.
- Held-back cases, 7 paired runs each: county-240 1.4 % lower, lake-140 2.4 % higher, suburbs-180 1.0 % higher, towns-80 1.5 % lower. Two were better, two worse, so "keep" makes sense to me.
- Details said "Ran all 6 planned rounds (41 combinations, about 42 planned)". It also offered "Check again with 12 runs per case instead of 7 (about 77 s)" and "bring more cases… that holds more of them back".
- The Study page and the report gave different figures for the search's best:
  - Study page: "The search's best was 1.8 % lower distance … on the 6 cases the search learned from (one run each)".
  - Report: "The best settings found give 3.8 % lower distance … not checked".
  - Neither says why they differ.
- Stop: Settings → *Stop the app* → "The app has stopped. Studies that are running carry on. To open it again, double-click evolvekit-app.exe…". app.log said "stopped", app.json was gone, no evolvekit process was left, and nothing had been written into the app's folder.

**Should I use it?** No. Keep PyVRP's defaults. The app says so plainly, and the per-case table shows why.

**Friction (worst first):**
- The search's best gain had two figures, 1.8 % on the Study page and 3.8 % in the report, with no explanation. My manager reads the report and I read the page, so we would quote different numbers.
- The cases it learned from swing wildly on one run each (valley-120 18.6 % lower, region-220 4.7 % higher). This is shown without a word that one run is noisy, and it makes the search look better than it is.
- The *Why* link next to the rule gave no visible text that I could capture. I cannot say whether it explains the rule.

## What worried me
- The app proposed a PyVRP in another user's folder (`C:\Users\norri\…\Python312`). It was right to refuse it, but it never found the right one by itself.
- "Your computer is busy" stayed on for the whole study. The explanation reassured me, but it is still a warning I could not act on.
- Windows itself showed nothing worrying: no SmartScreen, no antivirus prompt, no "file in use".

## Did anything claim "better" before the final check?
No. Before the verdict, every figure was labelled:
- "on the cases it learned from" with "Not checked yet" or "not checked";
- the Tries axis read "% change vs today, on the cases it learned from (not checked)";
- the strip said "No answer yet" during the check.

The only "better" before the check was "Nothing better yet" in round 1, which is a negative. *Use it* never appeared.

## Where the numbers came from
- PyVRP 0.14.0 from the PyVRP Python whose path I pasted, solving the harness's 10 sample delivery requests at 5 s each.
- The search used the 6 study cases, one run per combination.
- The verdict used the 4 held-back cases, today's defaults against combination 40, 7 paired runs per case, judged by the fixed rule (chance ≥ 9 in 10, worst within 1 %).
- Measure totals from "Every measure":
  - Distance: study cases 514.2 → 504.8 km; held-back 654.0 → 654.7 km.
  - Feasible: 1.00 → 1.00.
  - Routes: held-back 7.79 → 7.54.
- No AI was used ("AI help: off"; Pepper had no key).

## What would raise the score
1. One figure for "the search's best" everywhere, or one sentence where they differ saying which average each is (for example, 1.8 % of the totals vs 3.8 % averaged per case).
2. Find the right PyVRP by itself, or say where it looked, rather than offering only the wrong one in another user's folder.
3. Ask for the goal in plain words ("less driving / lower cost / fewer vans"), and for Distance offer the "Feasible at least 1" guardrail.
4. Warn when the time per case is much shorter than the solver's usual limit: "settings tuned at 5 s may not hold at 1 min".
5. Explain a "new best" whose figure is smaller than the one before.
6. Give a visible sign that the double-clicked app is running, such as a tray icon or a notification with the address, so the terminal step is rarely needed.
