"""The LLM operators on a tree (design §5.3): the prompt shows the open files as
they are after the parent's diff, with line numbers, and asks for a unified
diff; a reply that does not apply, or that changes a file outside `open`, is a
format failure; marked blocks inside an open file are the cheap special case."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_driver_tree import _raw
from tree_helpers import SOLVER, change, git_init_commit, head, make_repo

from evolvekit import tree
from evolvekit.candidate import Candidate
from evolvekit.config import ArchiveConfig, DescriptorConfig, build_config
from evolvekit.diffs import DiffError
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.archive import Archive, descriptor_value
from evolvekit.search.driver import Driver
from evolvekit.search.operators import TreeContext, run_operator
from evolvekit.tree_prompts import marked_block, numbered, open_files, parse_diff_response


@pytest.fixture
def project(tmp_path):
    repo = make_repo(tmp_path)
    yield repo, head(repo)
    tree.remove_all(tmp_path / "run")  # Windows keeps a temporary folder otherwise


@pytest.fixture
def config(project, tmp_path):
    repo, base = project
    return build_config(_raw(repo, base), base_dir=tmp_path)


@pytest.fixture
def ctx(project, tmp_path):
    repo, base = project
    return TreeContext(tmp_path / "run", tree.Repo(repo / "app dir"), base, ("solver.py",), "g001-c0001.draft")


@pytest.fixture
def parent(project):
    repo, _ = project
    block = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 3")})
    return Candidate(id="g000-c0000", generation=0, block=block, source="", operator="human-seed")


def _run(operator, provider, *, config, parent, ctx):
    return run_operator(operator, config=config, provider=provider, model_role="small", parent=parent,
                        parent_result=None, skeleton_prefix="", skeleton_suffix="", tree=ctx)


def test_prompt_shows_open_files_with_line_numbers(ctx, config, parent):
    provider = FakeProvider(responses=["no diff here"])
    result = _run("diff", provider, config=config, parent=parent, ctx=ctx)
    prompt = provider.calls[0]["messages"][-1]["content"]
    assert "### solver.py" in prompt and "    2  VALUE = 3" in prompt, "after the parent's diff (VALUE = 3), numbered"
    assert "bench.py" not in prompt, "only the open files"
    assert "unified diff" in provider.calls[0]["messages"][0]["content"]
    assert "Make VALUE small." in provider.calls[0]["messages"][0]["content"], "the problem's own sections"
    assert "fixed skeleton" not in provider.calls[0]["messages"][0]["content"]
    assert not result.ok and not result.provider_error and "holds no unified diff" in result.error
    assert not tree.tree_dir(ctx.run_dir, ctx.draft_id).exists(), "the draft is removed"


def test_numbered_and_open_files(ctx, parent, project):
    assert numbered("a\nb\n") == "    1  a\n    2  b"
    made = tree.materialise(ctx.run_dir, repo=ctx.repo, base=ctx.base, diff=parent.block, candidate_id="look")
    try:
        files = open_files(made, ("*.py",))
        assert list(files) == ["bench.py", "solver.py"] and "VALUE = 3" in files["solver.py"]
        assert list(open_files(made, ("solver.py",))) == ["solver.py"]
    finally:
        tree.remove(ctx.run_dir, "look")


def test_parse_diff_fenced_and_bare():
    body = "--- a/solver.py\n+++ b/solver.py\n@@ -2 +2 @@\n-VALUE = 3\n+VALUE = 2\n"
    assert parse_diff_response(f"Here it is:\n```diff\n{body}```\nDone.") == body
    assert parse_diff_response(f"```patch\n{body}```") == body
    assert parse_diff_response(f"```\n{body}```") == body
    assert parse_diff_response("I changed one line.\n" + body) == body
    assert parse_diff_response("diff --git a/solver.py b/solver.py\n" + body).startswith("diff --git")
    assert parse_diff_response(f"```python\nx = 1\n```\n```diff\n{body}```") == body, "the first fence with a diff"
    with pytest.raises(DiffError, match="holds no unified diff"):
        parse_diff_response("```python\nVALUE = 2\n```")


def test_diff_outside_open_is_format_failure(ctx, config, parent):
    outside = "--- a/bench.py\n+++ b/bench.py\n@@ -1 +1 @@\n-print('bench')\n+print('fast')\n"
    stale = "--- a/solver.py\n+++ b/solver.py\n@@ -2 +2 @@\n-VALUE = 9\n+VALUE = 2\n"
    for reply, message in ((f"```diff\n{outside}```",
                            "changes files outside the open files: app dir/bench.py (only solver.py may change)"),
                           (f"```diff\n{stale}```", "the diff does not apply")):
        result = _run("diff", FakeProvider(responses=[reply]), config=config, parent=parent, ctx=ctx)
        assert not result.ok and not result.provider_error and message in result.error
        assert not tree.tree_dir(ctx.run_dir, ctx.draft_id).exists()
    good = "--- a/solver.py\n+++ b/solver.py\n@@ -2 +2 @@\n-VALUE = 3\n+VALUE = 2\n"
    result = _run("diff", FakeProvider(responses=[f"```diff\n{good}```"]), config=config, parent=parent, ctx=ctx)
    assert result.ok and "+VALUE = 2" in result.block and "a/app dir/solver.py" in result.block, \
        "against the base, toplevel paths"
    assert "-VALUE = 5" in result.block, "cumulative: the parent's change is in the child's block"
    assert result.mode == "diff" and tree.applies(ctx.repo, ctx.base, result.block)
    assert not tree.tree_dir(ctx.run_dir, ctx.draft_id).exists()


def test_a_diff_that_changes_nothing_is_a_format_failure(ctx, config, parent):
    undo ="--- a/solver.py\n+++ b/solver.py\n@@ -2 +2 @@\n-VALUE = 3\n+VALUE = 5\n"
    result = _run("diff", FakeProvider(responses=[f"```diff\n{undo}```"]), config=config, parent=parent, ctx=ctx)
    assert not result.ok and "the reply changed nothing" in result.error


def test_operators_other_than_diff_are_refused_on_a_tree(ctx, config, parent):
    for operator in ("rewrite", "crossover", "big_step"):
        provider = FakeProvider(responses=["x"])
        result = _run(operator, provider, config=config, parent=parent, ctx=ctx)
        assert not result.ok and not provider.calls, "refused before any call is paid for"
        assert f"the {operator} operator does not work on a source tree" in result.error


def test_a_reply_with_paths_from_the_toplevel_or_without_prefixes(ctx, config, parent):
    bare = "--- solver.py\n+++ solver.py\n@@ -2 +2 @@\n-VALUE = 3\n+VALUE = 2\n"
    result = _run("diff", FakeProvider(responses=[f"```diff\n{bare}```"]), config=config, parent=parent, ctx=ctx)
    assert result.ok and "+VALUE = 2" in result.block, "-p0 when the paths carry no a/ b/"


def _block_repo(tmp_path: Path, *, extra: bytes = b"") -> tuple[Path, str]:
    top = tmp_path / "my repo"
    (top / "app dir").mkdir(parents=True)
    (top / "app dir" / "rules.py").write_bytes(
        b"import json\n" + extra
        + b"# EVOLVE-BLOCK-START\ndef rule():\n    return 1\n# EVOLVE-BLOCK-END\n"
        + b"print(json.dumps({'value': rule()}))\n")
    (top / "app dir" / "bench.py").write_bytes(b"print('bench')\n")
    git_init_commit(top)
    return top, head(top)


def _block_setup(tmp_path, extra=b""):
    repo, base = _block_repo(tmp_path, extra=extra)
    raw = _raw(repo, base)
    raw["problem"]["tree"]["open"] = ["rules.py"]
    config = build_config(raw, base_dir=tmp_path)
    ctx = TreeContext(tmp_path / "run", tree.Repo(repo / "app dir"), base, ("rules.py",), "g001-c0001.draft")
    seed = Candidate(id="g000-c0000", generation=0, block="", source="", operator="human-seed")
    return config, ctx, seed


def test_marked_block_round_trip(tmp_path):
    # an open file holding `# EVOLVE-BLOCK-START` / `# EVOLVE-BLOCK-END` around `def rule(): return 1`;
    # the byte outside the block is not UTF-8, and is written back as it was
    config, ctx, seed = _block_setup(tmp_path, extra=b"# caf\xe9\n")
    try:
        provider = FakeProvider(responses=["```python\ndef rule():\n    return 2\n```"])
        result = _run("diff", provider, config=config, parent=seed, ctx=ctx)
        prompt = provider.calls[0]["messages"][-1]["content"]
        assert "def rule()" in prompt and "import json" not in prompt, "only the block is shown"
        assert "## Current block (in rules.py)" in prompt and "SEARCH" in prompt
        assert "unified diff" not in provider.calls[0]["messages"][0]["content"]
        assert result.ok and "+    return 2" in result.block and "-    return 1" in result.block
        assert result.mode == "rewrite"
        stat = tree.diff_stat(result.block)
        assert (stat["added"], stat["removed"]) == (1, 1), "nothing outside the block changed"
        edit = "<<<<<<< SEARCH\n    return 1\n=======\n    return 7\n>>>>>>> REPLACE\n"
        result = _run("diff", FakeProvider(responses=[edit]), config=config, parent=seed, ctx=ctx)
        assert result.ok and result.mode == "diff" and "+    return 7" in result.block
        assert not tree.tree_dir(ctx.run_dir, ctx.draft_id).exists()
    finally:
        tree.remove_all(tmp_path / "run")


def test_marked_block_needs_exactly_one_pair():
    one = {"a.py": "x\n# S\nbody\n# E\n", "b.py": "y\n"}
    assert marked_block(one, "# S", "# E") == ("a.py", "body\n")
    assert marked_block({**one, "c.py": "# S\nz\n# E\n"}, "# S", "# E") is None
    assert marked_block({"a.py": "# S\nbody\n"}, "# S", "# E") is None
    assert marked_block({"b.py": "y\n"}, "# S", "# E") is None


def test_bytes_that_are_not_utf8_survive_the_prompt_and_the_diff(tmp_path):
    top = tmp_path / "my repo"
    (top / "app dir").mkdir(parents=True)
    (top / "app dir" / "solver.py").write_bytes(b"# caf\xe9\nVALUE = 5\nprint(VALUE)\n")
    git_init_commit(top)
    base = head(top)
    config = build_config(_raw(top, base), base_dir=tmp_path)
    ctx = TreeContext(tmp_path / "run", tree.Repo(top / "app dir"), base, ("solver.py",), "d.draft")
    seed = Candidate(id="s", generation=0, block="", source="", operator="human-seed")
    reply = "```diff\n--- a/solver.py\n+++ b/solver.py\n@@ -1,3 +1,3 @@\n # caf\udce9\n-VALUE = 5\n+VALUE = 2\n print(VALUE)\n```"
    provider = FakeProvider(responses=[reply])
    try:
        result = _run("diff", provider, config=config, parent=seed, ctx=ctx)
        assert "    1  # caf\udce9" in provider.calls[0]["messages"][-1]["content"]
        assert result.ok and "+VALUE = 2" in result.block, result.error
        assert tree.applies(ctx.repo, base, result.block)
    finally:
        tree.remove_all(tmp_path / "run")


# -- the archive: a diff's size is its complexity ----------------------------


def test_two_diffs_of_different_sizes_land_in_different_cells(project):
    repo, _ = project
    small = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 3")})
    large = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 3\nA = 1\nB = 2\nC = 3\nD = 4")})
    config = ArchiveConfig(descriptors=(DescriptorConfig("complexity", 4),))

    def make(cid, block):
        return Candidate(id=cid, generation=1, block=block, source="", operator="diff", score=1.0,
                         ranking_score=1.0)

    a, b, seed = make("a", small), make("b", large), make("s", "")
    assert descriptor_value(a, "complexity", tree=True) == 2.0
    assert descriptor_value(b, "complexity", tree=True) == 6.0
    assert descriptor_value(seed, "complexity", tree=True) == 0.0
    archive = Archive.from_config(config, tree=True)
    for c in (seed, a, b):
        archive.add(c)
    assert len({tuple(c.cell) for c in (seed, a, b)}) == 3
    skeleton = Archive.from_config(config)
    for c in (make("a", small), make("b", large)):
        skeleton.add(c)
    assert len(skeleton.cells) == 1, "a skeleton problem keeps the block's AST complexity"
    again = Archive.from_records([c.to_record() for c in (seed, a, b)], config, tree=True)
    assert len(again.cells) == 3, "the rebuild on resume places them alike"


# -- the driver: the last refusals reach the next prompt --------------------


def test_the_next_prompt_shows_the_last_build_failures(project, tmp_path):
    repo, base = project
    script = tmp_path / "build.py"
    script.write_text('import pathlib, sys\n'
                      'if "VALUE = 3" in pathlib.Path(sys.argv[1], "solver.py").read_text():\n'
                      '    sys.stderr.write("error: VALUE = 3 breaks the build\\n")\n'
                      '    sys.exit(3)\n', encoding="utf-8")
    raw = _raw(repo, base, build=f'{{python}} "{script.as_posix()}" {{tree}}')
    raw["search"]["children_per_generation"] = 1
    three = "```diff\n--- a/solver.py\n+++ b/solver.py\n@@ -2 +2 @@\n-VALUE = 5\n+VALUE = 3\n```"
    provider = FakeProvider(responses=[three, "no diff here"], cycle=False)
    driver = Driver(build_config(raw, base_dir=tmp_path), run_dir=tmp_path / "run",
                    providers={"small": provider, "strong": provider})
    driver.run()
    rows = [json.loads(line) for line in (tmp_path / "run" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    refused = [r for r in rows if r["generation"] == 1]
    assert refused and refused[0]["rejected"] and refused[0]["reject_reason"].startswith("build: exit code 3")
    first, second = (call["messages"][-1]["content"] for call in provider.calls[:2])
    assert "breaks the build" not in first
    assert "error: VALUE = 3 breaks the build" in second, "a rejected candidate is never a parent; its stderr still reaches the model"
    assert refused[0]["id"] in second and "build: exit code 3" in second
