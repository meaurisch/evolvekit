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
