# Autopilot: what runs by itself after the first tuning run, and what is left for a session

Written 2026-09-21. The measurements of `benchmarks/pyvrp_hard/TUNING_PLAN.md`
take about two days of solver time on one laptop. None of them needs a person,
so none of them waits for one.

## What is running

`runs/autopilot.py` (not in git: `runs/` is ignored; a copy of its logic is
described here) is started **detached** from any terminal or agent session:

```
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = '"C:\Users\norri\studio\evolvekit\.venv-pyvrp\Scripts\python.exe" "C:\Users\norri\studio\evolvekit\runs\autopilot.py"'
  CurrentDirectory = 'C:\Users\norri\studio\evolvekit' }
```

It does, in order, and **skips every step whose result already exists** -- so
starting it again after a reboot or a crash continues where it stopped:

| # | step | result | solver time |
|---|------|--------|-------------|
| 1 | wait for the first run's confirmation chain (`runs/pyvrp-hard-1.confirm.cmd`, started 2026-09-21 06:37), finish what is missing of it | `runs/pyvrp-hard-1/confirm/{validation,test,fresh}/comparison.md` | ≈ 8.5 h |
| 2 | the second run: `run --config tuning.llm.yaml --run-dir runs/pyvrp-hard-llm`; any exit code but 0 (a provider outage aborts with 4) is resumed after 15 minutes, up to 8 times | `runs/pyvrp-hard-llm/` | ≈ 12-18 h |
| 3 | its chain: validation (`top:2`, seeds 101, 102) → the finalist by the plan's rule → test (seeds 1001-1003) → fresh instances | `runs/pyvrp-hard-llm/confirm/...` | ≈ 8.5 h |
| 4 | finalist of run 2 `--against` finalist of run 1, seeds 2001-2003, all 14 instances | `runs/pyvrp-hard-llm/confirm/head-to-head/comparison.md` | ≈ 4.7 h |

* Log: `runs/autopilot.log` (ends with `==== autopilot done`). State: `runs/autopilot.state.json`.
* It keeps Windows from idle-sleeping for as long as it lives. A closed lid or a
  reboot still stops it; starting it again is all that is needed.
* Code: the worktree `runs/wt3` (integration branch, with the instances copied
  in and checked against `manifest.json`). **Do not touch `runs/wt3` while it runs.**
* The OpenRouter key is read from the repository's `.env` (never printed).
  Spend is capped by `budget.max_usd: 5`; the expected spend is about $1-2.
* Decision D20 holds throughout: **nothing else runs on the machine** --
  no test suite, no build. Reading files and sub-second `status` calls are fine.

## Is it alive?

```
Get-Content runs\autopilot.log -Tail 5
Get-Content runs\autopilot.state.json
python -m evolvekit status --run-dir runs\pyvrp-hard-llm          # or ...\confirm\<label>
```

Dead = no `python.exe` whose command line ends in `autopilot.py`, and no
`==== autopilot done` in the log. Then start it again with the command above.
Never start a second one next to a living one.

## What is left for a session once `==== autopilot done` is in the log

1. `docs/mission/REPORT.md`, section 4: the three comparisons of each run and
   the head-to-head, as tables (from the `comparison.json` files), the
   best-so-far curves of both runs, what the second run spent, and the honest
   reading -- an interval that includes zero is "not distinguishable".
2. The tuned configurations: `export --format yaml` of both finalists into
   `benchmarks/pyvrp_hard/tuned/`, and the results into PR #30's description.
   `tuning.llm.yaml` and the plan's addendum are on the integration branch
   only so far: they need #41 (`race`) and #30 (the benchmark), so they become
   one more small PR on top of the stack (#43 + #30), together with the tuned
   configurations and a test that the file loads.
3. Dashboard screenshots of both real runs (light, dark, phone width) into
   `docs/mission/img/`, for the report and for PRs #34 and #36.
4. `FRICTION_LOG.md` counts, `PROGRESS.md`, decision log, memory.
5. The brief's checklist: every PR open and green, none merged, nothing pushed
   to `master`, no private reference anywhere.

## Notes for the report, collected while it runs

* **2026-09-21 08:40, validation of run 1:** both tuned configurations
  (`g012-c0096`, `g006-c0046`) hit the 900 s stage timeout on
  `t02-n1200-clustered-banded`, seed 101 -- twice each (the retry too), with
  empty stdout, i.e. `pyvrp.solve` never came back from a 600 s limit. The
  defaults finished that instance and seed. The search saw the same thing on
  seed 0 for other candidates (16 timeouts on t02, t03, t08). `confirm` drops
  such pairs (friction Q-12), so section 4 must (a) give the failure counts per
  configuration next to every interval, (b) also give the result with a failed
  candidate run counted as a loss (e.g. at the worst observed relative
  difference, or as a sign-test loss), and (c) say plainly that a configuration
  that does not answer on 1 of 20 runs is not one to ship. Once the machine is
  free: reproduce (`solve.py --instance instances/t02-... --seed 101
  --time-limit 600` with the finalist's flags), find which parameter does it
  (the deadline is checked once per iteration, so one iteration runs > 5 min:
  suspects are `exhaustive_on_best`, `min_perturbations: 0`, the penalty
  bounds), and decide whether it is a solver defect to report upstream or a
  range that the benchmark should not offer.
* **2026-09-21 10:27, validation of run 1 finished:** `g012-c0096` +1.57 %
  [+0.39, +2.74], `g006-c0046` +1.44 % [+0.41, +2.48] -- each over **9**
  instances, 18 of 20 pairs: on `t02` *both* seeds (101 and 102) timed out for
  *both* tuned configurations, so the instance is missing from the interval
  altogether. On the search's seed 0 the same configurations finished t02.
  Finalist by the plan's rule: `g012-c0096`. The test step started at 10:27.
* **2026-09-21 12:40, test of run 1, half way -- a correction to the first
  note above:** the **defaults** time out on `t02` as well (seeds 1001 and
  1002, the retry too), and so does the finalist on seed 1001. So this is not
  something tuning introduced: on `t02-n1200-clustered-banded`, `pyvrp.solve`
  does not come back from a 600 s limit within 900 s for some seeds, whatever
  the configuration (seed 0 and the defaults' seeds 101/102 were fine). Q-12
  stands as a property of `confirm`, but the sentence "a configuration that does
  not answer is not one to ship" applies to the solver on this instance, not to
  the tuned configuration. For the report: t02 has to be shown separately (how
  many of its runs answered, per configuration), and the cause belongs to the
  benchmark section -- reproduce with the defaults and seed 1001 once the
  machine is free, find where the time goes (the deadline is only checked
  between iterations), and either fix `solve.py`'s deadline handling or report
  the hang upstream.
* Papercut seen in passing: `status` on a running `confirm` said "about 9 min
  left" with 30 of 60 runs done after 2.2 h -- the ETA of a confirmation is
  wrong by an order of magnitude (to be reproduced and logged as an O item).
* **2026-09-21 14:08, test of run 1 finished** (seeds 1001-1003, never used
  before): `g012-c0096` **+1.23 %, 95 % CI [+0.54, +1.92]**, 10 instances,
  9 better / 1 worse (t01 -0.33 %), Wilcoxon p = 0.0059; 28 of 30 pairs, the
  two missing pairs are t02 (defaults timed out on seeds 1001 and 1002, the
  finalist on 1001). The search had claimed +2.29 %, validation +1.57 %: the
  shrinkage the plan predicted. The gain grows with instance size (t07, t09,
  t10: +2.0 to +2.5 %). The fresh-instance step started at 14:08.
* **2026-09-21 15:28, fresh instances of run 1 finished** (f01-f04, never seen
  by the search, seeds 1001-1003): `g012-c0096` **+1.40 %, 95 % CI
  [-0.54, +3.33]**, 4 better / 0 worse, Wilcoxon p = 0.125 (the smallest p four
  instances can give), 12 of 12 pairs, no failures. By the plan's own
  definition this is **"not distinguishable"**: same size of effect as on the
  tuning instances, same pattern (f03, f04 -- the large ones -- +2.0 and
  +2.8 %; f01 +0.06 %), but four instances cannot carry an interval. Report it
  as that, not as a confirmation.
* **15:30:** the autopilot took over by itself and started the second run
  (`runs/pyvrp-hard-llm`); at 16:38 it was in generation 1's full stage, 2 model
  calls, $0.056, no failures.
