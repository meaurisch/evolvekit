# Making evolvekit good at real, expensive tuning runs — report

> **Status of this document: DRAFT.** Sections 1–3 and 5–8 describe work that is
> done and verified as stated. Section 4 (the result) is **pending**: the tuning
> run was started on 2026-09-20 07:40 and takes about 18 hours, followed by the
> validation and the held-out test. No number in section 4 exists yet, and none
> is anticipated here.

The brief named three disappointments from a real tuning trial — not being able
to see what a run is doing, runs that break or lose work, and results that did
not hold up — and asked for them to be fixed and then *proven* on a public,
artificial PyVRP benchmark with a realistic budget (10 minutes per instance),
through the interface a user of a non-Python solver would have.

Everything is in pull requests against `master` (none merged), and merged into
`integration/expensive-tuning`. The friction log, the decision log and the
working notes are next to this file.

## 1. What was built

29 pull requests, #13–#41. Bug fixes started from a failing test; features are
stacked, and each PR names a compare link that shows its own diff.

### Observability

| PR | What |
|---|---|
| #19 | `events.jsonl` (append-only, every evaluator run as it starts and ends) and `heartbeat.json` |
| #20 | **one status document** (`build_status`), rendered by `status`, `status --json` and the dashboard — a human, a script and an agent cannot be told three different things |
| #21 | **the live dashboard**: `run --dashboard` / `evolvekit dashboard --run-dir …`; one self-contained HTML file, standard-library server, no build chain, works offline, `--export` for a ticket; running, stalled, crashed, resumed and finished runs; light and dark, 375–1440 px |
| #24 | typed parameters in the status and on the dashboard: log scales, a slot per category, importance of booleans and choices |
| #25 | per-instance comparison by name, the stage in progress (runs done of planned, time left), failures with instance / seed / attempt and whether a retry made up for them |
| #34 | **host load per evaluation** — was the machine busy with something else? (learnt the hard way, see 6.) |
| #36 | what watching the real run asked for: time left during the first generation, progress over wall-clock time, does the cheap stage predict the expensive one |

### Robustness

| PR | What |
|---|---|
| #15 | a stage timeout bounds the whole process tree (Job Object + parent links on Windows, a session on POSIX); full logs kept |
| #16 #17 #18 | budget and patience survive a resume; a run killed in generation 1 can be resumed; a resumed run does not replay its random stream |
| #25 | `retries` per run; a candidate that failed for good stops costing |
| #28 | **a run that dies mid-generation loses what was in flight**: evaluation cache + children written down before they are evaluated |
| #35 #37 #39 #40 | `run` exits 4 when it aborted; the daily cap stops the run instead of burning generations; a run directory belongs to one problem; a resumed run finishes its plan instead of starting another |
| #41 | **racing**: a candidate clearly behind the best after *k* instances stops costing |
| #32 | `preflight` repeats a failed command's last words |

### Result quality

| PR | What |
|---|---|
| #13 | a missing / NaN / infinite objective no longer scores 0 and ranks first |
| #14 | only fully evaluated candidates compete (closes issue #6) |
| #25 | `normalize: baseline` — every instance has the same say |
| #27 | model-free operators that use what the run has learnt (`param_local`, `param_cross`, `param_tpe`), measured against `param_lhs` in two regimes, including the one where they do not help |
| #20 #31 | the run's own improvement carries a noise verdict and says why it is optimistic; **`confirm`** makes the honest measurement: paired, interleaved, unseen seeds and instances, confidence interval, exact Wilcoxon test, exit code |

### The generic user

| PR | What |
|---|---|
| #22 | a run that calls no model needs none (no `models` section, no key) |
| #24 | `problem.parameters`: declare a typed space instead of writing a skeleton; `{params}` / `{params_json}`; validated before any solver time |
| #25 | `instances:` — one run per instance, `workers`, `pin_cpus` |
| #26 | the solver's own output: the last JSON line of stdout, flat objects with metadata, `kpi_patterns` for text |
| #29 | `{python}` — a bare `python` silently ran another interpreter, and another version of the solver |
| #31 | `export` (JSON, YAML, flags, code) |
| #33 | `init --template tune` writes a setup that runs; `examples/cli-solver/` walks the whole path |

### The benchmark

| PR | What |
|---|---|
| #23 | `benchmarks/pyvrp_hard/`: a seeded, bit-reproducible generator; 10 tuning instances (1000–3000 clients), 4 fresh ones, 2 smoke instances; every modelling feature of PyVRP 0.14.0 in every instance (verified from the installed package, and all instances verified feasible with the defaults); `solve.py`, a command-line front end with 27 tunables as flags; manifest with SHA-256 |
| #30 | the tuning setup (`tuning.yaml`, `tuning.smoke.yaml`) and the plan, committed before the run |

## 2. The dashboard

Screenshots are in `docs/img/dashboard/` and in the PRs (#21, #24, #25); all are
taken from path-scrubbed `--export` files of real runs.

It was verified in a real browser on a running run, a run killed with
`taskkill /F`, a resumed run (two sessions, an abandoned evaluation accounted
for) and finished runs; in light and dark; at 375, 500, 1100 and 1440 px; and it
went through three recorded design iterations before the PR and two rounds of
changes driven by real runs after it (#25, #36). Cost: `build_status` 12 ms; a
5.35 s run took 5.85 s with a page polling at 1 Hz.

What it answers, in the order of the brief: is the run healthy (state, generation,
evaluations done / in flight / failed, stage progress, elapsed, ETA, every
stopping criterion); is it improving (best against baseline, in percent, with
the noise and a verdict); what is the best configuration and how does it differ
from the defaults; which parameters matter and where has the search looked;
where does it win and lose, instance by instance; what went wrong, one click
from the command line, the instance, the seed and the logs.

**PENDING:** final screenshots of the PyVRP run itself (both themes, phone
width), to be taken when the machine is free (see 6.).

## 3. The benchmark and the plan

`benchmarks/pyvrp_hard/README.md` documents the generator and which PyVRP
features are in every instance; `TUNING_PLAN.md` — written before the run —
documents the machine, the measured reason for three workers, what an
evaluation costs, how many fit, and what will count as evidence. In short:

* PyVRP is driven **only** through `solve.py`'s command line: flags in, one JSON
  object on stdout. No adapter was written; where evolvekit could not do that,
  evolvekit was changed.
* One full evaluation is 10 instances × 600 s = 100 CPU-minutes, ≈ 34 minutes on
  three pinned workers. Every generation screens 8 candidates on 4 instances for
  120 s and gives the full budget to the best 2: ≈ 90 minutes per generation,
  12 generations.
* Three workers because that is where per-run throughput is still flat on this
  machine (2 or 3 pinned processes: 106 % of a lone one each; 4: 77 % each).
* Evidence: seeds 101/102 choose the finalist; seeds 1001–1003 on the ten tuning
  instances **and** on four fresh instances test it against the defaults,
  interleaved, with a paired confidence interval and a Wilcoxon test.

## 4. Result

**Status 2026-09-21 evening: the first (model-free) run and its three
confirmations are finished and reported below. The second, LLM-mixed run is in
progress; 4.2 and 4.3 are PENDING.** Every number below is read from a file in
the run directory (`confirm/<label>/comparison.json`), not from memory.

### 4.1 First run: model-free operators (`runs/pyvrp-hard-1`)

**The search.** 12 generations of 8 children, 18.3 h of wall clock (51.5 h of
solver time on three pinned workers), 608 evaluations, $0. Best configuration
by generation, in percent of the defaults' cost on the search's own seed (lower
is better): 100 → 99.12 (gen 1) → 98.78 (gen 2) → 98.53 (gen 4) → 98.05
(gen 6) → flat for five generations → **97.71 (gen 12, `g012-c0096`)**. The
search's own claim is therefore **+2.29 %** — optimistic by construction, as
the status line says next to the number.

* The 120 s screen on four instances predicted the 600 s stage on ten with a
  Spearman correlation of **0.65** over the 18 configurations that finished both
  (the defaults and 17 candidates):
  good enough to choose 2 of 8, not good enough to skip the full stage.
* Two candidates per generation were promoted to the full stage, 24 in all; **17**
  finished it. The others lost an instance to the 900 s stage timeout (8 runs,
  16 failed evaluations with their retries, on t02, t03 and t08) and were never
  ranked — see "t02" below. That is more than a quarter of the full-stage
  budget spent on candidates that could not compete.
* The run was killed once with my agent session and resumed from its cache
  (D22); generation 1's screening was disturbed by my own tests (D20). Neither
  touches the confirmations below, which are separate, interleaved measurements.

**Validation (seeds 101, 102 — choosing the finalist).**

| configuration | mean improvement | 95 % CI | instances | better / worse | Wilcoxon p | pairs |
|---|--:|--:|--:|--:|--:|--:|
| `g012-c0096` | +1.57 % | [+0.39, +2.74] | 9 | 8 / 1 | 0.0078 | 18 of 20 |
| `g006-c0046` | +1.44 % | [+0.41, +2.48] | 9 | 9 / 0 | 0.0039 | 18 of 20 |

Finalist by the rule fixed in advance (the higher mean): **`g012-c0096`**. The
two are not distinguishable from each other, and the rule did not need them to
be. Nine instances, not ten: see "t02".

**Test (seeds 1001–1003, never used before; finalist against the defaults, interleaved).**

| instances | mean improvement | 95 % CI | better / worse | Wilcoxon p | pairs | verdict |
|---|--:|--:|--:|--:|--:|---|
| the 10 tuning instances | **+1.23 %** | **[+0.54, +1.92]** | 9 / 1 | 0.0059 | 28 of 30 | better than the defaults |
| the 4 fresh instances | +1.40 % | [−0.54, +3.33] | 4 / 0 | 0.125 | 12 of 12 | **not distinguishable** |

| instance | clients | improvement | pairs won |
|---|--:|--:|--:|
| t01 | 1000 | −0.33 % | 1 of 3 |
| t02 | 1200 | +0.44 % | 1 of 1 |
| t03 | 1400 | +0.94 % | 3 of 3 |
| t04 | 1600 | +1.48 % | 3 of 3 |
| t05 | 1800 | +0.80 % | 1 of 3 |
| t06 | 2000 | +1.83 % | 3 of 3 |
| t07 | 2200 | +2.34 % | 3 of 3 |
| t08 | 2500 | +0.21 % | 2 of 3 |
| t09 | 2800 | +2.55 % | 3 of 3 |
| t10 | 3000 | +2.03 % | 3 of 3 |
| f01 (fresh) | 1100 | +0.06 % | 2 of 3 |
| f02 (fresh) | 1700 | +0.75 % | 2 of 3 |
| f03 (fresh) | 2300 | +2.03 % | 3 of 3 |
| f04 (fresh) | 2900 | +2.75 % | 3 of 3 |

**Reading it.**

* The improvement is **real and small**: +1.2 % on seeds the search never saw,
  with an interval that excludes zero. It is about half of what the search
  claimed (+2.29 % → +1.57 % on validation → +1.23 % on test). That shrinkage is
  the winner's curse the plan predicted, and the reason `confirm` exists.
* On the fresh instances the *estimate* is the same (+1.4 %, 4 of 4 better, the
  same growth with size) but four instances cannot carry an interval: p = 0.125
  is the smallest value a four-instance Wilcoxon test can produce. By the plan's
  own definition this is "not distinguishable", and that is how it is reported.
  The design lesson is mine: four fresh instances were too few to be able to
  say yes.
* The gain grows with instance size: below 1500 clients it is within noise
  (−0.3 to +0.9 %), from 2000 clients on it is +1.8 to +2.8 % on six of seven
  instances. With a fixed ten minutes, the larger instances are further from
  converged, and what the tuned configuration buys is speed: a cheaper
  neighbourhood (`swap21` off, `relocate3` on, `exhaustive_on_best` off), a
  shorter acceptance history (214 instead of 300) and faster penalty updates
  (every 288 registrations instead of 500).
* What this is **not**: evidence that the configuration is better on another
  machine, under another time limit, or on instances of another family.

**t02.** On `t02-n1200-clustered-banded`, `pyvrp.solve` with a 600 s limit did
not return within the 900 s stage timeout for some seeds — *for the defaults as
well as for tuned configurations* (defaults: seeds 1001, 1002; `g012-c0096`:
seeds 101, 102, 1001; seed 0 was fine for both). During the search the same
happened to seven candidates: five times on t02, twice on t03, once on t08,
each time again on the retry. It is therefore not something
tuning introduced, but it has two consequences. First, `confirm` drops a pair
when either side fails (friction Q-12), so t02 is missing from the validation
interval and enters the test with one pair of three; the intervals above are
over the pairs that finished, and a user who needs an answer on *every* instance
should read "28 of 30" as part of the result. Second, the cause is open: the
deadline in `solve.py` is checked between iterations, so a single iteration ran
for more than five minutes. Reproducing it needs the machine, which is busy
with the second run; it is the first item of section 8.

**The tuned configuration** (`g012-c0096`; defaults in brackets where changed):
`num_neighbours` 59 (50), `history_length` 214 (300), `exhaustive_on_best`
false (true), `solutions_between_updates` 288 (500), `penalty_increase` 1.66
(1.5), `penalty_decrease` 0.920 (0.9), `min_penalty` 0.037 (0.1),
`weight_wait_time` 0.65 (0.2), `min_perturbations` 0 (1), `max_perturbations`
21 (25), `use_relocate3` true (false), `use_swap21` false (true);
`num_iters_no_improvement`, `target_feasible`, `feas_tolerance` and
`max_penalty` moved by less than 7 %; everything else is at its default.

### 4.2 Second run: a model among the operators (`runs/pyvrp-hard-llm`)

**PENDING** — started 2026-09-21 15:30 by the autopilot; same protocol.

### 4.3 The two finalists against each other

**PENDING** — seeds 2001–2003, all 14 instances, one interleaved `confirm`
(`--against`, PR #43). "The LLM added nothing" = an interval that includes zero.

## 5. Friction log

`FRICTION_LOG.md`: 63 items found by six docs-only "new user" personas and by me
before any fix was started. As of this draft: **40 fixed, 7 partly, 16 open**,
each with the PR that did it. All six items rated *blocker* (R-01, R-02, R-03,
Q-01, Q-02, Q-06) are among the fixed. The open ones are mostly documentation
and small CLI items, plus R-12 (nothing stops a run in which every child
fails) and R-07 (ids re-used after a partial record).

## 6. What went wrong along the way (mine)

* **I slowed my own measured run.** While generation 1 was screening, I ran test
  suites pinned to the one free core at low priority, believing that was safe.
  A configuration equal to the defaults to three decimal places then did 19–33 %
  fewer iterations than the baseline. On a laptop all cores share one power
  budget: pinning keeps work off the solver's cores, not from slowing them down.
  Eight screening scores are biased by about +2 to +5 %; no full-stage evaluation
  is. The run was kept and the incident recorded (decision D20); from then on
  nothing else ran on the machine, and CI became the test runner. It produced a
  feature (#34) and a paragraph in the README.
* A wide fan-out of sub-agents at the start exhausted the session budget twice
  and cost hours (decision D9); after that, at most one or two at a time.
* A defect in an open PR of mine was found three times by later work (#21 twice,
  #24 once); each was fixed on that PR's branch with its own failing test and
  merged forward (D19).

## 7. Decisions

`DECISION_LOG.md`, D1–D20. The ones that shape the result: no LLM backend was
available, so the search is model-free (D7); three pinned workers, measured
(D12); percent-of-baseline as the default aggregate (D15); the solver wrapped
with no adapter (D18); nothing else on the machine during the run (D20).

## 8. What to do next, in order

1. Review and merge the fix PRs #13–#18, #22, #29, #32, #35, #37, #38, #39: small, independent in spirit, each with its failing test.
2. The observability stack #19 → #20 → #21, then #24 → #25 → #26 (the generic-user path), then #27, #28, #31, #33, #34, #36.
3. Use racing (#41) in the next tuning run — it was written while this one was already going — and replace its fixed margin by a sequential test once there is data on how noisy paired differences are.
4. Re-evaluate the incumbent on a second seed when it changes (intensification), so the search itself is less exposed to a lucky seed — today only `confirm` protects against that.
5. R-12, R-07 and the documentation items in the friction log.
6. One page that states the whole evaluator contract (G-10).
7. With an LLM backend available: compare the model-free search with the LLM operators on the same benchmark; `configure()` is ordinary code, so conditional configurations ("fewer neighbours on large instances") are within reach of a model and out of reach of a parameter sweep.
