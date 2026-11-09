"""The prompt-tune harness (`harnesses/prompt-tune/`, design §5.7): an example
pipeline whose "code" is a prompt file (`examples/prompt-pipeline/`), behind
evolvekit. A code study changes `prompts/system.md`; the fake provider scores an
answer by hashing the prompt with the item, so a changed prompt changes the
score."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tree_helpers import diff_text, git, git_init_commit

from evolvekit.app.verdict import DECISIONS, build_verdict
from evolvekit.harness.check import check_harness
from evolvekit.harness.execute import run_study
from evolvekit.harness.library import create_study
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.study import load_study, save_study
from evolvekit.ledger import read_jsonl

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "harnesses" / "prompt-tune"
EXAMPLE = ROOT / "examples" / "prompt-pipeline"
PIPELINE = EXAMPLE / "pipeline.py"


def _pipeline(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(PIPELINE), *args], capture_output=True, text=True,
                          encoding="utf-8", timeout=60, env=env)


def test_the_pipeline_scores_an_item_by_its_prompt():
    assert _pipeline("--version").stdout.strip() == "prompt-pipeline 1.0"
    item = sorted((EXAMPLE / "items").glob("*.json"))[0]
    done = _pipeline("--item", str(item))
    assert done.returncode == 0, done.stderr
    score = json.loads(done.stdout.strip().splitlines()[-1])["score"]
    assert 0 <= score < 1
    other = _pipeline("--item", str(item), env={**os.environ, "EVOLVEKIT_PROMPT_PROVIDER": "remote"})
    assert other.returncode == 2 and "prompt-pipeline: only the fake provider is built in" in other.stderr


def test_the_harness_loads_with_its_template():
    harness = load_harness(HARNESS)
    assert harness.id == "prompt-tune" and harness.application.kind == "program"
    assert harness.code is not None and harness.code.open == ("prompts/system.md",)
    assert set(harness.templates) == {"improve"}
    assert len(list((EXAMPLE / "items").glob("*.json"))) == 6
    assert len(list((HARNESS / "samples").glob("*.json"))) == 3


def test_prompt_tune_check_passes():
    checks = check_harness(HARNESS, str(PIPELINE))
    failed = [c for c in checks if c.severity == "failure"]
    assert not failed, failed
    assert {"code.repo", "code.open", "code.samples"} <= {c.id for c in checks if c.ok}


def _pipeline_repo(tmp_path) -> Path:
    """examples/prompt-pipeline as a repository of its own: a study's run makes
    worktrees and candidate commits, never in the evolvekit checkout."""
    top = tmp_path / "prompt repo"
    shutil.copytree(EXAMPLE, top, ignore=shutil.ignore_patterns("__pycache__"))
    git_init_commit(top)
    return top


@pytest.mark.slow
def test_prompt_tune_code_study_runs(tmp_path):
    top = _pipeline_repo(tmp_path)
    folder = tmp_path / "study"
    create_study(folder, HARNESS, "Prompt", "improve")
    for item in sorted((EXAMPLE / "items").glob("*.json")):
        shutil.copy(item, folder / "cases" / item.name)
    study = load_study(folder)
    study.code.repo = str(top)
    study.application_path = str(top / "pipeline.py")
    names = sorted(p.name for p in (folder / "cases").iterdir())
    study.training, study.test = [f"cases/{n}" for n in names[:4]], [f"cases/{n}" for n in names[4:]]
    study.budget.ai_enabled, study.plan = True, {"auto": False, "children": 2, "generations": 2}
    save_study(study, folder)
    old = git(top, "show", "HEAD:prompts/system.md") + "\n"
    diffs = [diff_text("prompts/system.md", old, old + "Answer in %d words.\n" % n) for n in (5, 9, 12, 20)]
    replies = ["```diff\n" + d + "```" for d in diffs]
    models = {r: {"provider": "fake", "model": "f", "options": {"responses": replies}} for r in ("small", "strong")}
    job = run_study(folder, "r1", models=models, log=lambda _m: None)
    assert job["phase"] == "done" and job["search"]["generations"] == 2, job
    rows = list(read_jsonl(folder / "runs" / "r1" / "runs.jsonl"))
    assert any("Answer in" in r["block"] and not r["rejected"] for r in rows)
    assert len({r["kpis"].get("score") for r in rows if not r["rejected"]}) > 1, "a changed prompt changes the score"
    assert build_verdict(folder, "r1")["decision"] in DECISIONS
    assert git(top, "status", "--porcelain") == "", "the engineer's checkout is untouched"
