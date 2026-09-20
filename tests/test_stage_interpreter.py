"""`{python}` in a stage command is the interpreter evolvekit itself runs on.

A bare `python` is looked up by the operating system, and what it finds is not
what a reader of the config expects: inside a virtual environment on Windows it
is the *base* interpreter next to the launcher -- another set of installed
packages, silently another version of the solver. `{python}` cannot miss.
"""

from __future__ import annotations

import sys

from evolvekit.config import StageConfig
from evolvekit.evaluate import build_argv
from evolvekit.evaluate.stages import run_command_stage


def test_the_python_placeholder_is_this_interpreter(tmp_path):
    argv = build_argv(
        "{python} solve.py --out {out}", candidate=tmp_path / "c.py", inputs=[], out=tmp_path / "o.json"
    )
    assert argv[0] == sys.executable and argv[1] == "solve.py"


def test_a_stage_run_with_it_sees_the_packages_evolvekit_sees(tmp_path):
    (tmp_path / "solver.py").write_text(
        "import json, sys\nprint(json.dumps({'same': sys.prefix == sys.argv[1], 'cost': 1.0}))\n", encoding="utf-8"
    )
    stage = StageConfig.parse(
        {"id": "full", "kind": "command", "kpis_from": "stdout", "timeout": 60,
         "command": f'{{python}} solver.py "{sys.prefix}" {{candidate}}'}, 1,
    )
    candidate = tmp_path / "candidate.py"
    candidate.write_text("", encoding="utf-8")
    outcome = run_command_stage(candidate, stage, inputs=(), out_path=tmp_path / "out" / "o.json", cwd=tmp_path)
    assert outcome.ok and outcome.kpis["same"] == 1.0
