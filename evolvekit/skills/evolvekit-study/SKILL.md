---
name: evolvekit-study
description: "Runs an evolvekit study from a problem description: asks only what the request did not say, makes the study with the evolvekit command line, shows the plan and its time, starts it, hands over the study's address in the app, and reports the final check's verdict word for word. Use when someone says 'use evolvekit', asks to improve, tune or find better settings or data for an application with evolvekit, or asks to start, watch or report on a study."
---

Read `references/evolvekit.md` first: its words, commands and rules hold here.
Its sections are "Words", "Where things live", "Commands", "The verdict document" and "Rules"; this file does not repeat them.
Placeholders, each with one meaning: NAME the study's name, SLUG its short name, PATH the application, T a template, H the held-back cases, N the combinations, R the rounds, K the settings tuned, HH:MM the time the plan is done, <url> the study's address, <name> the study it is queued behind, REPO the source folder of a code study, A and B branches of it, BRANCH the branch a pull request goes into.
Use the app's words with the engineer (study, case, held-back case, measure, goal, combination, round, final check).

## 1. Ground

- Run `evolvekit doctor`. Follow the `fix:` line of every ✗.
- The provider line matters only when the study turns AI help on (`budget.ai.enabled`, off unless the engineer asks for it). A provider ✗ alone does not stop you.
- Find the harness: `harness/harness.yaml` in the repository, or `harness/<id>/harness.yaml` when it holds several.
- No harness there: run `evolvekit harness list` and pick the one made for this application.
- None fits: follow the `evolvekit-harness` skill, then come back here.
- Read the harness's templates (`templates/*.yaml` in its folder) and its measures (labels and help) before the interview.

## 2. Interview

Ask only what the request did not say.
Use the engineer's words, not the keys. Go through this checklist against the request; each line says where the answer lands.

- [ ] Where is the application? → `--app PATH` on `study new`.
- [ ] What may change: settings, data? → `vary.settings` (`tune`, `{fixed: value}` or `default` per setting), `vary.data`.
- [ ] What stays fixed? → `{fixed: value}`; what the request does not name stays as the template has it.
- [ ] Where are the cases, which are representative, how many are held back? → `study cases --add … --hold-back H`; "the requests in cases/" means all of them.
- [ ] The measure and its direction? → `kpis` and `goal.levels`: the harness measure whose label or help says what the engineer said ("shorter tours" → the tour length, `lower`). When two fit, ask.
- [ ] How long per case? → `limits.time_per_case_s`; when the request does not say, keep the harness's default and name it in the plan line.
- [ ] How much time in total? → `budget.hours`, the minutes ÷ 60 to three decimals (ten minutes → `0.167`). Never chosen by you.
- [ ] The starting point: today's settings or a settings file? → today's settings are the harness's defaults (nothing to write); a settings file goes into `inputs` when the harness takes one.

A request that answers every question is complete: go on without asking.
One unanswered question stops you with that question, in one message.
Several unanswered: ask them all in that one message.
In a one-shot run, where no answer can come, say what is missing and end.
Decide nothing about the goal or the total time alone.
When you sum up the interview or the study, name the measure and its direction in the app's words. Say the direction as 'lower is the goal' or 'higher is the goal', never 'lower is better'.
Do not go on to step 3 until every question has an answer: with one missing, nothing is made — no `study new`, no file — and your whole message is that question.

## 3. Make the study

Only when the interview is complete: every line of the checklist answered, by the request or by the engineer.
Pick the template whose goal matches the measure and direction from the interview (`--template T`, T the file's name without `.yaml`).
With none fitting, leave out `--template`: the study starts blank.

```
evolvekit study new NAME --harness harness/ --template T --app PATH --json
evolvekit study cases SLUG --add "cases/*.json" --hold-back H
evolvekit study application SLUG PATH
```

- `--harness` takes the harness folder (`harness/`, or `harness/<id>/`) or the id from `evolvekit harness list`.
- The `--json` line gives `slug` and `root`. Use that slug as SLUG from here on.
- Run the third command only when `study new` said the application was not accepted (the study is made all the same; the message names its slug, and no `root` is printed: the folder is `studies/SLUG/` in the library home).
- Quote a pattern for `--add`: evolvekit expands it in any shell.
- Open `study.yaml` in `root` and write what the interview gave:
  - `vary`: the settings and data that may change; with a blank study, `vary.settings: recommended`.
  - `kpis` and `goal`: the measure by name and its direction.
  - `guardrails`: only when the request names a limit.
  - `limits`: the time per case.
  - `budget`: the hours. Leave `budget.ai.enabled` off unless the engineer asked for AI help.
- Measures come from the harness by name, `{from: harness}`, or from its templates; never write SQL for a measure.
- Run `evolvekit study check SLUG`. Exit 2: each line names a key of `study.yaml`; fix it and check again until exit 0.
- Run `evolvekit study preview SLUG`: one solve of the smallest case at the starting values. Its error, if any, is the next thing to fix.
- The cases are now in `cases/` and any settings file in `inputs/`; touch neither by hand.

## 4. Show the plan

- Run `evolvekit study compile SLUG`. Its line says the rounds, the combinations, the final check on the held-back cases and "done around HH:MM", and the run folder it compiled the plan into, `runs/<id>/evolvekit.yaml`.
- On a thin plan `study compile` exits 2 with `plan: … fewer than 3 rounds …`; the sentences after it are the ways out — relay them, change nothing in `study.yaml` yourself.
- Show that line to the engineer as it is.
- Put the plan line (rounds, combinations, held-back cases, done around) in your message before `study start`, whether or not you must wait for a yes.
- A plan that fits, for a request complete as first sent, goes on to step 5 without a further question; for a request completed by the engineer's answers, wait for a yes after the plan line.
- A plan with fewer than 3 rounds is thin: show its three ways out, start nothing, and wait for the engineer's choice; in a one-shot run, say so and end.
- The ways out are the ones the line names: fewer cases, a shorter time per case, more time in total. Change `study.yaml` only as the engineer chooses, then check and compile again.

## 5. Start and hand over

- When time has passed since step 4 (the start waited for a yes), compile again just before `study start`, and quote the new line.
- Run `evolvekit study start SLUG`. It says `started run …`, or `queued behind <name>` while another study runs.
- Run `evolvekit study open SLUG`. It prints the study's address in the app (starting the app when none runs) and opens it.
- Fill in one of these two sentences and say it, word for word apart from the blanks (N, R and HH:MM from step 4's line; `<name>` from `study start`; `<url>` from `study open`):
  - Your study is running: <url>. It tries about N combinations in R rounds and ends around HH:MM; the answer is on the Study page.
  - Your study is queued behind <name>: <url>. It starts when that study finishes; the answer is on the Study page.
- K is the number of entries under `problem.parameters` in the compiled `runs/<id>/evolvekit.yaml` (the settings tuned). When N is under 6 × K, add: "The app will call this plan thin: K settings in N combinations; more time or fewer settings would sharpen it."
- Until the verdict exists, write no "better", "improved", "faster" or "cheaper", and no percentage.
- Then end, unless the engineer asked you to wait.
- Waiting is `evolvekit study wait SLUG --timeout 600`, run again while it exits 4 (no single command may run for hours), until it exits 0 (done) or 1 (stopped or failed).

## 6. Report the verdict

- Run `evolvekit study verdict SLUG`. It prints the verdict's line and its advice.
- Run `evolvekit study report SLUG --markdown > evolvekit-report.md` and name the file. Never paste the report or its numbers.
  In Windows PowerShell, write it with `evolvekit study report SLUG --markdown` piped to `Out-File -Encoding utf8 evolvekit-report.md`, since `>` writes UTF-16 there.
- Report only the verdict's line and its advice, word for word, and add no sentence that claims more.
- The gain on the cases it learned from is said only when asked, and named so.
- An interpretation, if any, is one sentence below the line and the advice, marked as yours, and claims nothing the line does not.
- A later request to report on the study starts here: `evolvekit study status SLUG` says whether it has ended. When it has not, say where it is and hand over as in step 5.

## Code studies

When the request asks to change the application's code (its source, or a file it reads such as a prompt), steps 1–6 hold with these changes.

- Pick a template whose `vary` has `code` (demo-tour's `faster`); the interview's line on settings and data does not apply, since a study changes code or settings, not both.
- After `study new`, say where the source is, with the brief in the engineer's own words: `evolvekit study code SLUG --repo REPO --brief "…"`.
  Add `--from working` only when the engineer says to start from their uncommitted files; the default is the commit checked out.
  For a program harness it also sets the application when none is set.
- A code study searches with AI help: write `budget.ai.enabled: true` and `budget.ai.max_usd` (the dollars the request allows) in `study.yaml`.
  A request that names no money gets one more question: the most the AI help may spend. Never chosen by you.
  With a provider whose coding agent edits files, that agent runs shell commands in a git worktree of REPO: git there reaches the engineer's branches and remotes (the commands that write them are denied, not airtight), and the repository's own agent settings and hooks apply. Its limits per call are `models.strong.options.act_timeout_s` (1800 s) and `act_max_usd` ($0.50). Tell the engineer this with the plan line when they have not heard it.
- Then check, preview, show the plan line and start as in steps 3–5.
- "Compare these branches": run `evolvekit study bakeoff SLUG --ref A --ref B` (one `--ref` per branch, in the engineer's order). It needs the source and held-back cases, not AI help: a `budget.ai` line alone from `study check` does not stop it.
  It exits 2 with a sentence when the study or a branch is refused (files outside the open files, a change that does not apply); relay it and stop.
  There is no plan line: say how many branches it compares, run `evolvekit study open SLUG`, give the address, and wait as in step 5 when asked.
- A pull request: run `evolvekit study pr SLUG` first (add `--base BRANCH` when the engineer names the branch it goes into). It prints the verdict's line and what it would do, and does nothing.
  Show those lines as they are. Only after the engineer's yes run `evolvekit study pr SLUG --yes`; a yes in the request counts once the lines are shown.
  It exits 1 when part of it failed: say the sentence it printed, and what it says was made.
- Report as in step 6: the verdict's line and its advice, and nothing more; after `--yes`, also the PR's address it printed.
- Never push, and never open a pull request, any other way.

## When it goes wrong

- No harness for the application: follow the `evolvekit-harness` skill, then return to step 1.
- `evolvekit study check SLUG` exits 2: fix the keys of `study.yaml` it names, and check again.
- `study compile` exits 2 with `plan: … fewer than 3 rounds …`: a thin plan, not a fault. Relay its ways out (step 4), change nothing yourself, and wait.
- A ✗ from `evolvekit doctor`: run its `fix:` line; a provider ✗ matters only with AI help on.
- `study start` says the study is already running: `evolvekit study status SLUG` says where it is; hand over as in step 5.
- The run failed (`study wait` exits 1): say the error line from `evolvekit study status SLUG` and point to the log `runs/<id>/job.log` in the study's folder, and stop. Claim nothing about the result.
