"""`builtin-build` (design §5.1, §5.3): a candidate's tree is built once per
commit before its runs, a failed build is a hard reject with its stderr as the
feedback, and the shared interpreter is built back from the engineer's checkout
when a run, the final check or a preflight ends (decision 31)."""

from __future__ import annotations

import json
import re
import sys
import threading
import time
from pathlib import Path

import pytest
from test_driver_tree import _raw, scripted
from tree_helpers import SOLVER, change, git, head, make_repo

from evolvekit import tree
from evolvekit.config import ConfigError, build_config
from evolvekit.confirm import confirm
from evolvekit.evaluate.cascade import Cascade
from evolvekit.evaluate.stages import build_command, restore_build, run_build_stage
from evolvekit.ledger import read_jsonl
from evolvekit.preflight import preflight
from evolvekit.search.driver import Driver

FAILING = '{python} -c "import sys; sys.stderr.write(\'error: solver.py does not compile\\n\'); sys.exit(3)"'
SLEEPING = '{python} -c "import time; time.sleep(30)"'


@pytest.fixture
def project(tmp_path):
    repo = make_repo(tmp_path)
    yield repo, head(repo)
    for folder in ("run", "run2", "trees", "pf", "run/confirm/confirm"):  # Windows keeps a folder otherwise
        tree.remove_all(tmp_path / folder)


def _setup(project, tmp_path, *, build: str, timeout_s: float = 600):
    """The base checked out as a tree, and the problem and build stage of a config with `build`."""
    repo, base = project
    raw = _raw(repo, base, build=build)
    raw["problem"]["tree"]["build_timeout_s"] = timeout_s
    config = build_config(raw, base_dir=tmp_path)
    made = tree.materialise(tmp_path / "trees", repo=tree.Repo(repo / "app dir"), base=base, diff="",
                            candidate_id="g000-c0001")
    return made, config.problem, config.evaluate.stages[1]


def _logging_build(log: Path) -> str:
    """A build that appends the folder it built (`{tree}`) and the time to `log`."""
    return (f'{{python}} -c "import sys, time; open(r\'{log}\', \'a\').write(sys.argv[1] + \'|\' + '
            f'str(time.time()) + chr(10))" {{tree}}')


def _built(log: Path) -> list[Path]:
    return [Path(line.split("|")[0]).resolve() for line in log.read_text(encoding="utf-8").splitlines()]


def test_build_command_splits_first_and_substitutes_second(tmp_path):
    folder = tmp_path / "a b" / "c"
    assert build_command("{python} -m pip install -q -e {tree}", tree=folder) == [
        sys.executable, "-m", "pip", "install", "-q", "-e", str(folder)]
    assert build_command('{python} -c "d = {}; print(d)"', tree=folder)[-1] == "d = {}; print(d)", \
        "other braces are the command's own"


def test_build_runs_in_the_tree(project, tmp_path):
    made, problem, stage = _setup(project, tmp_path, build='{python} -c "import sys, pathlib; '
                                  'pathlib.Path(\'built.txt\').write_text(sys.argv[1])" {tree}')
    outcome = run_build_stage(made, stage, problem)
    assert outcome.ok and outcome.stage_id == "build" and not outcome.cached
    assert (made.path / "built.txt").read_text() == str(made.path), "cwd is {tree}, and {tree} is its path"
    assert not (tmp_path / "trees" / "work" / "last-build.json").exists(), "nothing on disk claims a build"


def test_build_failure_is_a_hard_reject_with_feedback(project, tmp_path):
    counter = tmp_path / "count.txt"
    counting = f'{{python}} -c "open(r\'{counter}\', \'a\').write(\'x\'); raise SystemExit(3)"'
    made, problem, stage = _setup(project, tmp_path, build=counting)
    for _ in range(2):
        assert not run_build_stage(made, stage, problem).ok
    assert counter.read_text() == "xx", "a failed build is not cached"
    made, problem, stage = _setup(project, tmp_path, build=FAILING)
    outcome = run_build_stage(made, stage, problem)
    assert not outcome.ok and outcome.failure == "build: exit code 3: error: solver.py does not compile"
    assert "solver.py does not compile" in outcome.stderr
    # through the driver: refused, nothing left behind
    repo, base = project
    said: list[str] = []
    summary = Driver(build_config(_raw(repo, base, build=FAILING), base_dir=tmp_path), run_dir=tmp_path / "run",
                     log=said.append).run(generations=0)
    seed = list(read_jsonl(tmp_path / "run" / "runs.jsonl"))[0]
    assert summary.aborted and seed["rejected"] and seed["reject_reason"].startswith("build: exit code 3")
    assert "solver.py does not compile" in seed["feedback"]["build"]
    assert not any((tmp_path / "run" / "work" / "trees").iterdir())
    assert any(line.startswith("warning:") and "could not be built back" in line for line in said), \
        "building the checkout back failed too: a warning, never an error"


def test_build_timeout(project, tmp_path):
    made, problem, stage = _setup(project, tmp_path, build=SLEEPING, timeout_s=1)
    started = time.perf_counter()
    outcome = run_build_stage(made, stage, problem)
    assert not outcome.ok and not outcome.abandoned and outcome.failure == "build: no answer within 1 s"
    assert time.perf_counter() - started < 15


def test_build_ends_with_the_study_time_left(project, tmp_path):
    made, problem, stage = _setup(project, tmp_path, build=SLEEPING)
    started = time.perf_counter()
    outcome = run_build_stage(made, stage, problem, time_left_s=1.0)
    assert outcome.abandoned and not outcome.ok, "the study's time ran out: not the change's fault"
    assert time.perf_counter() - started < 15


def test_a_stop_kills_the_running_build_and_its_children(project, tmp_path):
    late = tmp_path / "late.txt"
    script = tmp_path / "spawn.py"
    script.write_text("import subprocess, sys\n"
                      "subprocess.run([sys.executable, '-c', \"import sys, time; time.sleep(3); "
                      "open(sys.argv[1], 'w').write('x')\", sys.argv[1]])\n", encoding="utf-8")
    made, problem, stage = _setup(project, tmp_path, build=f'{{python}} "{script}" "{late}"')
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    started = time.perf_counter()
    outcome = run_build_stage(made, stage, problem, cancel=stop)
    assert outcome.abandoned and not outcome.ok and time.perf_counter() - started < 3
    time.sleep(4)
    assert not late.exists(), "the build's whole process tree was killed"


def test_build_cached_by_commit(project, tmp_path):
    counter = tmp_path / "count.txt"
    build = f'{{python}} -c "open(r\'{counter}\', \'a\').write(\'x\')"'
    a, problem, stage = _setup(project, tmp_path, build=build)
    repo, base = project
    three = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 3")})
    b = tree.materialise(tmp_path / "trees", repo=tree.Repo(repo / "app dir"), base=base, diff=three,
                         candidate_id="g001-c0002")
    for made in (a, a):
        assert run_build_stage(made, stage, problem).ok
    assert counter.read_text() == "x", "built once while it is the last build"
    for made in (b, a):
        run_build_stage(made, stage, problem)
    assert counter.read_text() == "xxx", "the shared interpreter holds one build: A again after B is built"
    assert run_build_stage(a, stage, problem).cached
    elsewhere = tree.materialise(tmp_path / "trees", repo=tree.Repo(repo / "app dir"), base=base, diff="",
                                 candidate_id="g001-c0003")
    assert not run_build_stage(elsewhere, stage, problem).cached, "the same commit in another folder is built"
    restore_build(problem, log=lambda _m: None)
    assert counter.read_text() == "xxxxx", "the checkout built back"
    assert not run_build_stage(elsewhere, stage, problem).cached, "and then a tree is built again"


def test_builtin_build_needs_a_build_and_its_place(project, tmp_path):
    repo, base = project
    raw = _raw(repo, base)
    raw["evaluate"]["stages"].insert(1, {"id": "build", "kind": "builtin-build"})
    with pytest.raises(ConfigError, match=re.escape(
            "evaluate.stages[1]: a builtin-build stage runs problem.tree.build, and the problem gives none")):
        build_config(raw, base_dir=tmp_path)
    raw = _raw(repo, base, build='{python} -c "pass"')
    raw["evaluate"]["stages"].append(raw["evaluate"]["stages"].pop(1))
    with pytest.raises(ConfigError, match=re.escape(
            "evaluate.stages[2].kind: builtin-build comes right after the static stage")):
        build_config(raw, base_dir=tmp_path)
    for key, value in (("command", "make"), ("timeout", 5), ("promote", {"archive_percentile": 50})):
        raw = _raw(repo, base, build='{python} -c "pass"')
        raw["evaluate"]["stages"][1][key] = value
        with pytest.raises(ConfigError, match=re.escape(f"evaluate.stages[1].{key}")):
            build_config(raw, base_dir=tmp_path)
    raw = _raw(repo, base, build='{python} -c "pass"')
    raw["problem"]["tree"]["build_timeout_s"] = 42
    stage = build_config(raw, base_dir=tmp_path).evaluate.stages[1]
    assert stage.kind == "builtin-build" and stage.timeout == 42, "its timeout is problem.tree.build_timeout_s"


def test_a_skeleton_problem_has_no_build(minimal_raw, tmp_path):
    minimal_raw["evaluate"]["stages"].append({"id": "build", "kind": "builtin-build"})
    with pytest.raises(ConfigError, match=re.escape(
            "evaluate.stages[1]: a builtin-build stage runs problem.tree.build, and the problem gives none")):
        build_config(minimal_raw, base_dir=tmp_path)


def test_the_checkout_is_built_back_when_a_run_ends(project, tmp_path, monkeypatch):
    repo, base = project
    checkout = (repo / "app dir").resolve()
    log = tmp_path / "builds.log"
    raw = _raw(repo, base, build=_logging_build(log), generations=1)
    Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run").run(generations=0)
    built = _built(log)
    assert built[0] != checkout and built[-1] == checkout, built
    log.unlink()
    scripted(monkeypatch, [change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 3")})])
    original = Cascade.evaluate_generation

    def crashing(self, candidates, *args, **kwargs):
        original(self, candidates, *args, **kwargs)
        raise RuntimeError("the evaluator fell over")

    monkeypatch.setattr(Cascade, "evaluate_generation", crashing)
    with pytest.raises(RuntimeError, match="fell over"):
        Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run2").run()
    built = _built(log)
    assert len(built) == 2 and built[0] != checkout and built[-1] == checkout, "built back after the crash too"


def test_confirm_builds_each_tree_before_its_runs(project, tmp_path, monkeypatch):
    repo, base = project
    builds, runs = tmp_path / "builds.log", tmp_path / "runs"
    raw = _raw(repo, base, build=_logging_build(builds), workers=2, generations=1)
    raw["evaluate"]["stages"][-1]["command"] += " {seed}"           # confirm needs {seed}
    scripted(monkeypatch, [change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 3")})] * 2)
    config = build_config(raw, base_dir=tmp_path)
    Driver(config, run_dir=tmp_path / "run").run()
    builds.unlink()
    runs.mkdir()
    monkeypatch.setenv("TREE_TEST_LOG", str(runs))                  # SOLVER writes [folder, start, end], a file per run
    result = confirm(config, tmp_path / "run", seeds=[101, 102], candidates="best", log=lambda _m: None)
    assert result.candidates and len(result.runs) == 2 * 2 * 2 and all(r["ok"] for r in result.runs)
    built = [(Path(folder).resolve(), float(at)) for folder, at in
             (line.split("|") for line in builds.read_text(encoding="utf-8").splitlines())]
    assert len(built) == 3, "the starting point's tree, the best's, then the engineer's checkout"
    assert built[-1][0] == (repo / "app dir").resolve()
    trees_built = [folder for folder, _ in built[:2]]
    assert all("confirm" in folder.parts for folder in trees_built) and trees_built[0] != trees_built[1]
    solved = [json.loads(p.read_text(encoding="utf-8")) for p in runs.iterdir()]
    for folder, start, _end in solved:
        last = max((b for b in built if b[1] <= start), key=lambda b: b[1])
        assert last[0] == Path(folder).resolve(), "every run uses the tree built last before it"
    assert built[-1][1] >= max(end for _f, _s, end in solved), "built back after the last run"
    assert not any((tmp_path / "run" / "confirm" / "confirm" / "work" / "trees").glob("*")), "removed when done"


def test_confirm_refuses_a_starting_point_that_no_longer_builds(project, tmp_path, monkeypatch):
    repo, base = project
    raw = _raw(repo, base, build='{python} -c "pass"', generations=1)
    raw["evaluate"]["stages"][-1]["command"] += " {seed}"
    scripted(monkeypatch, [change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 3")})] * 2)
    Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run").run()
    raw["problem"]["tree"]["build"] = FAILING
    with pytest.raises(ValueError, match=re.escape("the starting point no longer builds: build: exit code 3")):
        confirm(build_config(raw, base_dir=tmp_path), tmp_path / "run", seeds=[101], log=lambda _m: None)
    assert not any((tmp_path / "run" / "confirm" / "confirm" / "work" / "trees").glob("*"))


def test_preflight_runs_the_stages_on_the_starting_point(project, tmp_path):
    repo, base = project
    log = tmp_path / "builds.log"
    config = build_config(_raw(repo, base, build=_logging_build(log)), base_dir=tmp_path)
    report = preflight(config, work_dir=tmp_path / "pf")
    assert not report.failures, report.failures
    assert [s.kind for s in report.stages] == ["builtin-static", "builtin-build", "command"]
    assert report.stages[-1].kpis["value"] == 5.0
    built = _built(log)
    assert len(built) == 2 and "pf" in built[0].parts and built[1] == (repo / "app dir").resolve()
    assert not any((tmp_path / "pf" / "work" / "trees").iterdir()), "the starting point's tree is removed"
    report = preflight(config)  # in a temporary folder
    assert not report.failures, report.failures
    assert git(repo, "worktree", "list").count("\n") == 0, "no worktree left registered"


def test_the_study_test_run_runs_a_tree(project, tmp_path, monkeypatch):
    """`run_test` is `preflight` on the compiled study; compiling a code study is
    a later task, so the compile is stood in for by a tree config."""
    from test_study_run import _study

    from evolvekit.harness import execute

    repo, base = project
    folder = _study(tmp_path)
    raw = _raw(repo, base, build='{python} -c "pass"')
    monkeypatch.setattr(execute, "compile_study", lambda *_a, **_k: (raw, {}))
    result = execute.run_test(folder)
    assert result["ok"], result["failures"]
    assert result["kpis"]["value"] == 5.0
    assert git(repo, "worktree", "list").count("\n") == 0
