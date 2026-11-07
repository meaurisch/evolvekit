"""The driver on trees: `problem.tree`, a candidate's block is a unified diff
against a base commit, materialised as a worktree, deduplicated by its
normalised diff, evaluated one at a time when a build is set, trees cleaned up."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from tree_helpers import SOLVER, change, git, head, make_repo

from evolvekit import tree
from evolvekit.candidate import Candidate
from evolvekit.config import ConfigError, build_config
from evolvekit.evaluate.cascade import Cascade
from evolvekit.ledger import read_jsonl
from evolvekit.search import driver as driver_module
from evolvekit.search.driver import Driver
from evolvekit.search.novelty import NoveltyIndex
from evolvekit.search.operators import OperatorResult

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path):
    repo = make_repo(tmp_path)
    yield repo, head(repo)
    tree.remove_all(tmp_path / "run")  # Windows keeps a temporary folder otherwise


def _raw(repo: Path, base: str, *, build: str = "", workers: int = 1, generations: int = 2) -> dict:
    build_stage = [{"id": "build", "kind": "builtin-build"}] if build else []
    return {
        "problem": {"description": "Make VALUE small.",
                    "tree": {"repo": str(repo / "app dir"), "base": base, "open": ["solver.py"],
                             **({"build": build} if build else {})}},
        "evaluate": {"stages": [{"id": "static", "kind": "builtin-static"},
                                *build_stage,
                                {"id": "run", "kind": "command", "kpis_from": "stdout", "workers": workers,
                                 "command": "{python} {candidate}/solver.py {instance}", "instances": ["a", "b"],
                                 "normalize": "none"}],
                     "score": {"objective": "value", "direction": "minimize"}},
        "models": {r: {"provider": "fake", "model": r, "options": {"responses": ["x"]}} for r in ("small", "strong")},
        "search": {"operators": {"diff": 1.0}, "children_per_generation": 2, "generations": generations, "seed": 1,
                   "big_step_every": 0, "scratchpad_every": 0,
                   "novelty": {"behavioural": "off", "near": {"method": "off"}}},
        "stop": {"patience": 50},
    }


def scripted(monkeypatch, diffs):
    """The operator, scripted: each child's block is the next diff (the diff prompts are Task 5's).

    The last diff is repeated once the list runs out: a duplicate is asked for
    again once (`search.novelty_retry`), and that call takes a diff too."""
    queue = list(diffs)

    def fake(operator, **_):
        return OperatorResult(operator=operator, messages=[], block=queue.pop(0) if len(queue) > 1 else queue[0],
                              mode="diff")

    monkeypatch.setattr(driver_module, "run_operator", fake)


def _rows(run_dir: Path) -> list[dict]:
    return list(read_jsonl(run_dir / "runs.jsonl"))


def _values(repo: Path, *values: int) -> list[str]:
    return [change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", f"VALUE = {v}")}) for v in values]


def test_seed_is_empty_diff_at_base(project, tmp_path):
    repo, base = project
    summary = Driver(build_config(_raw(repo, base), base_dir=tmp_path), run_dir=tmp_path / "run").run(generations=0)
    seed = _rows(tmp_path / "run")[0]
    assert seed["block"] == "" and seed["commit"] == base and seed["operator"] == "human-seed"
    assert seed["kpis"]["value"] == 5.0 and summary.best.id == seed["id"]
    assert tree.tree_dir(tmp_path / "run", seed["id"]).is_dir(), "the best's tree stays"


def test_whitespace_only_diffs_are_duplicates(project, tmp_path, monkeypatch):
    repo, base = project
    three, four = _values(repo, 3, 4)
    spaced = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "# the same rule\nVALUE  =  3   ")},
                    unified=5)
    scripted(monkeypatch, [three, four, spaced, spaced])
    Driver(build_config(_raw(repo, base), base_dir=tmp_path), run_dir=tmp_path / "run").run()
    rows = {r["id"]: r for r in _rows(tmp_path / "run")}
    later = [r for r in rows.values() if r["generation"] == 2]
    assert later and all(r["rejected"] and r["novelty"] in ("duplicate", "no_op") for r in later)
    assert all(r["commit"] is None for r in later), "refused before anything was made or run"
    assert not any(tree.tree_dir(tmp_path / "run", r["id"]).exists() for r in later)


def test_trees_are_removed_except_best(project, tmp_path, monkeypatch):
    repo, base = project
    scripted(monkeypatch, _values(repo, 3, 4, 2, 6))
    summary = Driver(build_config(_raw(repo, base), base_dir=tmp_path), run_dir=tmp_path / "run").run()
    left = sorted(p.name for p in (tmp_path / "run" / "work" / "trees").iterdir() if p.is_dir())
    assert left == [summary.best.id]
    assert git(repo, "worktree", "list").count("\n") == 1, "the main checkout and the best's tree"


def test_runs_rows_carry_commit(project, tmp_path, monkeypatch):
    repo, base = project
    scripted(monkeypatch, _values(repo, 3, 4, 2, 6))
    Driver(build_config(_raw(repo, base), base_dir=tmp_path), run_dir=tmp_path / "run").run()
    rows = [r for r in _rows(tmp_path / "run") if not r["rejected"]]
    assert len(rows) == 5
    for row in rows:
        assert re.fullmatch(r"[0-9a-f]{40}", row["commit"])
        parent = row["commit"] if row["block"] == "" else git(repo, "rev-parse", row["commit"] + "^")
        assert parent == base


def test_resume_keeps_commits_and_dedups_by_the_normalised_diff(project, tmp_path, monkeypatch):
    repo, base = project
    three, four = _values(repo, 3, 4)
    scripted(monkeypatch, [three, four])
    Driver(build_config(_raw(repo, base, generations=1), base_dir=tmp_path), run_dir=tmp_path / "run").run()
    spaced = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE  =   3  ")}, unified=1)
    scripted(monkeypatch, [spaced])
    again = Driver(build_config(_raw(repo, base, generations=2), base_dir=tmp_path), run_dir=tmp_path / "run")
    again.run()
    rows = _rows(tmp_path / "run")
    assert [r for r in rows if r["generation"] == 2] and all(
        r["rejected"] and r["novelty"] in ("duplicate", "no_op") for r in rows if r["generation"] == 2)
    assert all(c.source.endswith("app dir") for c in again.archive), "a tree's source is its folder"


def test_a_run_folder_keeps_its_tree(project, tmp_path):
    repo, base = project
    Driver(build_config(_raw(repo, base), base_dir=tmp_path), run_dir=tmp_path / "run").run(generations=0)
    raw = _raw(repo, base)
    raw["problem"]["tree"]["open"] = ["solver.py", "bench.py"]
    with pytest.raises(ValueError, match="the source's starting commit or its open files changed"):
        Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run").run(generations=0)


def _spans(log: Path) -> list[tuple[float, float]]:
    by_tree: dict[str, tuple[float, float]] = {}
    lines = [line for one in sorted(log.iterdir()) for line in one.read_text(encoding="utf-8").splitlines()]
    for folder, start, end in (json.loads(line) for line in lines):
        lo, hi = by_tree.get(folder, (start, end))
        by_tree[folder] = (min(lo, start), max(hi, end))
    assert len(by_tree) == 3, "the starting point and two combinations"
    return sorted(by_tree.values())


@pytest.mark.slow
def test_build_serialises_candidates(project, tmp_path, monkeypatch):
    repo, base = project
    log = tmp_path / "runs"  # a folder: one file per solver run
    log.mkdir()
    monkeypatch.setenv("TREE_TEST_LOG", str(log))
    monkeypatch.setenv("TREE_TEST_SLEEP", "0.4")
    scripted(monkeypatch, _values(repo, 3, 4))
    raw = _raw(repo, base, build='{python} -c "pass"', workers=4, generations=1)
    Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run").run()
    ordered = _spans(log)
    assert all(a[1] <= b[0] for a, b in zip(ordered, ordered[1:])), f"two combinations overlapped: {ordered}"


@pytest.mark.slow
def test_no_build_keeps_concurrency(project, tmp_path, monkeypatch):
    repo, base = project
    log = tmp_path / "runs"  # a folder: one file per solver run
    log.mkdir()
    monkeypatch.setenv("TREE_TEST_LOG", str(log))
    monkeypatch.setenv("TREE_TEST_SLEEP", "0.4")
    scripted(monkeypatch, _values(repo, 3, 4))
    Driver(build_config(_raw(repo, base, workers=4, generations=1), base_dir=tmp_path),
           run_dir=tmp_path / "run").run()
    ordered = _spans(log)
    assert any(a[1] > b[0] for a, b in zip(ordered, ordered[1:])), "without a build, combinations run together"


def test_static_stage_refuses_files_outside_open(project, tmp_path):
    repo, base = project
    outside = change(repo, {"app dir/bench.py": "print('always fast')\n",
                            "app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 1")})
    broken = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = (")})
    stale = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 2")}).replace(
        "-VALUE = 5", "-VALUE = 9")
    cascade = Cascade(build_config(_raw(repo, base), base_dir=tmp_path), work_dir=tmp_path / "run" / "work")
    a = Candidate(id="g001-c0002", generation=1, block=outside, source="", operator="diff")
    b = Candidate(id="g001-c0003", generation=1, block=broken, source="", operator="diff")
    c = Candidate(id="g001-c0004", generation=1, block=stale, source="", operator="diff")
    results = cascade.evaluate_generation([a, b, c])
    assert results[a.id].rejected and results[a.id].reject_reason == \
        "changes files outside the open files: app dir/bench.py (only solver.py may change)"
    assert results[b.id].rejected and results[b.id].reject_reason.startswith("SyntaxError in solver.py")
    assert results[c.id].rejected and results[c.id].reject_reason.startswith("the change does not apply")
    assert results[c.id].commit is None and re.fullmatch(r"[0-9a-f]{40}", results[a.id].commit)
    tree.remove_all(tmp_path / "run")


def test_no_git_needed_for_a_skeleton_problem(minimal_raw, tmp_path, monkeypatch):
    def no_git(*args, **kwargs):
        raise AssertionError("a skeleton problem started git")

    monkeypatch.setattr("evolvekit.tree._git", no_git)
    minimal_raw["search"] = {"operators": {"rewrite": 1.0}}
    Driver(build_config(minimal_raw, base_dir=tmp_path), run_dir=tmp_path / "run").run(generations=0)


def test_a_skeleton_run_never_imports_tree(minimal_raw, tmp_path):
    """In a fresh interpreter: settings studies must work on a machine without git."""
    minimal_raw["search"] = {"operators": {"rewrite": 1.0}}
    (tmp_path / "raw.json").write_text(json.dumps(minimal_raw), encoding="utf-8")
    script = (
        "import json, sys; from pathlib import Path\n"
        "from evolvekit.config import build_config\n"
        "from evolvekit.search.driver import Driver\n"
        "here = Path(sys.argv[1])\n"
        "raw = json.loads((here / 'raw.json').read_text(encoding='utf-8'))\n"
        "Driver(build_config(raw, base_dir=here), run_dir=here / 'run').run(generations=0)\n"
        "print(sorted(m for m in sys.modules if m.startswith('evolvekit.tree')))\n"
    )
    done = subprocess.run([sys.executable, "-c", script, str(tmp_path)], cwd=ROOT, capture_output=True,
                          text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == "[]"


def test_problem_tree_is_validated(project, tmp_path):
    repo, base = project
    for change_, message in (
        ({"skeleton": "s.py"}, "give one of `skeleton`, `parameters` or `tree`"),
        ({"parameters": {"x": {"type": "int", "low": 0, "high": 3, "default": 1}}},
         "give one of `skeleton`, `parameters` or `tree`"),
        ({"tree": {"repo": "app dir", "base": base, "open": ["solver.py"]}}, "problem.tree.repo: give the full path"),
        ({"tree": {"repo": str(repo / "app dir"), "base": "nope", "open": ["solver.py"]}}, "problem.tree.base: nope"),
        ({"tree": {"repo": str(repo / "app dir"), "base": base, "open": []}}, "problem.tree.open: name at least one"),
        ({"tree": {"repo": str(repo / "app dir"), "open": ["solver.py"]}}, "problem.tree.base: required"),
        ({"tree": {"repo": str(repo / "app dir"), "base": base, "open": ["solver.py"], "build_timeout_s": 0}},
         "problem.tree.build_timeout_s: must be > 0"),
        ({"tree": {"repo": str(repo / "app dir"), "base": base, "open": ["solver.py"], "nope": 1}},
         "problem.tree: unknown key(s) ['nope']"),
    ):
        raw = _raw(repo, base)
        raw["problem"].update(change_)
        with pytest.raises(ConfigError, match=re.escape(message)):
            build_config(raw, base_dir=tmp_path)
    raw = _raw(repo, base)
    raw["problem"]["tree"]["base"] = "main"
    config = build_config(raw, base_dir=tmp_path)
    assert config.problem.tree.base == base, "a ref is resolved to its commit at load"
    assert config.problem.skeleton is None and config.problem.skeleton_source() == ""


def test_tree_command_placeholders(project, tmp_path):
    repo, base = project

    def with_command(command: str, **tree_keys) -> dict:
        raw = _raw(repo, base, build=tree_keys.pop("build", ""))  # a build comes with its stage
        raw["problem"]["tree"].update(tree_keys)
        raw["evaluate"]["stages"][-1]["command"] = command
        return raw

    with pytest.raises(ConfigError, match=re.escape("uses {app} but problem.tree.app names no program")):
        build_config(with_command("{python} {app} {instance}"), base_dir=tmp_path)
    with pytest.raises(ConfigError, match=re.escape("uses {params}")):
        build_config(with_command("{python} {candidate}/solver.py {params} {instance}"), base_dir=tmp_path)
    with pytest.raises(ConfigError, match=re.escape("{candidate}")):
        build_config(with_command("{python} solver.py {instance}"), base_dir=tmp_path)
    assert build_config(with_command("{python} {app} {instance}", app="{tree}/solver.py"), base_dir=tmp_path)
    assert build_config(with_command("{python} -m solver {instance}", build='{python} -c "pass"'), base_dir=tmp_path)


def test_novelty_key_hashes_the_normalised_diff():
    index = NoveltyIndex(key=tree.normalise_diff)
    a = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-v = 1\n+v = 2\n"
    b = "--- a/x.py\t2020-01-01\n+++ b/x.py\n@@ -1 +1 @@\n-v = 1\n+v  =  2   \n"
    odd = a.replace("v = 2", "v = '\udce9'")  # a byte that is not UTF-8, kept as a surrogate
    index.add("one", a)
    assert index.lookup(b) == "one" and index.check(b, "", "seed").kind == "duplicate"
    assert index.check(a, b, "parent").kind == "no_op"
    assert index.check(odd, "", "seed").novel


# -- the review's fixes ----------------------------------------------------------


def _latin1_change(repo: Path, value: int, *, spacing: str = " ") -> str:
    """A change whose diff carries a byte that is not UTF-8 (a Latin-1 e-acute),
    as `tree` reads it: kept as a lone surrogate."""
    text = "# -*- coding: latin-1 -*-\n" + SOLVER.replace(
        "VALUE = 5", f"VALUE{spacing}={spacing}{value}\nNAME = 'caf\xe9'")
    (repo / "app dir" / "solver.py").write_bytes(text.encode("latin-1"))
    git(repo, "add", "-A")
    try:
        return tree._git(repo, "diff", "--cached")
    finally:
        git(repo, "reset", "-q", "--hard", "HEAD")


def test_a_diff_with_any_bytes_runs_end_to_end(project, tmp_path, monkeypatch):
    repo, base = project
    plain, four = _latin1_change(repo, 3), _latin1_change(repo, 4)
    spaced = _latin1_change(repo, 3, spacing="   ")
    assert "\udce9" in plain and plain != spaced
    scripted(monkeypatch, [plain, four, spaced])
    raw = _raw(repo, base, generations=2)
    raw["search"]["novelty"]["near"] = {"method": "local"}  # turned off for a tree, never run on a diff
    raw["search"]["inspirations"] = 2
    Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run").run()
    rows = _rows(tmp_path / "run")
    first = min((r for r in rows if r["generation"] == 1), key=lambda r: r["id"])
    assert first["block"] == plain, "the block round-trips through runs.jsonl"
    assert not first["rejected"] and first["kpis"]["value"] == 3.0 and first["commit"]
    later = [r for r in rows if r["generation"] == 2]
    assert later and all(r["rejected"] and r["novelty"] in ("duplicate", "no_op") for r in later)
    assert json.loads((tmp_path / "run" / "archive.json").read_text(encoding="utf-8"))
    resumed = Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run")
    assert resumed.resume() == 2 and resumed.novelty.lookup(spaced) == first["id"]


def test_trees_are_removed_when_the_run_crashes(project, tmp_path, monkeypatch):
    repo, base = project
    scripted(monkeypatch, _values(repo, 3, 4))
    original = Cascade.evaluate_generation
    calls = []

    def crashing(self, candidates, *args, **kwargs):
        results = original(self, candidates, *args, **kwargs)
        calls.append(len(candidates))
        if len(calls) == 2:  # the first round's children: evaluated, their trees made
            raise RuntimeError("the evaluator fell over")
        return results

    monkeypatch.setattr(Cascade, "evaluate_generation", crashing)
    with pytest.raises(RuntimeError, match="fell over"):
        Driver(build_config(_raw(repo, base, generations=1), base_dir=tmp_path), run_dir=tmp_path / "run").run()
    seed = _rows(tmp_path / "run")[0]
    left = sorted(p.name for p in (tmp_path / "run" / "work" / "trees").iterdir() if p.is_dir())
    assert left == [seed["id"]], "only the best's tree stays"
    assert git(repo, "worktree", "list").count("\n") == 1


def test_build_and_top_k_are_refused_together(project, tmp_path):
    repo, base = project
    raw = _raw(repo, base, build='{python} -c "pass"')
    raw["evaluate"]["stages"].insert(-1, {
        "id": "screen", "kind": "command", "kpis_from": "stdout", "normalize": "none",
        "command": "{python} {candidate}/solver.py {instance}", "instances": ["a"],
        "promote": {"top_k_per_generation": 1}})
    with pytest.raises(ConfigError, match=re.escape(
            "`promote.top_k_per_generation` cannot be used with `problem.tree.build`: "
            "candidates are built one at a time")):
        build_config(raw, base_dir=tmp_path)
    del raw["problem"]["tree"]["build"]
    raw["evaluate"]["stages"] = [s for s in raw["evaluate"]["stages"] if s["kind"] != "builtin-build"]
    assert build_config(raw, base_dir=tmp_path), "without a build, top-k ranks a whole round"


def test_near_duplicates_are_off_for_a_tree(project, tmp_path):
    repo, base = project
    raw = _raw(repo, base)
    raw["search"]["novelty"]["near"] = {"method": "local"}
    assert build_config(raw, base_dir=tmp_path).search.novelty.near.method == "off"
    del raw["search"]["novelty"]
    assert build_config(raw, base_dir=tmp_path).search.novelty.near.method == "off", "the default too"


def test_a_problem_names_what_it_varies(tmp_path):
    raw = {"problem": {"description": "nothing"},
           "evaluate": {"stages": [{"id": "static", "kind": "builtin-static"}], "score": {"objective": "value"}},
           "search": {"operators": {"param_lhs": 1.0}}}
    with pytest.raises(ConfigError, match=re.escape("problem: give one of `skeleton`, `parameters` or `tree`")):
        build_config(raw, base_dir=tmp_path)


def test_the_tree_identity_holds_build_and_app(project, tmp_path):
    repo, base = project
    raw = _raw(repo, base, build='{python} -c "pass"')
    raw["problem"]["tree"]["app"] = "{tree}/solver.py"
    identity = Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run")._problem_identity()
    assert identity["tree"] == {"base": base, "open": ["solver.py"], "build": '{python} -c "pass"',
                                "app": "{tree}/solver.py"}
