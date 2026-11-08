"""The result is a change (design §5.6): `study export --diff` writes the best
combination's patch, `study pr` makes a branch from it, pushes it when a
remote exists and opens a PR with `gh` -- and nothing is pushed or opened
without `--yes`.

Every repository is a temporary one (`tree_helpers.make_repo`); `gh` and the
push are replaced, or the remote is a bare repository in the test's folder.
Nothing touches the evolvekit checkout."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from test_app_results import finished_run, study_with_runs
from tree_helpers import SOLVER, git, head, make_repo

from evolvekit import tree
from evolvekit.app.store import Home
from evolvekit.cli import main
from evolvekit.harness import pr
from evolvekit.harness.study import load_study, save_study
from evolvekit.ledger import read_jsonl

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

RUN = "20260101-000000"
KPIS = {"tour_length": 100.0, "longest_leg": 4.0}


def finished_code_run(tmp_path: Path, *, working: str | None = None) -> tuple[Path, str, Path]:
    """A library home with a code study (the faster template) on `<repo>/app dir`, and a
    finished run `r1`: the starting point and `c1` (`VALUE = 3`), the final check deciding
    `use` on c1. `working`: the study started `from: working`, with solver.py as this text
    uncommitted (the base is the snapshot, c1 changes it to `VALUE = 3`)."""
    repo = make_repo(tmp_path)
    home = Home(tmp_path / "home")
    slug = home.create_study("demo-tour", "faster", "Solver")
    home.use_samples(slug)
    home.split(slug, 1)
    root = home.study_root(slug)
    study = load_study(root)
    study.code.repo = str(repo / "app dir")
    if working is not None:
        study.code.from_ref = "working"
    save_study(study, root)
    located = tree.Repo(repo / "app dir")
    if working is None:
        base = head(repo)
        old = SOLVER
    else:
        (repo / "app dir" / "solver.py").write_text(working, encoding="utf-8", newline="\n")
        base, old = tree.snapshot_working(located), working
    edited = old.replace("VALUE = 5", "VALUE = 3").replace("VALUE = 4", "VALUE = 3")
    scratch = tmp_path / "scratch"
    made = tree.materialise(scratch, repo=located, base=base, diff="", candidate_id="c1")
    (made.path / "solver.py").write_text(edited, encoding="utf-8", newline="\n")
    one = tree.staged_diff(made, base)
    tree.remove(scratch, "c1")
    made = tree.materialise(scratch, repo=located, base=base, diff=one, candidate_id="c1")
    tree.remove(scratch, "c1")
    run_dir = root / "runs" / "r1"
    finished_run(run_dir)  # job.json (final: use on c1), events.jsonl, the comparison
    rows = [{"id": "seed", "generation": 0, "operator": "human-seed", "parent_id": None, "score": -100.0,
             "params": {}, "kpis": KPIS, "block": "", "commit": base},
            {"id": "c1", "generation": 1, "operator": "diff", "parent_id": "seed", "score": -95.8, "params": {},
             "kpis": {"tour_length": 95.8, "longest_leg": 4.1}, "block": one, "commit": made.commit}]
    (run_dir / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (run_dir / "evolvekit.yaml").write_text(yaml.safe_dump({"problem": {"tree": {
        "repo": str(repo / "app dir"), "base": base, "open": ["solver.py"], "app": "{tree}/solver.py"}}}),
        encoding="utf-8")
    shutil.copyfile(root / "study.yaml", run_dir / "study.yaml")
    return home.root, slug, repo


def test_export_diff_writes_patch(tmp_path, capsys):
    home, slug, repo = finished_code_run(tmp_path)
    assert main(["study", "export", slug, "--diff", "--home", str(home)]) == 0
    patch = home / "studies" / slug / "runs" / "r1" / "best.patch"
    assert capsys.readouterr().out.strip() == str(patch)
    assert "+VALUE = 3" in patch.read_text(encoding="utf-8") and b"\r\n" not in patch.read_bytes()
    assert git(repo, "apply", "--check", str(patch)) == "", "it applies to the starting point"
    assert main(["study", "export", slug, "--diff", "--json", "--home", str(home)]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "patch": str(patch)}
    assert main(["study", "export", slug, "--home", str(home)]) == 2
    assert "name what to export: --diff (the best combination's change as a patch)" in capsys.readouterr().err


def test_export_and_pr_refuse_when_the_starting_point_stayed_best(tmp_path, capsys):
    home, slug, _ = finished_code_run(tmp_path)
    job_path = home / "studies" / slug / "runs" / "r1" / "job.json"
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job["final"]["candidate"] = "seed"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    assert main(["study", "export", slug, "--diff", "--home", str(home)]) == 2
    assert "the starting point stayed the best: there is no change to export" in capsys.readouterr().err
    assert main(["study", "pr", slug, "--home", str(home)]) == 2
    assert "the starting point stayed the best: there is no change to open a PR for" in capsys.readouterr().err


def test_pr_without_yes_only_prints(tmp_path, capsys, monkeypatch):
    home, slug, repo = finished_code_run(tmp_path)
    monkeypatch.setattr(pr, "push_branch", lambda *a, **k: pytest.fail("pushed without --yes"))
    monkeypatch.setattr(pr, "create_pr", lambda *a, **k: pytest.fail("opened a PR without --yes"))
    assert main(["study", "pr", slug, "--home", str(home)]) == 0
    out = capsys.readouterr().out
    assert f"branch: evolvekit/{slug}-r1" in out and "+1 −1 lines in app dir/solver.py" in out
    assert "nothing was done: run it again with --yes to do this" in out
    assert out.splitlines()[0].startswith("the verdict: "), "decision 29: the verdict's line first"
    assert "push: no remote, the branch stays here" in out
    assert "evolvekit/" not in git(repo, "branch", "--list"), "no branch either"
    body = (home / "studies" / slug / "runs" / "r1" / "pr.md").read_text(encoding="utf-8")
    assert body.startswith("# ")
    # Review ruling: a PR is read on another computer -- no local path (it carries the account's name).
    root = home / "studies" / slug
    assert str(home) not in body and str(root.resolve()) not in body and str(tmp_path) not in body
    assert "\n## Files\n" in body
    assert git(repo, "status", "--porcelain") == ""


def test_pr_creates_branch_from_commit(tmp_path, capsys, monkeypatch):
    home, slug, repo = finished_code_run(tmp_path)
    best_commit = list(read_jsonl(home / "studies" / slug / "runs" / "r1" / "runs.jsonl"))[1]["commit"]
    git(repo, "remote", "add", "origin", "https://github.com/example/solver.git")
    pushed, opened = [], []
    real_which = shutil.which
    monkeypatch.setattr(pr, "push_branch", lambda top, rem, br: pushed.append((top, rem, br)))
    monkeypatch.setattr(pr, "create_pr", lambda top, **k: opened.append(k) or "https://github.com/example/solver/pull/7")
    monkeypatch.setattr(pr.shutil, "which", lambda name: "gh" if name == "gh" else real_which(name))
    assert main(["study", "pr", slug, "--draft", "--yes", "--home", str(home)]) == 0
    branch = f"evolvekit/{slug}-r1"
    assert git(repo, "rev-parse", f"{branch}^") == head(repo), "one commit on the starting point"
    assert git(repo, "rev-parse", f"{branch}^{{tree}}") == git(repo, "rev-parse", f"{best_commit}^{{tree}}")
    assert git(repo, "log", "-1", "--format=%an", branch) == "Test User", "the engineer's own identity, not evolvekit's"
    assert pushed == [(repo.resolve(), "origin", branch)]
    assert opened[0]["branch"] == branch and opened[0]["draft"] is True and opened[0]["body_path"].name == "pr.md"
    assert opened[0]["base"] is None and opened[0]["title"].startswith("Solver: ")
    assert "https://github.com/example/solver/pull/7" in capsys.readouterr().out
    assert git(repo, "status", "--porcelain") == "" and git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert "trees" not in git(repo, "worktree", "list"), "the PR's tree is gone"
    assert main(["study", "pr", slug, "--yes", "--home", str(home)]) == 1, "the branch exists already"
    assert f"the branch {branch} exists already" in capsys.readouterr().err
    # Review item 4: with --json, a failure is a document on stdout too.
    assert main(["study", "pr", slug, "--yes", "--json", "--home", str(home)]) == 1
    said = capsys.readouterr()
    answer = json.loads(said.out)
    assert answer["ok"] is False and answer["state"] == "none" and answer["branch"] == branch
    assert "exists already" in answer["error"] and "exists already" in said.err


def test_pr_honours_the_engineers_hooks(tmp_path, capsys):
    """Decision D15, review item 2: the PR's commit runs the repository's own hooks; a hook that
    refuses it leaves no branch and no tree, and says so."""
    home, slug, repo = finished_code_run(tmp_path)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'no commits today' >&2\nexit 1\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    git(repo, "config", "core.hooksPath", str(hooks))  # wherever the machine's own hooks are
    assert main(["study", "pr", slug, "--yes", "--home", str(home)]) == 1
    err = capsys.readouterr().err
    assert "was not made: the commit was refused by the repository's hook" in err and "no commits today" in err
    assert "evolvekit/" not in git(repo, "branch", "--list"), "no branch is left"
    assert "trees" not in git(repo, "worktree", "list") and git(repo, "status", "--porcelain") == ""
    assert main(["study", "pr", slug, "--yes", "--json", "--home", str(home)]) == 1
    answer = json.loads(capsys.readouterr().out)
    assert answer["ok"] is False and answer["state"] == "none" and answer["commit"] is None


def test_pr_without_a_github_remote_says_where_the_branch_is(tmp_path, capsys, monkeypatch):
    home, slug, repo = finished_code_run(tmp_path)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    monkeypatch.setattr(pr, "create_pr", lambda *a, **k: pytest.fail("gh on a remote that is not on GitHub"))
    assert main(["study", "pr", slug, "--yes", "--home", str(home)]) == 0
    branch = f"evolvekit/{slug}-r1"
    assert git(remote, "rev-parse", branch) == git(repo, "rev-parse", branch), "pushed to the remote"
    out = capsys.readouterr().out
    body = home / "studies" / slug / "runs" / "r1" / "pr.md"
    assert "the remote is not on GitHub: open the PR there from" in out and str(body) in out


def test_pr_without_a_remote_makes_the_branch_only(tmp_path, capsys, monkeypatch):
    home, slug, repo = finished_code_run(tmp_path)
    monkeypatch.setattr(pr, "push_branch", lambda *a, **k: pytest.fail("pushed with no remote"))
    monkeypatch.setattr(pr, "create_pr", lambda *a, **k: pytest.fail("a PR with no remote"))
    assert main(["study", "pr", slug, "--yes", "--json", "--home", str(home)]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["done"] is True and answer["pushed"] is False and answer["url"] is None
    assert answer["branch"] == f"evolvekit/{slug}-r1" and git(repo, "rev-parse", answer["branch"]) == answer["commit"]


def test_pr_after_working_is_made_on_head(tmp_path, capsys, monkeypatch):
    """Decision 35: after `from: working`, the branch is HEAD with the change; the uncommitted files are not in it."""
    working = SOLVER + "# uncommitted\n"
    home, slug, repo = finished_code_run(tmp_path, working=working)
    assert main(["study", "pr", slug, "--yes", "--home", str(home)]) == 0
    branch = f"evolvekit/{slug}-r1"
    assert git(repo, "rev-parse", f"{branch}^") == head(repo)
    shown = git(repo, "show", f"{branch}:app dir/solver.py")
    assert "VALUE = 3" in shown and "# uncommitted" not in shown
    assert (repo / "app dir" / "solver.py").read_text(encoding="utf-8") == working, "the engineer's files are as they were"
    capsys.readouterr()


def test_pr_after_working_refuses_a_change_that_does_not_apply_on_head(tmp_path, capsys):
    home, slug, repo = finished_code_run(tmp_path, working=SOLVER.replace("VALUE = 5", "VALUE = 4"))
    assert main(["study", "pr", slug, "--home", str(home)]) == 2
    err = capsys.readouterr().err
    patch = home / "studies" / slug / "runs" / "r1" / "best.patch"
    assert "does not apply on HEAD" in err and str(patch) in err and patch.is_file()
    assert "evolvekit/" not in git(repo, "branch", "--list")


def test_pr_after_a_bakeoff_names_the_branch(tmp_path, capsys):
    """Task 8's review: a bake-off with no `use` still has `final.candidate` (the fallback); the PR names its
    ref and the verdict's line says what it is -- never "recommended"."""
    home, slug, _ = finished_code_run(tmp_path)
    run_dir = home / "studies" / slug / "runs" / "r1"
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job["bakeoff"] = [{"ref": "restart", "id": "c1", "commit": list(read_jsonl(run_dir / "runs.jsonl"))[1]["commit"]}]
    job["search"] = {"stop_reason": "branches compared: no search", "generations": 0, "best_id": "c1", "aborted": False}
    job["final"].update({"decisions": {"c1": "keep"}, "decision": "keep", "confirmed": False})
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    assert main(["study", "pr", slug, "--home", str(home)]) == 0
    out = capsys.readouterr().out
    assert "the verdict: restart is not shown to beat the starting point." in out
    assert "with the branch restart" in out and "recommend" not in out.lower() and "better" not in out.lower()
    body = (run_dir / "pr.md").read_text(encoding="utf-8")
    header = body.split("## Verdict")[0]
    assert "`restart`" in header and "recommend" not in header.lower() and "better" not in header.lower()


def test_pr_refuses_settings_study(tmp_path, capsys):
    root, home = study_with_runs(tmp_path)
    finished_run(root / "runs" / RUN)
    assert main(["study", "pr", root.name, "--home", str(home.root)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("this study changed settings, not code; the settings file is at ")
    assert main(["study", "export", root.name, "--diff", "--home", str(home.root)]) == 2
    assert capsys.readouterr().err.startswith("this study changed settings, not code; the settings file is at ")
