"""Screening as a controller (`search/screening.py`, S6).

A cascade bets that its cheap stage ranks candidates as the expensive one
does. Every promoted candidate has a score at both, so the bet is checked
after each round: a screen that agrees only weakly promotes one more a round,
and one that disagrees two rounds running -- or that would promote every
child -- is switched off, its stage passed over for the rest of the run.
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

import pytest

from evolvekit.candidate import Candidate
from evolvekit.config import ConfigError, PromoteRule, build_config
from evolvekit.events import read_events
from evolvekit.search.driver import Driver
from evolvekit.search.screening import SCREENING_FILE, ScreenControl

WEAK = [(float(i), float((4 * i) % 9)) for i in range(9)]  # rho = 0.100 (Decision 12)


# -- the controller ----------------------------------------------------------


def test_a_weak_screen_promotes_one_more_and_a_contrary_one_is_switched_off(tmp_path):
    control = ScreenControl("screen", k=2, cap=6, path=tmp_path / "screening.json")
    assert "promotes 3" in control.after_round(3, WEAK) and control.k == 3
    contrary = [(float(i), float(-i)) for i in range(9)]
    assert control.after_round(4, contrary) is None or control.k == 4
    assert "off" in control.after_round(5, contrary) and control.off
    assert ScreenControl.load(tmp_path / "screening.json", "screen", 2, 6).off, "it survives a resume"


def test_the_sentences_say_what_changed_and_why(tmp_path):
    control = ScreenControl("screen", k=2, cap=6, path=None)
    said = control.after_round(3, WEAK)
    assert said == (
        "screening promotes 3 a round from now on: it agrees with the full stage only weakly "
        "(rho = 0.10 over 9 candidates)"
    )
    contrary = [(float(i), float(-i)) for i in range(12)]
    control.after_round(4, contrary)
    assert control.after_round(5, contrary) == (
        "screening off for the rest of the run: its ranking disagreed with the full stage's two rounds "
        "running (rho = -1.00 over 12 candidates)"
    )
    assert control.after_round(6, contrary) is None, "off is off: nothing more to change"
    assert [h["generation"] for h in control.history] == [3, 4, 5]
    assert control.history[0] == {"generation": 3, "n": 9, "rho": pytest.approx(0.1), "k": 3, "off": False,
                                  "said": said}


def test_fewer_than_eight_pairs_change_nothing(tmp_path):
    control = ScreenControl("screen", k=2, cap=6, path=tmp_path / "screening.json")
    contrary = [(float(i), float(-i)) for i in range(7)]
    assert control.after_round(1, contrary) is None and control.after_round(2, contrary) is None
    assert (control.k, control.off, control.below_zero, control.history) == (2, False, 0, [])
    flat = [(1.0, float(i)) for i in range(9)]  # a screen that does not vary: no rank correlation
    assert control.after_round(3, flat) is None and (control.k, control.off) == (2, False)
    saved = json.loads((tmp_path / "screening.json").read_text(encoding="utf-8"))
    assert saved == {"stage": "screen", "k": 2, "off": False, "below_zero": 0, "history": []}


def test_a_check_at_or_above_zero_resets_the_count_of_contrary_rounds():
    control = ScreenControl("screen", k=2, cap=20, path=None)
    contrary = [(float(i), float(-i)) for i in range(9)]
    agreeing = [(float(i), float(i)) for i in range(9)]
    control.after_round(1, contrary)
    control.after_round(2, agreeing)
    assert control.below_zero == 0
    control.after_round(3, contrary)
    assert not control.off and control.below_zero == 1


def test_a_screen_whose_k_reaches_the_cap_is_switched_off(tmp_path):
    """Decision 10: promoting every child is no screen at all; the stage is skipped,
    with the same sentence and event as a contrary screen."""
    control = ScreenControl("screen", k=2, cap=3, path=None)
    control.after_round(3, WEAK)
    assert control.k == 3 and control.off and "off for the rest of the run" in control.history[-1]["said"]
    assert control.history[-1]["said"].endswith("it would promote every child)")


def test_a_missing_or_foreign_file_starts_from_the_configured_k(tmp_path):
    path = tmp_path / "screening.json"
    assert (ScreenControl.load(path, "screen", 2, 6).k, ScreenControl.load(path, "screen", 2, 6).off) == (2, False)
    path.write_text(json.dumps({"stage": "proxy", "k": 5, "off": True, "below_zero": 0, "history": []}),
                    encoding="utf-8")
    assert ScreenControl.load(path, "screen", 2, 6).off is False, "another stage's state is not this one's"
    path.write_text("{not json", encoding="utf-8")
    assert ScreenControl.load(path, "screen", 2, 6).k == 2


def _run(rng, rho0, rounds=10, children=6):
    control, pairs = ScreenControl("screen", k=2, cap=children, path=None), []
    grew = False
    for g in range(1, rounds + 1):
        if control.off:
            break
        drawn = []
        for _ in range(children):
            x = rng.gauss(0, 1)
            drawn.append((x, rho0 * x + math.sqrt(1 - rho0**2) * rng.gauss(0, 1)))
        drawn.sort(key=lambda p: -p[0])
        pairs += drawn[: control.k]
        before = control.k
        control.after_round(g, pairs)
        grew |= control.k > before
    return control, grew


def test_a_screen_that_agrees_is_switched_off_in_under_5_percent_of_runs():
    """rho 0.8 before selection; within the promoted top third it is about 0.65
    (range restriction; measured Spearman 0.64). Measured off 0.95 % (SE 0.22 %).
    Also printed: how often k grows (measured 14.3 %)."""
    rng = random.Random(41)
    runs = [_run(rng, 0.8) for _ in range(2000)]
    off = sum(c.off for c, _ in runs) / 2000
    print(f"agreeing screen: off {off:.1%}, k grew {sum(g for _, g in runs) / 2000:.1%}")
    assert off < 0.05


def test_a_screen_that_does_not_predict_grows_k_in_over_85_percent_of_runs():
    """rho 0: rho-hat < 0.2 with probability about 0.7 at each check from round 4 on;
    measured 90.8 % (SE 0.65 %), so the bound is 85 % (Decision 12)."""
    rng = random.Random(42)
    grew = sum(g for _, g in (_run(rng, 0.0) for _ in range(2000))) / 2000
    print(f"unrelated screen: k grew {grew:.1%}")
    assert grew > 0.85


# -- the configuration -------------------------------------------------------


def test_adapt_is_read_with_a_top_k_and_refused_without_one():
    rule = PromoteRule.parse({"top_k_per_generation": 2, "adapt": True}, "evaluate.stages[1].promote")
    assert rule.top_k_per_generation == 2 and rule.adapt is True
    assert PromoteRule.parse({"top_k_per_generation": 2}, "p").adapt is False
    with pytest.raises(ConfigError, match=r"evaluate\.stages\[1\]\.promote\.adapt"):
        PromoteRule.parse({"archive_percentile": 50, "adapt": True}, "evaluate.stages[1].promote")


# -- the cascade -------------------------------------------------------------

SOLVER = (
    "import argparse, json\n"
    "p = argparse.ArgumentParser()\n"
    "p.add_argument('--out'); p.add_argument('--stage'); p.add_argument('--x', type=float)\n"
    "a = p.parse_args()\n"
    # The screen ranks the other way round: the full stage minimises x, the screen 1 - x.
    "cost = a.x if a.stage == 'full' else 1.0 - a.x\n"
    "json.dump({'kpis': {'cost': cost}}, open(a.out, 'w'))\n"
)


def _raw(tmp_path: Path, *, generations: int = 7, children: int = 4) -> dict:
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")

    def stage(name: str, **extra) -> dict:
        return {"id": name, "kind": "command", "timeout": 60, **extra,
                "command": f'"{sys.executable}" solver.py --out {{out}} --stage {name} {{params}}'}

    return {
        "problem": {"parameters": {"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.5}},
                    "description": "Tune x."},
        "evaluate": {
            "stages": [{"id": "static", "kind": "builtin-static"},
                       stage("screen", promote={"top_k_per_generation": 2, "adapt": True}),
                       stage("full")],
            "score": {"objective": "cost", "direction": "minimize"},
        },
        "search": {
            "operators": {"param_lhs": 1.0},
            "children_per_generation": children,
            "generations": generations,
            "seed": 3,
            "novelty": {"behavioural": "off", "near": {"method": "off"}},
        },
    }


def test_the_cascade_promotes_its_adapted_k_and_passes_a_skipped_stage_over(tmp_path):
    config = build_config(_raw(tmp_path), base_dir=tmp_path)
    driver = Driver(config, run_dir=tmp_path / "run")  # for the skeleton's prefix and suffix
    cascade = driver.cascade

    def candidates(prefix: str) -> list[Candidate]:
        out = []
        for i, x in enumerate((0.1, 0.4, 0.7, 0.9)):
            block = driver.seed_block.replace("0.5", str(x))
            out.append(Candidate(id=f"{prefix}{i}", generation=1, block=block,
                                 source=driver._make_source(block), operator="param_lhs"))
        return out

    cascade.promote_k["screen"] = 3
    results = cascade.evaluate_generation(candidates("a"))
    reached = sorted(cid for cid, r in results.items() if "full" in r.stages_reached)
    assert reached == ["a1", "a2", "a3"], "the three best by the screen (largest x)"
    cascade.skipped.add("screen")
    results = cascade.evaluate_generation(candidates("b"))
    assert all(r.stages_reached == ["static", "full"] for r in results.values())
    assert all("screen" not in r.stage_scores and r.competes for r in results.values())


def test_a_screen_that_disagrees_grows_k_then_is_switched_off_in_a_run(tmp_path):
    """The screen ranks every candidate the other way round from the full stage:
    the first check (9 pairs, after round 4) promotes 3, the second switches it
    off, and the rounds after that pass the screen over. A resumed run keeps it off."""
    lines: list[str] = []
    config = build_config(_raw(tmp_path), base_dir=tmp_path)
    Driver(config, run_dir=tmp_path / "run", log=lines.append).run()
    run = tmp_path / "run"
    saved = json.loads((run / "work" / SCREENING_FILE).read_text(encoding="utf-8"))
    assert saved["stage"] == "screen" and saved["k"] == 3 and saved["off"] is True
    assert any("screening promotes 3 a round from now on" in line for line in lines)
    assert any("screening off for the rest of the run" in line for line in lines)
    changed = [e for e in read_events(run) if e.get("type") == "screening_changed"]
    assert [(e["stage"], e["k"], e["off"], e["n"]) for e in changed] == [("screen", 3, False, 9),
                                                                       ("screen", 3, True, 12)]
    assert all(e["rho"] == pytest.approx(-1.0) for e in changed)
    rows = [json.loads(line) for line in (run / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    late = [r for r in rows if r["generation"] >= 6]
    assert late and all("screen" not in r["stages_reached"] and r["competes"] for r in late)
    resumed = Driver(config, run_dir=run)
    assert resumed.cascade.skipped == {"screen"} and resumed.cascade.promote_k == {"screen": 3}


def test_a_stage_without_adapt_keeps_its_k(tmp_path):
    raw = _raw(tmp_path)
    raw["evaluate"]["stages"][1]["promote"] = {"top_k_per_generation": 2}
    driver = Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run")
    assert driver.screens == {} and driver.cascade.promote_k == {} and not driver.cascade.skipped
