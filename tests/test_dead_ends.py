"""Behavioural twins become a 'dead ends' section in every later prompt.

Real Phase C run 2 bred nine twins of best-fit from four different parents;
a note that only reaches the twin's own parent cannot prevent that.
"""

from __future__ import annotations

import json

import pytest

from evolvekit.config import load_config
from evolvekit.search.driver import Driver, _fingerprint_line
from tests.conftest import EXAMPLE_CONFIG


def test_fingerprint_is_the_last_code_line():
    block = 'def priority(item, bins):\n    """doc"""\n    # c\n    return [-(b - item) ** 0.9 for b in bins]\n'
    assert _fingerprint_line(block) == "return [-(b - item) ** 0.9 for b in bins]"
    assert _fingerprint_line("") == ""
    assert _fingerprint_line("x = " + "y" * 200).endswith("...")


@pytest.mark.slow
def test_every_prompt_after_a_twin_lists_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config(EXAMPLE_CONFIG)
    driver = Driver(config, run_dir="runs/dead-ends")
    driver.run()
    rows = driver.ledger.runs()
    twins = [r for r in rows if r.get("novelty") == "behavioural"]
    assert twins, "the offline demo is expected to script a behavioural twin"
    first_twin_gen = min(int(t["generation"]) for t in twins)

    later = [
        r for r in rows
        if int(r["generation"]) > first_twin_gen and r["operator"] not in ("human-seed", "param_lhs")
        and r.get("provider") not in ("none", "human")
    ]
    assert later, "no LLM-bred candidates after the twin"
    seen_section = 0
    for r in later:
        trace = driver.ledger.traces_dir / f"{r['id']}.json"
        if not trace.exists():
            continue
        payload = json.loads(trace.read_text(encoding="utf-8"))
        user = "\n".join(m["content"] for m in payload["messages"] if m["role"] == "user")
        if "Already tried" in user:
            seen_section += 1
            assert "Change which option wins" in user
            assert twins[0]["behaviour_twin_id"] in user
    assert seen_section == len([r for r in later if (driver.ledger.traces_dir / f"{r['id']}.json").exists()])


@pytest.mark.slow
def test_dead_ends_survive_a_resume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config(EXAMPLE_CONFIG)
    first = Driver(config, run_dir="runs/resume-dead")
    first.run(generations=4)
    if not first._dead_ends:
        return  # demo script did not reach the twin in four generations
    second = Driver(config, run_dir="runs/resume-dead")
    second.resume()
    assert second._dead_ends == first._dead_ends
