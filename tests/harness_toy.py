"""A tiny harness for the harness and study tests: items to pick, one data
change, three settings. Its runner is the one `test_harness_sdk.py` drives."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from evolvekit.harness.sdk import SDK_PATH
from test_harness_sdk import CASE, RUNNER

MANIFEST = {
    "harness": 1,
    "id": "toy",
    "version": "1.0.0",
    "title": "Toy",
    "summary": "Pick items: a stand-in for a real application.",
    "application": {"kind": "python", "label": "Any Python", "help": "The interpreter you run evolvekit with."},
    "cases": {"label": "item list", "formats": [".json"], "describe": "A JSON object with `items`."},
    "inputs": {
        "start": {"label": "Settings to start from", "formats": [".json"], "provides": "settings"},
    },
    "time_limit": {"accepts": True, "default_s": 5, "min_s": 1},
    "seeds": True,
    "settings": {
        "threshold": {"type": "float", "low": 0.0, "high": 10.0, "default": 3.0, "label": "Threshold",
                      "group": "Picking", "help": "Items above it are picked.",
                      "explain": "Items whose value exceeds the threshold are picked.", "recommended": True},
        "factor": {"type": "float", "low": 0.5, "high": 2.0, "default": 1.0, "label": "Factor",
                   "group": "Scoring", "help": "Scales the total.", "recommended": True},
        "crash": {"type": "bool", "default": False, "label": "Crash", "group": "Testing", "help": "Falls over."},
    },
    "tables": {
        "items": {"source": "request", "describe": "One row per item.", "columns": {
            "id": {"type": "int", "describe": "Item id"},
            "kind": {"type": "text", "categorical": True, "describe": "a or b"},
            "value": {"type": "float", "describe": "Its value"},
            "weight": {"type": "float", "describe": "Its weight"},
            "count": {"type": "int", "describe": "How many"},
            "tag": {"type": "text", "categorical": True, "describe": "A tag"},
        }},
        "picks": {"source": "solution", "columns": {"id": {"type": "int", "describe": "A picked item"}}},
        "summary": {"source": "solution", "columns": {
            "total": {"type": "float", "describe": "The total"},
            "time_limit": {"type": "float", "describe": "The time limit it was given"},
        }},
    },
    "levers": {
        "item_values": {"label": "Item values", "table": "items", "columns": {"value": {"label": "value"}},
                        "modes": ["scale", "set", "add"], "help": "Change some items' values.",
                        "explain": "The solver picks by value; changing values changes the picks."},
        "boost": {"label": "Boost", "table": "items", "code": True, "modes": ["add"], "help": "Tag and raise."},
    },
    "kpis": {
        "total": {"label": "Total", "direction": "lower", "sql": "SELECT total FROM summary", "positive": True},
        "picked_count": {"label": "Items picked", "direction": "higher", "measure": True},
        "value_moved": {"label": "Value moved", "direction": "lower", "changes_with_levers": True,
                        "sql": "SELECT TOTAL(i.value - o.value) FROM items i JOIN orig_items o ON o.id = i.id"},
    },
    "kpi_templates": {
        "kind_value": {"label": "Value of kind {kind}", "direction": "lower",
                       "params": {"kind": {"type": "choice", "from": "SELECT DISTINCT kind FROM items ORDER BY kind"}},
                       "sql": "SELECT TOTAL(value) FROM items WHERE kind = :kind"},
    },
    "exports": {"settings_json": {"label": "Settings (JSON)", "for": "settings"}},
    "defaults": {"time_per_case_s": 5, "retries": 1, "budget_hours": 0.05, "test_share": 0.3, "runs_per_case": 1},
}

TEMPLATES = {
    "tune": {
        "title": "Tune the settings",
        "summary": "The settings with the lowest total.",
        "vary": {"settings": "recommended"},
        "kpis": {"total": {"from": "harness"}},
        "goal": {"levels": [{"kpi": "total", "direction": "lower"}]},
    },
    "values": {
        "title": "Item values for more picks",
        "summary": "Which values of kind a give the most picks.",
        "vary": {"data": {"a_values": {"lever": "item_values", "column": "value", "where": "kind = 'a'",
                                       "mode": "scale", "low": 0.5, "high": 2.0, "start": 1.0}}},
        "kpis": {"picked_count": {"from": "harness"}, "total": {"from": "harness"}},
        "goal": {"levels": [{"kpi": "picked_count", "direction": "higher", "equal_within": 0.5},
                            {"kpi": "total", "direction": "lower"}]},
        "guardrails": [{"kpi": "total", "max": 1000}],
    },
}


def write_toy_harness(root: Path, *, manifest: dict | None = None, templates: dict | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "harness.yaml").write_text(yaml.safe_dump(manifest or MANIFEST, sort_keys=False), encoding="utf-8")
    shutil.copy(SDK_PATH, root / "evk_harness.py")
    (root / "runner.py").write_text(RUNNER, encoding="utf-8")
    (root / "templates").mkdir(exist_ok=True)
    for name, template in (TEMPLATES if templates is None else templates).items():
        (root / "templates" / f"{name}.yaml").write_text(yaml.safe_dump(template, sort_keys=False), encoding="utf-8")
    (root / "samples").mkdir(exist_ok=True)
    for index in range(3):
        items = [dict(item, value=item["value"] + index) for item in CASE["items"]]
        (root / "samples" / f"s{index}.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    return root
