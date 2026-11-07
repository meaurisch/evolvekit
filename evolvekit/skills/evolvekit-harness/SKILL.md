---
name: evolvekit-harness
description: "Puts an application behind evolvekit by writing a harness in harness/: reads what the application exposes, asks the engineer only what it cannot read, writes harness.yaml and runner.py, runs evolvekit harness check until it exits 0, and smoke-tests a study on the samples. Use when someone asks to put an application behind evolvekit or to write a harness, when harness check fails, or when evolvekit does not know an application."
---

Read `references/evolvekit.md` first: its words, commands and rules hold here.
Its sections are "Words", "Where things live", "Commands", "The verdict document" and "Rules"; this file does not repeat them.
Placeholders, each with one meaning: PATH the application (the program, or the Python that has it installed), NAME its Python module, ID a harness from `evolvekit harness list`, T the template written in step 4, SLUG the smoke-test study's short name.
The harness goes in `harness/` in the application's repository, or `harness/<id>/` when the repository holds several; then use that folder wherever a command below says `harness/`.
Use the app's words with the engineer (case, measure, goal, rule, study); the keys of `harness.yaml` stay as they are.

## 1. Look first

- A program: run `evolvekit harness discover --program PATH --json`.
- A Python package: run `evolvekit harness discover --python PATH --module NAME --json`.
- Its `settings` are candidates: a pointer to where to look, not a harness. A flag in the help may not be worth tuning, and a type guess can be wrong.
- Run `evolvekit harness list`. A harness for a similar application is the place to start.
- Read one case file, and the application's `--help` or its docs.

## 2. Interview

Answer from the application and its files first; ask the engineer only what neither says.

- [ ] What is a case, and what is its file format? → `cases` (`label`, `formats`, `describe`) and `read_case`.
- [ ] What does the application take as settings, and how is it called? → `settings`, `application` and `solve`.
- [ ] What does a solution contain? → the solution tables and `solution_tables`.
- [ ] What do people call things? → the words the labels, tables and columns use.
- [ ] What must never reach a model? → those columns get `private: true`.

Ask the five questions in one message before you write or copy anything, unless the request already answers them; an application that evolvekit already has a harness for (`evolvekit harness list`, or the probe accepting it) is a finding to tell the engineer, not a shortcut to take: ask whether to reuse it, and if yes hand over to `evolvekit-study`.
A request that answers every question is complete: go on without asking.
One unanswered question stops you with that question, in one message.
In a one-shot run, where no answer can come, say what is missing and end.

## 3. Start from something that passes

- Step 1 found a close harness: `evolvekit harness new harness/ --from ID`.
- None is close: `evolvekit harness new harness/ --kind program` for a program, `evolvekit harness new harness/ --kind python` for a Python package.
- Read `harness/AGENTS.md` (copied in by `harness new`: the file table, the `runner.py` hooks and their signatures, the `kit/` folder) and evolvekit's `docs/harness-authoring.md` for the full format; its section 'What `harness check` runs' says what each check means.
- The new folder passes its first check. From here on, change one thing at a time.

## 4. Write it, in this order

Run step 5's check after each item.
Write only what the interview named: no lever, template, data change, request format or retry the engineer did not ask for; a smaller harness that passes beats a fuller one.

1. `harness.yaml`, tables first: the request tables (`source: request`, a case as the application receives it) and the solution tables (`source: solution`). Every column has a `type`, a `unit` and a one-line `describe`. Use the people's words, units in the names (`distance_m`), a stable id per row. Give `private: true` to what must never reach a model (names, addresses, coordinates); `categorical: true` only to values safe to show one.
2. `runner.py`: `read_case` builds the request tables, `solve` builds the problem from `case.tables` and keeps to the time limit, `solution_tables` builds the solution tables; `discover()`, optional, reports what the installed application offers (its version, its settings and their defaults). It runs under the application's Python (or evolvekit's, for a program) and imports only `evk_harness`, never the `evolvekit` package. A value the application cannot take raises `evk.InvalidValues("…")`, a sentence.
3. Settings: each with a type and range, a default inside it, a `label`, a one-sentence `help` for people and a longer `explain` for a model (what it does, which way it trades off, when it matters). Mark the six to ten that matter most `recommended`, best as a rank (`1`, `2`, …).
4. Levers and measures: a lever names a request table and the numeric columns it may change, with its modes (`scale`, `set`, `add`). A measure (`kpis`) is SQL over the tables that returns one number, with a `direction`, `lower` or `higher`.
5. Templates: `templates/<id>.yaml`, a partial `study.yaml` (`title`, `summary`, `vary`, `kpis`, `goal`, …). Write at least one whose goal is the measure people care about, with `vary: {settings: recommended}`; it is T in step 6.
6. Samples: `samples/` holds 3–10 small cases that solve in a second or two, with the script that makes them next to them.

## 5. Check until it exits 0

Run `evolvekit harness check harness/ --app PATH --json` — without `--app` it exits 2 before checking anything — and quote the exact command and its exit code in your answer.

```
evolvekit harness check harness/ --app PATH --json
```

- Read every check with `"ok": false`. Do what its `fix` says: failures (`"severity": "failure"`) first, then warnings (`"warning"`).
- Exit 2 is failures, exit 1 is warnings only: neither is done. Fix and check again.
- Never call the harness done before `evolvekit harness check harness/ --app PATH --json` exits 0.
- Never edit `evk_harness.py`: it is evolvekit's SDK, copied in by `harness new`; a check that fails inside it is fixed in `runner.py` or `harness.yaml`.
- A check that fails on a random settings vector means a range that breaks: see "Every value in range is valid" below.

## 6. Smoke test

Make a study from the template written in step 4, on the samples, as the `evolvekit-study` skill's step 3 does:

```
evolvekit study new "Smoke test" --harness harness/ --template T --app PATH --json
evolvekit study cases SLUG --add "harness/samples/*.json" --hold-back 1
evolvekit study check SLUG
evolvekit study preview SLUG
```

- The `--json` line of `study new` gives `slug`: use it as SLUG.
- Put the case format's extension (from `cases.formats`) in place of `.json`, so the script beside the samples is not offered as a case.
- `study check` must exit 0 and `study preview` must solve a sample. A failure here is the harness's fault: fix it in `harness/`, go back to step 5, then make the smoke test again.
- Say which values the smoke test used (the harness's default time per case, the template's budget, the cases held back), that they are for the smoke test only, and quote the `study preview` line. The study stays in the library home as a draft; start nothing.

## 7. Every later change

- Bump `version:` in `harness.yaml` whenever what the harness does changes (a setting, a table, a measure, the runner), then check again until it exits 0.
- Studies already made keep their pinned copy of the harness; only new studies get the new version.
- When the application itself changes, the check (when `runner.py` has `discover()`) names every setting that appeared, disappeared or changed its default: fix `settings`, bump `version:`, check again.

## Two rules

The comparability trap. A lever that changes what the application sees (costs, weights, penalties) changes every measure computed from what it saw: a lower solver cost after halving every cost says nothing about the plan. Mark such measures `changes_with_levers: true`, and give the harness a measure of the untouched request (the `orig_<name>` tables hold it) for data studies to aim at; a data study is judged by that measure. The check warns when a template aims a data study at a marked measure (evolvekit's `docs/harness-authoring.md#the-comparability-trap`).

Every value in range is valid. The search tries any value in a setting's range, and a range in which some values crash the application wastes whole runs. In order of preference: make the ranges safe by construction (two ranges that cannot overlap, or a `min_x` and an `extra_x >= 0` with the maximum computed in `solve`); otherwise declare a `{says, expr}` constraint in the template, so the search draws again; otherwise an SQL constraint on the changed request, for rules that depend on the data (evolvekit's `docs/harness-authoring.md#settings-every-value-in-range-must-be-valid`).
