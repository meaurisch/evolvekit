"""The skeleton `evolvekit harness new DIR` writes when there is no harness to
copy from: a small, *runnable* example -- choosing items to fill a capacity --
so that the first `harness check` passes and every later change can be
checked against a green start. Replace it piece by piece with the real
application; `AGENTS.md` (written beside it) says how.

`kind: python` runs the "solver" inside the runner; `kind: program` runs it
as a separate program (`kit/toy_program.py`), the way a real executable is
run: flags in, one line of JSON out.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

__all__ = ["write_skeleton"]

MANIFEST = """\
# A new evolvekit harness. Replace the example (choosing items to fill a
# capacity) with your application; AGENTS.md says how, and
#   python -m evolvekit harness check . --app PATH --json
# says what is still wrong.
harness: 1
id: my-harness                  # lower-case letters, digits, dashes
version: 0.1.0
title: My application
summary: One sentence on what the application does and what can be tuned.

application:
{application}

cases:
  label: item list
  formats: [.json]
  describe: 'A JSON object with "items": [{{"id": 1, "size": 3.0, "value": 5.0, "group": "a"}}, ...]'

time_limit: {{accepts: false, default_s: 10, min_s: 1}}   # accepts: true when the application stops itself
seeds: false                                             # true when the application takes a seed

settings:
  capacity:
    type: float                 # int | float | bool | choice
    low: 1
    high: 100
    default: 20
    label: Capacity
    group: Packing
    help: How much room there is for items.
    explain: >
      The total size the chosen items may take up. More room lets more value in.
    recommended: true
  order:
    type: choice
    choices: [value, ratio]
    default: ratio
    label: Order of choice
    group: Packing
    help: Take the most valuable items first, or the most valuable per unit of size.
    explain: >
      The greedy order. `ratio` (value per size) is usually better; `value` favours big items.
    recommended: true

tables:
  items:
    source: request
    describe: One row per item that could be chosen.
    columns:
      item_id: {{type: int, describe: The item's number}}
      size: {{type: float, describe: How much room it takes}}
      value: {{type: float, describe: What it is worth}}
      group: {{type: text, categorical: true, describe: Its group}}
  chosen:
    source: solution
    describe: One row per chosen item.
    columns:
      item_id: {{type: int, describe: A chosen item}}
  summary:
    source: solution
    describe: One row about the choice.
    columns:
      value: {{type: float, describe: The value of the chosen items}}
      size: {{type: float, describe: The room they take}}

levers:
  item_values:
    label: Item values
    table: items
    columns:
      value: {{label: value}}
    modes: [scale, set]
    help: Change what some items are worth to the solver.

kpis:
  total_value:
    label: Value chosen
    direction: higher
    positive: true
    help: What the chosen items are worth, at their original values.
    sql: "SELECT TOTAL(o.value) FROM chosen c JOIN orig_items o ON o.item_id = c.item_id"
  room_used:
    label: Room used
    direction: lower
    help: How much room the chosen items take.
    sql: "SELECT size FROM summary"

kpi_templates:
  group_value:
    label: "Value chosen from group {{group}}"
    direction: higher
    params:
      group: {{type: choice, from: "SELECT DISTINCT \\"group\\" FROM items ORDER BY 1"}}
    sql: >
      SELECT TOTAL(o.value) FROM chosen c JOIN orig_items o ON o.item_id = c.item_id
      WHERE o."group" = :group

exports:
  settings_json: {{label: Settings (JSON), for: settings}}
  settings_flags: {{label: Settings as command-line flags, for: settings}}

defaults:
  time_per_case_s: 10
  retries: 1
  budget_hours: 1
  test_share: 0.3
  runs_per_case: 1
"""

PYTHON_APPLICATION = """\
  kind: python
  label: Python                 # e.g. "Python with mysolver installed"
  requires: {module: "", version: ""}   # e.g. {module: mysolver, version: ">=2.1,<3"}
  help: The Python interpreter the application is installed in."""

PROGRAM_APPLICATION = """\
  kind: program
  label: The example program
  probe: "{app} --version"
  expect: "toy-program (\\\\d+\\\\.\\\\d+)"
  help: For the example, kit/toy_program.py in this folder."""

RUNNER_HEAD = '''\
"""The runner of this harness: the application-specific hooks on top of the
evolvekit harness SDK (evk_harness.py -- never edit that file). See AGENTS.md.

The example chooses items greedily until a capacity is full. Replace it with
your application: read a case into tables, solve it, turn the solution into
tables.
"""

import json
import subprocess
import sys

import evk_harness as evk


def read_case(path):
    """The case as tables (lists of dicts), and whatever the solver needs."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError('expected a JSON object with a list of "items"')
    items = [
        {"item_id": int(i["id"]), "size": float(i["size"]), "value": float(i["value"]), "group": str(i.get("group", ""))}
        for i in data["items"]
    ]
    return evk.Case({"items": items}, native=data, summary="{} items".format(len(items)))

'''

PYTHON_SOLVE = '''\
def solve(case, settings, time_limit_s, seed):
    """Solve the case with these settings. Build the problem from
    case.tables: a lever may have changed its cells."""
    items = list(case.tables["items"])
    if settings["order"] == "ratio":
        items.sort(key=lambda i: -i["value"] / i["size"])
    else:
        items.sort(key=lambda i: -i["value"])
    room, chosen = float(settings["capacity"]), []
    for item in items:
        if item["size"] <= room:
            chosen.append(item["item_id"])
            room -= item["size"]
    return chosen

'''

PROGRAM_SOLVE = '''\
def solve(case, settings, time_limit_s, seed):
    """Run the program on the case with these settings. It reads the items
    from a file: write the (possibly changed) case for it first."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        request = os.path.join(folder, "case.json")
        with open(request, "w", encoding="utf-8") as handle:
            json.dump({"items": [{"id": i["item_id"], "size": i["size"], "value": i["value"]} for i in case.tables["items"]]}, handle)
        program = [sys.executable, evk.APP] if evk.APP.endswith(".py") else [evk.APP]
        done = subprocess.run(
            program + ["--case", request, "--capacity", str(settings["capacity"]), "--order", str(settings["order"])],
            capture_output=True, text=True, timeout=60,
        )
    if done.returncode != 0:
        raise evk.HarnessStop("the program failed: " + ((done.stderr.strip().splitlines() or ["no message"])[-1]))
    return json.loads(done.stdout.strip().splitlines()[-1])["chosen"]

'''

RUNNER_TAIL = '''\
def solution_tables(case, solution):
    """The solution as tables. Declare every column in harness.yaml."""
    sizes = {i["item_id"]: i for i in case.tables["items"]}
    return {
        "chosen": [{"item_id": item_id} for item_id in solution],
        "summary": [{"value": sum(sizes[i]["value"] for i in solution), "size": sum(sizes[i]["size"] for i in solution)}],
    }


# Optional hooks -- uncomment what you need:
#
# def write_case(case, path):              the changed request, for the "requests" export
# def apply_lever(case, name, rows, value):  levers declared with `code: true`
# def measure(case, solution, tables):     {kpi: number} for KPIs declared `measure: true`
# def export(format, values, out):         exports beyond the built-in ones


def discover():
    """What the installed application offers, for `harness check`'s drift report."""
    return {"version": "0.1", "settings": {"capacity": {"type": "float", "default": 20}, "order": {"type": "choice", "default": "ratio"}}}


if __name__ == "__main__":
    evk.main(globals())
'''

TOY_PROGRAM = '''\
"""A stand-in for your program: flags in, one line of JSON out."""

import argparse
import json

parser = argparse.ArgumentParser()
parser.add_argument("--version", action="version", version="toy-program 0.1")
parser.add_argument("--case", required=True)
parser.add_argument("--capacity", type=float, default=20)
parser.add_argument("--order", choices=["value", "ratio"], default="ratio")
args = parser.parse_args()
items = json.load(open(args.case, encoding="utf-8"))["items"]
items.sort(key=(lambda i: -i["value"] / i["size"]) if args.order == "ratio" else (lambda i: -i["value"]))
room, chosen = args.capacity, []
for item in items:
    if item["size"] <= room:
        chosen.append(item["id"])
        room -= item["size"]
print(json.dumps({"chosen": chosen}))
'''

TEMPLATE = """\
title: Tune the settings
summary: Find the settings that choose the most value.
vary:
  settings: recommended
kpis:
  total_value: {from: harness}
  room_used: {from: harness}
goal:
  levels:
    - {kpi: total_value, direction: higher}
"""


def write_skeleton(target: Path, kind: str) -> None:
    if kind not in ("python", "program"):
        raise ValueError(f"--kind: python or program, got {kind!r}")
    target.mkdir(parents=True, exist_ok=True)
    application = PYTHON_APPLICATION if kind == "python" else PROGRAM_APPLICATION
    (target / "harness.yaml").write_text(MANIFEST.format(application=application), encoding="utf-8", newline="\n")
    solve = PYTHON_SOLVE if kind == "python" else PROGRAM_SOLVE
    (target / "runner.py").write_text(RUNNER_HEAD + solve + RUNNER_TAIL, encoding="utf-8", newline="\n")
    (target / "templates").mkdir(exist_ok=True)
    (target / "templates" / "tune.yaml").write_text(TEMPLATE, encoding="utf-8", newline="\n")
    (target / "kit").mkdir(exist_ok=True)
    if kind == "program":
        (target / "kit" / "toy_program.py").write_text(TOY_PROGRAM, encoding="utf-8", newline="\n")
    (target / "samples").mkdir(exist_ok=True)
    for index in range(2):
        rng = random.Random(index)
        items = [{"id": i, "size": round(rng.uniform(1, 10), 2), "value": round(rng.uniform(1, 20), 2),
                  "group": "ab"[i % 2]} for i in range(12 + 6 * index)]
        (target / "samples" / f"sample-{index + 1}.json").write_text(json.dumps({"items": items}) + "\n", encoding="utf-8")
    (target / "README.md").write_text(
        "# My application\n\nWhat the harness is for, which application it needs and where to find it, and what its\n"
        "cases look like. The app shows this file under \"About this harness\".\n",
        encoding="utf-8", newline="\n",
    )
