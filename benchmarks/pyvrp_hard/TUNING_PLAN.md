# Tuning plan: what is run, on what, for how long, and what will count as evidence

Written **before** the tuning run was started. Nothing below is adjusted after
results are in; what turned out differently is recorded in the report, not here.

## The task

Find a PyVRP 0.14.0 configuration that reaches a lower cost than PyVRP's
defaults within **10 minutes of wall clock per instance** on the ten tuning
instances `t01 … t10` (1000–3000 clients, every modelling feature of 0.14.0).
The time limit bounds the whole `pyvrp.solve` call; instance loading (≤ 1.6 s)
is outside it and identical for every configuration.

The solver is driven through its command line only (`solve.py`: flags in, one
JSON object on the last line of stdout) — the interface a solver written in any
other language would offer. 27 parameters are tuned (`tuning.yaml`): everything
`solve.py` exposes, i.e. the ILS, penalty, neighbourhood and perturbation
parameters and the twelve operator switches the model does not depend on.
Ranges are my own judgement, set before any tuning result existed: about one
order of magnitude either side of the default on a log scale for counts and
penalties, the legal interval minus its degenerate ends for shares.

## The machine

| | |
|---|---|
| CPU | Intel Core i7-1185G7 (Tiger Lake, laptop), 4 cores / 8 threads |
| Memory | 16 GB; the largest instance peaks at 0.56 GB per solver process |
| OS / Python | Windows 11 (10.0.26200); Python 3.12.10; PyVRP 0.14.0 |

**How many runs at once.** Measured on `t05` (1800 clients), 60 s, iterations
per process relative to one process alone: 2 pinned processes 106 % each,
3 pinned 106 % each (3.17× in total), 4 pinned 77 % each (3.06×). So: **three
workers, pinned to logical CPUs 2, 4 and 6** — one per physical core, core 0
left to the operating system, the driver and the dashboard. A fourth worker
would buy nothing and would change what "ten minutes" means to the solver. The
baseline and every candidate run under this same setting, and so does the final
comparison.

## What one evaluation costs, and how many fit

| | runs | solver time | wall clock on 3 workers |
|---|---|---|---|
| **full** evaluation: 10 instances × 600 s | 10 | 100 CPU-min | ≈ 34 min |
| **screen**: 4 instances (t01, t04, t07, t10 — 1000, 1600, 2200, 3000 clients) × 120 s | 4 | 8 CPU-min | ≈ 2.7 min |

Screening every candidate at full cost would allow ~3 candidates per two hours.
Instead each generation breeds **8** candidates, screens all of them (≈ 22 min)
and gives the full ten minutes per instance only to the **best 2** (≈ 68 min):
about **90 minutes per generation**.

| Phase | Evaluations | Wall clock |
|---|---|---|
| generation 0: the defaults, screen + full | 1 + 1 | ≈ 0.6 h |
| 12 generations × (8 screens + 2 full) | 96 + 24 | ≈ 18 h |
| validation: the best 2 of the search and the defaults, 2 fresh seeds × 10 instances | 60 runs | ≈ 3.4 h |
| test (below) | 84 runs | ≈ 4.8 h |
| **total** | | **≈ 27 h** |

Why a 120 s screen can say anything about 600 s: with the defaults the cost is
still falling steeply at 120 s (in the verification runs the last tenth of the
time alone still bought 0.5–1 %), so at both horizons the objective is largely
*how fast the search descends*. It is not the same question, though — a
restart or penalty parameter that only matters late is invisible to the
screen. The run itself will show how well the two agree (every promoted
candidate has both scores, and `solve.py` reports the cost at each tenth of
its limit); if they do not, the report says so.

Both stages use `normalize: baseline`: every instance counts as a percentage of
what the defaults reached on it *at that stage's time limit*, so the 3000-client
instance does not outvote the 1000-client one and the two stages share a scale.

Search: model-free (`param_local` 0.5, `param_tpe` 0.25, `param_lhs` 0.15,
`param_cross` 0.1). No LLM backend is available on this machine (decision D7),
and the operator comparison in the README says that for a solver whose defaults
are already good, local steps are what finds anything.

Robustness in use: `retries: 1`, a 900 s process timeout around the 600 s
solve, the evaluation cache and `pending.json` (a crash costs the runs in
flight), one run per instance so that a failure names its instance.

## What will count as evidence

The search sees seed **0** of every tuning instance, and nothing else. Its own
"improvement" is therefore optimistic by construction (selection on noise) and
is **not** the result.

1. **Validation — choosing the finalist.** The two best fully evaluated
   configurations of the search and the defaults, on the ten tuning instances
   with seeds **101 and 102**, full budget. The finalist is the one with the
   lower mean percentage of the defaults. This spends fresh seeds on the choice
   so that the test below is not also a selection.
2. **Test — the result.** The finalist against the defaults:
   * the ten tuning instances, seeds **1001, 1002, 1003** — never used before;
   * the four fresh instances `f01 … f04` (generated by the same generator from
     other seeds, never touched by the search), seeds **1001, 1002, 1003**.
   Both configurations run under identical conditions: same machine state,
   same three pinned workers, same time limit, and **interleaved** — each
   (instance, seed) pair is run for both configurations back to back, so drift
   in the machine (a laptop throttles) hits both alike.
3. **Statistics.** Per instance: mean cost per configuration over the three
   seeds and the relative difference. Aggregate: the mean of the per-instance
   relative differences with a 95 % confidence interval (paired by instance,
   t-distribution) and a Wilcoxon signed-rank test over the per-instance
   differences, separately for the tuning instances and the fresh ones.
4. **What would be a negative result.** A confidence interval that includes
   zero, or an improvement on the tuning instances that does not carry over to
   the fresh ones. Either is reported as such.

## Threats to validity, known in advance

* **One machine, a laptop.** Thermal throttling can change how many iterations
  fit into ten minutes between the first hour and the fifteenth. Within a
  generation every candidate is affected alike; against the *baseline of
  generation 0* it is a bias of unknown sign. The test is interleaved for this
  reason, and `iterations` is recorded for every run so drift can be seen.
* **One seed per instance in the search.** Cheap, and it makes candidates
  comparable (common random numbers), but it rewards luck on seed 0. That is
  what validation and test are for.
* **The ranges are mine.** A better configuration outside them will not be
  found.
* **Ten minutes on this CPU** is not ten minutes on another. The result is a
  statement about this budget on this machine.
