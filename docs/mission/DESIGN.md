# Design: evolvekit for real, expensive tuning runs

Working design for the mission. It is a plan, not a record: the decision log
records what was decided and the report records what was built.

## 1. The three complaints, and what causes them in the code

| Complaint | Mechanism in today's code (origin/master @ bdb97a3) |
|---|---|
| **No insight into a running run** | `Driver._evaluate_and_record` writes a generation to `runs.jsonl` only after the *whole* generation's cascade has returned. With an hour-long evaluator the run directory does not change for hours. There is no event stream, no heartbeat, no notion of running / stalled / crashed, no machine-readable status, and `status` cannot see an evaluation in flight, a failure's stderr, or a per-instance result. |
| **Errors, often** | One external command per stage with `subprocess.run(timeout=…)`: a timeout kills the direct child only; nothing is retried; a crash mid-generation loses every evaluation of that generation and the LLM calls that bred it; resume resets the "hard" caps. Details in the audit section of the friction log. |
| **No clearly better configuration** | Selection is by a single noisy mean with no uncertainty attached; failed candidates carry `failure_score` into the archive (the default `-1000` *outranks* any minimised cost above 1000); a missing objective KPI scores `0`; proxy-only candidates compete with full-stage ones (#6); `param_lhs` resamples the whole range uniformly and cannot express booleans, categoricals or log scales; and there is no way to confirm a winner on seeds the search never saw. |

## 2. Principles

1. **The log is the record.** Everything new is append-only JSONL beside
   `runs.jsonl`; every view is a pure projection of the run directory.
2. **One source of truth for observability.** `evolvekit.status.build_status(run_dir)`
   returns one JSON-serialisable document. `status` renders it as text,
   `status --json` dumps it, the dashboard's `/api/status` serves it, the
   static export embeds it. No second implementation.
3. **Stdlib only, no build chain, offline.** The dashboard is one HTML file with
   inline CSS and JS, served by `http.server`. No CDN, no npm.
4. **A time-limited objective must not be distorted.** Parallelism is an explicit
   worker count, identical for baseline and candidates, optionally pinned to
   cores, never above the physical core count without a warning.
5. **Failures are data, not exceptions.** A crash, a timeout or garbage output is
   a recorded event with its command line, instance, seed and stderr tail, one
   click away — and it never takes the run down with it.
6. **Backwards compatible.** Existing configs, run directories and the three
   examples keep working unchanged; every new key is optional.

## 3. The observability layer

New files in the run directory:

| File | Writer | Content |
|---|---|---|
| `events.jsonl` | run process, append-only | `run_started`, `generation_started`, `candidate_bred`, `eval_started`, `eval_finished`, `candidate_recorded`, `generation_finished`, `log`, `run_finished`, `run_interrupted`. Every event has `ts`, `seq` and the session's `pid`. |
| `heartbeat.json` | run process, atomic overwrite every few seconds | `ts`, `pid`, `phase`, `generation`. Staleness while the pid is alive ⇒ *stalled*; gaps ⇒ the machine slept, which corrupts wall-clock-limited evaluations and is flagged. |

`build_status()` answers the six questions of the brief:

1. `health` — state (`running`/`stalled`/`finished`/`stopped`/`crashed`/`interrupted`/`empty`), pid, heartbeat age, generation *k of N*, evaluations done / in flight / failed / cached, elapsed, ETA and its basis, progress against each stopping criterion.
2. `progress` — baseline, best-so-far per generation with uncertainty (n, sd, CI across instances × seeds), improvement %, every candidate as a point.
3. `best` — id, lineage, code, unified diff against the seed, resolved parameters and their diff against the defaults.
4. `parameters` — per declared parameter: explored values × score, coverage of the declared range, an importance estimate with its sample size.
5. `instances` — per instance: baseline vs best (mean, sd, n, Δ%), wins and losses.
6. `failures` — every failed or timed-out evaluation: candidate, stage, instance, seed, exit status, duration, stderr tail, argv, resolved config.

Plus `candidates`, `spend`, `log_tail`. Candidate source is served on demand
(`/api/candidate/<id>`, `status --candidate <id>`) so the polled document stays small.

Entry points: `evolvekit run --dashboard` (serves while the run is alive),
`evolvekit dashboard --run-dir …` (any run directory: live, finished, crashed),
`evolvekit dashboard --export out.html` (static, shareable),
`evolvekit status --json` (agents).

## 4. Evaluation for expensive, noisy, crashing commands

* **Instance fan-out** — a command stage may declare `instances:` (and
  `private_instances:`); the framework runs one subprocess per
  instance × seed with `{instance}` substituted, on `workers: N` slots
  (optionally `pin_cpus`), each under its own `timeout`, with `retries` for
  transient crashes and a whole-process-tree kill on timeout.
* **Per-run records** — every run is an `eval_finished` event: the per-instance
  breakdown, the failure drill-down and the in-flight count all come from here.
* **Baseline-relative aggregation** — instances differ in scale by an order of
  magnitude, so the aggregate is the mean of per-(instance, seed) ratios to the
  seed candidate's result on the same pair (common random numbers), reported as
  a percentage gap. Raw objectives are kept.
* **Evaluation cache / crash-safe resume** — finished runs are keyed by
  (candidate source hash, stage, instance, seed, command) and reused after a
  crash; children that were bred but never recorded are re-adopted instead of
  re-bought.
* **Racing** — a candidate that is already statistically hopeless against the
  incumbent on the pairs evaluated so far stops consuming the remaining ones.
* **Only fully evaluated candidates compete** (#6): archive, parents and "best"
  are restricted to candidates that completed the final stage without failure.

## 5. The generic user: tuning an external command

`problem.parameters` declares a typed search space (int / float / bool /
categorical, bounds, log scale, default). From it evolvekit generates the
skeleton (a `configure()` returning a dict literal — no Python written by the
user), validates every candidate's configuration in the static stage *before*
it can cost solver time, substitutes `{params}` / `{params_json}` into the
stage command, and reads KPIs from the command's stdout (JSON or regex) when it
cannot write evolvekit's output file. `evolvekit init --template tune` scaffolds
this; `evolvekit export` writes the winning configuration as JSON / YAML /
flags; `evolvekit confirm` re-runs baseline and winner on held-out seeds and
reports a paired comparison.

## 6. PR plan

Bug fixes are independent PRs against `master`, each with its failing test.
Features stack where they truly depend on each other; each PR says what it is
stacked on. Everything is merged into `integration/expensive-tuning` as it lands.
