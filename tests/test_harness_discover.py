"""`evolvekit harness discover`: a first list of what an application exposes."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from evolvekit.cli import main
from evolvekit.harness.discover import discover_program, discover_python, parse_help

ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"

GNU_HELP = """Usage: packer [OPTION]... FILE
Pack boxes into crates.

  -c, --crates=COUNT     how many crates to fill (default: 4)
      --spread FACTOR    how far apart the heavy boxes go [default: 0.5]
  -h, --help             display this help and exit
  -V, --version          output version information and exit
"""


def _script(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "thing.py"
    path.write_text(body, encoding="utf-8")
    return path


def test_parse_help_argparse_shape():
    text = subprocess.run([sys.executable, str(SOLVER), "--help"], capture_output=True, text=True, timeout=30).stdout
    settings = {s["name"]: s for s in parse_help(text)}
    assert set(settings) >= {"instance", "seed", "time-limit", "neighbours", "restart-after", "accept-worse",
                             "init", "or-opt", "print-tour"}
    assert not set(settings) & {"help", "version"}
    assert settings["init"]["type"] == "choice" and settings["init"]["choices"] == ["random", "nearest"]
    assert settings["accept-worse"]["type"] == "float" and settings["accept-worse"]["default"] == 0.0
    assert settings["instance"]["required"] is True
    assert settings["seed"]["required"] is False
    assert settings["print-tour"]["type"] == "bool"
    assert settings["neighbours"]["help"].startswith("candidate moves per city")


def test_parse_help_gnu_shape():
    settings = parse_help(GNU_HELP)
    assert [s["name"] for s in settings] == ["crates", "spread"]
    assert settings[0]["type"] == "int" and settings[0]["default"] == 4
    assert settings[1]["type"] == "float" and settings[1]["default"] == 0.5


def test_discover_help_unparseable(tmp_path):
    program = _script(tmp_path, 'print("Usage: thing\\nSee the manual.")\n')
    found = discover_program(str(program))
    assert found["settings"] == []
    assert len(found["notes"]) == 1


def test_discover_help_hangs(tmp_path):
    program = _script(tmp_path, 'import sys, time\nif "--help" in sys.argv:\n    time.sleep(60)\nprint("thing 2.1")\n')
    started = time.monotonic()
    found = discover_program(str(program))
    assert time.monotonic() - started < 12
    assert any("did not answer --help" in note for note in found["notes"])
    assert found["application"]["version"] == "thing 2.1"


def test_discover_program_version():
    found = discover_program(str(SOLVER))
    assert found["application"]["kind"] == "program"
    assert found["application"]["version"] == "cli-solver 1.0"
    assert found["application"]["version_number"] == "1.0"
    assert "accept-worse" in {s["name"] for s in found["settings"]}


def test_discover_python_module():
    found = discover_python(sys.executable, "json")
    assert found["application"]["kind"] == "python"
    settings = {s["name"]: s for s in found["settings"]}
    assert "dumps.indent" in settings
    assert settings["dumps.indent"]["default"] is None
    assert settings["dumps.indent"]["source"] == "json.dumps"


def test_cli_discover_json(capsys):
    assert main(["harness", "discover", "--program", str(SOLVER), "--json"]) == 0
    found = json.loads(capsys.readouterr().out)
    assert found["application"]["version"] == "cli-solver 1.0"
    assert main(["harness", "discover", "--python", sys.executable, "--module", "json"]) == 0
    assert "dumps.indent" in capsys.readouterr().out
