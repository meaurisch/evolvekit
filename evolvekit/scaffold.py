"""What `init --template tune` writes: a tuning setup that runs as it is.

A scaffold that cannot be run teaches nothing -- the first thing a new user
learns from it is an error message. This one is complete: a stand-in solver
(replace it with your command), three tiny instances, and a config whose every
line is the one you will keep or change. `preflight` passes on it and `run`
finishes in about a minute, with no model and no key.
"""

from __future__ import annotations

__all__ = ["TUNE_FILES", "TUNE_NEXT"]

_CONFIG = """\
# Tuning a command-line program with evolvekit. Runs as it is:
#
#   python -m evolvekit preflight --config evolvekit.yaml
#   python -m evolvekit run --config evolvekit.yaml --run-dir runs/first --dashboard
#   python -m evolvekit confirm --config evolvekit.yaml --run-dir runs/first --seeds 1001,1002,1003
#   python -m evolvekit export --run-dir runs/first --format flags --config evolvekit.yaml
#
# Then make it yours: your command, your instances, your parameters.

problem:
  description: Minimise the cost the solver reaches within its time limit.
  # What may be tuned. The defaults are the baseline every result is compared
  # with, so give your program's real defaults. Types: int, float (log: true
  # for a range that spans decades), bool, choice.
  parameters:
    steps:    {type: int, low: 1, high: 64, default: 8, help: moves tried per iteration}
    cooling:  {type: float, low: 0.0001, high: 0.1, default: 0.01, log: true, help: how fast worse moves stop being accepted}
    greedy:   {type: bool, default: false, help: start from a greedy solution}
    # A parameter is passed as `--name value` (underscores become dashes;
    # booleans as true/false). `flag: "-n"` overrides the flag.

evaluate:
  stages:
    - {id: static, kind: builtin-static}     # checks every configuration before it costs anything

    - id: full
      kind: command
      # YOUR COMMAND HERE. {python} is the interpreter running evolvekit; a
      # binary is just `./solver --instance {instance} ...`.
      command: "{python} solver.py --instance {instance} --seed {seed} --time-limit 0.5 {params}"
      kpis_from: stdout          # the last JSON object the program prints.
                                 # Or: kpi_patterns: {cost: 'cost = ([0-9.]+)'} for text,
                                 # or {out} in the command for a JSON file it writes.
      instances: ["instances/*.json"]   # the command runs once per instance (and seed)
      seeds: 2                   # runs per instance; 1 if your solver has no seed
      timeout: 30                # per run; a run that exceeds it is killed, process tree and all
      workers: 2                 # runs side by side. Never more than physical cores;
      # pin_cpus: [2, 4]         # one logical CPU per worker, so a time-limited run has a core to itself
      retries: 1                 # run a crashed or timed-out run once more
      # A slow solver wants a cheap `screen` stage before this one: the same
      # command with a shorter time limit and fewer instances, and
      # `promote: {top_k_per_generation: 2}`.

  score: {objective: cost, direction: minimize}

search:
  # No model needed. Lead with param_local when the defaults are already good.
  operators: {param_local: 0.45, param_tpe: 0.25, param_lhs: 0.2, param_cross: 0.1}
  children_per_generation: 6
  generations: 6
  seed: 1
  novelty: {behavioural: "off"}

budget:
  max_full_evals_per_day: 500    # candidates admitted to the final stage per day
stop:
  patience: 6                    # generations without improvement before stopping
"""

_SOLVER = '''\
"""A stand-in for your solver: replace it. evolvekit never imports this file;
it runs it, passes flags, and reads the JSON object on its last line."""

import argparse
import json
import math
import random
import time

p = argparse.ArgumentParser()
p.add_argument("--instance", required=True)
p.add_argument("--seed", type=int, default=0)
p.add_argument("--time-limit", type=float, default=0.5)
p.add_argument("--steps", type=int, default=8)
p.add_argument("--cooling", type=float, default=0.01)
p.add_argument("--greedy", choices=("true", "false"), default="false")
a = p.parse_args()

spec = json.load(open(a.instance, encoding="utf-8"))
rng = random.Random(a.seed * 7919 + spec["seed"])
targets = [random.Random(spec["seed"] + i).uniform(-1, 1) for i in range(spec["size"])]
x = [t * 0.5 for t in targets] if a.greedy == "true" else [0.0] * len(targets)
cost = lambda v: sum((vi - ti) ** 2 for vi, ti in zip(v, targets))  # noqa: E731
current, temperature, deadline = cost(x), 1.0, time.perf_counter() + a.time_limit
while time.perf_counter() < deadline:
    for _ in range(a.steps):
        i = rng.randrange(len(x))
        old, x[i] = x[i], x[i] + rng.gauss(0, 0.1)
        new = cost(x)
        if new < current or rng.random() < math.exp(-(new - current) / max(temperature, 1e-9)):
            current = new
        else:
            x[i] = old
    temperature *= 1.0 - a.cooling
print(json.dumps({"cost": round(current, 6), "instance": spec["name"]}))
'''


def _instance(name: str, seed: int, size: int) -> str:
    return '{"name": "%s", "seed": %d, "size": %d}\n' % (name, seed, size)


TUNE_FILES: dict[str, str] = {
    "evolvekit.yaml": _CONFIG,
    "solver.py": _SOLVER,
    "instances/small.json": _instance("small", 11, 40),
    "instances/medium.json": _instance("medium", 12, 120),
    "instances/large.json": _instance("large", 13, 400),
}

TUNE_NEXT = """\

Runs as it is -- about a minute, no model, no key:
  python -m evolvekit preflight --config {config}
  python -m evolvekit run --config {config} --run-dir {run_dir} --dashboard
  python -m evolvekit confirm --config {config} --run-dir {run_dir} --seeds 1001,1002,1003

Then replace `solver.py` in the command by your program, list your instances,
and declare your parameters with your program's real defaults.
"""
