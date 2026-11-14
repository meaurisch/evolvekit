"""The LLM operators on a tree (design §5.3): the prompt shows the open files as
they are after the parent's diff, with line numbers, and asks for a unified
diff; a reply that does not apply, or that changes a file outside `open`, is a
format failure; marked blocks inside an open file are the cheap special case."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_driver_tree import _raw
from tree_helpers import SOLVER, change, git_init_commit, head, make_repo
from tree_helpers import git as tree_git

from evolvekit import tree
from evolvekit.candidate import Candidate
from evolvekit.config import ArchiveConfig, ConfigError, DescriptorConfig, build_config
from evolvekit.diffs import DiffError
from evolvekit.prompts import LEDGER_LINE_LIMIT, Inspiration, LedgerEntry
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.archive import Archive, descriptor_value
from evolvekit.search.driver import Driver
from evolvekit.search.operators import TreeContext, run_operator
from evolvekit.tree_prompts import (
    OPEN_LINES,
    marked_block,
    numbered,
    open_files,
    parse_diff_response,
    tree_messages,
)


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


def test_the_tree_prompt_carries_the_bounded_ledger(ctx, config, parent):
    provider = FakeProvider(responses=["no diff here"])
    ledger = [LedgerEntry(id=f"g001-c000{k}", operator="diff", change="+2/-1 diff lines; files: solver.py",
                          outcome="failed: `" + "e" * 5000 + "`" if k == 3 else f"+0.{k}") for k in range(6, 0, -1)]
    failing = Candidate(id=parent.id, generation=0, block=parent.block, source="", operator="human-seed",
                        last_failure="stage full: exit code 1\nboom")
    run_operator("diff", config=config, provider=provider, model_role="small", parent=failing,
                 parent_result=None, skeleton_prefix="", skeleton_suffix="", tree=ctx, ledger=ledger)
    prompt = provider.calls[0]["messages"][-1]["content"]
    assert "## Last failure artefact" not in prompt
    section = prompt[prompt.index("## What was tried on this parent"):prompt.index("## Your task")]
    bullets = [line for line in section.splitlines() if line.startswith("- ")]
    assert 0 < len(bullets) <= 5 and all(len(line) <= LEDGER_LINE_LIMIT for line in bullets)
    assert any(line.endswith("...`") for line in bullets), "the huge outcome was cut, its backtick kept"
    assert "## What was tried on this parent" not in tree_messages(
        config, parent=failing, result=None, operator="diff", files={"solver.py": "x = 1\n"})[1]["content"]


def test_parse_diff_fenced_and_bare():
    body = "--- a/solver.py\n+++ b/solver.py\n@@ -2 +2 @@\n-VALUE = 3\n+VALUE = 2\n"
    assert parse_diff_response(f"Here it is:\n```diff\n{body}```\nDone.") == body
    assert parse_diff_response(f"```patch\n{body}```") == body
    assert parse_diff_response(f"```\n{body}```") == body
    assert parse_diff_response("I changed one line.\n" + body) == body
    assert parse_diff_response("I changed one line.\n" + body + "\nThat should help.\n") == body, \
        "unfenced: cut at a blank line followed by a line no diff holds"
    two = body + "\n--- a/other.py\n+++ b/other.py\n@@ -1 +1 @@\n-a\n+b\n"
    assert parse_diff_response(two) == two, "a blank line between files is kept"
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
    over = "```diff\n--- a/solver.py\n+++ b/solver.py\n@@ -1,3 +1,3 @@\n # caf�\n-VALUE = 5\n+VALUE = 2\n print(VALUE)\n```"
    clear = "```diff\n--- a/solver.py\n+++ b/solver.py\n@@ -2,2 +2,2 @@\n-VALUE = 5\n+VALUE = 2\n print(VALUE)\n```"
    provider = FakeProvider(responses=[over, clear])
    try:
        result = _run("diff", provider, config=config, parent=seed, ctx=ctx)
        sent = "".join(m["content"] for m in provider.calls[0]["messages"])
        assert "    1  # caf�" in sent and not any("\ud800" <= ch <= "\udfff" for ch in sent), \
            "U+FFFD to the model, never a lone surrogate"
        assert not result.ok and not result.provider_error and "does not apply" in result.error, \
            "a hunk over such a line is an ordinary format failure"
        result = _run("diff", provider, config=config, parent=seed, ctx=ctx)
        assert result.ok and "+VALUE = 2" in result.block, result.error
        assert tree.applies(ctx.repo, base, result.block), "the byte on disk is kept"
    finally:
        tree.remove_all(tmp_path / "run")


def test_a_block_rewrite_that_writes_u_fffd_back_is_refused(tmp_path):
    repo, base = _block_repo(tmp_path)
    (repo / "app dir" / "rules.py").write_bytes(
        b"# EVOLVE-BLOCK-START\ndef rule():\n    # caf\xe9\n    return 1\n# EVOLVE-BLOCK-END\n")
    git_init_commit_again(repo)
    raw = _raw(repo, head(repo))
    raw["problem"]["tree"]["open"] = ["rules.py"]
    config = build_config(raw, base_dir=tmp_path)
    ctx = TreeContext(tmp_path / "run", tree.Repo(repo / "app dir"), head(repo), ("rules.py",), "b.draft")
    seed = Candidate(id="s", generation=0, block="", source="", operator="human-seed")
    try:
        echo = "```python\ndef rule():\n    # caf�\n    return 2\n```"
        result = _run("diff", FakeProvider(responses=[echo]), config=config, parent=seed, ctx=ctx)
        assert not result.ok and "byte that is not UTF-8" in result.error
        edit = "<<<<<<< SEARCH\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE\n"
        result = _run("diff", FakeProvider(responses=[edit]), config=config, parent=seed, ctx=ctx)
        assert result.ok and "+    return 2" in result.block and tree.applies(ctx.repo, head(repo), result.block)
    finally:
        tree.remove_all(tmp_path / "run")


def git_init_commit_again(top: Path) -> None:
    tree_git(top, "add", "-A")
    tree_git(top, "commit", "-q", "-m", "next")


# -- paths: the open folder's, or the toplevel's -----------------------------


def test_a_reply_in_toplevel_paths_applies_and_inspirations_read_relative(ctx, config, parent, project):
    repo, _ = project
    top = "--- a/app dir/solver.py\n+++ b/app dir/solver.py\n@@ -2 +2 @@\n-VALUE = 3\n+VALUE = 2\n"
    result = _run("diff", FakeProvider(responses=[f"```diff\n{top}```"]), config=config, parent=parent, ctx=ctx)
    assert result.ok and "+VALUE = 2" in result.block, result.error
    other = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 4")})
    assert "a/app dir/solver.py" in other
    provider = FakeProvider(responses=["no diff"])
    run_operator("diff", config=config, provider=provider, model_role="small", parent=parent, parent_result=None,
                 skeleton_prefix="", skeleton_suffix="", tree=ctx,
                 inspirations=[Inspiration(id="g001-c0002", summary="s", score_line="score 1", block=other)])
    prompt = provider.calls[0]["messages"][-1]["content"]
    assert "### Another combination's change (g001-c0002, score 1)" in prompt
    assert "--- a/solver.py" in prompt and "+++ b/solver.py" in prompt and "app dir/" not in prompt
    assert tree.relative_diff(other, "app dir/").startswith("diff --git a/solver.py b/solver.py")


def test_a_crlf_file_takes_an_lf_diff(tmp_path):
    top = tmp_path / "my repo"
    (top / "app dir").mkdir(parents=True)
    (top / ".gitattributes").write_bytes(b"* -text\n")
    (top / "app dir" / "win.py").write_bytes(b"A = 1\r\nB = 2\r\nC = 3\r\n")
    (top / "app dir" / "unix.py").write_bytes(b"X = 1\n")
    git_init_commit(top)
    repo = tree.Repo(top / "app dir")
    made = tree.materialise(tmp_path / "run", repo=repo, base=head(top), diff="", candidate_id="c")
    try:
        assert (made.path / "win.py").read_bytes().count(b"\r\n") == 3
        lf = ("--- a/win.py\n+++ b/win.py\n@@ -1,3 +1,3 @@\n A = 1\n-B = 2\n+B = 5\n C = 3\n"
              "--- a/unix.py\n+++ b/unix.py\n@@ -1 +1 @@\n-X = 1\n+X = 2\n")
        tree.apply_in(made, lf, directory="app dir")
        assert (made.path / "win.py").read_bytes() == b"A = 1\r\nB = 5\r\nC = 3\r\n"
        assert (made.path / "unix.py").read_bytes() == b"X = 2\n", "an LF file keeps LF"
        with pytest.raises(tree.TreeError, match="the diff does not apply"):
            tree.apply_in(made, "--- a/win.py\n+++ b/win.py\n@@ -2 +2 @@\n-B = 9\n+B = 1\n", directory="app dir")
    finally:
        tree.remove_all(tmp_path / "run")


def test_a_reply_of_two_files_without_git_headers_and_wrong_counts_applies(project, tmp_path):
    repo, base = project
    made = tree.materialise(tmp_path / "run", repo=tree.Repo(repo / "app dir"), base=base, diff="", candidate_id="m")
    try:
        # git's own --recount would read bench.py's `--- a/` line as a removed line
        reply = ("--- a/solver.py\n+++ b/solver.py\n@@ -1,9 +1,9 @@\n import json, os, sys, time\n-VALUE = 5\n"
                 "+VALUE = 1\n started = time.time()\n\n--- a/bench.py\n+++ b/bench.py\n@@ -1 +1 @@\n"
                 "-print('bench')\n+print('b')\n")
        tree.apply_in(made, reply, directory="app dir")
        assert "VALUE = 1" in (made.path / "solver.py").read_text(encoding="utf-8")
        assert (made.path / "bench.py").read_text(encoding="utf-8") == "print('b')\n"
    finally:
        tree.remove_all(tmp_path / "run")


def test_a_hunk_line_without_its_mark_refuses_the_diff(tmp_path):
    top = tmp_path / "my repo"
    (top / "app dir").mkdir(parents=True)
    (top / "app dir" / "abc.txt").write_bytes(b"a\nb\nc\nd\ne\n")
    git_init_commit(top)
    made = tree.materialise(tmp_path / "run", repo=tree.Repo(top / "app dir"), base=head(top), diff="",
                            candidate_id="u")
    try:
        reply = "--- a/abc.txt\n+++ b/abc.txt\n@@ -1,5 +1,5 @@\n a\n-b\n+B\nc\n-d\n+D\n e\n"
        with pytest.raises(tree.TreeError, match=re.escape("the diff does not apply: a hunk line has no ' ', '+' or "
                                                           "'-' mark: 'c'")):
            tree.apply_in(made, reply, directory="app dir")
        assert (made.path / "abc.txt").read_bytes() == b"a\nb\nc\nd\ne\n", "nothing applied, not half of it"
        tree.apply_in(made, reply.replace("\nc\n", "\n c\n"), directory="app dir")
        assert (made.path / "abc.txt").read_bytes() == b"a\nB\nc\nD\ne\n"
    finally:
        tree.remove_all(tmp_path / "run")


def test_a_folder_named_like_the_repo_folder_stays_relative(tmp_path):
    top = tmp_path / "my repo"
    (top / "src" / "src").mkdir(parents=True)
    (top / "src" / "x.py").write_bytes(b"OUTER = 1\n")
    (top / "src" / "src" / "x.py").write_bytes(b"INNER = 1\n")
    git_init_commit(top)
    made = tree.materialise(tmp_path / "run", repo=tree.Repo(top / "src"), base=head(top), diff="", candidate_id="s")
    try:
        tree.apply_in(made, "--- a/src/x.py\n+++ b/src/x.py\n@@ -1 +1 @@\n-INNER = 1\n+INNER = 2\n", directory="src")
        assert (made.path / "src" / "x.py").read_bytes() == b"INNER = 2\n", "relative to the open folder"
        assert (made.path / "x.py").read_bytes() == b"OUTER = 1\n"
    finally:
        tree.remove_all(tmp_path / "run")


# -- limits and faults -------------------------------------------------------


def test_open_files_past_the_line_limit_are_refused_before_a_call(ctx, config, project):
    repo, _ = project
    grown = change(repo, {"app dir/solver.py": SOLVER + "".join(f"X{i} = {i}\n" for i in range(OPEN_LINES))})
    big = Candidate(id="g001-c0001", generation=1, block=grown, source="", operator="diff")
    provider = FakeProvider(responses=["x"])
    result = _run("diff", provider, config=config, parent=big, ctx=ctx)
    lines = OPEN_LINES + SOLVER.count("\n")
    assert not result.ok and not provider.calls and not result.provider_error
    assert result.error == (f"the open files hold {lines} lines; a code study shows them whole, up to 2000 "
                            "— narrow vary.code.open")
    assert not tree.tree_dir(ctx.run_dir, ctx.draft_id).exists()


def test_a_parent_that_cannot_be_checked_out_is_not_the_model_s_failure(ctx, config):
    broken = Candidate(id="p", generation=1, block="--- a/x\n+++ b/x\n@@ -1 +1 @@\n-no\n+yes\n", source="",
                       operator="diff")
    provider = FakeProvider(responses=["x"])
    result = _run("diff", provider, config=config, parent=broken, ctx=ctx)
    assert not result.ok and result.provider_error and not provider.calls
    assert result.error.startswith("the parent's change could not be checked out")


def test_a_tree_config_refuses_the_other_ai_operators(project, tmp_path):
    repo, base = project
    for name in ("rewrite", "crossover"):  # big_step is no share; it is `big_step_every`
        raw = _raw(repo, base)
        raw["search"]["operators"] = {"diff": 1.0, name: 0.5}
        with pytest.raises(ConfigError, match=re.escape(f"search.operators.{name}: does not work on a source tree")):
            build_config(raw, base_dir=tmp_path)
    raw = _raw(repo, base)
    raw["search"]["big_step_every"] = 3
    with pytest.raises(ConfigError, match=re.escape("search.big_step_every: big steps do not work on a source tree")):
        build_config(raw, base_dir=tmp_path)


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
    seed_again = make("s2", "")  # a 4th member: the quantile bins are cut again at 4 members
    for c in (seed, seed_again, a, b):
        archive.add(c)
    assert len({tuple(c.cell) for c in (seed, a, b)}) == 3
    skeleton = Archive.from_config(config)
    for c in (make("a", small), make("b", large)):
        skeleton.add(c)
    assert len(skeleton.cells) == 1, "a skeleton problem keeps the block's AST complexity"
    again = Archive.from_records([c.to_record() for c in (seed, seed_again, a, b)], config, tree=True)
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
