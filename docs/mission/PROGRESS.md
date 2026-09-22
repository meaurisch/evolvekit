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
| [#21](https://github.com/meaurisch/evolvekit/pull/21) | `feat/dashboard` (on #20) | the live dashboard, export, deep links. Follow-ups on the branch: a CRLF-proof page test; CSS custom properties through `setProperty` (the per-instance bars overflowed when every instance was a win) | O-01 … O-18 |
| [#22](https://github.com/meaurisch/evolvekit/pull/22) | `fix/zero-llm-runs` | a run that calls no model needs none: `models` optional, no big steps, no scratchpad | G-04, G-08 |
| [#23](https://github.com/meaurisch/evolvekit/pull/23) | `bench/pyvrp-hard` | the benchmark: seeded generator, 10 tuning + 4 fresh + 2 smoke instances, `solve.py` (27 tunables as flags), verifier, manifest | Goal 2 |
| [#24](https://github.com/meaurisch/evolvekit/pull/24) | `feat/typed-parameters` (on #21 + #22) | `problem.parameters`: typed space, generated skeleton, validation before any solver time, `{params}` / `{params_json}`, `params` as data, typed views in status and dashboard, `#parameter=` links; preflight hands the configuration over | G-02 (input side), G-03, O-06 (values as data), G-05 (part: `<id>.params.json`, "Copy parameters as JSON") |
| [#25](https://github.com/meaurisch/evolvekit/pull/25) | `feat/instance-fanout` (on #24) | one run per instance: `workers`, `pin_cpus`, `retries`, `normalize: baseline`, `private_instances`, stage progress + ETA, per-instance status by name | G-06, G-07, R-09, Q-10, O-07 (part) |
| [#26](https://github.com/meaurisch/evolvekit/pull/26) | `feat/foreign-output` (on #25) | `kpis_from: stdout`, flat result objects with metadata, `kpi_patterns` | G-02 (output side) |
| [#27](https://github.com/meaurisch/evolvekit/pull/27) | `feat/exploiting-operators` (on #26) | `param_local`, `param_cross`, `param_tpe`; `benchmarks/operator_mixes.py` | Q-07 |
| [#28](https://github.com/meaurisch/evolvekit/pull/28) | `feat/crash-safe-generation` (on #27) | evaluation cache + `pending.json`: a killed run loses what was in flight | R-06, R-07 (part) |
| [#29](https://github.com/meaurisch/evolvekit/pull/29) | `fix/stage-interpreter` (on #28) | `{python}` | G-09 |
| [#30](https://github.com/meaurisch/evolvekit/pull/30) | `bench/pyvrp-hard-tuning` (on #29 + #23) | `tuning.yaml`, `tuning.smoke.yaml`, `TUNING_PLAN.md`; results to be added | Goal 2 |
| [#31](https://github.com/meaurisch/evolvekit/pull/31) | `feat/confirm` (on #29) | `confirm` (paired, interleaved, CI + Wilcoxon, exit code) and `export` | Q-06, Q-08, G-05 |
| [#32](https://github.com/meaurisch/evolvekit/pull/32) | `fix/preflight-says-why` (on #31) | a failed stage repeats the command's last words | O-16 (part) |
| [#33](https://github.com/meaurisch/evolvekit/pull/33) | `feat/tune-scaffold` (on #32) | `init --template tune`; `examples/cli-solver/` | G-01, G-02 |
| [#34](https://github.com/meaurisch/evolvekit/pull/34) | `feat/host-load` (on #33) | `host_busy` per evaluation, flagged in status and dashboard | O-18 |
| [#35](https://github.com/meaurisch/evolvekit/pull/35) | `fix/run-exit-code` (on #34) | `run` exits 4 when it aborted | R-11 |
| [#36](https://github.com/meaurisch/evolvekit/pull/36) | `feat/dashboard-from-the-real-run` (on #35) | stage ETA, progress by time, screening agreement, the right "nothing yet" message | dashboard requirements |
| [#37](https://github.com/meaurisch/evolvekit/pull/37) | `fix/stop-at-the-daily-cap` (on #36) | the run stops at the cap | R-03 |
| [#38](https://github.com/meaurisch/evolvekit/pull/38) | `chore/lint-gate` (on #37) | `tasks.py lint` in CI; `testpaths` | D8 |
| [#39](https://github.com/meaurisch/evolvekit/pull/39) | `fix/run-dir-belongs-to-one-problem` (on #38) | a different problem is refused | R-02 |
| [#40](https://github.com/meaurisch/evolvekit/pull/40) | `fix/resume-finishes-the-plan` (on #39) | `search.generations` is the plan for the directory | R-10 |
| [#41](https://github.com/meaurisch/evolvekit/pull/41) | `feat/racing` (on #40) | `race: {after, margin_pct}` | tractability |
| [#42](https://github.com/meaurisch/evolvekit/pull/42) | `feat/dotenv` (on #41) | `.env` is read, as the README said; values never printed | X (docs promised it) |
| [#43](https://github.com/meaurisch/evolvekit/pull/43) | `feat/confirm-across-runs` (on #42) | `confirm`: `ID@RUN_DIR` and `--against` -- the winners of two runs in one interleaved comparison | result quality |
| [#44](https://github.com/meaurisch/evolvekit/pull/44) | `bench/pyvrp-hard-second-run` (on #43 + #30) | the second (LLM-mixed) run's setup, both tuned configurations, the result; `solve.py` dumps its stack on a hang | benchmark |

Stack base for features: `c0bfd0d` = `origin/master` + the six fix branches.
Every stacked PR names a compare link that shows its own diff.

## Bug-fix queue (each: failing test, fix, PR against master, merge into integration)

1. ~~G-04 zero-LLM run impossible~~ → #22
1a. `pytest` with no path collects a worktree or run output under `runs/` (`testpaths = ["tests"]`)
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

~~F5 typed parameters~~ (#24), ~~stdout KPI parsing~~ (#26), `init --template
tune` → ~~F3 instance fan-out, `workers`, retries, CPU pinning, per-instance
records~~ (#25) → F6 zero-LLM operators that exploit
(local perturbation, parameter crossover, model-based) → F4 evaluation cache
and crash-safe resume → F7 racing → F8 `confirm` (paired, held-out seeds) and
`export` → F9 docs and a worked example with a foreign-language solver →
benchmark PR.

## Benchmark

* `benchmarks/pyvrp_hard/` is PR #23: generator, loader, `solve.py`, verifier,
  manifest (SHA-256 per instance), smoke set, 40 tests. All 16 instances were
  regenerated in a fresh worktree and matched the manifest byte for byte; all
  are feasible with PyVRP's defaults (first feasible solution within 9 s).
* Verified by probe: every PyVRP 0.14.0 modelling feature coexists in one
  feasible instance.
* **Throughput** (t05, 1800 clients, 60 s, iterations per worker relative to
  one worker alone): 1 worker 7,106 it; 2 workers 106 % each; 3 workers 106 %
  each (3.17x in total); 4 workers 77 % each (3.06x). So `workers: 3`, pinned
  to logical CPUs 2, 4, 6 -- one per physical core, core 0 left to the OS, the
  driver and the dashboard.
* PyVRP through the generic interface only: `solve.py --instance {instance}
  --seed {seed} --time-limit N {params}` + `kpis_from: stdout`. Smoke-tuned for
  two generations on the 60- and 120-client instances: 14 evaluations, 0
  failures, nothing written to adapt the solver.
* `runs/wt` is a detached worktree with the instances generated: measurements
  and the tuning run use it, so that switching branches in the main checkout
  cannot pull files out from under a running evaluation (it did, once).

## Dashboard requirements discovered while using it

(to be filled during the PyVRP run: "every time the dashboard fails to tell you
something you wanted to know, that is a dashboard requirement")

* best-so-far over wall-clock time, not only over generations -- open
* ~~instance names instead of indices~~ (#25)
* ~~how far is this two-hour generation?~~ stage progress line + refined ETA (#25)
* ~~which instance did that crash belong to, and did the retry work?~~ (#25)
* ~~what unit is an objective of 97.8?~~ `objective.unit` (#25)
* ~~bool / choice / log-scale parameters were invisible~~ (#24)
* does a short screening stage predict the full one? (proxy-vs-full agreement) -- open
* **was the machine busy with something else during this evaluation?** -- open, and learnt the hard way (D20): my own pinned test runs cost generation 1's screening 15-30 % of its iterations, and nothing on the dashboard said so. Wanted: host CPU load per evaluation, flagged when it exceeds what the workers explain
* the per-instance card gives a wrong reason while nothing is recorded yet ("the final stage neither runs per instance...") -- open
* "Time remaining: -" for the whole first generation although the stage in progress knows what it has left -- open
* no ETA at all until the first non-seed generation has finished -- open

## Machine notes

* This venv's Python 3.13 is the Microsoft Store build: its processes break
  away from a parent Job Object. `.venv-pyvrp` is python.org 3.12.
* Shell heredocs mangle backslash escapes on this setup; write files with
  escapes through the editor tools, not through `cat <<EOF`.
* Headless Chrome on Windows has a ~500 px minimum window width; narrower
  layouts are verified with the browser pane's mobile emulation instead.
* Screenshots for the public repo are taken from `dashboard --export` files
  with local paths replaced (`scratchpad/export_scrubbed.py`).

## Done (2026-09-23 01:00)

Both runs, seven confirmations and the report are finished; `docs/mission/REPORT.md` is the deliverable. 32 PRs (#13-#44) open, none merged, `master` untouched. Dashboards for the two runs: `.claude/launch.json` (`dashboard-run1`, `dashboard-run2`).
