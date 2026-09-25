"""`confirm`: the run's best against its baseline, paired, on seeds the search
never saw -- and `export`, the winner in a form another program can use."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evolvekit.cli import main
from evolvekit.config import ConfigError, build_config, load_config
from evolvekit.confirm import confirm, paired_summary, wilcoxon_signed_rank
from evolvekit.space import ParameterSpace

# cost = scale * (1 + x) * (1 + noise(instance, seed, x)); `x` is the tuned parameter.
SOLVER = r'''
import argparse, json, random, sys
p = argparse.ArgumentParser()
p.add_argument("--instance"); p.add_argument("--seed", type=int); p.add_argument("--x", type=float)
p.add_argument("--mode", default="std")
a = p.parse_args()
scale = {"small": 100.0, "medium": 1000.0, "large": 10000.0, "fresh": 500.0}[a.instance]
if a.instance == "medium" and a.seed == 1002 and a.x != 0.0:
    sys.exit(7)
noise = random.Random("%s/%d/%s" % (a.instance, a.seed, a.x)).gauss(0, 0.004)
print(json.dumps({"cost": scale * (1.0 + a.x) * (1.0 + noise), "instance": a.instance}))
'''


def _write_config(tmp_path: Path, **stage) -> Path:
    (tmp_path / "solver.py").write_text(SOLVER, encoding="utf-8")
    raw = {
        "problem": {"parameters": {
            "x": {"type": "float", "low": -0.5, "high": 0.5, "default": 0.0},
            "mode": {"type": "choice", "choices": ["std", "deep"], "default": "std", "flag": "--mode"},
        }},
        "evaluate": {
            "stages": [
                {"id": "static", "kind": "builtin-static"},
                {"id": "full", "kind": "command", "kpis_from": "stdout", "timeout": 60,
                 "instances": ["small", "medium", "large"], "workers": 2,
                 "command": "{python} solver.py --instance {instance} --seed {seed} {params}", **stage},
            ],
            "score": {"objective": "cost", "direction": "minimize"},
        },
        "search": {"operators": {"param_lhs": 1.0}},
    }
    import yaml

    path = tmp_path / "evolvekit.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    build_config(raw, base_dir=tmp_path)  # a mistake in this helper should fail here
    return path


def _write_run(tmp_path: Path, config_path: Path, candidates: dict[str, dict]) -> Path:
    """A run directory by hand: the seed, and candidates with a score each."""
    space: ParameterSpace = load_config(config_path).problem.parameters
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rows = [{"id": "g000-c0001", "generation": 0, "operator": "human-seed", "score": -100.0,
             "params": space.defaults(), "block": space.render_block(space.defaults())}]
    for index, (cid, spec) in enumerate(candidates.items(), start=1):
        params = {**space.defaults(), **spec["params"]}
        rows.append({"id": cid, "generation": index, "operator": "param_local", "score": spec["score"],
                     "params": params, "block": space.render_block(params)})
    with (run_dir / "runs.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({"rejected": False, "competes": True, "stages_reached": ["static", "full"], **row}) + "\n")
    return run_dir


# -- the statistics --------------------------------------------------------


def test_the_wilcoxon_test_is_exact():
    # All ten differences positive: only 2 of 2^10 sign assignments are this extreme.
    result = wilcoxon_signed_rank([0.5, 1.2, 0.3, 2.0, 0.9, 1.1, 0.7, 0.4, 1.6, 0.8])
    assert result["n"] == 10 and result["w_plus"] == 55.0 and result["p"] == pytest.approx(2 / 1024)
    # n = 6, W- = 6: fourteen of the 64 subsets of {1..6} sum to 6 or less, so p = 2 * 14 / 64.
    assert wilcoxon_signed_rank([1, 2, 3, 4, 5, -6])["p"] == pytest.approx(28 / 64)
    assert wilcoxon_signed_rank([0.0, 0.0])["p"] is None, "nothing but zeros is no evidence either way"
    tied = wilcoxon_signed_rank([1.0, 1.0, -1.0, 2.0])
    assert tied["w_plus"] == pytest.approx(2.0 + 2.0 + 4.0), "ties share their mean rank"


def test_the_critical_value_is_right_beyond_thirty_degrees_of_freedom():
    """1.96 is the limit, not the value at 31 (2.040): an interval on 32
    instances was reported about 4 % narrower than it is."""
    from evolvekit.confirm import _t95

    exact = {1: 12.706, 30: 2.042, 31: 2.040, 40: 2.021, 60: 2.000, 120: 1.980, 1000: 1.962}
    for df, value in exact.items():
        assert _t95(df) == pytest.approx(value, abs=0.001), df
    values = [_t95(df) for df in range(1, 500)]
    assert all(a > b for a, b in zip(values, values[1:])), "it only ever falls towards 1.96"


def test_a_summary_says_in_words_what_the_interval_says():
    clear = paired_summary([1.0, 1.2, 0.8, 1.1, 0.9])
    assert clear["ci95"][0] > 0 and clear["verdict"].startswith("better than the baseline")
    assert (clear["wins"], clear["losses"]) == (5, 0)
    unclear = paired_summary([1.0, -1.2, 0.8, -0.7, 0.2])
    assert unclear["ci95"][0] < 0 < unclear["ci95"][1] and "includes zero" in unclear["verdict"]
    worse = paired_summary([-1.0, -1.2, -0.8, -1.1])
    assert worse["verdict"].startswith("WORSE")
    assert paired_summary([0.4])["ci95"] is None


# -- the comparison --------------------------------------------------------


def test_a_real_improvement_is_confirmed_on_fresh_seeds_instance_by_instance(tmp_path):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001, 1003, 1004], label="test", log=lambda m: None)

    result = comparison.per_candidate["g003-c0012"]
    assert [r["instance"] for r in result["per_instance"]] == ["small", "medium", "large"]
    assert all(r["pairs"] == 3 for r in result["per_instance"])
    summary = result["summary"]
    assert summary["mean"] == pytest.approx(5.0, abs=0.6), "5 % better, whatever the instance's scale"
    assert summary["ci95"][0] > 0 and summary["wins"] == 3
    out = run_dir / "confirm" / "test"
    assert {p.name for p in out.iterdir()} >= {"results.json", "comparison.json", "comparison.md", "events.jsonl"}
    assert len(json.loads((out / "results.json").read_text(encoding="utf-8"))) == 2 * 3 * 3
    assert "**Mean improvement +" in (out / "comparison.md").read_text(encoding="utf-8")


def test_an_improvement_that_was_luck_is_reported_as_not_distinguishable(tmp_path):
    config_path = _write_config(tmp_path)
    # The search believed in this one (score -97), but `mode` does nothing at all.
    run_dir = _write_run(tmp_path, config_path, {"g002-c0007": {"params": {"mode": "deep"}, "score": -97.0}})
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001, 1003], log=lambda m: None)
    summary = comparison.per_candidate["g002-c0007"]["summary"]
    assert summary["mean"] == pytest.approx(0.0, abs=1e-9)
    assert "WORSE" not in summary["verdict"] and not summary["verdict"].startswith("better")


def test_a_failed_run_costs_its_pair_not_the_comparison(tmp_path):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001, 1002], log=lambda m: None)
    result = comparison.per_candidate["g003-c0012"]
    by_instance = {r["instance"]: r for r in result["per_instance"]}
    assert by_instance["medium"]["pairs"] == 1, "seed 1002 crashed for the candidate on `medium`"
    assert by_instance["small"]["pairs"] == 2 and by_instance["large"]["pairs"] == 2
    assert result["pairs"] == 5 and result["pairs_planned"] == 6 and result["failed_runs"] == 1


def test_a_failed_pair_is_counted_and_said_in_the_verdict(tmp_path):
    """A failed or timed-out run drops its pair from the interval. The interval
    is still computed without it -- what to make of that is policy -- but the
    report must say how many pairs are missing, not leave it to a reader to
    notice that five is less than six."""
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001, 1002, 1003], log=lambda m: None)
    result = comparison.per_candidate["g003-c0012"]
    by_instance = {r["instance"]: r for r in result["per_instance"]}
    assert result["failed_pairs"] == 1 and result["zero_baseline_pairs"] == 0
    assert by_instance["medium"]["failed_pairs"] == 1 and by_instance["small"]["failed_pairs"] == 0
    assert "1 of 9 pairs failed and are not in this interval" in result["summary"]["verdict"]
    out = run_dir / "confirm" / "confirm"
    written = json.loads((out / "comparison.json").read_text(encoding="utf-8"))["per_candidate"]["g003-c0012"]
    assert written["failed_pairs"] == 1 and written["per_instance"][1]["failed_pairs"] == 1
    report = (out / "comparison.md").read_text(encoding="utf-8")
    assert "1 of 9 pairs failed and are not in this interval" in report
    assert "| medium |" in report and "1 failed" in report


def test_a_pair_with_a_baseline_of_zero_is_counted_too(tmp_path):
    """An improvement in percent of zero is no number; the pair is dropped,
    and said so, like a failed one."""
    config_path = _write_config(tmp_path)
    solver = (tmp_path / "solver.py").read_text(encoding="utf-8")
    (tmp_path / "solver.py").write_text(
        solver.replace("print(json.dumps({\"cost\": scale", "print(json.dumps({\"cost\": 0.0 if (a.instance, a.x) == (\"small\", 0.0) else scale"),
        encoding="utf-8",
    )
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001, 1003], log=lambda m: None)
    result = comparison.per_candidate["g003-c0012"]
    assert result["zero_baseline_pairs"] == 2 and result["failed_pairs"] == 0 and result["pairs"] == 4
    assert result["per_instance"][0]["zero_baseline_pairs"] == 2
    assert "2 of 6 pairs had a baseline of 0 and are not in this interval" in result["summary"]["verdict"]


def test_a_seed_given_twice_is_refused(tmp_path):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    with pytest.raises(ValueError, match="--seeds: 1001 is given twice"):
        confirm(load_config(config_path), run_dir, seeds=[1001, 1003, 1001], log=lambda m: None)
    assert main(["confirm", "--config", str(config_path), "--run-dir", str(run_dir), "--seeds", "1001,1001"]) == 1


def test_the_baseline_is_the_seed_as_last_evaluated(tmp_path):
    """A run that aborted on its seed evaluates the seed again when it is run
    again: the first seed row is the failed attempt, the last one counts."""
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    rows = (run_dir / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    seed = json.loads(rows[0])
    failed = {**seed, "id": "g000-c0000", "competes": False, "score": -1000.0, "last_failure": "stage full: exit 1"}
    retried = {**seed, "id": "g000-c0013"}
    lines = [json.dumps(failed), *rows[1:], json.dumps(retried)]
    (run_dir / "runs.jsonl").write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001], label="t", log=lambda m: None)
    assert comparison.baseline_id == "g000-c0013"


def test_top_n_and_instances_the_search_never_saw(tmp_path):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {
        "g001-c0002": {"params": {"x": -0.02}, "score": -98.0},
        "g002-c0005": {"params": {"x": -0.10}, "score": -90.0},
        "g003-c0009": {"params": {"x": 0.10}, "score": -110.0},
    })
    comparison = confirm(load_config(config_path), run_dir, seeds=[1001], candidates="top:2",
                         instances=["fresh"], label="fresh", log=lambda m: None)
    assert comparison.candidates == ["g002-c0005", "g001-c0002"] and comparison.instances == ["fresh"]
    assert comparison.per_candidate["g002-c0005"]["summary"]["mean"] == pytest.approx(10.0, abs=1.0)


def test_an_interrupted_confirmation_does_not_pay_twice(tmp_path):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    first = confirm(load_config(config_path), run_dir, seeds=[1001], log=lambda m: None)
    again = confirm(load_config(config_path), run_dir, seeds=[1001, 1003], log=lambda m: None)
    assert not any(r["cached"] for r in first.runs)
    assert sum(1 for r in again.runs if r["cached"]) == 6 and sum(1 for r in again.runs if not r["cached"]) == 6


def test_what_cannot_be_compared_is_refused_with_the_reason(tmp_path, minimal_raw):
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="holds no seed candidate"):
        confirm(config, empty, seeds=[1], log=lambda m: None)
    run_dir = _write_run(tmp_path, config_path, {})
    with pytest.raises(ValueError, match="no fully evaluated candidate besides its seed"):
        confirm(config, run_dir, seeds=[1], log=lambda m: None)
    with pytest.raises(ValueError, match="holds no candidate 'g009-c0099'"):
        confirm(config, run_dir, seeds=[1], candidates="g009-c0099", log=lambda m: None)
    minimal_raw["evaluate"]["stages"].append({"id": "full", "kind": "command", "command": "e {candidate} {out}"})
    with pytest.raises(ConfigError, match="has to list `instances`"):
        confirm(build_config(minimal_raw, base_dir=tmp_path), run_dir, seeds=[1], log=lambda m: None)


# -- the winners of two runs -----------------------------------------------


def _two_runs(tmp_path: Path, config_path: Path) -> tuple[Path, Path]:
    """Two searches of the same problem: the first found x = -0.05, the second x = -0.10."""
    (tmp_path / "first").mkdir()
    (tmp_path / "second").mkdir()
    first = _write_run(tmp_path / "first", config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    second = _write_run(tmp_path / "second", config_path, {"g003-c0012": {"params": {"x": -0.10}, "score": -90.0}})
    return first, second


def test_a_candidate_of_another_run_is_compared_in_the_same_interleaved_comparison(tmp_path):
    config_path = _write_config(tmp_path)
    first, second = _two_runs(tmp_path, config_path)
    comparison = confirm(load_config(config_path), second, seeds=[1001, 1003],
                         candidates=f"g003-c0012,g003-c0012@{first}", label="both", log=lambda m: None)
    # The same id in both runs: the other run's candidate carries its run directory's name.
    assert comparison.candidates == ["g003-c0012", "g003-c0012@run"]
    assert comparison.per_candidate["g003-c0012"]["summary"]["mean"] == pytest.approx(10.0, abs=0.8)
    assert comparison.per_candidate["g003-c0012@run"]["summary"]["mean"] == pytest.approx(5.0, abs=0.8)
    assert len(comparison.runs) == 3 * 3 * 2, "the baseline once, not once per run"


def test_against_names_what_the_candidates_are_compared_with(tmp_path):
    config_path = _write_config(tmp_path)
    first, second = _two_runs(tmp_path, config_path)
    comparison = confirm(load_config(config_path), second, seeds=[1001, 1003, 1004],
                         candidates="g003-c0012", against=f"g003-c0012@{first}", label="duel", log=lambda m: None)
    assert comparison.baseline_id == "g003-c0012@run"
    summary = comparison.per_candidate["g003-c0012"]["summary"]
    # 0.90 against 0.95: 5.26 % better than the other run's winner, not 10 % better than the seed.
    assert summary["mean"] == pytest.approx(100 * 0.05 / 0.95, abs=0.8) and summary["ci95"][0] > 0
    assert len(comparison.runs) == 2 * 3 * 3, "the seed is not run: it is not part of this comparison"
    report = (second / "confirm" / "duel" / "comparison.md").read_text(encoding="utf-8")
    assert "Baseline: `g003-c0012@run`" in report


def test_two_run_directories_with_the_same_name_are_told_apart(tmp_path):
    """Candidates of other runs were named `ID@<directory name>`: `a/run` and
    `b/run` both became `ID@run`, and two different configurations were
    refused as "compared with itself". The name grows by parent directories
    until it is unique."""
    config_path = _write_config(tmp_path)
    first, second = _two_runs(tmp_path, config_path)
    (tmp_path / "third").mkdir()
    third = _write_run(tmp_path / "third", config_path, {"g003-c0012": {"params": {"x": -0.02}, "score": -98.0}})
    comparison = confirm(load_config(config_path), second, seeds=[1001, 1003], candidates=f"g003-c0012@{first}",
                         against=f"g003-c0012@{third}", label="same-name", log=lambda m: None)
    assert comparison.baseline_id == "g003-c0012@third/run"
    assert comparison.candidates == ["g003-c0012@first/run"]
    both = confirm(load_config(config_path), second, seeds=[1001], candidates=f"g003-c0012@{first},g003-c0012@{third}",
                   label="both-named", log=lambda m: None)
    assert both.candidates == ["g003-c0012@first/run", "g003-c0012@third/run"]
    with pytest.raises(ValueError, match="compared twice under one name"):
        confirm(load_config(config_path), second, seeds=[1], candidates=f"g003-c0012@{first},g003-c0012@{first}",
                log=lambda m: None)


def test_a_candidate_that_is_not_there_is_refused_by_name(tmp_path):
    config_path = _write_config(tmp_path)
    config = load_config(config_path)
    first, second = _two_runs(tmp_path, config_path)
    with pytest.raises(ValueError, match="holds no run"):
        confirm(config, second, seeds=[1], candidates=f"g003-c0012@{tmp_path / 'nowhere'}", log=lambda m: None)
    with pytest.raises(ValueError, match="holds no candidate 'g009-c0099'"):
        confirm(config, second, seeds=[1], candidates=f"g009-c0099@{first}", log=lambda m: None)
    with pytest.raises(ValueError, match="--against: .* holds no candidate 'g009-c0099'"):
        confirm(config, second, seeds=[1], against="g009-c0099", log=lambda m: None)
    with pytest.raises(ValueError, match="compared with itself"):
        confirm(config, second, seeds=[1], candidates="g003-c0012", against="g003-c0012", log=lambda m: None)


# -- the command line ------------------------------------------------------


def test_the_exit_code_says_whether_the_improvement_is_real(tmp_path, capsys):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {"g003-c0012": {"params": {"x": -0.05}, "score": -95.0}})
    code = main(["confirm", "--config", str(config_path), "--run-dir", str(run_dir), "--seeds", "1001,1003,1004"])
    printed = capsys.readouterr().out
    assert code == 0 and "Better than the baseline" in printed and "| small |" in printed
    (tmp_path / "run2").mkdir()
    lucky = _write_run(tmp_path / "run2", config_path, {"g002-c0007": {"params": {"mode": "deep"}, "score": -97.0}})
    assert main(["confirm", "--config", str(config_path), "--run-dir", str(lucky), "--seeds", "1001,1003"]) == 1


def test_the_command_line_compares_the_winners_of_two_runs(tmp_path, capsys):
    config_path = _write_config(tmp_path)
    first, second = _two_runs(tmp_path, config_path)
    arguments = ["confirm", "--config", str(config_path), "--seeds", "1001,1003,1004", "--label", "duel"]
    # The second run's winner is the better one: exit 0 one way round, 1 the other.
    assert main([*arguments, "--run-dir", str(second), "--candidates", "g003-c0012",
                 "--against", f"g003-c0012@{first}"]) == 0
    assert "Baseline: `g003-c0012@run`" in capsys.readouterr().out
    assert main([*arguments, "--run-dir", str(first), "--candidates", "g003-c0012",
                 "--against", f"g003-c0012@{second}"]) == 1
    assert "Worse than the baseline" in capsys.readouterr().out


def test_export_hands_over_the_winner(tmp_path, capsys):
    config_path = _write_config(tmp_path)
    run_dir = _write_run(tmp_path, config_path, {
        "g001-c0002": {"params": {"x": -0.02}, "score": -98.0},
        "g002-c0005": {"params": {"x": -0.1, "mode": "deep"}, "score": -90.0},
    })
    assert main(["export", "--run-dir", str(run_dir)]) == 0
    assert json.loads(capsys.readouterr().out) == {"x": -0.1, "mode": "deep"}
    assert main(["export", "--run-dir", str(run_dir), "--format", "flags", "--config", str(config_path)]) == 0
    assert capsys.readouterr().out.strip() == "--x -0.1 --mode deep"
    assert main(["export", "--run-dir", str(run_dir), "--candidate", "g001-c0002", "--format", "yaml"]) == 0
    assert "x: -0.02" in capsys.readouterr().out
    out = tmp_path / "winner.json"
    assert main(["export", "--run-dir", str(run_dir), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["mode"] == "deep"
    assert main(["export", "--run-dir", str(run_dir), "--format", "code"]) == 0
    assert "def configure" in capsys.readouterr().out
    assert main(["export", "--run-dir", str(run_dir), "--candidate", "nope"]) == 1
    assert main(["export", "--run-dir", str(run_dir), "--format", "flags"]) == 1
