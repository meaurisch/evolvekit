"""A candidate's change is what the engineer's git would commit (persona round 3, E1).

The engineer's checkout is often CRLF on Windows (`core.autocrlf=true`) while the
repository holds LF. An operator that writes a file -- an AI's diff, a marked
block's rewrite, a coding agent's edits, which may come back CRLF -- must not turn
a two-line change into a rewrite of the whole file: the block is computed with
the repository's own line-ending rules, and a file an operator rewrites keeps the
line endings it has in the checkout. `study export --diff` and `study pr` then
agree with the block.

Every repository sets the `core.autocrlf` it needs in its own config: this
machine has it globally, CI on Linux does not."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from test_agent_patch import FakeAgentProvider
from test_cli_pr import finished_code_run
from test_driver_tree import _raw
from tree_helpers import git, head

from evolvekit import tree
from evolvekit.candidate import Candidate
from evolvekit.cli import main
from evolvekit.config import build_config
from evolvekit.providers.fake import FakeProvider
from evolvekit.search.agent_patch import agent_patch
from evolvekit.search.operators import TreeContext, run_operator

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

LINES = [f"STEP_{i} = {i}" for i in range(1, 21)]
TEXT = "\n".join(LINES) + "\n"
EDITED = TEXT.replace("STEP_5 = 5", "STEP_5 = 50").replace("STEP_15 = 15", "STEP_15 = 150")
REPLY = ("```diff\n--- a/solver.py\n+++ b/solver.py\n@@ -4,3 +4,3 @@\n STEP_4 = 4\n-STEP_5 = 5\n+STEP_5 = 50\n"
         " STEP_6 = 6\n@@ -14,3 +14,3 @@\n STEP_14 = 14\n-STEP_15 = 15\n+STEP_15 = 150\n STEP_16 = 16\n```")


def _repo(tmp_path: Path, *, autocrlf: str, data: bytes = TEXT.encode(), attributes: bytes | None = None) -> Path:
    """`<tmp>/my repo` with `app dir/solver.py` committed as `data` (LF unless said), its own
    `core.autocrlf`, and the engineer's checkout made again under it (CRLF when "true")."""
    top = tmp_path / "my repo"
    (top / "app dir").mkdir(parents=True)
    git(top, "init", "-q", "-b", "main")
    for key, value in (("user.name", "Test User"), ("user.email", "test@example.com"), ("commit.gpgsign", "false"),
                       ("core.autocrlf", "false")):
        git(top, "config", key, value)
    if attributes is not None:
        (top / ".gitattributes").write_bytes(attributes)
    (top / "app dir" / "solver.py").write_bytes(data)
    git(top, "add", "-A")
    git(top, "commit", "-q", "-m", "base")
    git(top, "config", "core.autocrlf", autocrlf)
    (top / "app dir" / "solver.py").unlink()
    git(top, "checkout", "--", ".")
    return top


def _ctx(top: Path, tmp_path: Path) -> tuple[TreeContext, Candidate]:
    seed = Candidate(id="g000-c0000", generation=0, block="", source="", operator="human-seed")
    return TreeContext(tmp_path / "run", tree.Repo(top / "app dir"), head(top), ("solver.py",), "g001-c0001.draft"), seed


def _config(top: Path, tmp_path: Path):
    raw = _raw(top, head(top))
    raw["problem"]["description"] = "Make the steps larger.\nWhat to improve: larger steps."
    return build_config(raw, base_dir=tmp_path)


def _new_blob(block: str) -> str:
    return re.search(r"^index [0-9a-f]+\.\.([0-9a-f]+)", block, re.MULTILINE).group(1)


def _engineer_commits(top: Path, data: bytes) -> str:
    """The blob the engineer's own git makes of `data` as app dir/solver.py (their rules)."""
    target = top / "app dir" / "solver.py"
    before = target.read_bytes()
    target.write_bytes(data)
    try:
        git(top, "add", "--", "app dir/solver.py")
        return git(top, "rev-parse", ":app dir/solver.py")
    finally:
        git(top, "reset", "-q", "--", "app dir/solver.py")
        target.write_bytes(before)


def _two_lines(block: str) -> None:
    stat = tree.diff_stat(block)
    assert (stat["added"], stat["removed"], stat["files"]) == (2, 2, ["app dir/solver.py"]), block
    assert "\r" not in block, "the repository holds LF: no line of the change carries a CR"


def test_the_checkout_is_crlf_and_the_repository_lf(tmp_path):
    top = _repo(tmp_path, autocrlf="true")
    assert (top / "app dir" / "solver.py").read_bytes().count(b"\r\n") == 20, "a CRLF checkout, as on Windows"
    assert "\r" not in git(top, "show", "HEAD:app dir/solver.py"), "the repository holds LF"


def test_a_diff_reply_on_a_crlf_checkout_is_two_changed_lines(tmp_path):
    top = _repo(tmp_path, autocrlf="true")
    ctx, seed = _ctx(top, tmp_path)
    try:
        result = run_operator("diff", config=_config(top, tmp_path), provider=FakeProvider(responses=[REPLY]),
                              model_role="small", parent=seed, parent_result=None, skeleton_prefix="",
                              skeleton_suffix="", tree=ctx)
        assert result.ok, result.error
        _two_lines(result.block)
        assert _new_blob(result.block) == _engineer_commits(top, EDITED.replace("\n", "\r\n").encode())
    finally:
        tree.remove_all(tmp_path / "run")


@pytest.mark.parametrize("autocrlf", ["true", "false"])
@pytest.mark.parametrize("ending", ["\n", "\r\n"], ids=["LF", "CRLF"])
def test_an_agent_edit_is_two_changed_lines_whatever_endings_it_writes(tmp_path, autocrlf, ending):
    """The persona's run: the agent rewrote solver.py with CRLF and the block read +151 -124."""
    top = _repo(tmp_path, autocrlf=autocrlf)
    ctx, seed = _ctx(top, tmp_path)

    class Agent(FakeAgentProvider):
        def act(self, brief, *, cwd, model, timeout_s, **options):
            done = super().act(brief, cwd=cwd, model=model, timeout_s=timeout_s, **options)
            (Path(cwd) / "solver.py").write_bytes(EDITED.replace("\n", ending).encode())  # as its tool wrote it
            return done

    try:
        result = agent_patch(config=_config(top, tmp_path), provider=Agent({}), model_role="strong", parent=seed,
                             parent_result=None, context=ctx, best=None, worst=None, failures=())
        assert result.ok, result.error
        _two_lines(result.block)
        made = tree.materialise(tmp_path / "run", repo=ctx.repo, base=ctx.base, diff=result.block, candidate_id="c")
        assert (made.path / "solver.py").read_bytes() == EDITED.encode(), \
            "the tree holds the file with the endings it was checked out with"
        if autocrlf == "true":
            assert _new_blob(result.block) == _engineer_commits(top, EDITED.replace("\n", ending).encode()), \
                "the same blob the engineer's git commits"
    finally:
        tree.remove_all(tmp_path / "run")


def test_a_marked_block_rewrite_keeps_a_crlf_files_endings(tmp_path):
    data = ("import json\r\n# EVOLVE-BLOCK-START\r\ndef rule():\r\n    return 1\r\n# EVOLVE-BLOCK-END\r\n"
            "print(json.dumps({'value': rule()}))\r\n").encode()
    top = _repo(tmp_path, autocrlf="false", data=data, attributes=b"* -text\n")
    ctx, seed = _ctx(top, tmp_path)
    try:
        provider = FakeProvider(responses=["```python\ndef rule():\n    return 2\n```"])
        result = run_operator("diff", config=_config(top, tmp_path), provider=provider, model_role="small",
                              parent=seed, parent_result=None, skeleton_prefix="", skeleton_suffix="", tree=ctx)
        assert result.ok and result.mode == "rewrite", result.error
        stat = tree.diff_stat(result.block)
        assert (stat["added"], stat["removed"]) == (1, 1), result.block
        made = tree.materialise(tmp_path / "run", repo=ctx.repo, base=ctx.base, diff=result.block, candidate_id="c")
        assert (made.path / "solver.py").read_bytes() == data.replace(b"return 1", b"return 2"), \
            "a CRLF file stays CRLF"
    finally:
        tree.remove_all(tmp_path / "run")


def test_export_and_pr_agree_with_the_block(tmp_path, capsys, monkeypatch):
    """The persona's PR: best.patch, the size line and the pushed commit all say the same two lines."""
    home, slug, repo = finished_code_run(tmp_path)
    git(repo, "config", "core.autocrlf", "true")
    (repo / "app dir" / "solver.py").unlink()
    git(repo, "checkout", "--", ".")
    base = head(repo)
    located = tree.Repo(repo / "app dir")
    scratch = tmp_path / "scratch"
    made = tree.materialise(scratch, repo=located, base=base, diff="", candidate_id="c1")
    text = (made.path / "solver.py").read_text(encoding="utf-8")
    edited = text.replace("VALUE = 5", "VALUE = 3").replace("started = time.time()", "started = time.monotonic()")
    (made.path / "solver.py").write_bytes(edited.replace("\n", "\r\n").encode())  # as the agent wrote it
    block = tree.staged_diff(made, base)
    tree.remove(scratch, "c1")
    _two_lines(block)
    commit = tree.materialise(scratch, repo=located, base=base, diff=block, candidate_id="c1").commit
    tree.remove(scratch, "c1")
    run_dir = home / "studies" / slug / "runs" / "r1"
    rows = [json.loads(line) for line in (run_dir / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    rows[1].update(block=block, commit=commit)
    (run_dir / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    assert main(["study", "export", slug, "--diff", "--home", str(home)]) == 0
    capsys.readouterr()
    assert (run_dir / "best.patch").read_text(encoding="utf-8") == block, "best.patch is the block"
    monkeypatch.setattr("evolvekit.harness.pr.push_branch", lambda *a, **k: pytest.fail("no remote"))
    assert main(["study", "pr", slug, "--home", str(home)]) == 0
    assert "+2 −2 lines in app dir/solver.py" in capsys.readouterr().out
    assert main(["study", "pr", slug, "--yes", "--json", "--home", str(home)]) == 0
    branch = json.loads(capsys.readouterr().out)["branch"]
    assert git(repo, "diff", "--numstat", base, branch) == "2\t2\tapp dir/solver.py", "the commit is the same two lines"
    assert git(repo, "rev-parse", f"{branch}^{{tree}}") == git(repo, "rev-parse", f"{commit}^{{tree}}")
