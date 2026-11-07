# The agent path: evolvekit for engineers and planners — design

7 November 2026 · approved in conversation by the owner · five projects, built in order

This document holds every decision the owner took for the second programme of
evolvekit. A builder who has read the README, [docs/app.md](../../app.md) and
[docs/harness-authoring.md](../../harness-authoring.md) can plan each project
from it without going back to the owner. Where the owner left a choice to the
builder, this document says so. The plans are in `docs/superpowers/plans/`
(`2026-11-07-agent-1-one-world.md` … `2026-11-07-agent-5-installer.md`), and
the session that builds it all starts from
[docs/superpowers/design/2026-11-07-agent-path-session-prompt.md](../design/2026-11-07-agent-path-session-prompt.md).

## 1. Why

evolvekit has two kinds of users, and the app that just landed (PRs #47–#69)
serves the first one well:

1. **Planners.** People who are not programmers configure a complicated
   application — PyVRP is the example, but any program with variable inputs
   (settings, cost sets, objectives), fixed inputs (the cases) and a measure.
   They use the app and the harnesses others wrote for them. It must be
   painstakingly easy to use, to monitor and to interpret.
2. **Engineers.** They describe a problem to a coding agent — GitHub Copilot
   (CLI, VS Code, desktop) at work, Claude Code at home — and say "use
   evolvekit". The agent understands the problem, writes or finds a harness,
   sets up a study and starts it, with the full app, so the engineer monitors
   and interprets it as if they had made the study themselves. Harnesses are
   always written by such agents. Later, evolvekit improves code, not only
   settings: a recursive improvement engine for implementations, performance
   work and investigations.

Today the pieces for the second user exist but do not meet: the CLI makes
studies the app cannot see, the app runs studies the CLI cannot drive, the
verdict lives in the page's code, and no skill tells an agent what to do.
This programme joins them, in five projects, in this order:

| # | Project | Gives |
|---|---|---|
| 1 | One world | one store and one runner behind the CLI and the app; the verdict as a command; the skill installer; `doctor` and `harness discover` |
| 2 | Skills and the agent gate | two skills an agent follows; an end-to-end test in which a fresh agent turns one sentence into a running study |
| 3 | Code studies | a candidate is a change to a source tree; the bake-off of given branches; the coding agent as the mutation operator; a PR as the result |
| 4 | Studies over time | nightly and weekend campaigns; memory across studies; settings per kind of case |
| 5 | The installer | a packaged app for planners, with harness zip install |

## 2. Decisions that hold everywhere

- **Generic names, never tool-branded.** Skills go in `.agents/skills/`; the
  harness in an application's repository goes in a folder called `harness/`
  (or `harness/<id>/` when a repository holds several); nothing is named after
  Claude, Copilot or any vendor, except the provider modules that already are.
- **No GitHub Actions, no remote workers.** Everything runs on the
  engineer's or the planner's own computer. Work use is a proof of concept
  without official support in the company's repositories: nothing in this
  programme may require a CI system, a server, SSH or a shared drive.
- **The verdict is the only claim.** Nothing — a page, a command, a skill, a
  PR body — says "better" before the final check on held-back cases says so,
  and every number shows its range. The verdict is computed in one place and
  rendered everywhere from that document (§3.4). An agent reports the
  verdict's own line and nothing more.
- **One harness per application.** A harness declares settings, data levers,
  tables, measures, and (from project 3) code. A study picks what to vary.
  There is never a second harness for the same application because the kind
  of change differs.
- **The harness lives with the application.** For an application whose source
  the engineer owns, the harness lives in that repository, versioned with the
  code it wraps. For a third-party application (PyVRP installed from PyPI) it
  lives in the engineer's project or in the library home, as today.
- **The words.** The app's vocabulary (study, case, held-back case, starting
  point, measure, goal, rule, combination, round, final check; never KPI,
  seed, generation, instance, config, baseline) holds in every command's
  output meant for people, in the skills, and in generated reports. `--json`
  documents keep the engine's key names.
- **Windows is the first platform.** Every feature is developed and tested on
  Windows 11, and must also pass on Linux (CI runs there). Detached
  processes, paths with spaces and drive letters, CRLF, and the absence of
  symlinks without developer mode are first-class concerns.
- **Python ≥ 3.11, standard library first.** New runtime dependencies need a
  reason in the PR body. The harness SDK stays standard library only and
  Python 3.9-compatible.
- **Quality process.** Every project: a spec section here, a plan in
  `docs/superpowers/plans/`, a builder check of the plan by a fresh reviewer
  before building, TDD per task, a fresh code reviewer per task
  (subagent-driven development), `python tasks.py check` green before every
  PR, a persona gate (§8) per project logged in `docs/superpowers/reviews/`,
  and the README and docs updated in the same PR. Stacked PRs against
  `master` with merge commits, one branch per project (`agent/1-one-world`,
  `agent/2-skills`, `agent/3-code`, `agent/4-over-time`, `agent/5-installer`);
  a maintainer merges. The full gate has timing-sensitive tests: never run a
  demo study alongside it.

## 3. Project 1: One world

### 3.1 One store, one runner

The library home (`EVOLVEKIT_HOME`, else `~/evolvekit`) is the one store.
`evolvekit study new` writes into `<home>/studies/<slug>/` through the same
code the app uses (`Home.create_study`), so slugs, the pinned harness, and the
folder layout are identical whoever made the study. A study made by the app
can be driven from the CLI and a study made by the CLI appears on the app's
Home with its state, its verdict and its figure.

`evolvekit study start` submits a run through the app's runner
(`evolvekit.app.jobs.start`): a detached `python -m evolvekit.app.job`
process, `job.json`, `launch.json`, the heartbeat, the queue
(`queued.json`), resume after a crash, and the final check — identical to
pressing Start in the app. The CLI never speaks HTTP to the app; both call the
same library functions, and all state is on disk, so it does not matter
whether the app is running.

**The queue without the app.** When another study is running, `start`
queues the run, as the app does. Today only the app's ticker launches the
next queued run. From now on `study wait` and `study watch` also call
`jobs.launch_queued` on each tick, so an agent-driven study progresses with
no app open. A queued study prints "queued behind <name>; it starts when
that study finishes (the app or `evolvekit study wait` starts it)".

`evolvekit study run` stays as it is: compile, search and check in the
foreground of this terminal, for scripts and for the engine's own tests. It
is not what the skill uses.

### 3.2 The commands

All take `--home DIR` (default as above) and, where they print for people,
`--json` for a document instead. Exit codes: 0 success; 1 a problem the
message names (every `AppError` the library raises, whatever its HTTP
status: a missing study, a study already running, a refused file); 2 a
configuration or validation error (`ConfigError`, `HarnessError`, and
`study check` with problems); 4 a timeout.

| Command | Does |
|---|---|
| `study new NAME --harness ID [--template T] [--app PATH] [--dir PATH]` | a new study in the library home (prints the slug; `--json` prints `{"slug", "root"}`); `--app PATH` sets the application as `study application` does; `--dir PATH` writes a standalone folder as today, outside the home |
| `study application SLUG PATH` | sets the study's application: runs the harness's probe, records the path and the version, exactly as the app's "Set the application" does (`POST /api/studies/{slug}/application`); `--detect` instead lists the applications found on this computer for the harness, as the app's "found for you" (`evolvekit.app.detect`) |
| `study list` | every study in the home: slug, name, harness, and the state line the app's card shows (`draft · step 3 of 7`, `running · round 2 of 6`, …) |
| `study cases SLUG --add FILE... [--hold-back N]` | copies cases into `cases/`, refuses files the harness's formats do not cover, and splits with the app's rule (`split`): `--hold-back N` cases held back, default the app's suggestion |
| `study check SLUG` | `study_problems` as a list; exit 2 when there are any |
| `study start SLUG [--wait]` | submits a run; prints `{"run", "queued"|"started"}`; `--wait` then waits as `study wait` |
| `study stop SLUG` | asks the running or queued run to stop |
| `study status SLUG` | the state line for people; `--json` the same document GET `/api/studies/{slug}/status` returns |
| `study watch SLUG [--every S]` | the status every `S` seconds (default 5) until the run ends; `--json` one JSON document per line; ticks the queue |
| `study wait SLUG [--timeout S]` | returns when the run has ended: exit 0 with the verdict (JSON with `--json`) when the phase is `done`, 1 when `stopped` or `failed`, 4 on timeout; ticks the queue |
| `study verdict SLUG [--run ID]` | the verdict document (§3.4), one line for people |
| `study report SLUG [--run ID] [--markdown]` | the report (§3.5); `--markdown` for a PR body or a note, else JSON |
| `study open SLUG [--no-browser]` | prints the study's URL in the running app, starting the app when none runs; opens the browser unless `--no-browser` |
| `doctor [--app PATH]` | the environment (§3.6) |
| `harness discover (--program PATH \| --python PATH --module NAME)` | what an application exposes, before a harness exists (§3.7) |
| `skill list` / `skill install [--dir DIR] [--force]` | the skills shipped in the package (§3.8) |

`study preview`, `study compile`, `study run` keep their arguments and gain
the slug form: a positional that names an existing study in the home, or a
path. The rule: a positional that is a directory on disk is a path; otherwise
it is a slug in the home.

### 3.3 The app says it is running

`evolvekit app` writes `<home>/app.json` as soon as its server has bound
its port (in `AppServer.__init__`, so both `start()` and `serve_forever()`
are covered) and removes it on a clean stop:

```json
{"pid": 1234, "started_at": "2026-11-07T10:00:00Z", "host": "127.0.0.1",
 "port": 8780, "url": "http://127.0.0.1:8780/?token=…"}
```

A stale file (its pid is not alive, or is alive but not an evolvekit app
started at that time, by the same liveness rule `jobs.run_alive` uses) counts
as "not running" and is overwritten. `study open` reads it, prints
`<url>#/study/<slug>` (the page's route for a study), and when no app runs
it starts one detached (`python -m evolvekit app --no-browser`, the same
detached launch as `jobs._launch`) and waits up to 15 s for the file.

This is a deliberate change to today's rule that the token is never written
to disk: the file lives in the library home beside `.env`, which already
holds API keys, and the app's other protections (loopback only, the Origin
check on every change, the token on every API call) stay. docs/app.md says
so.

### 3.4 The verdict document

The rule that decides (`confirm.decide`), the chance words and the final
words (`jobs._chance_words`, `_decision_of`, `_final_words`) move into one
module, `evolvekit/app/verdict.py`, with one entry point:

```python
def build_verdict(root: Path, run: str | None = None) -> dict
```

It reads the study folder and the run's `job.json` and `confirm/final/`
files, needs no server, and returns:

| Key | Holds |
|---|---|
| `schema` | 1; bumped when a key changes meaning |
| `study`, `run`, `phase` | the slug, the run id, the job phase (`queued`, `starting`, `search`, `check`, `done`, `stopped`, `failed`) |
| `decision` | `use`, `keep`, `worse`, `none` (nothing beat the start: the job's `final.skipped` says the starting point stayed the best), `single` (one held-back case), `pending` (no final check yet), `stopped` (the check was stopped), `skipped` (every other skip, such as no cases held back) |
| `reason` | with `skipped` and `none`: the sentence the job recorded |
| `line` | the one sentence the Study page shows as the verdict, e.g. "Probably better: 19 in 20; at worst 0.3 % worse" |
| `advice` | the one sentence after it ("Use the new settings." / "Keep today's settings.") |
| `measure` | `{"kpi", "label", "unit", "direction"}` of the deciding level |
| `held_back` | `{"cases": n, "runs_per_case": k}` |
| `mean_pct`, `ci95_pct`, `worst_pct`, `chance`, `chance_words` | the paired statistics in percent of the starting point; absent when `decision` is `pending`, `stopped`, `skipped` |
| `levels` | with goals in order of importance: one entry per level with the same keys, and `deciding: true` on the one that decided |
| `learned_from` | `{"cases": n, "pct": x}`: the training cases, labelled as such |
| `rule` | the fixed rule in words (`confirm.RULE_WORDS`) |
| `by_case` | one row per held-back case: name, start, best, change_pct, outcome (`better`, `worse`, `same`, `failed`) |
| `changed` | the settings and data changes of the best combination: name, label, from, to |
| `files` | paths of the exports that exist in `runs/<id>/downloads/`, relative to the study folder. So that they always exist for a finished run, the final check writes the SDK's four exports there as soon as the decision is computed (today the app makes them on demand when Download is pressed); `build_verdict` itself only reads |
| `started_at`, `finished_at`, `elapsed_s`, `rounds`, `combinations` | how it ran |

`jobs.results` and the Study page render from this document; `study
verdict` prints it. A test asserts that the page's headline and advice equal
the document's `line` and `advice` for every decision, so they cannot
diverge.

### 3.5 The report

`study report --markdown` is written by code from the verdict document and
the results: title (the study's name and the application), the verdict line
and advice, the held-back table by case, what changed (a table of settings
from → to, and data changes), how it ran (cases learned from, rounds,
combinations, time, the final check's runs per case), the rule, and the
files. Numbers appear in the verdict document's own wording ("1.3 % lower",
"between 0.3 % worse and 2.8 % better"), never as bare signed
percentages. A sentence of
interpretation is never generated: the agent may add one below the report,
clearly its own.

### 3.6 `doctor`

Prints, in order, with ✓ or ✗ and a fix for every ✗: the Python version and
path; the evolvekit version; the library home and whether it is writable;
whether the app is running (from `app.json`) and its URL; the AI provider
configured in `settings.yaml` and whether its key or CLI is present (without
printing the key); the harnesses found (built-in and installed); with
`--app PATH`, the application probe of every harness that accepts it. Exit 0
when every line is ✓, 1 otherwise. `--json` prints a list of the same
checks as `{"ok", "name", "message", "fix"}` (a flat list; `harness check
--json` keeps its own shape).

### 3.7 `harness discover`

Before a harness exists, an agent needs a first list of what an application
exposes. `harness discover` is a pointer to where to look, labelled as such:

- `--program PATH`: runs `PATH --version` and `PATH --help` (10 s timeout
  each; through `evolvekit.harness.probe.program_command`, so a `.py` file
  runs with evolvekit's Python on Windows too) and parses argparse-style and
  GNU-style help into candidate settings: every flag except `--help` and
  `--version`, with a type guess (`int`, `float`, `bool`, `choice` with the
  choices seen, `text`), the default when the help says it, `required: true`
  when the usage line shows the flag outside brackets, and the help line.
  `version` is the whole first line `--version` printed, and
  `version_number` the first dotted number in it, or null.
- `--python PATH --module NAME`: runs `PATH -c` with a small script that
  imports the module, reads its version, and lists public dataclasses,
  classes with keyword-only constructors, and functions whose parameters have
  defaults, as candidate settings with their defaults and type annotations.

Output: `{"application": {"kind", "path", "version", "version_number"},
"settings": [{"name", "type", "default", "choices", "required", "help",
"source"}], "notes": [...]}`. Nothing is
executed from the application beyond `--version`, `--help` and an import.

### 3.8 Skills ship with the package

`evolvekit/skills/<name>/SKILL.md` (plus `references/`) is package data.
`skill list` prints them; `skill install` copies each into
`<dir>/.agents/skills/<name>/` (`--dir` defaults to the current directory)
and writes a pointer into `<dir>/.claude/skills/<name>/SKILL.md` for the tool
that does not read the generic path yet:

```markdown
---
name: <name>
description: <the same description>
---
Read and follow `.agents/skills/<name>/SKILL.md` in this repository.
```

Existing files are left alone unless `--force`; the command prints what it
wrote and what it skipped. Project 1 ships the mechanism with the two skill
folders present and containing only a frontmatter and the line "written in
project 2"; project 2 writes the skills.

## 4. Project 2: Skills and the agent gate

### 4.1 Two skills, one shared reference

- `evolvekit-study`: triggers on "use evolvekit", "improve/tune/find better
  settings or data for X with evolvekit", "start a study". It is an interview
  script plus commands.
- `evolvekit-harness`: triggers on "put X behind evolvekit", "write a
  harness", a failing `harness check`, "evolvekit does not know X". It turns a
  problem description into a harness through the check loop.
- `references/evolvekit.md`, identical in both (one source,
  `evolvekit/skills/shared/evolvekit.md`, copied into each skill's
  `references/` by `skill install` and by the package build; a test asserts
  the copies are byte-identical): the vocabulary, the folder layouts, the
  commands with their exit codes, the verdict document's keys, and the
  rules below.

Each `SKILL.md` body is at most 150 lines. The frontmatter has `name` (equal
to the folder) and `description` (one paragraph that starts with what it is
for and ends with "Use when …", under 1024 characters), nothing else, so
both tools read it.

### 4.2 The study skill

In order:

1. **Ground.** Run `evolvekit doctor`. Find the harness: `harness/` in the
   repository, else `evolvekit harness list`. None → hand over to
   `evolvekit-harness`, then come back.
2. **Interview, before anything runs.** Ask, in the engineer's words and only
   what the request did not already say: where the application is, what
   may change (settings, data, from project 3 code), what stays fixed,
   where the cases are and which are representative, the measure and its
   direction, how long per case, how much time in total, what "better" must
   beat (today's settings or a settings file). Decide nothing about the goal
   or the time alone. **The completeness rule:** a request that already
   answers every question is complete, and the skill goes on without
   asking; one unanswered question stops the skill with that question, in
   one message. In a tool that cannot ask (a one-shot `-p` run) the skill
   says what is missing and ends.
3. **Make the study.** `study new`, `study application`, `study cases --add …
   --hold-back N`, edit `study.yaml` for `vary`, `kpis` (the harness's
   measures by name, `{from: harness}`; never invented SQL), `goal`,
   `guardrails`, `limits`, `budget` from the interview, `study check`,
   `study preview`.
4. **Show the plan and the cost.** `study compile` prints the plan (rounds,
   combinations, held-back cases, time, done around). A plan that fits is
   started without a further question when the request was complete; a
   thin or impossible plan is shown with the three ways out, and the
   engineer chooses (in a one-shot run: the skill says so and ends).
5. **Start and hand over.** `study start`, then `study open`: give the URL.
   Say what the engineer will see and when. Then end, unless the engineer
   asked to wait: waiting is `study wait --timeout 600` repeated until it
   returns 0 or 1 (a tool's single command cannot run for hours).
6. **Report the verdict and nothing else.** `study verdict` and
   `study report --markdown`. The skill forbids any sentence that claims
   improvement that the verdict's `line` does not; the training gain is
   named as "on the cases it learned from" only.

### 4.3 The harness skill

In order: `harness discover`; the interview (what a case is and its format,
what the application takes as settings and how it is called, what a solution
contains, what people call things, what must never reach a model); `harness
new --kind …` into `harness/`; write `harness.yaml` tables first, then
`runner.py`, then settings with `help` and `explain`, then levers and
measures, then templates and samples; `harness check --app PATH --json`
until exit 0; bump the version on every later change. It names the
comparability trap and the "every value in range is valid" rule. It ends by
running the study skill's step 3 on the samples as a smoke test.

### 4.4 Tests

- **Static.** Frontmatter valid and identical in both locations after
  install; body length; every `evolvekit …` command named in a skill parses
  (`build_parser().parse_args` on the words, with placeholders substituted);
  every file path named exists in a freshly made study; the shared reference
  identical in both skills; no vendor name in any skill file.
- **The gate script** `tests/agent_gate.py` (not collected by pytest; run by
  hand): builds a temporary repository with the skills installed, the
  demo-tour harness in `harness/`, its samples in `cases/`, a clean library
  home, `solver.py` copied to the repository's root, and the environment of
  the chosen tool; runs the tool non-interactively (`claude -p`, or
  `copilot -p`) with one complete sentence ("Use evolvekit to find solver
  settings that give shorter tours on the requests in cases/; the solver is
  solver.py in this folder; hold two back; ten minutes in total; today's
  settings are the starting point"); and checks: a study exists in the
  home, its application is set, it is started or queued, the tool's output
  names the study URL, and the output contains no claim of improvement.
  Then the gate itself runs `study wait`, runs the tool a second time, fresh,
  with "The evolvekit study <slug> has finished; report its result.", and
  checks that this output contains the verdict's `line` verbatim and no
  other percentage claim. It prints PASS/FAIL per check and writes both
  transcripts beside the log.
- **The persona gate.** As the intern loop for the app: a fresh reviewer
  agent that never saw the skills' source, playing an engineer with a
  problem description, works through the skill in a temporary repository
  and scores it (8+ with no blocker to pass), logged in
  `docs/superpowers/reviews/2026-11-07-engineer-gate-log.md`. One extra
  round plays an engineer whose application has no harness, to gate the
  harness skill: from `solver.py --help` and two sentences it must produce
  a harness that passes `harness check` without editing the SDK.

## 5. Project 3: Code studies

### 5.1 The harness declares code

A new top-level key in `harness.yaml`:

```yaml
code:
  label: The solver's source
  repo: .                      # the source tree: a path relative to the harness, or absolute
  open: [solver.py, "lib/**/*.py"]   # what a study may change (globs, relative to the repo)
  build: "{python} -m pip install -q -e {tree}"   # optional: run in a candidate tree before it is used
  build_timeout_s: 600
  app: "{tree}/solver.py"      # program harnesses: the program inside a candidate tree
```

`repo` names a folder inside a git repository (it may be the repository's
root). **Where it resolves from:** in `harness.yaml` it is a default,
relative to the harness folder as written by its author; a study records
the absolute path in `vary.code.repo` (like `application.path`, the other
path that is absolute), set when `vary.code` is written (`study code SLUG
--repo PATH`, or the app's Set up, which proposes the harness's default
resolved against the original harness folder when that still exists). The
pinned copy in `<study>/harness/` never resolves `repo` itself.
`harness check` resolves it against the folder being checked.

**What a tree is:** a candidate's worktree checks out the whole repository
at the base commit; `{tree}` is the folder `repo` names inside that
worktree (`git rev-parse --show-prefix` of `repo`), and `open`, `app` and
`build` are relative to it. For demo-tour, `repo: ../../examples/cli-solver`
makes `{tree}` = `<worktree>/examples/cli-solver` and `open: [solver.py]`
that folder's `solver.py`.

`open` limits what the search may change; a candidate that touches another
file is refused at the static stage. For a `python` harness, `build`
installs the candidate tree into the study's application Python (the one
`application.path` names), so `solve` runs the candidate's code; for a
`program` harness `app` names the program inside the tree and no build is
needed. `harness check` verifies that `repo` is inside a git repository,
that `open` matches at least one file, that a clean checkout builds (when
`build` is given), and that the samples solve from the tree.

**One at a time when the build is shared.** A `build` that installs into
one interpreter cannot serve two candidates at once: for tree problems with
`build`, the driver evaluates candidates one after the other (breadth stays,
fan-out is sequential; `workers` still parallelise one candidate's cases),
and `harness check` says so in a warning ("code studies on this harness try
one combination at a time"). Program harnesses without `build` evaluate
concurrently as today.

### 5.2 The study varies code

```yaml
vary:
  code:
    repo: C:/work/solver        # absolute: the folder the harness's `code.repo` names
    from: HEAD          # the base: a ref of the repo, or `working` for the working tree as it is
    open: [solver.py]   # optional: a subset of the harness's `open`
    brief: >            # what the search is asked to improve, in the engineer's words
      Make the local search find shorter tours in the same time.
```

A study may vary code alone or together with settings. The app's Set up
page shows a third summary, "What may change: code", with the files and the
brief; `study check` refuses a code study on a harness without `code:`.

### 5.3 A candidate is a change to a tree

The engine gains a second kind of problem beside the skeleton:

```yaml
problem:
  tree: {repo: …, base: <commit>, open: [...], build: …, build_timeout_s: …}
```

- A candidate's `block` is a unified diff against `base`; the seed is the
  empty diff. Dedup, the structural novelty filter and traces work on the
  diff text (normalised: no index lines, no timestamps).
- `Driver._make_source` becomes `tree.materialise(run_dir, candidate) ->
  Path`: `git worktree add --detach work/trees/<id> <base>`, then
  `git apply --index`, and a commit on a detached head so every candidate
  has a commit id (`candidate.commit`). Trees are removed when a candidate
  is rejected or raced out, and at the end of a run except the best's.
- A new builtin stage `builtin-build` runs `build` in the tree once per
  candidate; its failure is a hard reject with the build's stderr tail as
  the feedback. The command stages then run with `{candidate}` = the tree
  path and `{app}` = the program inside it.
- The existing LLM operators keep working on trees: the prompt shows the
  open files as they are after the parent's diff, with line numbers, and asks
  for a unified diff; a response that does not apply is a format failure as
  today. Marked blocks (`# EVOLVE-BLOCK-START/END`) inside an open file are
  the cheap special case: when present, the prompt shows only the blocks
  and the diff is built from the returned block.

### 5.4 The coding agent as the operator

A new operator, `agent_patch`, for providers that can run as agents. The
provider protocol gains an optional capability:

```python
def act(self, brief: str, *, cwd: Path, model: str, timeout_s: float) -> AgentResult
```

`ClaudeCliProvider.act` runs `claude -p` with Edit, Write, Read, Glob, Grep
and Bash allowed, `--max-turns` bounded, in `cwd`; `CopilotCliProvider.act`
runs `copilot -p` with `--allow-tool=write` and shell allowed in `cwd`. The
operator materialises the parent's tree, writes the brief (the problem
description, `vary.code.brief`, the open files, the parent's diff, the
archive's best and worst with their measures, the last failures' stderr
tails, and the rule "change only the open files, keep the tests passing, do
not run the benchmark yourself"), calls `act`, takes `git diff` of the tree
as the child's block, and refuses a diff outside `open`. Every call counts
toward the budget as the provider reports it (USD, or requests for Copilot).
The study's AI settings choose the operator mix: `agent_patch` is on by
default for code studies when the provider supports `act`, beside the
diff-prompt operators.

### 5.5 The bake-off

`evolvekit study bakeoff SLUG --ref A --ref B […]` (and the app's Compare
page, "Compare branches") makes a run with no search: each ref is a
candidate whose block is `git diff base..ref`, the plan has zero rounds, and
the final check runs every candidate against the starting point (the base)
on the held-back cases with the paired statistics. The verdict document gains
`candidates: [{ref, decision, line, mean_pct, ci95_pct, worst_pct, chance}]`
and the Compare page shows them side by side. Scope: the bake-off is built
first in project 3, before any mutation, because it needs only §5.1–5.3's
materialise and build.

### 5.6 The result is a change

- `study export --diff [--run ID]` writes `best.patch` into the run folder
  and prints its path.
- `study pr SLUG [--run ID] [--base BRANCH] [--draft]` creates a branch
  `evolvekit/<slug>-<run>` in the harness's repo from the best candidate's
  commit, pushes it when a remote exists, and opens a PR with `gh` when it
  is installed, with `study report --markdown` as the body; without `gh` it
  prints the branch and the body's path. Nothing is pushed or opened without
  the engineer's yes: the skill asks first, and the command prints what it
  will do and needs `--yes`.
- Settings studies keep ending in the settings file as today; `study pr` on
  a settings study refuses with "this study changed settings, not code; the
  settings file is at …".

### 5.7 Two harnesses exercise it

- **demo-tour** gains `code:` with `repo: ../../examples/cli-solver` (a git
  repository is made for it in tests: the repository is the evolvekit
  checkout itself, so `repo` resolves to a folder inside it and `base` to
  `HEAD`), `open: [solver.py]`, and a template `faster.yaml` (vary code,
  goal `tour_length`). It is the first milestone's test subject and the
  gate's.
- **prompt-tune** (`harnesses/prompt-tune/`): the engineer's wedge. The
  application is `examples/prompt-pipeline/pipeline.py`, a program that
  reads an evaluation item (a JSON file with `input` and `expected`), builds
  a prompt from `prompts/system.md` and the input, calls the provider named
  in its settings (`fake` in tests: deterministic by hashing, so a changed
  prompt changes the score), and prints a score; `code.open` is
  `[prompts/system.md]`, the measure is the score, higher is better. It
  shows that "code" is any text the application reads.

PyVRP's own source is not a test subject: its build is C++ and takes
minutes. The owner tries it by hand once the mechanism works, with
`build: "{python} -m pip install -q -e {tree}"`.

### 5.8 The app

The Tries view shows a candidate's diff in the hover card and the drawer
(syntax-neutral, added and removed lines coloured); the Study page's verdict
and the Compare page work unchanged from the verdict document; Set up shows
the code summary (§5.2). No other page changes.

## 6. Project 4: Studies over time

### 6.1 Campaigns

```yaml
schedule:
  windows:
    - {days: [mon, tue, wed, thu, fri], from: "01:00", to: "06:00"}
    - {days: [sat, sun], from: "00:00", to: "23:59"}
  until: 2026-12-31     # optional
```

The app's ticker (and `study wait`/`watch`) honours windows. At a window's
start: no run yet → start one; the last run was stopped short of its plan →
resume it (`jobs.start(resume=run)`, which finishes the plan); the last run
finished its plan → continue it with the window's length as the added hours
(`jobs.continue_run`), keeping the archive. At a window's end it asks the
run to stop cleanly; a run that is in its final check is not stopped. For a
scheduled study `budget.hours` is the whole campaign's search time, spread
over as many windows as it takes; the final check runs when the plan is
done. Every resume and continuation is a session of the same run, so
the Study page shows one history. Home shows "Campaign · next window Tue
01:00". `study start` on a scheduled study submits the first window instead
of running now unless `--now`.

### 6.2 The app as a service

`evolvekit app --service`: no browser, no console, logs to `<home>/app.log`
(rotated at 5 MB), the ticker and the scheduler running, `app.json` written.
`evolvekit app install-startup` registers it to start at logon: on Windows
through `schtasks` (a task "evolvekit app" running `evolvekit app --service`),
on macOS and Linux it prints the launchd or systemd user unit to install and
where. `evolvekit app uninstall-startup` reverses it.

### 6.3 Memory across studies

`evolvekit/harness/memory.py`: `learned(home, harness_id) -> dict` reads every
finished study on that harness in the home and returns, per setting: how
often it was tuned, its importance (mean absolute Spearman across studies,
with the number of studies), the winning values (from studies whose decision
was `use`), and the direction most wins moved it; plus the list of studies
with their verdicts. Used by:

- `study new --warm`: the starting values are the latest `use` winner's, and
  `recommended` is re-ranked by importance when at least three studies
  exist. The study's `step` records that it was warm-started and from which
  study.
- The sampler: `problem.parameters[*].prior: {value, weight}` in the compiled
  config makes `param_lhs` draw around the value with the weight as the
  share of draws, and the weight decays by half for every study since the
  winner. A prior never excludes any value in range. This is the
  "directional oracle as a decaying prior" decision of September.
- The app: a harness's page gains "What we learned", the same facts in
  words and a table.

### 6.4 Settings per kind of case

- Features of a case: `harness.yaml` may declare `features: {name: {sql,
  describe}}`; without it, the row count of every request table and the
  mean of every numeric request column.
- `study groups SLUG [--k N]` computes groups over the training and
  held-back cases (standardised features, k-means with k = 2 or 3 in the
  standard library, at most 20 restarts, the best silhouette wins; a group
  needs at least two training cases, else one group) and writes
  `groups.json`; planners may instead tag cases in the app's Cases view
  (`cases/<name>.tags`, one tag per case), and tags win over features.
- `per_group: true` in `study.yaml` compiles one search per group,
  sequential, under the study's budget split in proportion to the group's
  cases; the final check is per group; the verdict document gains `groups`
  and the Cases view shows a badge per group; the export adds
  `settings_by_group.json` and `rule.md`: "If <feature> is above <value>
  use settings A, else B" from a one-split decision stump over the features.

## 7. Project 5: The installer

- `python tasks.py dist` builds, with PyInstaller (a new `dist` extra), a
  one-folder app `dist/evolvekit-app/` with `evolvekit-app.exe` that runs
  `evolvekit app` (and `evolvekit-app.exe --service`, `… doctor`), bundling
  the page, fonts, the dashboard, the harness SDK and templates, and the
  built-in harnesses; and zips it as `evolvekit-app-<version>-win64.zip`.
- First run makes the library home, opens the browser, and offers the
  harness zip install on Home when no harness is installed. The application
  (PyVRP's Python) is still the planner's own: the app's "application found
  for you" detection is unchanged.
- **The exe is its own Python.** The job processes (`python -m
  evolvekit.app.job`), the runners of `program` harnesses and the compiled
  stages' `{python}` all run "evolvekit's Python". When frozen, that is the
  exe: `evolvekit-cli.exe -m <module> …` and `evolvekit-cli.exe --script
  <file> …` dispatch to `runpy`, and one function,
  `evolvekit.resources.python_argv() -> list[str]` (`[sys.executable]`
  unfrozen; `[<exe>, "-m"]` / `[<exe>, "--script"]` frozen), replaces every
  `sys.executable` use for these three purposes. So the packaged app runs
  demo-tour and any `program` harness with no Python on the machine, and
  `doctor` prints "Python: bundled". A `python` harness still needs the
  application's own Python.
- A smoke test `tests/dist_smoke.py` (run by hand after `dist`): the exe
  prints its version, starts the app, GET `/api/home` answers with the
  token, and stops.
- macOS and Linux packages are out of scope; `pipx install evolvekit` is
  the documented path there.

## 8. The persona gates

Each project has one, as the intern loop for the app: a fresh reviewer
agent that never sees the code, the design or earlier reviews, given only a
persona, materials and a note; it drives the real thing and scores it out of
10 with any blocker named; the loop repeats until 8+ with no blocker; each
round is logged in `docs/superpowers/reviews/`.

| Project | Persona | Does |
|---|---|---|
| 1 | an engineer who reads the README only | makes, starts, watches and reads a demo-tour study from the CLI alone, then finds it in the app |
| 2 | an engineer with a problem description, in Claude Code (and once in Copilot CLI when installed) | the gate of §4.4; and the no-harness round |
| 3 | an engineer with a slow solver | a code study on demo-tour from one sentence; a bake-off of two branches; the PR |
| 4 | a planner who comes back on Monday | a weekend campaign (simulated clock) and the "What we learned" page |
| 5 | an intern on a clean Windows machine | installs from the zip, installs the PyVRP harness zip, runs a study |

## 9. Testing standards

- TDD per task; a test that fails first, then the code.
- Unit tests for every function the plan names; an end-to-end test on
  demo-tour for every command and every study kind, offline, with the `fake`
  provider; the CLI tested through `main([...])` and `capsys`, never
  through a shell.
- Every feature tested on Windows (the owner's machine) and in CI on
  Linux; detached-process and file-locking behaviour has Windows-specific
  tests where the platforms differ.
- Timing-sensitive tests marked `slow` and kept off `tasks.py test`; the
  full gate never runs beside a demo study.
- Docs are part of the task: a command without its README table row is not
  done.

## 10. Out of scope

GitHub Actions and any CI integration of studies; remote workers; macOS and
Linux installers; PyVRP as a code-study test subject in the suite; the
expert dashboard; multi-objective fronts beyond the existing levels; any
change to the harness SDK's contract beyond what §5 adds.

## 11. Left to the builder

The exact parsing of `--help` in `harness discover`; the k-means and
silhouette implementation; the diff normalisation; the brief's wording for
`agent_patch` (within §5.4's list); the wording of every new sentence for
people, within the vocabulary; the PyInstaller spec; file and function
names not fixed here.
