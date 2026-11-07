# evolvekit reference

Both skills, `evolvekit-study` and `evolvekit-harness`, read this file: the words, where things live, the commands, the verdict document and the rules.

## Words

| Say | Meaning | Not |
|---|---|---|
| study | one question put to evolvekit: an application, what may change, the cases, the goal | config |
| case | one problem the application solves; the study's material | instance |
| held-back case | a case kept out of the search and used only in the final check | test instance |
| starting point | today's settings and data, the values the result is compared with | baseline |
| measure | a number the application reports about a solution, with a direction (lower or higher is better) | KPI |
| goal | the measures in order of importance, and which way each should go | |
| rule | a limit a solution must keep, or the fixed rule the final check decides by | |
| combination | one set of values to try for the settings and data that may change | candidate |
| round | one pass of the search over a group of combinations | generation |
| final check | the best combination against the starting point on the held-back cases, paired | confirm |
| runs per case | how many times each held-back case is solved in the final check | seed |

`--json` documents and `study.yaml` keep the engine's key names (`kpis`, `seeds`, ...); sentences for people never do.

## Where things live

The library home is `EVOLVEKIT_HOME`, else `~/evolvekit`. It holds `studies/<slug>/`, `harnesses/` (installed ones), `settings.yaml`, `.env` (keys, never printed) and `app.json` (the running app's address).

A study folder `studies/<slug>/` holds `study.yaml`, `harness/` (the study's pinned copy of its harness), `cases/`, `inputs/`, `preview/` and `runs/<id>/`.

A run folder `runs/<id>/` holds `job.json` (the phase), `job.log` (what a failed run said), `queued.json`, `launch.json`, `confirm/final/` (the final check) and `downloads/` (the exports).

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
| `evolvekit study new NAME --harness ID [--template T] [--app PATH] [--json]` | a new study folder in the library home from a harness (`--harness` takes ID, or a harness folder) and a template; `--dir PATH` writes a standalone study folder outside the home instead | |
| `evolvekit study application SLUG PATH` | say where the study's application is | |
| `evolvekit study application SLUG --detect` | list the applications found on this computer | |
| `evolvekit study code SLUG [--repo PATH] [--from REF] [--brief TEXT] [--json]` | a code study: where the source is (default: the harness's own folder), the base it starts from (`HEAD`, a branch, or `working`), and what to improve | 0 set; 1 a relative path, no proposal, no `code:` in the harness, not a git folder, no such ref |
| `evolvekit study list [--json]` | the studies in the library home | |
| `evolvekit study cases SLUG --add FILE... [--hold-back N] [--json]` | add cases and hold some back for the final check | |
| `evolvekit study check SLUG` | name what is still missing before the study can run | 0 ready, 2 problems |
| `evolvekit study preview SLUG` | solve the smallest training case at the starting values | |
| `evolvekit study compile SLUG` | prints the plan: rounds, combinations, the final check, "done around HH:MM", or the thin plan with its three ways out | 0 a plan; 2 a thin plan or a problem |
| `evolvekit study run SLUG` | compile, search and run the final check in the foreground | |
| `evolvekit study start SLUG [--wait [--timeout S]] [--json]` | start the study as the app's Start does (queued while another study runs) | |
| `evolvekit study stop SLUG [--json]` | stop the running run, or take back a queued one | |
| `evolvekit study status SLUG [--run ID] [--json]` | where the run is: the state line, or the app's status document | |
| `evolvekit study watch SLUG [--every S] [--json]` | the status every few seconds until the run ends; starts a queued run | |
| `evolvekit study wait SLUG [--timeout S] [--json]` | wait until the run has ended | 0 done, 1 stopped or failed, 4 timeout |
| `evolvekit study verdict SLUG [--run ID] [--json]` | the final check's answer: the verdict and what to do about it | |
| `evolvekit study report SLUG [--run ID] [--markdown \| --json]` | the report of a run: the verdict document, or `--markdown` for people | |
| `evolvekit study open SLUG [--no-browser]` | print the study's address in the app (starting the app if none runs) and open it | |
| `evolvekit skill list` | the skills that ship with evolvekit | |
| `evolvekit skill install [--dir DIR] [--force]` | write the skills into a project (`.agents/skills`, with a pointer for a tool that reads only its own skills folder) | |

Exit codes: 0 success; 1 a problem the message names; 2 a configuration or validation error, and `study check` with problems; 4 a timeout. `harness check` and `doctor` have their own, in the table. A command that runs a study exits 3 when another run holds the run lock.

## The verdict document

`study verdict --json` prints it; `study wait --json` and `study report --json` print the same document.

| Key | Holds |
|---|---|
| `schema` | 1; bumped when a key changes meaning |
| `study`, `run` | the study's slug and the run's id |
| `phase` | `queued`, `starting`, `search`, `check`, `done`, `stopped` or `failed` |
| `decision` | the answer, one of the values below |
| `reason` | with `skipped` and `none`: the sentence the run recorded |
| `line` | the one sentence the app shows as the verdict |
| `advice` | the one sentence after it, what to do |
| `measure` | the deciding measure: its label, unit and direction |
| `held_back` | `cases`, `runs_per_case`: how many held-back cases, and the runs per case |
| `mean_pct`, `ci95_pct`, `worst_pct` | the paired change in percent of the starting point, its 95 % range, the worst case |
| `chance`, `chance_words` | how likely the gain is real, and in words |
| `levels` | with several goals: one entry per goal, `deciding: true` on the one that decided |
| `learned_from` | `cases`, `pct`: the training cases and their gain, labelled as such |
| `rule` | the fixed rule in words |
| `by_case` | one row per held-back case, with `name`, `start`, `best`, `change_pct`, `outcome` |
| `changed` | the settings and data changes of the best combination: from, to |
| `files` | the exports that exist in `runs/<id>/downloads/` |
| `started_at`, `finished_at`, `elapsed_s` | when and how long |
| `rounds`, `combinations` | how much the search tried |

The `decision` values:

| Value | Means |
|---|---|
| `use` | the new settings pass the rule: use them |
| `keep` | the final check did not show a gain: keep today's settings |
| `worse` | the best combination is worse than the starting point |
| `none` | nothing beat the starting point, so it stayed the best |
| `single` | only one held-back case, too few to decide |
| `pending` | no final check yet |
| `stopped` | the final check was stopped |
| `skipped` | the final check was skipped (such as no held-back cases); see `reason` |

`line` and `advice` are the only sentences an agent repeats.

## Rules

1. Report only the verdict's line and its advice, word for word, and add no sentence that claims more. The gain on the training cases is named only as "on the cases it learned from" (`learned_from`), and only when asked.
2. Until the verdict exists, write no "better", "improved", "faster" or "cheaper", and no percentage.
3. The plan before the start: `study compile`'s line is shown before `study start`. A request complete as first sent then starts without a further question, one completed by the engineer's answers waits for a yes after the plan line, and a thin plan never starts.
4. Measures come from the harness by name (`{from: harness}`) or from its templates; never SQL in a study.
5. Never edit `evk_harness.py`: it is evolvekit's SDK, copied in by `harness new`; fix a failing check in `runner.py` or `harness.yaml`.
6. The comparability trap: a data change that alters what the application sees (costs, weights) alters every measure computed from it. Such measures are `changes_with_levers: true`, and a data study is judged by a measure of the untouched request.
7. Every value in range is valid: the search tries anything in a setting's range. Keep ranges that cannot break by construction; otherwise add a `{says, expr}` constraint; otherwise an SQL constraint.
