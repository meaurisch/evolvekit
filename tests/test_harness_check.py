"""`evolvekit harness check` (`evolvekit/harness/check.py`) and the
application probe (`evolvekit/harness/probe.py`)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from evolvekit.harness.check import check_harness, exit_code, render
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.probe import probe_application, version_matches, version_words
from harness_toy import MANIFEST, write_toy_harness

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "harnesses" / "demo-tour"
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"


@pytest.mark.parametrize(
    "version, specifier, expected",
    [
        ("0.14.0", ">=0.14,<0.15", True),
        ("0.15.1", ">=0.14,<0.15", False),
        ("0.13.9", ">=0.14", False),
        ("1.2.7", "==1.2.*", True),
        ("1.3.0", "==1.2.*", False),
        ("2.0", "!=2.0", False),
        ("1.4.2", "~=1.4", True),
        ("2.0.0", "~=1.4", False),
        ("0.14.0rc1", ">=0.14", True),
        ("1.0", "", True),
        ("1.0", "about 1", False),
    ],
)
def test_version_specifiers(version, specifier, expected):
    assert version_matches(version, specifier) is expected


@pytest.mark.parametrize(
    "specifier, words",
    [(">=0.14,<0.15", "0.14.x"), ("==1.2.*", "1.2.x"), (">=2", "2 or later"),
     (">=1.2,<2", "1.2 or later, before 2"), ("~=1.4", "~=1.4")],
)
def test_version_specifiers_in_words(specifier, words):
    assert version_words(specifier) == words


def test_a_python_application_is_asked_for_its_module(tmp_path):
    toy = load_harness(write_toy_harness(tmp_path / "toy"))
    assert probe_application(toy, sys.executable).ok, "no module required: any Python will do"
    wants = dict(MANIFEST, application={"kind": "python", "label": "Python with a solver",
                                        "requires": {"module": "surely_not_installed_here", "version": ">=1"}})
    harness = load_harness(write_toy_harness(tmp_path / "wants", manifest=wants))
    probe = probe_application(harness, sys.executable)
    assert not probe.ok and probe.message.endswith(", no surely_not_installed_here")
    assert probe.message.startswith(f"Python {sys.version_info.major}.{sys.version_info.minor}")
    assert not probe_application(harness, str(tmp_path / "nowhere.exe")).ok


def test_a_program_is_probed_with_its_command():
    demo = load_harness(DEMO)
    probe = probe_application(demo, str(SOLVER))
    assert probe.ok and probe.version == "1.0" and probe.message == "The example solver (solver.py) 1.0 found"
    wrong = probe_application(demo, str(ROOT / "tasks.py"))
    assert not wrong.ok and "does not look like The example solver" in wrong.message


@pytest.mark.slow
def test_the_demo_harness_passes_its_check():
    checks = check_harness(DEMO, str(SOLVER))
    failed = [c for c in checks if not c.ok]
    assert not failed, render(failed)
    ids = {c.id for c in checks}
    assert {"manifest", "probe", "describe", "trap", "random"} <= ids
    # Levers and exports are checked on the first sample, in name order.
    assert "sample.county-50.json.lever.stop_weights.weight" in ids
    assert "sample.county-50.json.export.requests" in ids
    assert "sample.region-60.json.kpis" in ids and "sample.region-60.json.tables" in ids
    assert exit_code(checks) == 0
    document = json.loads(render(checks, as_json=True))
    assert document["ok"] is True and set(document["checks"][0]) == {"id", "ok", "message", "fix", "severity"}


def _demo_copy(tmp_path: Path, **edit) -> Path:
    target = tmp_path / "demo"
    shutil.copytree(DEMO, target)
    manifest = yaml.safe_load((target / "harness.yaml").read_text(encoding="utf-8"))
    for dotted, value in edit.items():
        keys = dotted.split("__")
        node = manifest
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    (target / "harness.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return target


@pytest.mark.slow
def test_a_setting_the_application_no_longer_has_is_drift(tmp_path):
    broken = _demo_copy(tmp_path, settings__turbo={"type": "bool", "default": False, "label": "Turbo", "help": "Faster."})
    checks = check_harness(broken, str(SOLVER), samples=1)
    messages = [c.message for c in checks if c.severity == "failure"]
    assert "settings.turbo: the harness declares 'turbo', and the installed application no longer has it" in messages
    assert exit_code(checks) == 2


@pytest.mark.slow
def test_a_declared_column_the_runner_does_not_produce_is_found(tmp_path):
    broken = _demo_copy(tmp_path, tables__summary__columns__speed={"type": "float", "describe": "Not produced"})
    checks = check_harness(broken, str(SOLVER), samples=1)
    messages = [c.message for c in checks if c.severity == "failure"]
    assert messages == ["tables.summary.columns.speed: declared, and the runner's rows have no such key"]


def test_the_comparability_trap_is_a_warning(tmp_path):
    broken = _demo_copy(tmp_path)
    template = yaml.safe_load((broken / "templates" / "stop-weights.yaml").read_text(encoding="utf-8"))
    template["kpis"]["solver_length"] = {"from": "harness"}
    template["goal"]["levels"] = [{"kpi": "solver_length", "direction": "lower"}]
    (broken / "templates" / "stop-weights.yaml").write_text(yaml.safe_dump(template), encoding="utf-8")
    checks = check_harness(broken, str(tmp_path / "no-solver-here.py"))
    trap = [c for c in checks if c.id == "trap.stop-weights"]
    assert trap and trap[0].severity == "warning" and "changes with the data changes it tries" in trap[0].message


def test_a_program_harness_needs_its_program():
    checks = check_harness(DEMO)
    assert checks[-1].id == "probe" and checks[-1].fix == "give --app PATH"


def test_a_manifest_mistake_stops_the_check_at_once(tmp_path):
    broken = _demo_copy(tmp_path, kpis__tour_length__sql="SELECT lenght FROM summary")
    checks = check_harness(broken, str(SOLVER))
    assert len(checks) == 1 and checks[0].id == "manifest"
    assert "no such column: lenght" in checks[0].message and exit_code(checks) == 2
