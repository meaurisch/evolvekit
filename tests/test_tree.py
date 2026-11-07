"""`evolvekit.tree`: a candidate is a commit in a git worktree; diffs are
normalised, filtered and counted; the engineer's checkout is never touched."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from tree_helpers import SOLVER, change, git, git_raw, head, make_repo

from evolvekit import tree


@pytest.fixture
def repo(tmp_path):
    return make_repo(tmp_path)


def test_repo_toplevel_and_prefix(repo):
    inside = tree.Repo(repo / "app dir")
    assert inside.toplevel == repo.resolve() and inside.prefix == "app dir/"
    assert tree.Repo(repo).prefix == "" and tree.Repo(repo).path == repo.resolve()


def test_repo_outside_git_is_an_error(tmp_path):
    (tmp_path / "plain").mkdir()
    with pytest.raises(tree.TreeError, match="is not inside a git repository"):
        tree.Repo(tmp_path / "plain")
    with pytest.raises(tree.TreeError, match="no such folder"):
        tree.Repo(tmp_path / "missing")


def test_materialise_applies_and_commits(repo, tmp_path):
    base = head(repo)
    diff = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 3")})
    run_dir = tmp_path / "run dir"
    made = tree.materialise(run_dir, repo=tree.Repo(repo / "app dir"), base=base, diff=diff, candidate_id="g001-c0001")
    assert made.worktree == run_dir / "work" / "trees" / "g001-c0001"
    assert "VALUE = 3" in (made.path / "solver.py").read_text(encoding="utf-8")
    assert git(made.worktree, "rev-parse", "HEAD^") == base and git(made.worktree, "rev-parse", "HEAD") == made.commit
    # raw times: `--date=iso-strict` prints UTC as `+00:00` or `Z` depending on git's version
    assert git(made.worktree, "log", "-1", "--format=%an <%ae> %at | %cn <%ce> %ct") == \
        "evolvekit <evolvekit@localhost> 946684800 | evolvekit <evolvekit@localhost> 946684800"
    assert git(repo, "status", "--porcelain") == "", "the engineer's working tree is untouched"
    again = tree.materialise(tmp_path / "other run", repo=tree.Repo(repo / "app dir"), base=base, diff=diff,
                             candidate_id="g001-c0001")
    assert again.commit == made.commit, "a fixed author and date: the same change is the same commit"
    with pytest.raises(tree.TreeError, match="does not apply"):
        tree.materialise(run_dir, repo=tree.Repo(repo / "app dir"), base=base,
                         diff=diff.replace("-VALUE = 5", "-VALUE = 9"), candidate_id="bad")
    assert not tree.tree_dir(run_dir, "bad").exists()
    assert "bad" not in git(repo, "worktree", "list")


def test_materialise_empty_diff_is_the_base(repo, tmp_path):
    made = tree.materialise(tmp_path / "run dir", repo=tree.Repo(repo / "app dir"), base=head(repo), diff="",
                            candidate_id="g000-c0001")
    assert made.commit == head(repo) and git(made.worktree, "rev-parse", "HEAD") == head(repo)


def test_materialise_again_replaces_a_crashed_attempt(repo, tmp_path):
    run_dir, r = tmp_path / "run dir", tree.Repo(repo / "app dir")
    diff = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 3")})
    first = tree.materialise(run_dir, repo=r, base=head(repo), diff=diff, candidate_id="g001-c0001")
    (first.path / "left over.txt").write_text("from a crash\n", encoding="utf-8")
    again = tree.materialise(run_dir, repo=r, base=head(repo), diff=diff, candidate_id="g001-c0001")
    assert again == first and not (again.path / "left over.txt").exists()
    shutil.rmtree(again.worktree)  # gone without a prune: git still lists it
    assert tree.materialise(run_dir, repo=r, base=head(repo), diff=diff, candidate_id="g001-c0001") == first


def test_materialise_skips_the_repositorys_hooks(repo, tmp_path):
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("post-checkout", "post-commit"):
        (hooks / name).write_text(f"#!/bin/sh\necho {name} >> hook-ran.txt\n", encoding="utf-8", newline="\n")
        (hooks / name).chmod(0o755)
    git(repo, "worktree", "add", "-q", "--detach", str(tmp_path / "plain tree"), "HEAD")
    assert (tmp_path / "plain tree" / "hook-ran.txt").is_file(), "the hooks do run for plain git"
    diff = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 3")})
    made = tree.materialise(tmp_path / "run dir", repo=tree.Repo(repo), base=head(repo), diff=diff, candidate_id="a")
    assert not (made.worktree / "hook-ran.txt").exists() and not (repo / "hook-ran.txt").exists()


def test_tree_path_is_prefix_inside_worktree(repo, tmp_path):
    inside = tree.materialise(tmp_path / "r", repo=tree.Repo(repo / "app dir"), base=head(repo), diff="", candidate_id="a")
    whole = tree.materialise(tmp_path / "r", repo=tree.Repo(repo), base=head(repo), diff="", candidate_id="b")
    assert inside.path == inside.worktree / "app dir" and (inside.path / "solver.py").is_file()
    assert whole.path == whole.worktree


def test_remove_retries_on_windows_lock(repo, tmp_path, monkeypatch):
    run_dir, r = tmp_path / "run dir", tree.Repo(repo / "app dir")
    made = tree.materialise(run_dir, repo=r, base=head(repo), diff="", candidate_id="g001-c0001")
    calls, real = [], shutil.rmtree

    def flaky(path, *args, **kwargs):
        calls.append(path)
        if len(calls) < 3:
            raise PermissionError("The process cannot access the file because it is being used by another process")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(tree.shutil, "rmtree", flaky)
    monkeypatch.setattr(tree.time, "sleep", lambda s: None)
    assert tree.remove(run_dir, "g001-c0001") is True
    assert len(calls) == 3 and not made.worktree.exists()
    assert "g001-c0001" not in git(repo, "worktree", "list"), "pruned from the repository's list"
    held = tree.materialise(run_dir, repo=r, base=head(repo), diff="", candidate_id="g001-c0002")

    def locked(*args, **kwargs):
        raise PermissionError("in use")

    monkeypatch.setattr(tree.shutil, "rmtree", locked)
    assert tree.remove(run_dir, "g001-c0002") is False and held.worktree.exists()
    assert tree.remove(run_dir, "never-made") is True


def test_remove_all_keeps_what_it_is_told(repo, tmp_path):
    run_dir, r = tmp_path / "run dir", tree.Repo(repo / "app dir")
    for name in ("g000-c0001", "g001-c0001", "g001-c0002"):
        tree.materialise(run_dir, repo=r, base=head(repo), diff="", candidate_id=name)
    assert tree.remove_all(run_dir, keep={"g001-c0001"}) == ["g000-c0001", "g001-c0002"]
    assert [p.name for p in (run_dir / "work" / "trees").iterdir()] == ["g001-c0001"]
    listed = git(repo, "worktree", "list")
    assert "g001-c0001" in listed and "g000-c0001" not in listed and "g001-c0002" not in listed
    assert tree.remove_all(tmp_path / "never ran") == []


def test_working_base_commits_a_snapshot_without_touching_the_tree(repo):
    (repo / "app dir" / "solver.py").write_text("VALUE = 7\n", encoding="utf-8", newline="\n")
    (repo / "app dir" / "new.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    status, before, index = git(repo, "status", "--porcelain"), head(repo), (repo / ".git" / "index").read_bytes()
    commit = tree.resolve_base(tree.Repo(repo / "app dir"), "working")
    assert git(repo, "status", "--porcelain") == status and head(repo) == before
    assert (repo / ".git" / "index").read_bytes() == index, "the real index is never written"
    assert git(repo, "show", f"{commit}:app dir/solver.py") == "VALUE = 7"
    assert git(repo, "show", f"{commit}:app dir/new.py") == "x = 1"
    assert git(repo, "rev-parse", f"{commit}^") == before
    refs = git(repo, "for-each-ref", "--format=%(refname)").splitlines()
    assert any(ref.startswith("refs/evolvekit/base-") for ref in refs)
    assert (repo / "app dir" / "solver.py").read_text(encoding="utf-8") == "VALUE = 7\n"


def test_working_base_keeps_the_checkouts_line_endings(repo):
    git(repo, "config", "core.autocrlf", "true")
    for rel in ("app dir/solver.py", "app dir/bench.py", "README.md"):
        (repo / rel).unlink()
    git(repo, "checkout", "--", ".")
    assert b"\r\n" in (repo / "README.md").read_bytes(), "a CRLF checkout, as on a Windows machine"
    assert tree.resolve_base(tree.Repo(repo), "working") == head(repo), "a clean checkout is HEAD itself"
    (repo / "app dir" / "bench.py").write_bytes(b"print('fast')\r\n")
    commit = tree.resolve_base(tree.Repo(repo), "working")
    assert git(repo, "diff", "--name-only", "HEAD", commit) == "app dir/bench.py", \
        "the checkout's CRLF is not recorded as a change to every file"


def test_normalise_diff_ignores_index_and_timestamps(repo):
    one = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 3")})
    norm = tree.normalise_diff(one)
    variants = [
        re.sub(r"^index .*$", "index 1234567..89abcde 100644", one, flags=re.MULTILINE),
        one.replace("--- a/app dir/solver.py", "--- a/app dir/solver.py\t2026-11-07 10:00:00.000000000 +0100"),
        one.replace("+VALUE = 3", "+VALUE = 3   "),
        one.replace("\n", "\r\n"),
        change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE  =  3")}),
        change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 3")}, unified=6),
        change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "# shorter tours\nVALUE = 3")}),
        change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "\nVALUE = 3")}),
    ]
    assert all(tree.normalise_diff(v) == norm for v in variants)
    other = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 4")})
    elsewhere = change(repo, {"app dir/solver.py": SOLVER + "VALUE = 3\n"})
    assert tree.normalise_diff(other) != norm and tree.normalise_diff(elsewhere) != norm
    readme = change(repo, {"README.md": "top\n# shorter tours\n"})
    assert tree.normalise_diff(readme) != "", "a '#' line is a heading in Markdown, not a comment"
    assert tree.normalise_diff("") == ""


def test_touches_only_names_offenders_relative_to_prefix(repo):
    diff = change(repo, {"app dir/solver.py": SOLVER.replace("VALUE = 5", "VALUE = 1"),
                         "app dir/bench.py": "print('fast')\n", "README.md": "x\n"})
    assert tree.changed_files(diff) == ["README.md", "app dir/bench.py", "app dir/solver.py"]
    assert tree.touches_only(diff, ["solver.py"], prefix="app dir/") == ["README.md", "app dir/bench.py"]
    assert tree.touches_only(diff, ["*.py"], prefix="app dir/") == ["README.md"]
    assert tree.touches_only(diff, ["**/*"], prefix="") == []
    nested = change(repo, {"app dir/lib/a.py": "a = 1\n", "app dir/lib/deep/b.py": "b = 1\n"})
    assert tree.touches_only(nested, ["lib/**/*.py"], prefix="app dir/") == []
    assert tree.touches_only(nested, ["lib/*.py"], prefix="app dir/") == ["app dir/lib/deep/b.py"]
    assert tree.diff_stat(diff) == {"added": 3, "removed": 3, "files": ["README.md", "app dir/bench.py", "app dir/solver.py"]}


def test_changed_files_sees_renames_binaries_deletions_and_quoted_names(repo):
    (repo / "q.sql").write_text("-- a comment\nselect 1;\n", encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "sql")
    git(repo, "mv", "app dir/bench.py", "app dir/timing.py")
    (repo / "app dir" / "logo.bin").write_bytes(bytes(range(256)))
    (repo / "app dir" / "café.py").write_text("x = 1\n", encoding="utf-8", newline="\n")
    git(repo, "rm", "-q", "README.md")
    (repo / "q.sql").write_text("select 1;\n", encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    diff = git_raw(repo, "-c", "core.quotepath=true", "diff", "--cached", "--binary", "-M")
    git(repo, "reset", "-q", "--hard", "HEAD")
    assert '"b/app dir/caf\\303\\251.py"' in diff and "rename from app dir/bench.py" in diff
    assert "\n--- a comment\n" in diff, "a removed '-- ' line looks like a file header"
    files = ["README.md", "app dir/bench.py", "app dir/café.py", "app dir/logo.bin", "app dir/timing.py", "q.sql"]
    assert tree.changed_files(diff) == files
    assert tree.touches_only(diff, ["*.py"], prefix="app dir/") == ["README.md", "app dir/logo.bin", "q.sql"]
    assert tree.diff_stat(diff) == {"added": 1, "removed": 2, "files": files}


def test_diff_between_refs(repo, tmp_path):
    base = head(repo)
    git(repo, "switch", "-q", "-c", "faster")
    (repo / "app dir" / "solver.py").write_text(SOLVER.replace("VALUE = 5", "VALUE = 2"), encoding="utf-8", newline="\n")
    git(repo, "commit", "-q", "-am", "faster")
    git(repo, "switch", "-q", "main")
    r = tree.Repo(repo / "app dir")
    diff = tree.diff_between(r, base, "faster")
    assert diff.startswith("diff --git a/app dir/solver.py b/app dir/solver.py") and "+VALUE = 2" in diff
    assert tree.diff_between(r, base, base) == ""
    assert tree.resolve_commit(r, "faster") == git(repo, "rev-parse", "faster")
    with pytest.raises(tree.TreeError, match="no-such-branch"):
        tree.diff_between(r, base, "no-such-branch")
    with pytest.raises(tree.TreeError, match="--output=x"):
        tree.resolve_commit(r, "--output=x")
    made = tree.materialise(tmp_path / "r", repo=r, base=base, diff=diff, candidate_id="b")
    assert "VALUE = 2" in (made.path / "solver.py").read_text(encoding="utf-8")


def test_diff_between_is_the_branchs_own_change(repo, tmp_path):
    git(repo, "switch", "-q", "-c", "older")
    (repo / "app dir" / "solver.py").write_text(SOLVER.replace("VALUE = 5", "VALUE = 2"), encoding="utf-8", newline="\n")
    git(repo, "commit", "-q", "-am", "older")
    git(repo, "switch", "-q", "main")
    (repo / "README.md").write_text("top\nmoved on\n", encoding="utf-8", newline="\n")
    git(repo, "commit", "-q", "-am", "the base moves on")
    r = tree.Repo(repo / "app dir")
    diff = tree.diff_between(r, head(repo), "older")
    assert tree.changed_files(diff) == ["app dir/solver.py"], "the base's newer commit is not reverted"
    made = tree.materialise(tmp_path / "r", repo=r, base=head(repo), diff=diff, candidate_id="b")
    assert "VALUE = 2" in (made.path / "solver.py").read_text(encoding="utf-8")
    assert "moved on" in (made.worktree / "README.md").read_text(encoding="utf-8")


def test_git_version_and_missing_git(monkeypatch):
    assert re.match(r"\d+\.\d+", tree.git_version() or "")

    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(tree.subprocess, "run", missing)
    assert tree.git_version() is None
    with pytest.raises(tree.TreeError, match="git is not installed"):
        tree.Repo(Path.cwd())
