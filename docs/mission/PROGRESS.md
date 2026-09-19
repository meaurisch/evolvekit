# Progress

Working notes; the report is written from these. Newest state at the top of
each section.

## Pull requests

| PR | Branch | Friction items | State |
|---|---|---|---|
| [#13](https://github.com/meaurisch/evolvekit/pull/13) | `fix/objective-kpi-missing-or-non-finite` | Q-01 | open, CI green, merged into integration |
| [#14](https://github.com/meaurisch/evolvekit/pull/14) | `fix/only-fully-evaluated-candidates-compete` | Q-02, Q-03, Q-04, Q-05 (closes #6), archive half of R-03 | open, CI green, merged into integration |
| [#15](https://github.com/meaurisch/evolvekit/pull/15) | `fix/stage-timeout-kills-the-process-tree` | R-05, R-16 (Windows), part of O-04 | open, CI green, merged into integration |

## Bug-fix queue (each: failing test, fix, PR against master, merge into integration)

1. R-01 budget caps and stop policy are per invocation, not per run directory
2. R-04 a run interrupted in generation 1 cannot be resumed (seed flagged a twin of itself)
3. R-07 resume reuses candidate ids
4. R-08 resume replays the `param_lhs` random stream
5. R-03 the run does not stop when the daily full-evaluation cap is reached
6. R-02 a run directory accepts a different problem silently
7. R-11 `run` exits 0 on ABORT; exit codes undocumented
8. R-14 `status` / `leaderboard` create a missing run directory
9. R-13 missing fake responses file is a traceback
10. O-03 progress output is block-buffered when redirected
11. O-14 `leaderboard --html` never draws the economics chart it documents
12. G-01 `init` scaffold cannot run
13. G-04 zero-LLM run impossible (`big_step_every: 0`, plateau big step, mandatory models)
14. R-17 em dash mojibake on Windows pipes
15. D8 lint gate (ruff F,E9) in CI + the 8 unused imports

## Feature queue

F1 events + heartbeat + status document + `status --json` → F2 dashboard →
F3 instance fan-out / workers / retries / pinning → F4 evaluation cache and
crash-safe resume → F5 typed parameters, `{params}`, stdout KPI parsing,
`init --template tune` → F6 zero-LLM operators that exploit (local, crossover,
model-based) → F7 racing → F8 `confirm` and `export` → F9 docs and a worked
example with a foreign-language solver → benchmark PR.

## Benchmark

* `benchmarks/pyvrp_hard/generate.py` exists as an unreviewed 82 KB draft from
  an agent that died on the session limit; nothing else of the benchmark
  exists yet. Not committed.
* Verified by probe: every PyVRP 0.14.0 modelling feature coexists in one
  feasible instance (see DECISION_LOG D4 and the design doc).

## Machine notes

* This venv's Python 3.13 is the Microsoft Store build: its processes break
  away from a parent Job Object. `.venv-pyvrp` is python.org 3.12.
* Shell heredocs mangle backslash escapes on this setup; write files with
  escapes through the editor tools, not through `cat <<EOF`.
