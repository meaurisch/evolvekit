# App redesign, stage 4: the build — implementation plan

> **For agentic workers:** steps use checkbox (`- [ ]`) syntax for tracking. Execute inline (superpowers:executing-plans); one helper agent at a time at most.

**Goal:** The evolvekit app looks and behaves like the approved Claude Design (rounds 11 and 12, 2026-11-06): Dusk tokens, Pepper as logo, companion and assistant, four pages (Home, Start a study, Set up, Study) plus Compare, Settings and the Ask Pepper drawer, and GitHub Copilot CLI as a fourth AI service. Everything the app can do today stays reachable.

**Source of truth:** the hand-over pack `r12-handover.md` and the generators `r11-generator/`, `r12-generator/` in the side-session notes of `local_ef065d7e-…`; the canvas https://claude.ai/artifact/5YRCM131jKk4F2D93PbYPq, pages r11 and r12 (read only). Where the r11 Study sheets and the pack differ, the pack wins (range strips mark both ends).

**Architecture:** The page stays plain HTML, CSS and JavaScript with no build step, served by the existing `AppServer`. `static/app.html` becomes a small shell that loads a handful of static files: `css/tokens.css` (every token as a CSS variable, light and dark), `css/app.css`, `js/parts.js` (one function per component, named as in the pack), `js/pepper.js` (Pepper's seven phases and the mark, exact SVG exported from the generator), `js/plots.js` (triesPlot, casesView, settingsPanel, intervalStrip, runTimeline), one file per page under `js/pages/`, `js/drawer.js` (Ask Pepper), `js/app.js` (router, API client with the token, polling, theme). Fonts ship as woff2 under `static/fonts/` with their OFL licences. The server gains one read-only route for these files; the JSON API gains what the new Study views need and nothing else changes shape.

**Tech stack:** Python ≥ 3.11 standard library; pytest with real HTTP against `AppServer(port=0)`; fontTools + brotli (already installed) to make the woff2 files once; headless Edge for the screenshots (`docs/app/make_screenshots.py`, updated).

## Global constraints

- Branch `claude/app-redesign-build-17432c`, cut from `app/hardening` (PR #68, the top of the stacked app PRs). One PR, one commit per task below. `python tasks.py check` green before the PR; never alongside demo studies.
- Nothing from the internet at run time. No framework, no bundler.
- Word rules on screen: study, case, held-back case, starting point, measure, goal, rule, combination, round, final check, chance, worst case. Never: KPI, seed, generation, instance, config, baseline, SQL (outside the expert box), confidence interval, p-value. Labels ≤ 3 words, sentences ≤ 12. A test enforces the banned words over the static files.
- Honest: nothing says "use it" before the final check does; every estimate shows its range; ranges mark both ends, "worst" and "best", same weight, plus the dashed 1 % limit.
- WCAG 2.2 AA in both themes, keyboard reachable, visible focus, `prefers-reduced-motion`, the Study page's status strip an `aria-live` region.
- What to tune and the goal come from the harness only (`settings` with label, group, help, explain, recommended; `kpis`, `kpi_templates`, goal levels). No PyVRP-specific code in the page.

## Where today's features go

The old page is a seven-step wizard plus Running, Results, Compare and Settings (full inventory: `old-page-inventory.md` in the shared notes folder `local_83b68dce-…`). In the new app:

| Today | New place |
|---|---|
| Home: cards, ⋯ menu, compare row, archived, trash, harness install, study zip drop, library path | Home (studyCard, studyMenu, dropZone; "Applications you can tune", trash and library path as one-click sections under the cards) |
| `#/new` + steps 1–3 (question, application, cases, split) | Start a study (kindChoice = harness + template, application detect / browse / paste, dropZone + caseStrip, held-back count) |
| Steps 4–7 (what may change, goal, limits and budget, review and start) | Set up, option A: summaries What to tune, Goal, Time, each with Change → drawer; planBar with fixes; test run; Start / Save for later; the "more" row opens drawers for held-back cases, runs per case, rules, measures (incl. data changes, own measures, the expert SQL box, guardrails), AI help, and the expert plan numbers |
| Running + Results | Study: the status strip becomes the verdict; Tries / Cases / Settings; final check, recheck, continue, resume, stop / dequeue, Hand it over (downloads, report, summary, settings file), next study, runs list, dashboard link |
| Compare, Settings, assistant drawer | Compare, Settings, Ask Pepper (docked drawer on every screen) |

Old `#/study/<slug>/<n>`, `…/running` and `…/results` hashes redirect to the new routes so bookmarks keep working.

## Review focus

1. The static route serves only files under `static/`, refuses `..`, absolute and encoded escapes, and sends the right content types with `nosniff`. Test in Task 1.
2. The page never shows "use it" (text or bright butterfly) before the final check has decided. Test in Task 6 (page logic is driven by the server's verdict field, asserted on the API side).
3. Keep reads "chance under 9 in 10" — no rounding that reads like a pass. Test in Task 6.
4. The Copilot provider never lets the CLI use tools, passes long prompts safely on Windows (`copilot.cmd` → cmd.exe 8 191-character limit), and maps "not signed in" and "Access denied by policy settings" to the two Settings states. Tests in Task 9.
5. Everything the old page could do is reachable: a checklist test maps each API route the old page used to the new page's code (Task 10).

---

## Task 1: tokens, fonts and the static route

**Files:**
- Create: `evolvekit/app/static/css/tokens.css` (from `tok.py`: 22 colour tokens light/dark, type, space, radius, elevation, motion with reduced-motion values), `evolvekit/app/static/fonts/{YoungSerif-Regular,Overpass[wght],OverpassMono[wght]}.woff2` + `OFL-YoungSerif.txt`, `OFL-Overpass.txt` (Latin + Latin-1 + punctuation subset via fontTools)
- Modify: `evolvekit/app/server.py` (`GET /static/<path>` without the token: these are public files; an allow-list of extensions → content types; `Cache-Control: no-store` stays), `pyproject.toml` / `MANIFEST.in` so the files ship with the package
- Test: `tests/test_app_server.py` (served, content types, traversal refused), `tests/test_app_page.py` (new: every token of the pack exists in `tokens.css` for both themes; theme selection by `prefers-color-scheme` and `?theme=`)

## Task 2: the shell and the components

**Files:**
- Create: `static/js/app.js` (router on `location.hash`, `api()` with `X-Evolvekit-Token` and the locked page, polling, theme, toasts, focus management), `static/js/parts.js` (appBar, button, notice, toast, undoBar, verdictMark, icon set, textInput, numberUnit, viewTabs, chosenChip, hoverCard, summaryRow, planBar, studyCard, studyMenu, dropZone, kindChoice, caseStrip, settingRow, infoTip, serviceChoice, compareView, casePair, runList, lockedPage), `static/js/pepper.js` (pepper(phase), mark(size); SVG exported once from the generator), `static/css/app.css`
- Rewrite: `static/app.html` (shell only)
- Test: `tests/test_app_page.py` (every component named in the pack exists as a function; banned words absent from the static files; every page file parses — `node --check` when Node is on PATH, skipped otherwise)

## Task 3: Pepper the companion and Ask Pepper

**Files:**
- Create: `static/js/drawer.js` (assistantDrawer, chatBubble, proposalCard, thinking, no answer, no key; docked 420 px on the right, full screen on phones with Back), companion in `parts.js` (92 px / 64 px, draggable, click = update bubble, right-click / Shift+F10 / long-press = menu: Ask a question…, What's happening now?, Move to the corner, Hide until it ends)
- Uses: the existing `POST/GET /api/studies/{slug}/assistant`, `/assistant/cancel`, `/assistant/cards`, `/cards`
- Phase rule: egg (draft), caterpillar (first half of the search), cocoon / cocoon-late (second half and the final check), cocoon-asleep (queued, stopped), butterfly (use it), butterfly-dull (keep, worse, no answer); a stopped check stays in the cocoon. The phase comes from one function, `pepperPhase(state)`, tested through the API's state fields.

## Task 4: Home and Start a study

- Home: studyCard grid (draft, queued, running, continued, finished by verdict, stopped, failed, archived), studyMenu (rename, duplicate, archive, delete with Undo, export the study), drop zone for a study zip, Active / Archived, the deleted-with-Undo bar, first run.
- Start a study: kindChoice, harness pick, application (detect, browse, recent), dropZone + caseStrip (learns from · held back · refused), name, notices for no application, file refused, too few cases, too few held back.
- Uses existing routes only (`/api/home`, `/api/harnesses`, `/api/detect`, `/api/pick-file`, `/api/studies`, `…/application`, `…/cases/{name}`, `…/samples`, `…/import`, `…/inspect`, `…/split`, trash restore, duplicate, delete, archive).

## Task 5: Set up (option A)

- One screen, three summaries with Change: Cases, What to tune, Goal; then the planBar (fits · thin with fixes · impossible) and the time inputs; test run; Start.
- tuneDrawer: the harness's settings grouped and ranked, each settingRow with Tune · Fixed · Default and the 44 px (i) right after the name → infoTip with `help`, "More detail" for `explain`. The "Tune the top 9" fix uses `api.plan`'s keep count and the harness's `recommended` ranks.
- goalPicker: the harness's `kpis` and `kpi_templates` (fill-in-the-blank, choices from `GET …/choices`), optional second goal within 1 % (goal levels); `POST …/kpis/try` for the preview line.
- Missing, thin, impossible, test failed, removed with Undo.
- If `api.plan` or `describe_harness` lacks a field the drawer needs (group order, recommended rank, template blanks), add it there with a test.

## Task 6: Study

- statusStrip (8 states) that becomes the verdict; runTimeline; intervalStrip with both ends; viewTabs Tries · Cases · Settings; triesPlot (best-so-far line over full looks only, quick looks as rings), casesView, settingsPanel; hoverCard on hover/focus/tap; one chosenChip carried across the three views; Hand it over (save the settings file, copy); Stop, Final check, Continue with more time; runList one click away; dashboard link per run.
- Server: a new read-only `GET /api/studies/{slug}/tries` digesting the run's `events.jsonl`/`runs.jsonl` the way `r11-generator/data.py` does: every combination (round, start, end, % against the starting point, full or quick look, failed), per-case results, round starts, the best-so-far steps over full looks, and the final check's progress run by run. Cached on the run stamp like the status document. Tests on a small synthetic run.
- Wording, server side: keep says "chance under 9 in 10" (today `_chance_words` rounds 0.897 to "about 18 in 20"). Test.
- The 15 states of round 11: searching, use it, keep, starting, queued, no gain, adding runs, worse, nothing, failed, stopped, continued, computer busy, single case, check stopped.

## Task 7: Compare and Settings

- compareView + casePair: same goal, different goals, one side unchecked (`GET /api/compare`).
- Settings: serviceChoice with four cards (OpenRouter, Azure OpenAI, Claude Code, GitHub Copilot CLI), key fields, models, prices, Check; states key set, no key, refused, no answer, cost not known, Copilot signed in / not signed in / blocked.

## Task 8: phone and accessibility pass

- Below 760 px: menu button, one column, plots 250 px, t01 names, hover cards on tap with Close, drawers full screen with Back, Pepper 64 px bottom right, toasts 96 px up. 44 px targets.
- Keyboard walk of every page; focus order; `aria-live` on the strip; reduced motion; contrast spot-check with the token pairs.

## Task 9: the GitHub Copilot CLI provider

**Files:**
- Create: `evolvekit/providers/copilot_cli.py` (`CopilotCliProvider`, name `copilot-cli`): one `copilot -p` per completion, `--output-format json` (JSONL; tolerant reader) or `-s` text, `--model` when set, `--no-ask-user`, every tool denied; no key; `COPILOT_CLI_PATH` override; on Windows the npm `copilot.cmd` shim is resolved to `node <entry>.js` so long prompts do not hit cmd.exe's limit; errors classified as `not_signed_in` (tell them to run `copilot`, then `/login`) and `policy_blocked` ("Access denied by policy settings"); usage reported as Copilot requests, cost `0` USD with `requests` counted.
- Modify: `evolvekit/config.py` `PROVIDER_NAMES` (+ no embeddings, like claude-cli), `evolvekit/providers/__init__.py`, `evolvekit/app/store.py` `PROVIDERS`/`KEY_NAMES`, `evolvekit/app/assistant.py` (check: signed in / not signed in / blocked), `evolvekit/app/jobs.py` `REPORTS_COST` (the dollar cap holds trivially; the page shows requests)
- Test: `tests/test_providers.py` (fake runner: argv, JSONL parse, the two failure texts, long prompt), `tests/test_config.py`, `tests/test_app_store.py`, `tests/test_app_assistant.py`

## Task 10: everything still reachable, screenshots, docs, gate, PR

- A test lists every API route and asserts the new page's code calls each one the old page did (or says why not).
- `docs/app/make_screenshots.py` takes 1280 and 390 px, light and dark, of every screen from a library home built from the real runs (`pyvrp-hard-1`, `pyvrp-hard-llm`, `cli-solver`); compare by eye with the r11/r12 boards, fix differences.
- `docs/app.md` updated (screens, Copilot).
- An intern review (fresh subagent) at 8/10 with no blocker; fixes.
- `python tasks.py check`; push; PR.
