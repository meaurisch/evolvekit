# evolvekit reference

Both skills, `evolvekit-study` and `evolvekit-harness`, read this file: the words, where things live, the commands, the verdict document and the rules.

## Words

| Say | Meaning | Not |
|---|---|---|
| study | one question put to evolvekit: an application, what may change, the cases, the goal | config |
| case | one problem the application solves; the study's material | instance |
| held-back case | a case kept out of the search and used only in the final check | test instance |
| starting point | today's settings and data (in a code study, today's code: the starting commit), what the result is compared with | baseline |
| measure | a number the application reports about a solution, with a direction (lower or higher is better) | KPI |
| goal | the measures in order of importance, and which way each should go | |
| rule | a limit a solution must keep, or the fixed rule the final check decides by | |
| combination | one set of values to try for the settings and data that may change; in a code study, one change to the open files | candidate |
| round | one pass of the search over a group of combinations | generation |
| final check | the best combination against the starting point on the held-back cases, paired | confirm |
| runs per case | how many times each held-back case is solved in the final check | seed |

`--json` documents and `study.yaml` keep the engine's key names (`kpis`, `seeds`, ...); sentences for people never do.

## Where things live

The library home is `EVOLVEKIT_HOME`, else `~/evolvekit`. It holds `studies/<slug>/`, `harnesses/` (installed ones), `settings.yaml`, `.env` (keys, never printed) and `app.json` (the running app's address).

A study folder `studies/<slug>/` holds `study.yaml`, `harness/` (the study's pinned copy of its harness), `cases/`, `inputs/`, `preview/` and `runs/<id>/`.

A run folder `runs/<id>/` holds `job.json` (the phase), `job.log` (what a failed run said), `queued.json`, `launch.json`, `confirm/final/` (the final check) and `downloads/` (the exports). A code study's run also holds `work/trees/` (each combination's folder, a git worktree of the source, removed as soon as its evaluation has finished; none stays when the run ends, the best's included: `study export --diff` and `study pr` rebuild the change from the run's recorded diff), `best.patch` (`study export --diff`) and `pr.md` (the body `study pr` writes), and `pr.json` (the branch it made, and whether it was pushed). A code study's `study.yaml` says where the source is in `vary.code` (`repo`, `from`, `open`, `brief`).

A harness in an application's repository is `harness/` (or `harness/<id>/` when the repository holds several): `harness.yaml`, `runner.py`, `evk_harness.py` (the SDK), `AGENTS.md`, `templates/`, `samples/`. Templates come from the harness folder: `harness/<id>/templates/*.yaml` in a repository, or the study's pinned copy `<study>/harness/templates/` once `study new` has made the study. The skills live in `.agents/skills/<name>/`, with this file as `references/evolvekit.md`.

## Commands

Every command starts with `evolvekit`; `python -m evolvekit` is the same when `evolvekit` is not on PATH. Every `study` command takes `--home DIR`.

| Command | Does | Exit |
|---|---|---|
| `evolvekit doctor [--app PATH] [--json]` | is this machine ready; with `--app`, asks every harness whether the application is its own; a provider ✗ on a fresh home is no fault unless AI help is on | 0 when every line is ✓, else 1 |
| `evolvekit harness list` | the harnesses evolvekit can offer | |
| `evolvekit harness discover (--program PATH \| --python PATH --module NAME) [--json]` | what an application exposes, before a harness exists: a first list of candidate settings | |
| `evolvekit harness new DIR --from ID` | a new harness as a copy of the closest one | |
| `evolvekit harness new DIR --kind program` | a runnable skeleton; the kinds are `program` and `python` (default) | |
| `evolvekit harness pack DIR [--out OUT]` | zip a harness to share it; the zip goes next to the harness, or into OUT | |
| `evolvekit harness install ARCHIVE [--home DIR]` | install a packed harness into the library home | |
| `evolvekit harness check DIR --app PATH --json` | does the harness work with this application; prints `{"ok", "checks"}` | 0 all pass, 1 warnings only, 2 failures |
| `evolvekit study new NAME --harness ID [--template T] [--app PATH] [--warm] [--json]` | a new study folder in the library home from a harness (`--harness` takes ID, or a harness folder) and a template; `--dir PATH` writes a standalone study folder outside the home instead; `--warm` starts from the latest `use` winner of the finished studies on this harness, with a prior around it that halves with every study since | 2 `--warm` with `--dir` |
| `evolvekit study application SLUG PATH` | say where the study's application is | |
| `evolvekit study application SLUG --detect` | list the applications found on this computer | |
| `evolvekit study code SLUG [--repo PATH] [--from REF] [--brief TEXT] [--json]` | a code study: where the source is (default: the harness's own folder), the base it starts from (`HEAD`, a branch, or `working`), and what to improve | 0 set; 1 a relative path, no proposal, no `code:` in the harness, not a git folder, no such ref |
| `evolvekit study list [--json]` | the studies in the library home | |
| `evolvekit study cases SLUG --add FILE... [--hold-back N] [--json]` | add cases and hold some back for the final check | |
| `evolvekit study groups SLUG [--k N] [--json]` | group the cases by their features (or by the tags given in the app's Cases list) into `groups.json`; a group needs at least two cases to learn from | 1 a case cannot be solved or a run is going, 2 `--k` outside 2 to 5 |
| `evolvekit study check SLUG` | name what is still missing before the study can run | 0 ready, 2 problems |
| `evolvekit study preview SLUG` | solve the smallest training case at the starting values | |
| `evolvekit study compile SLUG` | prints the plan: rounds, combinations (with AI help, the seconds it assumes for each AI change), the final check, "done around HH:MM" (a study with a `schedule`: "starts Sat 00:00; about 18 min of search; done around Sat 00:18", on its windows) and a note when too few cases are held back for a firm answer, or the thin plan with its three ways out; writes no run folder unless `--run-id ID` is given | 0 a plan; 2 a thin plan or a problem |
| `evolvekit study run SLUG` | compile, search and run the final check in the foreground | |
| `evolvekit study start SLUG [--now] [--more H] [--wait [--timeout S]] [--json]` | start the study as the app's Start does (queued while another study runs). A study with a `schedule` is armed and waits for its first window (`scheduled: next window Sat 00:00`; only a running app keeps the windows); `--now` starts it at once, as a session no window's end stops; after a finished campaign it begins a new one; `--more H` gives its finished run H hours more search time, spent in its next window, never now ("Scheduled 18 min more for the next window (Sat 00:00)") | 1 `--more` on a study with no schedule, a run that cannot go on, or under 10 minutes in all |
| `evolvekit study bakeoff SLUG --ref REF [--ref REF] [--wait [--timeout S]] [--json]` | a code study: check given branches against the starting point on the held-back cases, with no search; the verdict's `candidates` hold each branch's own line; needs no AI help | 0 started or queued (with `--wait`, as `study wait`); 2 a settings study, a study not ready, no held-back case, no such ref, a branch outside the open files or one that does not apply on the starting commit |
| `evolvekit study stop SLUG [--json]` | stop the running run, or take back a queued one; on a scheduled study it also pauses the campaign (`study start` resumes it) | |
| `evolvekit study status SLUG [--run ID] [--json]` | where the run is: the state line, or the app's status document | |
| `evolvekit study watch SLUG [--every S] [--json]` | the status every few seconds until the run ends; starts a queued run | |
| `evolvekit study wait SLUG [--timeout S] [--json]` | wait until the run has ended; on a scheduled study, until the campaign has its answer | 0 done, 1 stopped or failed (a campaign: paused, or ended without a finished run), 4 timeout |
| `evolvekit study verdict SLUG [--run ID] [--json]` | the final check's answer: the verdict and what to do about it | |
| `evolvekit study report SLUG [--run ID] [--markdown \| --json]` | the report of a run: the verdict document, or `--markdown` for people | |
| `evolvekit study export SLUG --diff [--run ID] [--json]` | a code study: the best combination's change as `runs/<id>/best.patch`; prints its path | 0 written; 2 no `--diff`, a settings study, a run that has not ended, or the starting point stayed the best or the search did not run |
| `evolvekit study pr SLUG [--run ID] [--base BRANCH] [--draft] [--yes] [--json]` | a code study: prints the verdict's line (after a final check on fewer than 3 held-back cases, a `note:` line under it, which `pr.md` carries too) and what it will do -- the branch `evolvekit/<slug>-<run>` with the best's change, the push, the PR with `gh` (the report as its body) -- and does it only with `--yes`; ask the engineer first. The commit is the engineer's own (their git identity and hooks); `--base` is the branch the PR goes into (default: the repository's default) | 0 printed or done; 1 the branch exists, a hook of the repository refused the commit, or the push or `gh` failed (with `--json`, `{"ok": false, "error", "state"}`); 2 a settings study, a run that has not ended, the starting point stayed the best or the search did not run, or the change does not apply on `HEAD` |
| `evolvekit study open SLUG [--no-browser]` | print the study's address in the app (starting the app if none runs) and open it | |
| `evolvekit app --service [--home DIR]` | the app with no browser or console, logging to `<home>/app.log` (private, no key in it); it keeps the windows of scheduled studies. A second app on the same home opens the first | |
| `evolvekit app stop [--home DIR]` | stop the app of this home cleanly | 0 stopped, 1 not stopped within 10 s |
| `evolvekit app install-startup` | start `app --service` at logon: on Windows registers the task "evolvekit app" (untried on a real logon); elsewhere prints the launchd or systemd unit and its commands | 1 refused |
| `evolvekit app uninstall-startup` | undo it | 1 refused |
| `evolvekit app --service --clock ISO --clock-speed N` | a rehearsal clock for campaigns (speed 1 to 3600): windows follow it, runs keep real time; ends with `app stop` | 2 a bad clock or speed |
| `evolvekit skill list` | the skills that ship with evolvekit | |
| `evolvekit skill install [--dir DIR] [--force]` | write the skills into a project (`.agents/skills`, with a pointer for a tool that reads only its own skills folder) | |

Exit codes: 0 success; 1 a problem the message names; 2 a configuration or validation error, and `study check` with problems; 4 a timeout. `harness check` and `doctor` have their own, in the table. A command that runs a study exits 3 when another run holds the run lock.

## The verdict document

`study verdict --json` prints it; `study wait --json` and `study report --json` print the same document.

| Key | Holds |
|---|---|
| `schema` | 2 (1 before the verdict read a chance of a gain); bumped when a key changes meaning |
| `study`, `run` | the study's slug and the run's id |
| `phase` | `queued`, `starting`, `search`, `check`, `done`, `stopped` or `failed` |
| `decision` | the answer, one of the values below |
| `reason` | with `skipped` and `none`: the sentence the run recorded |
| `not_run` | `true` with `skipped` when the search stopped before its first round ("The search did not run: there was not enough time left for its first round."): nothing was searched or compared, so say that, never that nothing better was found; the app's *Continue* gives the search its time on the same run, or `study start` makes a new run |
| `line` | the one sentence the app shows as the verdict |
| `advice` | the one sentence after it, what to do |
| `measure` | the deciding measure: its label, unit and direction |
| `held_back` | `cases`, `runs_per_case`: how many held-back cases, and the runs per case |
| `mean_pct`, `ci95_pct`, `worst_pct` | the paired change in percent of the starting point, its 95 % range, and the 5 % bound the rule reads (at least this, with a chance of 19 in 20): not the worst that can happen; with several goals, those of the level the answer is read on |
| `chance`, `chance_words` | how likely the gain is real, and in words |
| `levels` | with several goals: one entry per goal (with its own `chance`), `deciding: true` on the one that decided, `read_on: true` on the one the answer is read on |
| `numbers_from` | with several goals: `level`, `kpi`, `unit`, `deciding`: which level the figures above are |
| `limited_by` | `"cases"` when only more held-back cases could firm the answer up: the check added no runs, and more runs will not help; hold back more cases |
| `learned_from` | `cases`, `pct`: the training cases and their gain, labelled as such |
| `rule` | the fixed rule in words |
| `by_case` | one row per held-back case, with `name`, `start`, `best`, `change_pct`, `outcome` |
| `changed` | the settings and data changes of the best combination: from, to |
| `candidates` | only on a `study bakeoff` run: one entry per branch in the order given (`ref`, `commit`, `decision`, `line`, and with an answer `mean_pct`, `ci95_pct`, `worst_pct`, `chance`, or `same: true` and no figures when every run of the branch gave exactly today's result; `by_case`, the branch's held-back rows as in `by_case` above); the run's own `line` names the chosen branch as one of N, or says none is shown to beat the starting point, or -- when no run of any branch differed -- says so; a branch at the starting commit says it is today's code |
| `files` | the exports that exist in `runs/<id>/downloads/` |
| `started_at`, `finished_at`, `elapsed_s` | when and how long |
| `rounds`, `combinations` | how much the search tried |
| `planned` | `rounds`, `combinations`: what the plan said the search would try (not on a bake-off) |
| `how_it_ran` | only when fewer rounds ran than planned (or all of them, but some combinations repeated one already tried): the sentence that says how much of the plan ran, and why |
| `why_keep` | only on a `keep` whose held-back cases all moved the goal's way while the range still allows a loss: one sentence that says why it is still keep ("Every held-back case was lower (3.1 % and 8.2 %), but with only 2 cases the range is too wide to rule out a loss: hold back more cases for a firmer answer.") |
| `spent` | only on a run with AI help: what its AI spent, `usd`, `cap_usd` and `line` ("AI: $0.87 of $1.00"), or `requests` and `line` for a provider that counts requests |

The `decision` values:

| Value | Means |
|---|---|
| `use` | the new settings (or the change to the code) pass the rule: use them |
| `keep` | the final check did not show a gain: keep today's settings (or code); with no figures and a `line` that starts "No difference in any run" when every run gave exactly today's result, so the study may not exercise the change |
| `worse` | the best combination is worse than the starting point |
| `none` | nothing beat the starting point, so it stayed the best |
| `single` | only one held-back case, too few to decide |
| `pending` | no final check yet |
| `stopped` | the final check was stopped |
| `skipped` | the final check was skipped (such as no held-back cases, or a search that did not run: `not_run`); see `reason` |

`line` and `advice` are the only sentences an agent repeats. `how_it_ran`, when there is one, claims nothing:
pass it on when the plan's rounds did not all run. `why_keep` claims no gain either: it says why there is none, and
goes right after `line` when there is one. `spent`'s `line` claims nothing either: it may be quoted beside them.

## Rules

1. Report only the verdict's line and its advice, word for word, and add no sentence that claims more. The gain on the training cases is named only as "on the cases it learned from" (`learned_from`), and only when asked.
2. Until the verdict exists, write no "better", "improved", "faster" or "cheaper", and no percentage.
3. The plan before the start: `study compile`'s line is shown before `study start`. A request complete as first sent then starts without a further question, one completed by the engineer's answers waits for a yes after the plan line, and a thin plan never starts.
4. Measures come from the harness by name (`{from: harness}`) or from its templates; never SQL in a study.
5. Never edit `evk_harness.py`: it is evolvekit's SDK, copied in by `harness new`; fix a failing check in `runner.py` or `harness.yaml`.
6. The comparability trap: a data change that alters what the application sees (costs, weights) alters every measure computed from it. Such measures are `changes_with_levers: true`, and a data study is judged by a measure of the untouched request.
7. Every value in range is valid: the search tries anything in a setting's range. Keep ranges that cannot break by construction; otherwise add a `{says, expr}` constraint; otherwise an SQL constraint.
8. Nothing is pushed and no pull request is opened without the engineer's yes: `study pr` without `--yes` first, its lines shown as they are.
