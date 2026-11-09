"""The prompt-tune harness: evolvekit's example prompt pipeline behind the
harness SDK (evk_harness.py, beside this file).

The application is a program, `pipeline.py` (examples/prompt-pipeline/): it
reads an evaluation item, builds a prompt from `prompts/system.md` beside it
and the item's input, and prints the answer's score as one line of JSON. This
runner writes the item for the pipeline, runs it, and reports the score as the
`result` table and the `score` measure. Everything else -- KPIs, guardrails,
exit codes -- is the SDK's.
"""

import json
import os
import re
import subprocess
import sys
import tempfile

import evk_harness as evk


def read_case(path):
    with open(path, "r", encoding="utf-8") as handle:
        item = json.load(handle)
    if not isinstance(item, dict) or not isinstance(item.get("input"), str):
        raise ValueError('an evaluation item is a JSON object with an "input" text')
    return evk.Case({}, native=item, summary=item["input"][:40])


def _program():
    """The pipeline's command. A `.py` program is run with this interpreter."""
    return [sys.executable, evk.APP] if evk.APP.lower().endswith(".py") else [evk.APP]


def solve(case, settings, time_limit_s, seed):
    with tempfile.TemporaryDirectory(prefix="prompt-tune-") as folder:
        item = os.path.join(folder, "item.json")
        with open(item, "w", encoding="utf-8") as handle:
            json.dump(case.native, handle)
        done = subprocess.run(_program() + ["--item", item], capture_output=True, text=True, timeout=120)
    if done.returncode != 0:
        said = (done.stderr.strip().splitlines() or ["no message"])[-1]
        raise evk.HarnessStop("the pipeline failed with exit code {}: {}".format(done.returncode, said))
    for line in reversed(done.stdout.splitlines()):
        if line.strip().startswith("{"):
            return json.loads(line)
    raise evk.HarnessStop("the pipeline printed no score")


def solution_tables(case, solution):
    return {"result": [{"score": float(solution["score"])}]}


def measure(case, solution, tables):
    return {"score": float(solution["score"])}


def discover():
    """The pipeline's version. It has no settings to list (no `settings` key:
    `harness check` then has no drift to look for, rather than warning that the
    list is empty)."""
    version = subprocess.run(_program() + ["--version"], capture_output=True, text=True, timeout=10)
    found = re.search(r"prompt-pipeline (\d+\.\d+)", version.stdout + version.stderr)
    return {"version": found.group(1) if found else None}


if __name__ == "__main__":
    evk.main(globals())
