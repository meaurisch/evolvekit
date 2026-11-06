# Claude Design session: the evolvekit app

Paste everything below the line into a new Claude Design session. The canvas "evolvekit app: five approaches" (https://claude.ai/artifact/SZBz3JZ8URx6LKqTjkJdA6) holds the five sketches from the first round; this session picks one and makes it in full.

---

I am the owner of evolvekit and I make every decision in this session. Propose, show me options side by side, and wait for my pick before going further. Nothing is settled by default.

## What we are doing

Redesign the evolvekit app: a local web app in which a planner who is not a programmer tunes a program like PyVRP, a route planner, on their own cases, and gets an honest answer about whether the result can be trusted. The app works end to end and passed a usability test with an intern persona at 8 out of 10. Its problem is the look: a system font, blue buttons and grey cards on beige, and everything told in sentences. It reads as generic and has no personality.

Five approaches were sketched already (the canvas "evolvekit app: five approaches"): Instrument, Report, Conversation, Map, Cards. Each has Set up, Running and Results at 1280 px and Results at 390 px, with a one-line idea and what it gives up.

The session has three parts:

1. **The pick.** Show me the five side by side, in two or three sentences each, and ask me which one, or which mix ("the results of B with the set-up of D").
2. **The personality.** Four decisions, each with two or three options to compare before I pick:
   - the mark: a logo built on an evolution motif (lineage, branching, variation), usable at 16 px and as an empty-state illustration;
   - the accent and the surfaces: one accent colour that is not the default blue; surfaces designed for light and for dark, not inverted;
   - the type: a face with tabular figures and a character of its own, open-licensed so the offline app can ship it;
   - the voice: how the app says the three hard things, *not proven*, *worse*, *it failed*. Plain, exact, warm, never alarming; the number first.
3. **The chosen approach in full:** every screen below at 1280 px and 390 px, light and dark, every state in the table; the five signature elements first, since they carry the personality more than any colour; components and tokens named so the build maps them one to one; a clickable prototype of the main path (New study, then Start, then Results).

## The product in a minute

- **Who:** planners, analysts and interns at a logistics company. They know their routes, costs and time windows; they are not programmers or statisticians. They read literally and stop at jargon.
- **The one job:** "Find settings that do better on our data, in the time I have, and tell me if I can trust them."
- **What they take away:** use it or not, how much better, how sure, and the settings file to hand over.
- **How it runs:** a Python process on their own computer serves a page on 127.0.0.1, at an address that carries a key for this launch (a bare bookmark shows one sentence). A study runs 30 minutes to a few hours; people set it up, press Start, leave, and come back to the answer. Nothing loads from the internet.
- **How it decides:** it learns on most cases and holds a few back. At the end the final check runs the winner and the starting point on the held-back cases several times each, and the app decides by one fixed rule: *use it* when the chance of a gain is at least 9 in 10 and the worst plausible case is within 1 % of today; *keep today's settings* otherwise; *worse* when the chance of a gain is 1 in 10 or less. The user sets nothing. The check keeps adding runs on its own while time remains and the answer is open.
- **One study at a time.** A second study waits in a queue and starts by itself. A run that died with the computer continues where it stopped.
- **The assistant:** a drawer on every screen. It proposes parts of the study as cards (a measure, a data change, a rule, a goal, a guardrail), each checked on real data before it is shown, and it answers questions in plain words ("Why is this not proven?") with the run's own numbers.

## Principles

1. **One screen, one question.** Its answer is the heading; everything else waits behind a click.
2. **Show, don't tell.** A mark, a strip, a dot or a bar before any sentence.
3. **Defaults first.** The main path is three choices; everything else sits behind "Change".
4. **Few words.** Labels of up to 3 words, sentences of up to 12, help behind an (i). No screen shows more than 100 words before a click.
5. **Calm, alive where it is live.** One accent colour; motion only while a study runs.
6. **Honest.** Nothing says "use it" before the final check does, and every estimate shows its range.

## The five signature elements

| Element | What it shows | What it replaces |
| --- | --- | --- |
| Verdict mark | Three states (use it · keep today's settings · worse) with the chance ("19 in 20") and the worst case in one line | A 26-word headline and an amber sentence |
| Interval strip | A bar through 0 %: the likely range as a band, the estimate as a dot, the worst case marked | "somewhere between 0.3 % worse and 2.8 % better" |
| Case dots | One row per case: start and best as two dots, joined, coloured by which way it went | Two per-case tables |
| Run timeline | Rounds as segments, the best so far above them, the final check last and growing while it adds runs, "done around 17:40" | Four fact tiles, a progress bar and a chart |
| Plan bar | The total time split into search and final check, rounds ticked off | "about 6 rounds, about 42 combinations tried, a final check on 3 held-back cases with 6 runs each (≈ 2 min); done around 02:49." |

## The main path

Today the path is 11 screens: home, new study, seven set-up steps, running and results. The new one needs five moments, everything else one click away: Start a study → Set up → Running → Results → Hand it over. The order is fixed: an answer needs cases, a goal and a time before Start. How the moments are laid out (pages, one canvas, a chat, cards) is the approach's own choice. Open question for you to settle with me: can the seven set-up steps become one Set up screen?

## Screens and states

| Screen | Its question | Always visible | One click away | States to design |
| --- | --- | --- | --- | --- |
| Home | Your studies | Study cards: name, state (running with time left; queued behind another study; continuing after a restart; finished with its verdict mark); New study | Installed applications, adding a harness or a study zip, the library folder; per study: rename, duplicate, archive, delete, save as zip, its past runs; compare two studies; archived studies; deleted studies with Restore | First run with no studies; a study running; a study queued; a study that continued; an archived study; a deleted study with Undo for ten seconds |
| Start a study | What do you want to find out? | The application (found for you), the kind of study (settings or data), the cases (drop a folder), a suggested name, how firm an answer this many cases can give ("3 held back: a fair answer for a clear, consistent gain") | Other applications, a blank study, file formats, per-file details, which cases are held back | No application found; a file that cannot be read; too few cases; too few held back for a firm answer |
| Set up | Ready to start? | Three summaries with Change: what to tune (4 of 27 settings), the goal, the time (20 s a case, 30 min, done around 17:40); the test run; Start | Each setting and its range, data changes, rules, measures, held-back cases, runs per case, AI help, the assistant | Preview running (values arrive); something missing (named, with a jump to it); plan thin or impossible (with fixes); test run failed; a case, measure or rule just removed, with Undo |
| Running | Searching, 12 min left | The time bar with rounds; best so far, marked "not checked yet"; Stop; the assistant | What runs now, failed runs, the expert dashboard | Starting; waiting in the queue ("Waiting for 'Night run' to finish"); final check, still adding runs while time remains ("6 runs per case so far"); stopping; stopped; stopped before it started; failed; continuing after a restart ("Searching again, from where it stopped"); computer busy |
| Results | The verdict, in one sentence | Verdict mark with the chance and the worst case; the rule in one line; interval strip; held-back cases as dots; the settings file (copy, save); the assistant | Every measure, what changed, the cases it learned from, this study's past runs with their verdicts, the report, the summary text, a new study from here, continue with more time, compare with another run or study, save as zip | Use it; keep today's settings; worse; one held-back case only (a number, no range, no verdict); nothing beat the start; check still running; check stopped; a run that died, with Continue where it stopped |
| Compare | Which did better? | Two studies or runs side by side: verdict, interval, cases, what changed, the set-up | Either study's results | Same goal; different goals (say so); one side without a final check |
| Settings | The AI assistant | Provider, key (set, or not, with Remove), Test | Models and their prices (expert), the library folder | No key; key refused; model gave no answer; cost not known for this model |
| Assistant drawer | Ask in your own words | On every screen. The conversation; plain answers; proposal cards with Apply, Edit and Dismiss, and Apply all | The cost so far (real, or "not known"), earlier answers and their cards, which were applied | Thinking, with Cancel (up to a minute); no answer, with Ask again; no key; a card that could not be expressed; a card edited inline; a card applied |

## Words

A third of today's words, each doing a job: the heading is the answer, labels are nouns, help waits behind an (i).

- **Length:** labels up to 3 words, sentences up to 12, one sentence visible per block.
- **Order:** the number first, then what it means, then what to do.
- **Never on screen:** KPI, seed, generation, instance, config, baseline, SQL (outside the expert box), confidence interval, p-value.

**One name per idea**

| Say | Meaning | Not |
| --- | --- | --- |
| study | One search for better settings or data, from set-up to answer | experiment, run |
| case | One input the program solves, such as one day's requests | instance, request file |
| held-back case | A case the search never sees, kept for the final check | test set |
| starting point | What is used today: the program's defaults or a settings file | baseline, seed |
| measure | A number that says how good a plan is (cost, km) | KPI, metric |
| goal | The measure to improve, and which way | objective |
| rule | A condition every combination or plan must keep | constraint, guardrail |
| combination | One set of values tried | candidate |
| round | A batch of combinations tried together | generation |
| final check | The comparison on held-back cases at the end | confirmation |
| chance | How likely it is that the new settings are really better: "about 19 in 20" | probability, confidence |
| worst case | The worst plausible outcome: "at worst 0.3 % worse" | lower bound |

**Before and after**

| Today | Rewritten |
| --- | --- |
| Probably better: 1.3 % lower real cost on 3 held-back cases, a chance of about 19 in 20; at worst 0.3 % worse than your starting point (PyVRP's own defaults). | Verdict mark: Use it. 1.3 % cheaper on 3 new cases · 19 in 20 · at worst 0.3 % worse |
| Not proven on 3 held-back cases: a chance of about 11 in 20 of a gain; at worst 24.9 % worse (somewhere between 24.9 % worse and 26.3 % better). | Verdict mark: Keep today's settings. 11 in 20 · at worst 24.9 % worse, and the interval strip |
| The app says use it when the chance of a gain is at least 9 in 10 and the worst plausible case is within 1 % of today. | The rule, one line under the mark, with an (i) |
| About 42 combinations for 8 things to tune is thin: the search may find a better combination, but it will likely miss the best one. | Thin plan: 5 tries per setting. [Tune the top 4] [Allow 1 h] |
| For each setting: Tune: the search tries values in its range. Keep fixed: always the value you give. Default: its value from your settings file, or else PyVRP's own. | A Tune · Fixed · Default switch, a tooltip per option |
| Waiting for 'Night run' to finish. | Queued · after Night run, on the time bar |

## Components and tokens

Name everything: the build turns each token into a CSS variable and each component into one small function, one to one.

**Tokens:** colour (surface layers, three ink levels, one accent, good / warning / critical with their tints, focus ring), type (5 or 6 sizes, tabular figures), spacing on a 4 px grid, radius, at most two elevations, motion (durations, easing, reduced motion). Light and dark for each.

| Component | Used on | States |
| --- | --- | --- |
| Verdict mark | Results, Home, Compare | Use it, keep today's settings, worse, nothing better, no verdict (one case); with the chance and the worst case |
| Interval strip | Results, Compare | Above 0, across 0, below 0, no range (one case); the worst case marked |
| Case dots | Results, Compare | Better, worse, the same, did not finish |
| Time bar with rounds | Running | Queued, starting, running, final check (adding runs), stopped, failed, continued |
| Best-so-far line | Running | Empty, rising, flat |
| Plan bar | Set up | Fits, thin (with fixes), impossible |
| Summary row with Change | Set up | Complete, something missing, open |
| Drawer | Set up, Results, the assistant | Open, closed, full screen on a phone |
| Setting row with a Tune · Fixed · Default switch | Set up drawer | Each mode, recommended, range edited |
| Number with unit | Set up | Normal, invalid, both decimal marks read |
| Kind-of-study choice | Start a study | Selected, hover, focus |
| Drop zone for cases, harnesses and study zips | Start a study, Home | Empty, dragging, reading, read, a file refused |
| Study card with a menu | Home | Draft, queued, running, continued, finished (by verdict), stopped, failed, archived; rename, duplicate, archive, delete, save as zip |
| Run list | Results | This study's past runs with their verdicts; the current one marked; continue with more time |
| Compare view | Home, Results | Two columns aligned by label; cases joined by name |
| Undo bar | Anywhere | After removing a case, measure, rule or study: one line and Undo, for ten seconds |
| Notice | Anywhere | Info, warning, error: one line and an action |
| Chat bubble and proposal card | Assistant | You, assistant (plain answer), thinking with Cancel, no answer; proposed, edited, applied, dismissed, not expressible; Apply all |
| Toast | Anywhere | Done, error |
| App bar with where you are | Every screen | Wide, phone |
| Locked page | The app opened without its key | One sentence: open the app from the address printed in the window it runs in |
| Empty state with the motif | Home, Running | First run, nothing yet |

## Constraints

- **Local and offline.** A Python process serves the page and a JSON API on 127.0.0.1. Nothing loads from the internet: fonts, icons and images ship with the app.
- **A plain web page.** HTML, CSS and JavaScript with no build step; a few static files are fine. No framework unless a tiny one earns its place.
- **Light and dark.** It follows the computer's setting; `?theme=light` or `?theme=dark` forces one.
- **Widths.** From 360 px to a wide desktop; the phone layout starts below 760 px.
- **Accessible.** WCAG 2.2 AA contrast (today's faint grey text fails it), everything reachable by keyboard, visible focus, screen-reader labels, reduced motion respected, the running page a live region.
- **Live, and slow in places.** The running page refreshes every 2.5 s; the preview takes about 10 s; the assistant up to a minute, in the background with Cancel. Each wait needs a calm state. The tab title and a browser notification say when a study is done.
- **Honest.** Nothing says "use it" before the final check does, and every estimate shows its range.

## Real data to design with

From a real 12-minute PyVRP study on eight days of delivery requests.

**Results**

- **Verdict:** use it. Chance about 19 in 20; at worst 0.3 % worse than PyVRP's own defaults.
- **Learned from 5 cases:** 3.2 % lower cost, one run each.
- **Final check on 3 held-back cases, 6 runs each (the check added three on its own):** 1.3 % lower on average; likely between 0.3 % worse and 2.8 % better.
- **Set-up:** 10 s per case, 12 min in total, 32 combinations tried, took 11 min; compared with PyVRP's own defaults.
- **What changed:** plans between penalty adjustments 500 → 71; highest penalty 100,000 → 7,259.29; share of plans that keep the rules 0.65 → 0.629; swap two tasks with one: on → off.
- **Other measures, held-back cases:** distance 677.1 → 689.0 km; routes 7.0 → 6.7; every plan kept every rule.

A second study for the *keep* state: 2 held-back cases, chance about 19 in 20 but at worst 2.7 % worse (likely between 2.7 % worse and 17.7 % better): the rule says keep today's settings.

**Cases, for the case dots** (cost in the request's currency)

| Case | Kind | Start | Best | Change |
| --- | --- | --- | --- | --- |
| 2026-03-05 | held back | 1,418.3 | 1,399.5 | 1.3 % lower |
| 2026-03-09 | held back | 1,892.8 | 1,880.5 | 0.6 % lower |
| 2026-03-10 | held back | 2,518.8 | 2,470.9 | 1.9 % lower |
| 2026-03-02 | learned from | 1,326.6 | 1,286.8 | 3.0 % lower |
| 2026-03-03 | learned from | 1,139.6 | 1,140.6 | 0.1 % higher |
| 2026-03-04 | learned from | 1,842.9 | 1,794.7 | 2.6 % lower |
| 2026-03-06 | learned from | 1,736.8 | 1,662.0 | 4.3 % lower |
| 2026-03-11 | learned from | 1,786.4 | 1,679.0 | 6.0 % lower |

**Running** (a data study, 3 minutes in): 3 min used, 12 min left, done around 04:52, round 1 of about 6. No combination has beaten the starting point yet; 7 tried, the closest within 1.6 %. Right now: round 2, 7 combinations on 5 cases, 12 of 35 runs done. A second study, "Cost rates per vehicle class", is queued after it.

**Set up:** 8 cases of 78 to 192 stops, 3 held back ("a fair answer for a clear, consistent gain"); 4 of 27 settings tuned; goal: cost as PyVRP counts it, lower is better; 20 s per case, 30 min in total; about 6 rounds and 42 combinations, final check on 3 held-back cases with 6 runs each (about 2 min), done around 17:40.

**Home:** three studies. "PyVRP settings for the March requests": finished, use it. "Cost rates per vehicle class": queued after "Night run". "Night run": running, 12 min left. One archived study, "First try", and one deleted an hour ago, "Test", with Restore.

**Assistant, on Results:** "Why is this not proven?" → "Only two cases were held back, so the range is wide: the worst case is 2.7 % worse, beyond the 1 % the rule allows. Hold back three or more next time." Cost of the answer: $0.04.

## What comes back

- The chosen design: screens as images and a link, the prototype.
- Tokens as a list: name, light value, dark value.
- Components with sizes and states.
- Assets: the mark, icons, fonts with their licences.

The build happens afterwards, in the app, not in Claude Design: tokens become CSS variables, each component one small function, the words follow the word rules, and everything the app can do today stays reachable. Screenshots in light, dark and at phone width are compared with the design screen by screen, and a fresh intern review must pass at 8 out of 10 with no blocker.
