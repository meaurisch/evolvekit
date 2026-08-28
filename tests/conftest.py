"""Shared fixtures. Everything here is offline and deterministic."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXAMPLES_ROOT = ROOT / "examples"
EXAMPLE_DIR = EXAMPLES_ROOT / "binpacking"
EXAMPLE_CONFIG = EXAMPLE_DIR / "evolvekit.yaml"


@pytest.fixture(scope="session")
def example_root() -> Path:
    """`examples/`, for tests that assert across both worked examples."""
    return EXAMPLES_ROOT


@pytest.fixture(scope="session")
def example_dir() -> Path:
    return EXAMPLE_DIR


@pytest.fixture(scope="session")
def example_config_path() -> Path:
    return EXAMPLE_CONFIG


@pytest.fixture(scope="session")
def skeleton_source() -> str:
    return (EXAMPLE_DIR / "skeleton.py").read_text(encoding="utf-8")


@pytest.fixture
def minimal_raw(tmp_path: Path) -> dict:
    """A minimal valid config document plus a skeleton file on disk."""
    skeleton = tmp_path / "skeleton.py"
    skeleton.write_text(
        "# EVOLVE-BLOCK-START\ndef f():\n    return 1\n# EVOLVE-BLOCK-END\n",
        encoding="utf-8",
    )
    return {
        "problem": {"skeleton": "skeleton.py"},
        "evaluate": {
            "stages": [{"id": "static", "kind": "builtin-static"}],
            "score": {"objective": "value"},
        },
        "models": {
            "small": {"provider": "fake", "model": "s", "options": {"responses": ["x"]}},
            "strong": {"provider": "fake", "model": "b", "options": {"responses": ["x"]}},
        },
    }
