# Progress

Working notes; the report is written from these. Newest state at the top of
each section.

## Pull requests (all open, none merged; every one is merged into `integration/expensive-tuning`)

| PR | Branch | What | Friction items |
|---|---|---|---|
| [#13](https://github.com/meaurisch/evolvekit/pull/13) | `fix/objective-kpi-missing-or-non-finite` | a missing / NaN / inf objective no longer scores 0 and ranks first | Q-01 |
| [#14](https://github.com/meaurisch/evolvekit/pull/14) | `fix/only-fully-evaluated-candidates-compete` | failed, proxy-only, skipped and hold-out-less candidates never compete (closes #6) | Q-02, Q-03, Q-04, Q-05, R-03 (archive half) |
| [#15](https://github.com/meaurisch/evolvekit/pull/15) | `fix/stage-timeout-kills-the-process-tree` | the timeout bounds the whole process tree; full logs kept | R-05, R-16, O-04 (part) |
| [#16](https://github.com/meaurisch/evolvekit/pull/16) | `fix/budget-and-patience-survive-a-resume` | caps and patience belong to the run directory | R-01 |
| [#17](https://github.com/meaurisch/evolvekit/pull/17) | `fix/resume-a-run-that-only-has-its-seed` | a run killed in generation 1 can be resumed | R-04 |
| [#18](https://github.com/meaurisch/evolvekit/pull/18) | `fix/resumed-run-replays-its-random-stream` | a resumed sweep no longer re-draws what it holds | R-08 |
| [#19](https://github.com/meaurisch/evolvekit/pull/19) | `feat/run-events` (stacked on #13–#18) | `events.jsonl` + `heartbeat.json` | O-01, O-02, O-03 (log kept), O-04, O-09 |
| [#20](https://github.com/meaurisch/evolvekit/pull/20) | `feat/status-json` (on #19) | one status document; `status --json` | O-01, O-02, O-05, O-06, O-08, O-09, O-10, R-14 (status) |
| [#21](https://github.com/meaurisch/evolvekit/pull/21) | `feat/dashboard` (on #20) | the live dashboard, export, deep links | O-01 … O-18 |

Stack base for features: `c0bfd0d` = `origin/master` + the six fix branches.

## Bug-fix queue (each: failing test, fix, PR against master, merge into integration)

1. G-04 zero-LLM run impossible (`big_step_every: 0`, plateau big step, mandatory models) — **needed for the proving run**
2. R-10 `search.generations` means "N more" on resume
3. R-02 a run directory accepts a different problem silently
4. R-07 resume reuses candidate ids
5. R-03 the run does not stop when the daily full-evaluation cap is reached
6. R-11 `run` exits 0 on ABORT; exit codes undocumented
7. R-13 missing fake responses file is a traceback
8. O-03 progress output is block-buffered when redirected
9. O-14 `leaderboard --html` never draws the economics chart it documents
10. G-01 `init` scaffold cannot run
11. R-17 em dash mojibake on Windows pipes
12. D8 lint gate (ruff F,E9) in CI + the 8 unused imports
13. R-14 `leaderboard` still creates a missing run directory (`status` fixed in #20)

## Feature queue (critical path to the proving run first)

F5 typed parameters (`problem.parameters`), `{params}` / `{params_json}`,
stdout KPI parsing, `init --template tune` → F3 instance fan-out, `workers`,
retries, CPU pinning, per-instance records → F6 zero-LLM operators that exploit
(local perturbation, parameter crossover, model-based) → F4 evaluation cache
and crash-safe resume → F7 racing → F8 `confirm` (paired, held-out seeds) and
`export` → F9 docs and a worked example with a foreign-language solver →
benchmark PR.

## Benchmark

* A background agent is building `benchmarks/pyvrp_hard/` (generator, loader,
  `solve.py`, verifier, manifest, smoke set, tests). Untracked and unreviewed
  until it reports.
* Verified by probe: every PyVRP 0.14.0 modelling feature coexists in one
  feasible instance.

## Dashboard requirements discovered while using it

(to be filled during the PyVRP run: "every time the dashboard fails to tell you
something you wanted to know, that is a dashboard requirement")

* best-so-far over wall-clock time, not only over generations
* instance names instead of indices (arrives with the instance fan-out)

## Machine notes

* This venv's Python 3.13 is the Microsoft Store build: its processes break
  away from a parent Job Object. `.venv-pyvrp` is python.org 3.12.
* Shell heredocs mangle backslash escapes on this setup; write files with
  escapes through the editor tools, not through `cat <<EOF`.
* Headless Chrome on Windows has a ~500 px minimum window width; narrower
  layouts are verified with the browser pane's mobile emulation instead.
* Screenshots for the public repo are taken from `dashboard --export` files
  with local paths replaced (`scratchpad/export_scrubbed.py`).
