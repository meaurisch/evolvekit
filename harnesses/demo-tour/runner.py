"""The demo-tour harness: evolvekit's example command-line solver behind the
harness SDK (evk_harness.py, beside this file).

The application is a program, `solver.py` (examples/cli-solver/): it reads a
tour request, takes its settings as `--flags`, and prints one line of JSON.
This runner turns a request into the `stops` table, writes the (possibly
changed) request back for the solver, runs it, and turns its answer into the
`tour` and `summary` tables. Everything else -- data changes, constraints,
KPIs, guardrails, exit codes -- is the SDK's.
"""

import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile

import evk_harness as evk


def _tag(y, height):
    return "north" if y >= height / 2 else "south"


def read_case(path):
    with open(path, "r", encoding="utf-8") as handle:
        spec = json.load(handle)
    if not isinstance(spec, dict):
        raise ValueError("a tour request is a JSON object")
    if "stops" in spec:
        raw = spec["stops"]
        if not isinstance(raw, list) or len(raw) < 4:
            raise ValueError("`stops` must list at least four [x, y] or [x, y, weight] entries")
        tags = spec.get("tags") or {}
        height = max(float(s[1]) for s in raw) + min(float(s[1]) for s in raw)
        stops = [
            {"stop_id": i, "x": float(s[0]), "y": float(s[1]),
             "weight": float(s[2]) if len(s) > 2 else 1.0,
             "tag": str(tags.get(str(i), _tag(float(s[1]), height)))}
            for i, s in enumerate(raw)
        ]
    elif "seed" in spec and "cities" in spec:
        # The example's own format: the solver places the cities at random.
        # The same formula as solver.py, so the tables match what it solves.
        seed = int(spec["seed"])
        stops = [
            {"stop_id": i, "x": random.Random(seed + i).random(), "y": random.Random(seed - i - 1).random(),
             "weight": 1.0, "tag": ""}
            for i in range(int(spec["cities"]))
        ]
        for stop in stops:
            stop["tag"] = _tag(stop["y"], 1.0)
    else:
        raise ValueError("not a tour request: it has neither `stops` nor a `seed` and a number of `cities`")
    return evk.Case({"stops": stops}, native=spec, summary="{} stops".format(len(stops)))


def write_case(case, path):
    spec = {k: v for k, v in case.native.items() if k not in ("cities", "stops", "tags", "crash_rate")}
    spec.setdefault("name", os.path.splitext(os.path.basename(case.path or "case"))[0])
    spec["stops"] = [[row["x"], row["y"], row["weight"]] for row in case.tables["stops"]]
    spec["tags"] = {str(row["stop_id"]): row["tag"] for row in case.tables["stops"]}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(spec, handle)


def _program():
    """The solver's command. A `.py` program is run with this interpreter."""
    return [sys.executable, evk.APP] if evk.APP.lower().endswith(".py") else [evk.APP]


def _flag_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def solve(case, settings, time_limit_s, seed):
    limit = float(time_limit_s or 2.0)
    with tempfile.TemporaryDirectory(prefix="demo-tour-") as folder:
        request = os.path.join(folder, "request.json")
        write_case(case, request)
        command = _program() + ["--instance", request, "--seed", str(seed), "--time-limit", repr(limit), "--print-tour"]
        for name, value in settings.items():
            command += ["--" + name.replace("_", "-"), _flag_value(value)]
        done = subprocess.run(command, capture_output=True, text=True, timeout=limit * 3 + 30)
    if done.returncode != 0:
        said = (done.stderr.strip().splitlines() or ["no message"])[-1]
        raise evk.HarnessStop("the solver failed with exit code {}: {}".format(done.returncode, said))
    for line in reversed(done.stdout.splitlines()):
        if line.strip().startswith("{"):
            return json.loads(line)
    raise evk.HarnessStop("the solver printed no result")


def solution_tables(case, solution):
    stops = case.tables["stops"]
    tour = solution["tour"]
    rows = []
    for position, stop in enumerate(tour):
        previous = tour[position - 1]
        a, b = stops[previous], stops[stop]
        leg = math.hypot(a["x"] - b["x"], a["y"] - b["y"])
        rows.append({"position": position, "stop_id": stop, "previous_id": previous, "leg": leg,
                     "weighted_leg": leg * (a["weight"] + b["weight"]) / 2})
    summary = {"length": sum(r["leg"] for r in rows), "weighted_length": sum(r["weighted_leg"] for r in rows),
               "iterations": int(solution["iterations"])}
    return {"tour": rows, "summary": [summary]}


def discover():
    """The solver's version, and the settings its command line offers."""
    version = subprocess.run(_program() + ["--version"], capture_output=True, text=True, timeout=10)
    helped = subprocess.run(_program() + ["--help"], capture_output=True, text=True, timeout=10)
    found = re.search(r"cli-solver (\d+\.\d+)", version.stdout + version.stderr)
    skip = {"help", "version", "instance", "seed", "time_limit", "print_tour"}
    options = sorted({o.replace("-", "_") for o in re.findall(r"--([a-z][a-z-]*)", helped.stdout)} - skip)
    return {"version": found.group(1) if found else None, "settings": {name: {} for name in options}}


if __name__ == "__main__":
    evk.main(globals())
