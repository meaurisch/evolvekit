# Tuning a command-line solver you did not write for evolvekit

You have a solver — C++, Java, Rust, it does not matter — with a command line,
a test set, a time limit, and defaults somebody already tuned by hand. You want
better defaults, and you want to be able to believe the result. This example is
that situation end to end. It needs no model, no API key, and **none of your
code is written for evolvekit**: `solver.py` stands in for your binary and is
never imported; the framework only ever runs

    solver --instance FILE --seed N --time-limit S --neighbours 8 --init random ...

and reads the JSON object the solver prints on its last line.

## 1. Say what can be tuned, and how the solver is called

[`evolvekit.yaml`](evolvekit.yaml), in full:

- **`problem.parameters`** — name, type, range, default of each knob. The
  defaults are the baseline: "improvement" always means "against what the
  solver does out of the box". Integers, floats (`log: true` where the range
  spans decades), booleans, choices. `--restart-after 2000 --or-opt false` is
  generated from the names; `flag: "-n"` overrides one.
- **Two stages** with the same command and different budgets. `screen` runs
  every candidate on two instances for half a second; the best three go on to
  `full` — every instance, the real time limit, two seeds. With a solver that
  takes ten minutes per instance this is what makes the search affordable.
- **`instances:`** makes the framework run the command once per instance (and
  seed). That is what lets it run them side by side (`workers`), retry the one
  run that crashed (`retries`), tell you *which* instance a failure or a loss
  belongs to, and weigh instances fairly: each counts as a percentage of what
  the defaults reached on it, so your largest instance does not decide alone.
- **`kpis_from: stdout`** — the solver's own JSON; its strings and nested
  objects are ignored, its numbers are KPIs. A solver that prints text instead
  gets `kpi_patterns: {cost: 'best ([0-9.]+)'}`; one that writes a file gets
  `{out}` in the command.

On your machine, add `pin_cpus` (one logical CPU per physical core) and keep
`workers` at or below the number of physical cores minus one: for a
time-limited solver, two runs sharing a core each get less done, and the
difference becomes the score. `preflight` warns when it can tell.

## 2. Check before you spend

```
python -m evolvekit preflight --config examples/cli-solver/evolvekit.yaml
```

Runs the defaults through every stage, the way a run would: what each stage
reports, how long it takes on the clock, whether the timeouts leave headroom,
how long the whole run will take. Exit code 0 clean, 1 warnings, 2 failures —
a solver that cannot be started, a result that cannot be read or a missing
objective stops here, with the solver's own stderr, not after an hour.

## 3. Run, and watch

```
python -m evolvekit run --config examples/cli-solver/evolvekit.yaml --run-dir runs/cli-solver --dashboard
```

About four minutes. The dashboard (also `evolvekit dashboard --run-dir …` for a
run that is already going, finished, or dead) answers, live: is it healthy and
how far is it — generation, runs done of planned in the stage in progress, time
left; is it improving, and is that more than noise; what is the best
configuration and how does it differ from the defaults; which parameters
matter; where does it win and lose, instance by instance; what went wrong —
`c-80` crashes in about one run of twenty-five, and each crash is listed with
its instance, seed, attempt, command line and stderr, marked "retried ✓" when
the retry went through. `python -m evolvekit status --run-dir runs/cli-solver
--json` is the same document for a script or an agent.

Kill it (`Ctrl+C`, or harder) and start the same command again: the run picks
up the generation it was in, with the same candidates, and the evaluator runs
that had finished are looked up rather than run again.

## 4. Find out whether it is real

The search saw seeds 0 and 1. What it reports as its improvement is therefore
optimistic — it selected on noise. Measure on seeds it never saw, and on
instances it never saw:

```
python -m evolvekit confirm --config examples/cli-solver/evolvekit.yaml --run-dir runs/cli-solver \
    --seeds 1001,1002,1003
python -m evolvekit confirm --config examples/cli-solver/evolvekit.yaml --run-dir runs/cli-solver \
    --seeds 1001,1002,1003 --instances "fresh/*.json" --label fresh
```

The defaults and the winner, interleaved, under identical conditions; per
instance and in aggregate, with a 95 % confidence interval and a Wilcoxon test;
exit code 0 only if the interval lies above zero. "Not distinguishable from the
baseline" is a possible and honest outcome — with only two fresh instances, as
here, it is the likely one even for a real gain: an interval over two
differences is wide. Hold back more instances than that for a claim.

## 5. Take the result home

```
python -m evolvekit export --run-dir runs/cli-solver --format flags --config examples/cli-solver/evolvekit.yaml
python -m evolvekit export --run-dir runs/cli-solver --out tuned.json
```

## Making it yours

Replace `{python} solver.py` by your command; list your instances; declare your
parameters with your solver's defaults; set the two time limits and `timeout`
(per run, comfortably above the time limit — a run that exceeds it is killed
with its whole process tree). If your solver has no seed, drop `{seed}` and
`seeds:`. That is all there is to write.
