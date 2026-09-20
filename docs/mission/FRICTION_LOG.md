# Friction log

What it is like to use evolvekit as a brand-new user, recorded on 2026-09-19
against `origin/master` @ bdb97a3 **before anything was fixed**.

## How it was produced

Six personas, each played by a fresh agent that had never seen the source. The
rules: follow only user-facing material (README, `docs/`, the example
directories, `--help`); work in a scratch directory; never touch the repo;
never fix anything; give the exact command and the exact output for every
entry; say so when something worked well. Providers were limited to `fake` and
the zero-LLM `param_lhs` operator, because the machine has no API key. Windows
11, Python 3.12/3.13.

| Persona | Goal | First run of their *own* problem |
|---|---|--:|
| **quickstart** | zero to the shipped example, then the newcomer's usual mistakes | 6 min |
| **cli-solver** | tune a slow, stochastic, crashing Node.js solver with 8 mixed-type flags and 6 instance files; export the result; validate it on unseen seeds | 8 min (13 min for a defensive setup) |
| **observer-agent** | supervise a slow run from a second shell, machine-readably; hard-kill it mid-generation; resume | 10 min |
| **noisy-objective** | 5 numeric parameters, known optimum, noise sd = 3× the real gap: is the reported winner real? | 4 min |
| **pyvrp-example** | follow `examples/pyvrp/README.md` literally; extend to a test set | 9 min |
| **evaluator-fuzz** | sixteen ways an external evaluator can misbehave | 2 min |

The raw reports, 105 entries with commands and outputs, are in
[`friction/`](friction/) (local paths and the user name scrubbed). Below they
are de-duplicated into 63 items. **Hit by** counts personas, which is a fair
proxy for how likely a real user is to meet the item.

The `Status` column is filled in as pull requests land; `open` means nothing
has been done yet.

Severity: **blocker** = cannot proceed, a silent wrong result, or lost
money/data · **major** = costs an hour or misleads · **minor** = costs minutes ·
**papercut**.

## The headline

The cli-solver persona got a real result: 20 evaluations found a configuration
at 76 % of the default's cost, and it held up on unseen seeds (75.3 % against
99.3 %). To get there they wrote **300 lines of glue** that every such user
would have to write again: a per-call kill, a retry, per-instance normalisation
against a stored baseline, failure counters, an exporter and a fresh-seed
validator. Along the way a single crash wrote off five of their first nine
candidates, a timeout left an orphaned solver process behind, a missing KPI
made a broken candidate the run's "best", and at no point could they tell from
outside whether the run was alive.


> **Status as of 2026-09-20** (PR numbers refer to github.com/meaurisch/evolvekit): 39 of 63 items fixed, 7 partly, 17 open. "Fixed" means: in an open pull request, with tests, merged into `integration/expensive-tuning` -- none is merged into `master`.

## A. Observability

| ID | Sev | Hit by | What happened | Status |
|---|---|--:|---|---|
| O-01 | major | 6 | `status` cannot tell a running run from a stalled, crashed, interrupted or finished one: identical output and exit 0 after `taskkill /F`. `.lock` holds the pid but nothing reads it. The **stop reason is persisted nowhere** — it is printed once and lost with the terminal. | **fixed** #19 #20 #21 -- liveness from the lock, the heartbeat and the event log; `status` names running / stalled / finished / interrupted / crashed |
| O-02 | major | 6 | Nothing is visible during a generation. `runs.jsonl` is written when the whole generation's cascade returns, so `status` says "no runs recorded" for the first 45 s of a slow run and then stands still: no candidate/stage/seed in flight, no evaluations done / in flight / failed, no planned generations, no elapsed time, no ETA. | **fixed** #19 #25 -- every evaluator run is an event as it starts and ends; the stage in progress says runs done of planned and time left |
| O-03 | major | 5 | `run`'s progress lines are block-buffered when stdout is redirected: the log file stays at 0 bytes for the whole run and everything is lost on a hard kill. `python -u` fixes it; nothing says so. | **part** #19 -- every printed line is also a `log` event, so nothing is lost; the redirected stdout itself is still block-buffered. open |
| O-04 | major | 5 | Evaluator failures are invisible. 5 of 29 evaluator calls crashed in one run and neither stdout, `status` nor the leaderboard mentioned one. `run` and `preflight` print `exit code 1`; the traceback is in an undocumented `last_failure` field of `runs.jsonl`, truncated to 2000 characters. stdout of a successful evaluation is discarded. | **fixed** #15 #19 #20 #21 -- failures with command line, instance, seed, attempt, stderr tail and full logs, in `status` and one click away on the dashboard |
| O-05 | major | 3 | No uncertainty next to "best" anywhere. `kpi_cv` exists only as raw JSON, is a coefficient of variation (meaningless near zero), and `n` is not recorded. Nothing says whether an improvement is distinguishable from noise. | **fixed** #20 #25 -- n, sd, sem next to every best; a paired verdict (clear / within noise / unknown) |
| O-06 | major | 3 | No way to see what the best candidate *is*: no command prints a block, a diff against the seed, or the parameter values as data. Users found `work/candidates/<id>.py` by browsing. For PyVRP: no way to see which `SolveParams` a candidate actually used. | **fixed** #20 #21 #24 #31 -- code, diff against the seed, parameters against the defaults; `export` |
| O-07 | minor | 3 | Per-instance, per-seed and hold-out results exist only in undocumented `work/stage_out/*.json`. Hold-out KPIs are not in `runs.jsonl` at all. | **part** #25 -- per-instance results by name in the event log, status and dashboard; hold-out KPIs still not in `runs.jsonl`. open |
| O-08 | minor | 2 | No machine-readable status: no `--json` on `status` or `leaderboard`; the `runs.jsonl` schema and `work/` are undocumented. | **fixed** #20 -- `status --json`, schema-versioned; the dashboard draws the same document |
| O-09 | minor | 3 | No durations anywhere: not per evaluation, stage, candidate, generation or run. Evaluator wall clock cannot be reconstructed. | **fixed** #19 #20 -- durations per evaluation, stage, generation and run; evaluator time |
| O-10 | minor | 2 | `status` shows `best` but not the seed baseline or the improvement; those appear only in the end-of-run summary. Budget caps, the patience counter and "full evals today" are not shown. | **fixed** #20 #21 -- baseline, improvement, caps, patience and ETA in `status` and on the dashboard |
| O-11 | minor | 1 | `status` contradicts itself mid-generation: `generations : N` while the economics table already has an unmarked partial row `N+1`. | open (the text `status`'s economics table; the status document itself is consistent) |
| O-12 | minor | 3 | `rejected: 11 (1 no-op, 6 duplicate, 2 behavioural)` does not add up: static hard rejects and unappliable LLM responses are counted in the total and appear in no breakdown, console line or view. | open |
| O-13 | minor | 2 | The leaderboard's `cell` column goes stale after a `range: auto` re-bin, so it disagrees with the archive grid on the same page. | open |
| O-14 | major | 1 | The README promises an inline-SVG best-score-versus-USD chart in the HTML dashboard; `leaderboard --html` produces none. The HTML's `SPENT` tile ($0.0214) disagrees with `status` ($0.0218) for the same run. | open (`leaderboard --html` is superseded by the dashboard, but its README promise still stands) |
| O-15 | minor | 2 | The leaderboard ranks 1-seed proxy-only scores and N-seed full-stage scores in one column with no stage and no n. | **fixed** #14 #20 -- only fully evaluated candidates are ranked; stage and n are shown |
| O-16 | papercut | 2 | `preflight` truncates the KPI list and hid the only penalty KPI; its "noisiest KPI varied by 141 %" note was about a 0/1 crash counter, not the objective (CV 0.7 %). | **part** #26 #32 -- preflight's KPI line leads with the objective and a failed stage repeats the command's last words; the CV note on a 0/1 KPI is unchanged. open |
| O-17 | papercut | 1 | The seed line prints the public score while `best=` prints the hold-out-penalised rank, so generation 1 looks like a regression; `improvement` and `d best` use different bases. | open |
| O-18 | minor | 1 | Whether the machine was under load during an evaluation cannot be seen (iterations are recorded, never shown). Two evaluations of the *identical* seed configuration a minute apart differed by 5 %. | **fixed** #34 -- `host_busy` per evaluation, flagged in `status` and on the dashboard (learnt the hard way: decision D20) |

## B. Robustness and resume

| ID | Sev | Hit by | What happened | Status |
|---|---|--:|---|---|
| R-01 | **blocker** | 3 | `budget.max_usd`, `max_tokens`, `max_full_evals_per_day` and `stop.patience` are **per invocation, not per run directory**. Re-running the same command on a budget-stopped run spent the "hard cap" again: $0.0524 against a $0.01 cap after three invocations. | **fixed** #16 |
| R-02 | **blocker** | 1 | Re-using a run directory silently merges a different (or edited) problem into the ledger. A bin-packing child was bred from a circle-packing parent, exit 0, no warning. The default run dir is a fixed, cwd-relative `runs/latest`, so iterating on a config does this with pure defaults. | **fixed** #39 -- the run directory records the problem's identity; a different one is refused before anything is bred or recorded |
| R-03 | **blocker** | 1 | `max_full_evals_per_day` is not a stop: once reached, every promoted candidate is scored `failure_score` **and archived**, silently, for the rest of the run. | **fixed** #14 (never archived or ranked) and #37 (the run stops at the cap) |
| R-04 | major | 1 | A run interrupted during generation 1 can never be resumed: the seed is re-evaluated, flagged a behavioural duplicate *of itself*, and the run ABORTs with "fix the harness". | **fixed** #17 |
| R-05 | major | 3 | A stage timeout is not a wall-clock bound. It kills the direct child only; a grandchild holding stdout makes evolvekit block until the grandchild exits, and a hung solver survives as an orphan long after the run has finished. | **fixed** #15 -- the timeout bounds the whole process tree (Job Object + parent links on Windows, a session on POSIX) |
| R-06 | major | 4 | A hard kill mid-generation loses the whole generation: the paid LLM calls and every finished evaluation. The resume message does not say what was discarded. Finished `work/stage_out` files are never reused. | **fixed** #28 -- evaluation cache + `pending.json`: a killed run loses what was in flight |
| R-07 | major | 1 | Resume reuses candidate ids: traces are overwritten, stale candidate files sit under the same id, and per-candidate USD is double-attributed. | **part** #28 -- an interrupted generation is adopted under its own ids; a generation that was *recorded* partially still re-uses ids. open |
| R-08 | major | 3 | Resume replays the `param_lhs` random stream: 89 of 100 (then 20 of 20) children of a resumed run were rejected as structural duplicates. Two runs with the same `search.seed` draw identical candidates. | **fixed** #18 |
| R-09 | major | 3 | No retry and no tolerance for a flaky evaluator. One transient failure writes the candidate off at `failure_score` for good; on a `seeds: 3` stage a failure on seed 1 discards the finished seed-0 run. There is no way to mark a failure as infrastructure. | **fixed** #25 -- `retries`, per run, with every attempt's logs kept |
| R-10 | minor | 4 | `search.generations` / `--generations` mean "N more" on resume, undocumented: a 6-generation run that crashed in generation 3 ran to generation 8; re-running a *finished* run buys N more. `--generations` has no help text and accepts negative values. | open -- `generations` still means "N more" on resume (documented nowhere) |
| R-11 | minor | 4 | `run` exits 0 for every stop reason, including `ABORT: seed failed evaluation`. Only `preflight`'s exit codes are documented. | **fixed** #35 -- `run` exits 4 when it aborted; exit codes documented |
| R-12 | major | 1 | Nothing stops a run in which every child fails evaluation; adaptive breadth even *grows*, so it spends faster. | open -- nothing stops a run in which every child fails (the dashboard now makes it obvious: failed evaluations tile, failure reasons) |
| R-13 | minor | 2 | A missing `fake_responses.yaml` is a raw Python traceback mid-run; `preflight` does not check for it and says "clean". | open |
| R-14 | minor | 3 | `status` and `leaderboard` on a mistyped or nonexistent run directory create it and exit 0 with "no runs recorded". | **part** #20 -- `status` no longer creates a mistyped directory; `leaderboard` still does. open |
| R-15 | minor | 1 | Ledger writability is not checked up front: with a read-only `runs.jsonl` a resumed run pays for a generation of LLM calls and then dies on the first write. | open |
| R-16 | minor | 1 | A hard-killed run leaves its in-flight evaluator subprocess running. | **fixed** #15 on Windows -- the evaluator sits in a kill-on-close job, which the operating system closes when evolvekit dies; on POSIX a hard-killed driver can still leave it running. part open |
| R-17 | papercut | 4 | The em dash in the ABORT / stop-reason message is emitted as cp1252 byte 0x97 when output is piped on Windows, and renders as mojibake. | open |

## C. Result quality

| ID | Sev | Hit by | What happened | Status |
|---|---|--:|---|---|
| Q-01 | **blocker** | 3 | A missing, NaN or infinite objective KPI is silently scored as `-0.0` and, under `direction: minimize`, **ranks first**. `preflight` reports "clean". The same happens when `score.objective`, a weight or a penalty names a KPI the evaluator never emits (the `init` scaffold does exactly that). | **fixed** #13 |
| Q-02 | **blocker** | 1 | The documented default `failure_score: -1000` outranks every healthy candidate whose cost is above 1000. Crashed candidates become "best", displace the seed's archive cell and are bred from. | **fixed** #14 |
| Q-03 | major | 2 | Crashed and timed-out candidates enter the MAP-Elites archive as cell elites and are sampled as parents, whatever `failure_score` is. | **fixed** #14 |
| Q-04 | major | 1 | A crashed hold-out evaluation silently drops the hold-out penalty, and in the observed run that candidate became the run's "best". | **fixed** #14 |
| Q-05 | major | 2 | Proxy-only candidates are ranked against full-stage scores; the reported best may never have seen the full set (known: issue #6). | **fixed** #14 (closes issue #6) |
| Q-06 | **blocker** | 3 | On a noisy objective the reported "best" and "improvement" are winner's curse: the seed reported 0.228 on one seed against a true 1.486. There is no way to confirm a winner on fresh seeds or to re-evaluate the incumbent. | **fixed** #20 (the verdict says "within noise" and why a clear result is still optimistic) and #31 (`confirm`: the honest measurement) |
| Q-07 | major | 3 | `param_lhs` does not exploit. It resamples the whole declared box uniformly and ignores the parent (documented as "a variant of the block's own constants"): 2 of 200 children truly beat the default. Sampling is linear even over `[1000, 10000000]`. | **fixed** #27 -- `param_local`, `param_cross`, `param_tpe`; measured against `param_lhs` in two regimes |
| Q-08 | major | 2 | `{seed}` is always `0..N-1`. There is no way to evaluate on seeds the search never saw without editing the evaluator; `preflight --candidate` replays the tuning seeds. | **fixed** #31 -- `confirm --seeds`, `--instances` |
| Q-09 | major | 1 | A wall-clock-bounded score swings 5 % between two evaluations of the identical configuration a minute apart, and a within-noise gain is reported as an improvement. | **fixed** #20 #25 #31 #34 -- paired verdicts, pinned workers, host load recorded, interleaved confirmation |
| Q-10 | minor | 1 | No guidance or support for aggregating across instances of different scale: the example takes a raw mean; a gap to a per-instance baseline has to be hand-built. | **fixed** #25 -- `normalize: baseline` |

## D. The generic user: interface, defaults, time to first run

| ID | Sev | Hit by | What happened | Status |
|---|---|--:|---|---|
| G-01 | major | 3 | The `init` scaffold cannot be run as its own "Next:" message says: it references a skeleton, an evaluator and a fake-responses file it never writes, names an objective KPI nothing emits, and `preflight` calls the result "clean". | **fixed** #33 -- `init --template tune` writes a setup that runs; the default template is unchanged. open for the `program` template |
| G-02 | major | 1 | There is no documented pattern, example or support for tuning an external, non-Python command-line program. 300 lines of glue (see the headline). | **fixed** #24 #25 #26 #29 #33 -- flags in, the solver's own output out, one run per instance, a worked example; no glue code |
| G-03 | major | 3 | `# PARAMS:` accepts numeric `[lo, hi]` only. Boolean, categorical, integer-typed or log-scale entries fail **silently**: they freeze the parameter or disable the operator, and the only symptom is an unrelated fake-provider traceback. | **fixed** #24 -- typed parameters: int, float (log), bool, choice; validated before any solver time |
| G-04 | major | 4 | A zero-LLM run is not supported: `big_step_every: 0` is rejected, a plateau big step calls the strong model whatever the operator weights say, and a `models` block is mandatory. | **fixed** #22 |
| G-05 | major | 4 | No export of the winner, as code or as parameter values. The PyVRP README points at a `winner-….py` that no documented command produces. | **fixed** #31 -- `export` (json, yaml, flags, code); "Copy parameters as JSON" on the dashboard |
| G-06 | major | 2 | No support for, or documentation of, parallel evaluation or CPU-core management; evaluations are strictly sequential. | **fixed** #25 -- `workers`, `pin_cpus`, measured guidance, a preflight warning |
| G-07 | major | 1 | Extending an example to a test set of N instances is undocumented, and an evaluator that aggregates hides the per-instance results from the framework. | **fixed** #25 -- `instances:`; per-instance results by name |
| G-08 | minor | 1 | `extends` deep-merges `search.operators`, so a child config cannot drop an inherited operator: an intended `param_lhs`-only variant still made LLM calls. | **fixed** #22 (documented: a share of 0 switches an inherited operator off) |
| G-09 | minor | 3 | A stage command's bare `python` resolves to the *system* interpreter, not the one running evolvekit. Documented only in the PyVRP example's README; copying an example out of the repo, as `docs/new-experiment.md` recommends, breaks it. | **fixed** #29 -- `{python}` |
| G-10 | minor | 2 | The evaluator contract is undocumented: exit codes, where stderr goes, cwd, environment, retries, whether a shell is used, the layout of `work/`. | **part** #26 #25 #15 -- what is read from where, retries, timeouts and the process tree are documented; one page that states the whole evaluator contract is still missing. open |
| G-11 | minor | 1 | No install instructions. The literal Quickstart fails on a clean interpreter, and `tasks.py run` deletes `runs/demo` before failing. | open |
| G-12 | minor | 3 | The defaults of `--config` and `--run-dir` are undocumented; most flags have no help text; the README's command list implies `run` writes `runs/demo`. | open |
| G-13 | papercut | 1 | Defaults that misread a pure-parameter problem: the `complexity` descriptor splits cells on the sign of a constant; a minimised objective is shown as an unlabelled negated "score". | **part** #25 -- a normalised objective names its unit; the `complexity` descriptor is still the default for a pure-parameter problem. open |
| G-14 | papercut | 1 | End-of-run output is noisy: economics printed twice, `--quiet` still prints ~35 lines, `preflight` emits six boilerplate notes on the shipped example. | open |

## E. Documentation

| ID | Sev | Hit by | What happened | Status |
|---|---|--:|---|---|
| X-01 | major | 1 | `examples/pyvrp/README.md` understates the real config's wall clock by about 2×: the hold-out also runs `seeds: N`. The docs give three different answers for one full-stage evaluation. | open |
| X-02 | papercut | 2 | Stale statements: `text_feedback` and `{seed}` "not landed yet", matrix sizes, a branch name, the test count (548 stated, 569 collected), sample outputs. | open |
| X-03 | papercut | 1 | README commands are bash-only on a repo whose paths are Windows-only (`.venv/Scripts/…`). | open |
| X-04 | papercut | 1 | The offline PyVRP demo states no expected outcome; `param_lhs` never fires in it; one child's −2600 score is unexplained. | open |

## What went well

Recorded because it is true, and because none of it should regress.

* Config validation is excellent: wrong paths, another cwd, typo'd keys and YAML
  errors all exit 2 with a key path and a line number.
* `preflight` is genuinely useful: the seed through every stage including the
  hold-out, KPIs with their CV, `text_feedback` verbatim, clear messages for
  non-JSON output, a missing output file and a string KPI.
* A seed that fails evaluation aborts the run **before** anything is spent, and
  says so clearly.
* A failing child never killed a run.
* The run lock works: a second run is refused with the owner's pid, and a stale
  lock from a killed run is reclaimed automatically.
* `seeds: N` averaging, `private_inputs` and the hold-out penalty behaved as
  documented. `param_lhs` cost $0 and kept integers integral.
* The shipped examples match their READMEs.

## Questions users wanted answered and could not

De-duplicated across personas. This list is the requirement for the dashboard
and for `status --json`.

1. Is the run alive, stalled, crashed or finished — and if finished, why did it stop?
2. Which candidate, stage, instance and seed is being evaluated now, since when, against what timeout?
3. How many evaluations are done, in flight and failed — by stage, and by kind of failure?
4. Which generation of how many? Elapsed time across sessions, and an ETA against each stopping criterion (generations, USD, tokens, patience, full evaluations today)?
5. What is the baseline, what is the best, what is the improvement in % — and is it distinguishable from noise (n, sd, CI)?
6. What *is* the best candidate: its code, its diff against the seed, its parameter values as data, and how they differ from the defaults?
7. How did a candidate do per instance and per seed, on the public set and the hold-out? Where does the winner beat the baseline and where does it lose?
8. Which stage produced the score in a given row, from how many seeds?
9. What failed, with what command, on which instance and seed, with what stderr?
10. Was this run resumed, how often, and what was discarded each time?
11. How much evaluator wall clock did the run, a generation, a candidate cost? Was the machine under load?
12. Which declared parameters did the sweep actually vary, which did it skip, and where in each range has the search been?
13. Does this run directory belong to the config I am about to run against it?
