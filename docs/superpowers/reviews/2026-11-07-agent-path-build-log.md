# Agent path — build log

The running log of the session that builds the five projects of
[the agent-path design](../specs/2026-11-07-agent-path-design.md): one entry
per project with the decisions taken, the gate counts, the persona scores and
the PR link. The session prompt is
[docs/superpowers/design/2026-11-07-agent-path-session-prompt.md](../design/2026-11-07-agent-path-session-prompt.md).

## Step 0 — a clean start (2026-11-07)

- `master` at 3c5fb8e, clean, equal to `origin/master`; `user.email` and
  `user.name` as required.
- `python tasks.py check` on master: **1633 passed, 2 skipped** in 13:44.
- Decision: on this machine "python" is the project's `.venv`
  (`.venv/Scripts/python.exe`, Python 3.13, evolvekit installed editable,
  ruff 0.16.8, pytest 9.1.1). The global Python 3.12 has no ruff and no
  PyVRP, so a gate run with it fails lint and the PyVRP tests; every gate
  and every test in this log ran with the venv.

## Project 1 — One world

Branch `agent/1-one-world` from master 3c5fb8e. Plan:
[2026-11-07-agent-1-one-world.md](../plans/2026-11-07-agent-1-one-world.md)
(already at step level; used as it is).

### Decisions

Taken where the design or the plan was silent or where the two disagreed;
each with what it costs if wrong.

- **Liveness of `app.json` is one-sided** (`born <= started_at + 120 s`),
  the exact rule of `jobs.run_alive`, not "within 120 s". Cost: an app
  whose process started long before it bound counts as alive (harmless).
- **A run stopped during the search has decision `pending`, not
  `stopped`**: the design's table makes `stopped` "the check was stopped"
  and `pending` "no final check yet". Cost: a stopped search reads as
  pending in the CLI.
- **With goal levels the top-level numbers are the summary's** (what the
  page shows) and `measure` names the deciding level; `levels` carries each
  level.
- **A broken harness folder through the home route exits 1**:
  `Home.create_study` wraps the `HarnessError` in an `AppError`, which the
  design says is exit 1; exit 2 holds for `--dir` and for
  `preview`/`compile`/`run`. Two older tests in `tests/test_harness_cli.py`
  changed from exit 1 to 2 for the same rule. Cost: one exit code differs
  between the two routes.
- **A queued run is claimed across processes** by an exclusive create of the
  pending `launch.json`; `queued.json` is removed at claim time and put back
  only when the launcher clearly started nothing (an `AppError`); a present
  `job.json` always wins over `queued.json`; `stop` on a queued run claims it
  the same way. The plan's `_launch_guard` is per process and could not
  stop the app's ticker and `study wait` from launching the same run twice.
  Cost: a process dying within a second of claiming leaves the run "failed"
  after 120 s instead of queued.
- **The report uses the measure's direction words** ("between 3.0 % and
  5.4 % lower", "toward the goal") so "better" appears only in the verdict's
  own line and advice: the plan's test forbids "better" elsewhere while the
  design's example interval uses it; the test wins, because the verdict is
  the only claim. Large numbers are written as people write them
  (1,248,920), never in scientific notation.
- **`doctor` prints the app's URL without its `?token=` query**:
  diagnostics never print a secret; `study open` prints the full URL. With
  `--app`, a harness that refuses the application is ✓ "not for this
  application: …" when another harness accepts it; when none accepts, every
  probe line is ✗ with the harness's own application help as the fix.
  `doctor` never crashes: an exception in a check becomes a ✗ line.
- **`app.json` is private to the user** (0600 on POSIX, like `.env`): the
  token lives in it.
- **Skill files are pinned to LF in git** (`.gitattributes`), and a skill
  whose frontmatter is missing, multi-line or misnamed refuses to install
  with the skill's name in the error, before anything is written.
- **Exit 130 on Ctrl-C** in `wait`/`watch` stays (the conventional code).
- **Engine words in the engine's own commands** (`preflight`, `confirm`,
  `run`) and in the README's engine sections are outside this project; the
  vocabulary rule governs the app's commands and the new ones.
- **Six fixes from driving the commands for real** (found while writing the
  docs): a compiled-but-never-started run folder is not a run (it read as
  "failed"); `study new --dir --app` sets the application; the CLI advances
  the study's step as the app's wizard would; `study check` says "ready";
  `--add` expands globs for Windows shells; piped output is UTF-8 so ✓ and
  · survive a coding agent's capture.
- On this machine the subagents' commits carried their own model's
  attribution line; each was amended to the required line before review
  (unpushed).

### Gate

`python tasks.py check` on `agent/1-one-world`, nothing else running:

| When | Commit | Result |
|---|---|---|
| after Tasks 1–11b, before the whole-branch review's fixes | b82bf19 | 1753 passed, 3 skipped in 14:47 |
| after the whole-branch review's fix wave | a5b2815 | 1761 passed, 4 skipped in 14:49 |
| after the persona gate's round-2 fixes | 2dc344d | 1786 passed, 4 skipped in 14:34 |
| after their review's fix round (the final tree) | e323086 | 1787 passed, 4 skipped in 15:17 |
| after the persona gate's round-3 fixes | 3774d11 | 1797 passed, 4 skipped in 14:32 |
| the PR's tree (one guard in `study wait` after) | e8ab740 | **1797 passed, 4 skipped** in 15:02 |

(The skipped tests are the POSIX file-mode tests, which run in CI on Linux,
and the macOS skips.)

### Persona gate

An engineer who reads the README only, from a terminal alone, then the app
(design §8, project 1); three rounds, each a fresh reviewer and a fresh
library home whose path has a space; the rounds and every friction are in
[2026-11-07-agent-path-gate-log.md](2026-11-07-agent-path-gate-log.md).

| Round | Score | Blockers | What changed after it |
|---|---|---|---|
| 1 | 8/10 | none | the round in the state line; one figure in verdict and report; "Keep today's settings" in every non-use advice; `next:` hints; `plan:` in the README |
| 2 | 8/10 | none | the Study page's headline and advice are the verdict's own line and advice; the held-back table and the chosen combination use the document's figures; the result before the hint; `starting round 1 of M`; `study check` echoes the plan |
| 3 | 9/10 | none | the Home card speaks the verdict's words; `wait` shows progress on stderr; the single-case line leads with its decision; the report names the study folder |

The gate passed at round 1 and held; the loop stopped after round 3's
fixes. Final score **9/10, no blocker**.

### PR

[meaurisch/evolvekit#72](https://github.com/meaurisch/evolvekit/pull/72) — `agent/1-one-world` against `master`, 32 commits, opened 2026-11-07; CI on Linux green (4 checks, after the one post-gate fix commit b069b8a cherry-picked from project 2). **PASS** (every task built and reviewed; gate 1797 passed, 4 skipped; persona 9/10, no blocker).

## Project 2 — Skills and the agent gate

Branch `agent/2-skills` from `agent/1-one-world` (e2d5d8e). Plan:
[2026-11-07-agent-2-skills.md](../plans/2026-11-07-agent-2-skills.md),
expanded to step level on this branch (838 lines, seven tasks), then the
builder check by a fresh reviewer; the choices are in the plan's
"Decisions" section.

### Decisions

From the plan's expansion and the builder check (the plan's "Decisions"
section holds all 21 with their reasons); the ones a reader should know:

- **The per-case time may default to the harness's** when the request does
  not give one; only the goal and the total time are never chosen by the
  skill (the design's own gate sentence gives no per-case time).
- **The report is written to a file, never pasted** into the agent's answer
  (`study report SLUG --markdown > evolvekit-report.md`); the answer is the
  verdict's line and advice. Pasting would put other percentages into the
  answer.
- **The gate judges percentages, not words,** in the second answer: the
  verdict's `advice` carries the decision's own words.
- **The fixture carries the tool's own settings** (`.claude/settings.json`
  with the allowed tools and the library home as an additional directory)
  and `--fixture` prints the three lines to set (`EVOLVEKIT_HOME`, a PATH
  with the venv first, `cd`), so the persona's plain `claude -p` behaves as
  the gate's run does.
- **"Thin" is what `study compile` blocks on** (fewer than 3 rounds); a
  plan with 3+ rounds goes ahead and the hand-over names the app's thin
  warning when there are fewer than 6 combinations per setting tuned.
- **Templates come from the harness folder** (`harness/<id>/templates/`, or
  the study's pinned copy); with none fitting, the skill writes `vary`,
  `kpis` (`{from: harness}`) and `goal` by hand, never SQL.
- **The harness skill's smoke test uses the template the skill wrote.**
- **`copilot` is not installed on this machine:** the gate records "not
  installed" for it and runs the tool checks with `claude` only.
- **The shared reference is copied at install only**, as project 1 built
  it; no build-time copy into each skill's `references/`.

### Gate

`python tasks.py check` on `agent/2-skills`, nothing else running:

| When | Commit | Result |
|---|---|---|
| after Tasks 1–7 and the persona rounds' fixes | 8e85214 | 1856 passed, 4 skipped in 16:21 |
| the PR's tree, after the whole-branch review's fixes and the gate script's last runs | a40fc6e | **1864 passed, 4 skipped** in 16:06 |

### Persona gate

Design §4.4 and §8 for project 2, in
[2026-11-07-engineer-gate-log.md](2026-11-07-engineer-gate-log.md):

- **The gate script**, `python tests/agent_gate.py --tool claude`, on the
  shipped tree: the complete sentence 6/6 PASS, the sentence without the
  total time 2/2 PASS. Earlier runs caught, and the fixes closed: the agent
  making the study before asking the missing question; its interview
  summary saying "lower is better" before any verdict; the check itself
  counting a figure the verdict's own line gives as another claim. One run
  stopped on this account's usage limit for the tool's default model and is
  logged as invalid; the later runs set `ANTHROPIC_MODEL=claude-opus-5-5`. Copilot CLI is not installed on this machine;
  the gate was not run with it.
- **Round A** (the study skill, an engineer through their coding agent):
  **8/10, no blocker** at round 1. Found a project-1 regression (a literal
  `&#39;` in the Study page's verdict, fixed on project 1's branch as well)
  and one wording fix (show the plan line before `study start`).
- **Round B** (the harness skill, an application evolvekit does not know):
  7/10, 7/10, then **8/10, no blocker** at round 3. The fixes between
  rounds: the two questions only the engineer can answer come first, with
  nothing written until they are answered; write only what the interview
  named; `harness check` always with `--app`; the smoke test is the agent's
  to run and names its values; samples are never the engineer's own files.
  The gate's no-harness fixture had to become a program evolvekit does not
  know (the example solver with another name and version, a scrubbed
  docstring, neutral request names), since the first two rounds tested
  adapting the demo instead of writing a harness.

### PR

[meaurisch/evolvekit#73](https://github.com/meaurisch/evolvekit/pull/73) — `agent/2-skills` against `master`, stacked on #72, opened 2026-11-07. **PASS** (every task built and reviewed; gate 1864 passed, 4 skipped; gate script 6/6 and 2/2 on the shipped tree; persona A 8/10, B 8/10 at round 3, no blocker).

## Project 3 — Code studies

Branch `agent/3-code` from `agent/2-skills` (9caaa61). Plan:
[2026-11-07-agent-3-code.md](../plans/2026-11-07-agent-3-code.md), expanded
to step level on this branch (1800 lines, 13 tasks), then the builder
check by a fresh reviewer; the choices are in the plan's "Decisions" (D1–D23
by the planner, 24–40 by the controller). The build runs the bake-off
milestone first (Tasks 1, 2, 3, 4, 6, 7, 8), then mutation (5, 9), then
10–13.

### Decisions

The ones a reader should know (all in the plan with their reasons):

- **Code alone in this project.** §5.2 lets a study vary code together with
  settings, but nothing says how one candidate carries both; `study check`
  refuses the mix with a sentence. A recorded deviation, for the owner.
- **The bake-off's diff is the branch's own change** (`git diff
  base...ref`, from the merge-base), not `base..ref`, so a branch cut from
  an older base is not measured as reverting the base's newer commits. A
  recorded deviation from §5.5's wording.
- **A bake-off chooses, but never says "the best branch"**: the chosen
  candidate (largest mean among those whose own verdict is `use`) carries
  the top line, prefixed "Of N branches, `<ref>`: "; with none `use`, the
  verdict keeps today's; the report says how many branches were compared.
- **A bake-off branch that touches files outside `open` is refused**, with
  how to include them.
- **"One combination at a time" is a passing note** in `harness check`, not
  a warning, so the check can still end at exit 0.
- **The shared interpreter is put back** after any build into the
  engineer's Python; the build cache is in memory only.
- **`study pr` after `from: working`** branches from `HEAD` with the
  candidate's diff applied; it does not refuse on a non-`use` verdict (the
  verdict's line comes first and `--yes` is needed).
- **An agent call's time is bounded by the study's time left**, and a Stop
  ends a running call; the brief is a file named in a short prompt.
- **Every agent call is billed**: its cap is the smaller of the budget left
  and `act_max_usd` ($0.50); a call that reports no cost is charged its cap;
  the token cap counts uncached input and output.
- **Files the agent adds outside the open files are dropped** from the
  change and named; an edit of any other tracked file still refuses it.
- **The coding-agent operator runs with claude-cli only** for now. Copilot's
  agent is not verified against a real Copilot CLI, so a code study on
  copilot-cli gets the diff operator alone, and a config naming the agent
  with it is refused. The agent's environment carries no git-host token.
- **No candidate tree stays**: each goes when its evaluation ends, the
  best's included (a recorded deviation from §5.3, which keeps the best's;
  `study export --diff` and `study pr` rebuild it from the recorded diff).
  The base snapshot is one ref per commit, `refs/evolvekit/base-<commit>`,
  documented with how to delete it.
- **A hand-written config must pin its base** (`base: working` is refused;
  studies pin it for you).

The final whole-branch review (most capable model): with fixes, 0 Critical,
2 Important (the Copilot agent had no git denials and kept git-host tokens;
the README's agent paragraph predated the billing fix), 7 minors. One fix
wave took them all, plus two deferred minors; its re-review found nothing
open.

### Gate

| Commit | Command | Result |
|---|---|---|
| b8aae3b | `python tasks.py check`, nothing else running | exit 0: **2069 passed, 4 skipped** in 19:43 |
| dd58f55 | `python tasks.py check`, nothing else running (after the persona gate's fixes) | exit 0: **2159 passed, 4 skipped** in 21:41 |

### Persona gate

The engineer of design §8 asks a fresh, isolated coding agent, one sentence each, to improve the solver's code,
to compare two prepared branches against `main`, and to open a PR if a verdict says to use a change. Materials
rebuilt from nothing each round. Log: [2026-11-07-code-gate-log.md](2026-11-07-code-gate-log.md).

| Round | Code | Score | Blocker | What it found |
|---|---|---|---|---|
| 1 | b8aae3b | 5/10 | yes | the bake-off ran a branch with its changed default overridden by the harness's flags and called it "not proven"; the plan ignored the AI's time; two "best so far" figures; stale Study page |
| 2 | 808e305 | 7/10 | none | no warning before the run that two held-back cases rarely reach "use"; AI spend not shown; the no-difference hint blamed a fixed setting |
| 3 | f6c8d5b | 7/10 | none | the recorded change was a whole-file line-ending rewrite (+151 -124 against the real +35 -8); the AI spend passed its cap ($1.03 of $1.00); a later run hid an earlier "use" |
| 4 | 52d7649 | **8/10** | none | **pass**; "about 20 in 20" sounds certain; compile-only run folders; a stale redraw |

The fixes, in short: in a code study only the settings the study fixes reach the program; a change that makes no
difference in any run is said so, with no figures; the plan counts the AI's time and re-estimates live; a note
before the run when the held-back cases are too few; the AI spend shown and kept within its cap; the recorded
change follows the repository's line-ending rules, so the review surface is the commit; an earlier "use" stays
visible; no "better" over unchecked figures anywhere. Each wave had its own review; all approved.

### PR

[meaurisch/evolvekit#74](https://github.com/meaurisch/evolvekit/pull/74), stacked on #73; opened 2026-11-08 from `agent/3-code` (dd58f55 plus the logs).


## Project 4 — Studies over time

Branch `agent/4-over-time` from `agent/3-code`. Plan:
[2026-11-07-agent-4-over-time.md](../plans/2026-11-07-agent-4-over-time.md), expanded to step level on this
branch by a planner (1486 lines, ten tasks), then the builder check by a fresh reviewer; its six questions and
the planner's two were ruled and folded in as D46–D52. Tasks run strictly in order (shared files everywhere),
except where two touched disjoint files and ran side by side.

### Decisions

The ones a reader should know (all in the plan with their reasons, the rest in the SDD ledger):

- **A campaign's `budget.hours` is its whole search time**; a window only decides when it runs. A finished plan is
  continued for the smaller of the window's time left and the campaign's search time left (D5).
- **The rehearsal clock**: `evolvekit app --service --clock <time> --clock-speed <n>` runs the scheduler on a
  simulated clock, written to the home and shown as a warning on Home, so a campaign can be watched in minutes.
  `ticked_at` and every window judgement use that clock.
- **A person's session is not a scheduled one**: `study start --now`, Carry on, Continue and Check again on an
  armed campaign mark that session by hand, and the window never stops it; a person's stop pauses the campaign.
- **Nothing stays stuck**: a failed session is resumed in the next window, three failed sessions in a row pause
  the campaign with a sentence; a stopped continuation goes on while campaign time is left and is checked again
  when it is used up; a finished campaign started again begins a new one.
- **The missed window is kept**: a weekend the app slept through is said on Home until the next window starts.
- **Keep-awake**: a scheduled session keeps a Windows computer awake while inside its window (released at the
  run's end; `schedule.keep_awake: false` turns it off).
- **The logon task is never created on this machine** (it is persistent system configuration the owner has not
  approved): `app install-startup` is tested with a patched `schtasks`, and is unverified against a real logon.
- **The service never opens a console window**: every console program the app or a run starts gets
  `CREATE_NO_WINDOW` through one helper; `app.log` is private and never holds the app's key.
- **A prior's weight starts at 0.5 and halves per study since the winner**; a weight of 1 is refused, so a prior
  never excludes any value in range; a study without priors draws exactly as before (pinned against the old code).
- **Features never read a private column** (refused when the harness loads); tags win over features and are
  folded when their folder names would collide.

The final whole-branch review (most capable model): with fixes, 0 Critical, 2 Important (arming a campaign over a
running run let the next tick outside a window stop it; a run that could not go on left its campaign armed for ever),
3 minors. One fix wave took them; its re-review found nothing open.

### Gate

| Commit | Command | Result |
|---|---|---|
| dde6ff9 | `python tasks.py check`, nothing else running | exit 0: **2452 passed, 5 skipped** in 28:38 |
| e283eeb | the same, after the persona gate's fixes | exit 1: one timing-tight test (`test_preflight` 0.6 s sleep against a 0.8 s timeout) failed under load; it passes alone and its code was unchanged; its margins were widened (f03ab98) |
| f03ab98 | the same | exit 0: **2498 passed, 5 skipped** in 28:23 |

### Persona gate

A transport planner, not a programmer, schedules "Weekend depot" for the weekend on Friday 17:30, closes the
browser, and on Monday reads its answer and what all three studies on the solver learned, on a rehearsal clock at
120 times real speed. Materials restored from one template each round. Log:
[2026-11-08-over-time-gate-log.md](2026-11-08-over-time-gate-log.md).

| Round | Code | Score | Blocker | What it found |
|---|---|---|---|---|
| 1 | dde6ff9 | 7/10 | none | the weekend barely used and the plan bar said "done around 17:34"; a keep whose cases both improved read as a contradiction; when it ran only in `app.log`; What we learned's strength unexplained |
| 2 | ffb59c5 | **8/10** | none | **pass**; "Search through the whole window" left no room for the final check (fixed before the gate); a drawer cut at 1024 px; no Save confirmation |

Both rounds: the campaign ran only inside the weekend, nothing claimed "better" before the final check, every
`app.log` campaign line carried its rehearsal and real time, `evolvekit app stop` left no `app.json` or
`clock.json`, and no scheduled task existed.

### PR

[meaurisch/evolvekit#75](https://github.com/meaurisch/evolvekit/pull/75), stacked on #74; opened 2026-11-08 from `agent/4-over-time` (f03ab98 plus the logs).

## Project 5 — The installer

Branch `agent/5-installer` from `agent/4-over-time`. Plan:
[2026-11-07-agent-5-installer.md](../plans/2026-11-07-agent-5-installer.md), expanded to step level by a planner
(900 lines, six tasks), then the builder check; its four questions and the planner's three were ruled and folded in
(D31–D38, D12 rewritten).

### Decisions

- **No Windows account was created** for the persona gate: a persistent system change the owner has not approved.
  The gate ran under a simulated fresh profile instead (USERPROFILE, APPDATA, LOCALAPPDATA and TEMP into
  `C:\gate\p5\Users\Zoë Intern`, no Python on PATH); what only a real second account would show is not covered.
- **PyInstaller only through the `dist` extra**, pinned to the newest release at least 14 days old (6.22.3, uploaded
  2026-09-12); nothing system-wide, no signing, no Defender exclusions.
- **PyVRP is not bundled**: `tasks.py dist` packs `pyvrp-1.0.0.zip` beside the app's zip and the planner installs it
  from Home; demo-tour and prompt-tune (with `examples/prompt-pipeline`) are the built-ins. The version stays 0.3.0.
- **The exe is its own Python**: one function, `resources.python_argv()` and its variants, replaces every
  `sys.executable` use; `evolvekit-cli.exe -m`/`-c`/`--script`/a `.py` run as python would, multiprocessing workers
  included; a `python` harness keeps the application's own Python.
- **Nothing in the app's folder**: a CLI command that would write there, or any library home inside it, is refused;
  a bare `stop` on the app exe is `app stop`; only git-tracked files are bundled.
- **An engine bug from 2026-09-26 fixed here** (found by the persona gate): the first round was priced as the
  starting point's time times every child, ignoring screening, so a short study could stop before its first round and
  say it found nothing better. It is now priced stage by stage; a search that does not run says so everywhere.

The final whole-branch review (most capable model): with fixes, 0 Critical, 2 Important (no
`multiprocessing.freeze_support` in the frozen entry; updating instructions that failed while a study ran), 7 minors;
one fix wave took them, and its re-review found nothing open.

### Gate

| Commit | Command | Result |
|---|---|---|
| b563b89 | `python tasks.py check`, nothing else running | exit 0: **2678 passed, 5 skipped** in 27:54 |
| 9b48f98 | the same, after the persona gate's fixes | exit 0: **2711 passed, 5 skipped** in 28:23 |

The build at 9b48f98: `evolvekit-app-0.3.0-win64.zip` 37.9 MB (39,712,404 bytes), `pyvrp-1.0.0.zip` 104 KB;
`tests/dist_smoke.py` 14 of 14.

### Persona gate

An intern, not a programmer, installs evolvekit from its zip on a "new" Windows profile, adds PyVRP from its zip,
runs a ten-minute study of PyVRP's settings and stops the app. Log:
[2026-11-08-installer-gate-log.md](2026-11-08-installer-gate-log.md).

| Round | Code | Score | Blocker | What it found |
|---|---|---|---|---|
| 1 | b563b89 | 5/10 | yes | the first Start "finished" in 37 s with nothing searched (the engine bug above); no install confirmation; the report led with the search figure |
| 2 | 506a9d2 | **8/10** | none | **pass**; one search figure differed between the report and the page (fixed before the gate); a time per case below the harness's own unwarned (fixed) |

Both rounds: the runs were the bundle's `evolvekit-cli.exe` and the solves PyVRP's own Python, the extracted folder
was byte-for-byte the zip, the home was under the simulated profile, the app stopped, no scheduled task existed, and
the owner's real home was untouched.

### PR

[meaurisch/evolvekit#76](https://github.com/meaurisch/evolvekit/pull/76), stacked on #75; opened 2026-11-09 from
`agent/5-installer`.

## The end of the build (2026-11-09)

`python tasks.py check` on the last branch, `agent/5-installer` at 2f70d32, nothing else running: exit 0,
**2711 passed, 5 skipped** in 28:02; afterwards no worktree was left registered by a test.

| Project | PR | Result | Last gate | Persona gate |
|---|---|---|---|---|
| 1 One world | [#72](https://github.com/meaurisch/evolvekit/pull/72) | PASS | 1797 passed, 4 skipped (e8ab740) | 9/10, no blocker |
| 2 Skills and the agent gate | [#73](https://github.com/meaurisch/evolvekit/pull/73) | PASS | 1864 passed, 4 skipped (a40fc6e) | 8/10 (study skill), 8/10 (harness skill); gate script 6/6 and 2/2 |
| 3 Code studies | [#74](https://github.com/meaurisch/evolvekit/pull/74) | PASS | 2159 passed, 4 skipped (dd58f55) | 8/10, no blocker (round 4) |
| 4 Studies over time | [#75](https://github.com/meaurisch/evolvekit/pull/75) | PASS | 2498 passed, 5 skipped (f03ab98) | 8/10, no blocker (round 2) |
| 5 The installer | [#76](https://github.com/meaurisch/evolvekit/pull/76) | PASS | 2711 passed, 5 skipped (2f70d32) | 8/10, no blocker (round 2) |

All five PRs are open, stacked in order, and green in CI (ubuntu and windows). Fixes made after a PR opened and carried
up the stack by merge commits: an 8.3 short-path worktree leak seen only on Windows CI (on `agent/3-code`, f7dd038), and
two tests that depended on this machine (on `agent/4-over-time`, a3d13f6). Nothing is merged into master.

Left for the owner, each named in its PR: whether a real second Windows account, a real SmartScreen prompt and
`app install-startup` behave as on this machine (none was tried: persistent system changes); the Copilot CLI paths
(not installed here); the release number (still 0.3.0).

