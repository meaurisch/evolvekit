"""The result of a code study is a change (design §5.6).

`export_diff` writes the best combination's diff as `runs/<id>/best.patch`.
`plan_pr` says what `study pr` would do -- the branch `evolvekit/<slug>-<run>`
from the run's starting point with the best's change, the push, the PR -- and
writes the PR's body, the run's report as Markdown, to `runs/<id>/pr.md`; it
touches no repository. `open_pr` does it: one fresh commit with the engineer's
own identity, hooks and signing (never evolvekit's candidate commit, decision
D15), the branch, the push when there is a remote, the PR with `gh` when the
remote is on GitHub and `gh` is installed. The command runs `open_pr` only
with `--yes`.

The verdict is the only claim: the title and the body's header name the change
and where it is from, and the report under them says what the final check
found -- a "keep today's" verdict included (decision 29).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from evolvekit.harness import HarnessError
from evolvekit.app.detach import no_window_flags

__all__ = ["NotCodeStudy", "PrFailed", "PrPlan", "create_pr", "describe", "export_diff", "open_pr", "plan_pr", "push_branch"]

PATCH = "best.patch"
BODY = "pr.md"
PR_TREE = "pr"
"""The candidate id of the tree the PR's commit is made in (`<run>/work/trees/pr`)."""
GH_TIMEOUT_S = 300.0


class NotCodeStudy(HarnessError):
    """`study pr` or `study export --diff` on a study that changed settings."""


@dataclass
class PrPlan:
    toplevel: Path
    branch: str
    base_commit: str
    best_id: str
    files: list[str]
    added: int
    removed: int
    remote: str | None
    remote_url: str | None
    gh: bool
    title: str
    body_path: Path
    base_branch: str | None
    draft: bool
    # What `open_pr` needs besides, and what `describe` says.
    run_dir: Path
    repo_path: Path
    block: str
    line: str
    ref: str | None = None
    """The branch a bake-off compared, when the best is one."""
    from_head: bool = False
    """The study started `from: working`: the branch is made on HEAD (decision 35)."""
    why_not: str = ""
    """Why no PR is opened (no remote, not on GitHub, no gh); empty when one is."""
    notes: list[str] = field(default_factory=list)
    """Said under the verdict's line, here and in the body: a final check on too few
    held-back cases for a firm answer (`plan.check_notes`)."""

    def to_json(self) -> dict[str, Any]:
        out = {k: str(v) if isinstance(v, Path) else v for k, v in asdict(self).items()}
        out.pop("block")
        return out


# --- the run's best -----------------------------------------------------------


class _Run:
    """What both commands read of a finished code run."""

    def __init__(self, root: Path, run: str | None):
        from evolvekit.app import AppError, jobs
        from evolvekit.harness.execute import run_study_of
        from evolvekit.ledger import read_jsonl

        self.root = Path(root)
        try:
            self.run = run or jobs.latest_run(self.root)
            if not self.run:
                raise HarnessError("the study has not been started yet")
            self.dir = jobs._run_dir(self.root, self.run)  # noqa: SLF001 - the one check of a run's name
        except AppError as exc:
            raise HarnessError(str(exc)) from None
        self.study = run_study_of(self.root, self.dir)
        if self.study.code is None:
            raise NotCodeStudy(f"this study changed settings, not code; the settings file is at {self._settings_file()}")
        self.job = jobs.phase(self.dir)
        if self.job.get("phase") in (*jobs.ACTIVE, "queued"):
            raise HarnessError(f"run {self.run} has not ended yet; wait for it (evolvekit study wait {self.root.name})")
        final, search = self.job.get("final") or {}, self.job.get("search") or {}
        self.best_id = str(final.get("candidate") or search.get("best_id") or "")
        rows = list(read_jsonl(self.dir / "runs.jsonl")) if (self.dir / "runs.jsonl").is_file() else []
        self.rows = {str(r.get("id")): r for r in rows}
        self.best = self.rows.get(self.best_id) if self.best_id else None
        self.block = str((self.best or {}).get("block") or "")
        self.stayed = self.best is None or self.best.get("operator") == "human-seed" or not self.block.strip()
        # A search that stopped before its first round kept nothing as best: it says so (persona p5, M3).
        self.not_run = str(final.get("skipped") or "") if final.get("not_run") else ""

    def nothing(self, what: str) -> str:
        """Why there is no change `what` ("to export", "to open a PR for"), in the run's own words."""
        if self.not_run:
            return f"{self.not_run}; there is no change {what}"
        return f"the starting point stayed the best: there is no change {what}"

    def _settings_file(self) -> str:
        from evolvekit.harness.execute import DOWNLOADS, export_path
        from evolvekit.harness.manifest import load_harness

        try:
            exports = load_harness(self.root / "harness").exports
        except HarnessError:
            exports = {}
        for name, export in exports.items():
            if export.applies_to == "settings" and export_path(self.dir, name).is_file():
                return str(export_path(self.dir, name))
        return str(self.dir / DOWNLOADS) + os.sep

    def tree_spec(self) -> dict[str, Any]:
        """The run's `problem.tree`: its source and its base pinned to a commit."""
        try:
            config = yaml.safe_load((self.dir / "evolvekit.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            config = {}
        spec = dict(((config.get("problem") or {}).get("tree")) or {})
        spec.setdefault("repo", self.study.code.repo)
        if not spec.get("base"):
            spec["base"] = str((self.rows.get("seed") or {}).get("commit") or "")
        if not spec["repo"] or not spec["base"]:
            raise HarnessError(f"run {self.run} does not say which source and base it changed (runs/{self.run}/evolvekit.yaml)")
        return spec

    def ref(self) -> str | None:
        """The bake-off's branch of the best, when the run is a bake-off."""
        return next((str(e.get("ref")) for e in self.job.get("bakeoff") or [] if e.get("id") == self.best_id), None)


def _write_patch(run: _Run) -> Path:
    patch = run.dir / PATCH
    text = run.block.replace("\r\n", "\n")
    patch.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", errors="surrogateescape",
                     newline="\n")
    return patch


def export_diff(root: Path, run: str | None) -> Path:
    """The best combination's change as `runs/<id>/best.patch` (LF, paths from the
    repository's top, as `git apply` takes it there)."""
    found = _Run(root, run)
    if found.stayed or found.not_run:
        raise HarnessError(found.nothing("to export"))
    return _write_patch(found)


# --- the plan -----------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    from evolvekit import tree

    try:
        return tree.git(cwd, *args)
    except tree.TreeError as exc:
        raise HarnessError(str(exc)) from None


def _remote(toplevel: Path) -> tuple[str | None, str | None]:
    names = _git(toplevel, "remote").split()
    remote = "origin" if "origin" in names else (names[0] if names else None)
    if remote is None:
        return None, None
    try:
        url = _git(toplevel, "remote", "get-url", remote).strip() or None
    except HarnessError:
        url = None
    return remote, url


def _body(run: _Run, document: dict[str, Any], ref: str | None, base: str, from_head: bool,
          notes: list[str]) -> str:
    """The report as `study report --markdown` writes it, with a header that says what the PR is,
    `notes` under the verdict, and no section that would only say there is nothing. It names
    files relative to the study folder only (`root=None`): a PR is read on another computer,
    and the folder's absolute path carries the account's name."""
    from evolvekit.app import jobs
    from evolvekit.app.report import render_report
    from evolvekit.status import build_status

    report = render_report(document, jobs.results(run.root, run.run, build_status), root=None, notes=notes,
                           drop_empty=True)
    where = f"HEAD ({base[:12]})" if from_head else base[:12]
    if ref is not None:
        header = (f"This PR holds the branch `{ref}` as the bake-off {run.run} of the evolvekit study "
                  f"`{run.root.name}` compared it (combination {run.best_id}), made on {where}. Its own line "
                  "under \"Branches compared\" says what the final check found.")
    else:
        header = (f"This PR holds the change of combination {run.best_id} of run {run.run} of the evolvekit study "
                  f"`{run.root.name}`, made on {where}. The verdict below is the final check's answer on held-back cases.")
    first, _, rest = report.partition("\n")
    return f"{first}\n\n{header}\n{rest}" if rest else f"{first}\n\n{header}\n"


def plan_pr(root: Path, run: str | None, *, base: str | None = None, draft: bool = False) -> PrPlan:
    """What `study pr` would do; writes `runs/<id>/pr.md` and touches nothing else."""
    from evolvekit import tree
    from evolvekit.app.verdict import build_verdict
    from evolvekit.harness.plan import check_notes

    found = _Run(root, run)
    if found.stayed or found.not_run:
        raise HarnessError(found.nothing("to open a PR for"))
    spec = found.tree_spec()
    try:
        repo = tree.Repo(spec["repo"])
        base_commit = str(spec["base"])
        from_head = found.study.code.from_ref == "working"
        if from_head:  # decision 35: HEAD with the change; the uncommitted files are not in it
            base_commit = tree.resolve_commit(repo, "HEAD")
            if not tree.applies(repo, base_commit, found.block):
                patch = _write_patch(found)
                raise HarnessError(f"the change does not apply on HEAD ({base_commit[:12]}): the study started from "
                                   f"uncommitted files that HEAD does not have; the change is at {patch}")
        else:
            base_commit = tree.resolve_commit(repo, base_commit)
    except tree.TreeError as exc:
        raise HarnessError(f"vary.code.repo: {exc}") from None
    document = build_verdict(found.root, found.run)
    line = str(document.get("line") or "")
    notes = check_notes(int((document.get("held_back") or {}).get("cases") or 0))
    ref = found.ref()
    remote, url = _remote(repo.toplevel)
    gh_found = shutil.which("gh") is not None
    on_github = "github.com" in (url or "")
    why_not = ("no remote" if remote is None else "the remote is not on GitHub" if not on_github
               else "gh is not installed" if not gh_found else "")
    name = found.study.name or found.root.name
    title = (f"{name}: the branch {ref} from evolvekit study {found.root.name}" if ref is not None
             else f"{name}: the change of evolvekit study {found.root.name}")
    body_path = found.dir / BODY
    body_path.write_text(_body(found, document, ref, base_commit, from_head, notes), encoding="utf-8",
                         newline="\n")
    stat = tree.diff_stat(found.block)
    return PrPlan(toplevel=repo.toplevel, branch=f"evolvekit/{found.root.name}-{found.run}", base_commit=base_commit,
                  best_id=found.best_id, files=list(stat["files"]), added=int(stat["added"]),
                  removed=int(stat["removed"]), remote=remote, remote_url=url, gh=not why_not, title=title,
                  body_path=body_path, base_branch=base, draft=draft, run_dir=found.dir, repo_path=repo.path,
                  block=found.block, line=line, ref=ref, from_head=from_head, why_not=why_not, notes=notes)


def describe(plan: PrPlan) -> list[str]:
    """What `study pr` will do, one line each; the verdict's line first (decision 29)."""
    where = f"HEAD ({plan.base_commit[:12]})" if plan.from_head else plan.base_commit[:12]
    what = f"the branch {plan.ref} (combination {plan.best_id})" if plan.ref is not None else f"combination {plan.best_id}"
    lines = [f"the verdict: {plan.line}", *(f"note: {note}" for note in plan.notes),
             f"branch: {plan.branch} in {plan.toplevel}",
             f"from: {where} with {what}: +{plan.added} −{plan.removed} lines in {', '.join(plan.files)}"]
    if plan.from_head:
        lines.append("note: the study started from uncommitted files; the branch holds HEAD and the change only")
    lines.append(f"push: to {plan.remote} ({plan.remote_url})" if plan.remote else "push: no remote, the branch stays here")
    if plan.gh:
        into = plan.base_branch or "the repository's default branch"
        lines.append(f"PR: with gh into {into}" + (", as a draft" if plan.draft else ""))
    else:
        lines.append(f"PR: not opened ({plan.why_not})")
    lines.append(f"body: {plan.body_path}")
    return lines


# --- doing it -----------------------------------------------------------------


def push_branch(toplevel: Path, remote: str, branch: str) -> None:
    """`git push -u <remote> <branch>`, with the engineer's own credentials and hooks."""
    _git(toplevel, "push", "-u", remote, branch)


def create_pr(toplevel: Path, *, base: str | None, branch: str, title: str, body_path: Path, draft: bool) -> str:
    """`gh pr create` in the repository; the PR's address (the last line gh prints)."""
    argv = ["gh", "pr", "create", "--head", branch, "--title", title, "--body-file", str(body_path)]
    if base:
        argv += ["--base", base]
    if draft:
        argv.append("--draft")
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"}
    try:
        done = subprocess.run(argv, cwd=toplevel, capture_output=True, encoding="utf-8", errors="replace", env=env,
                              stdin=subprocess.DEVNULL, check=False, timeout=GH_TIMEOUT_S,
                              creationflags=no_window_flags())
    except FileNotFoundError:
        raise HarnessError("gh is not installed") from None
    except subprocess.TimeoutExpired:
        raise HarnessError(f"gh pr create did not finish in {GH_TIMEOUT_S:g} s") from None
    if done.returncode:
        said = (done.stderr or done.stdout).strip().splitlines()
        raise HarnessError(said[-1].strip() if said else f"gh pr create stopped with exit code {done.returncode}")
    out = done.stdout.strip().splitlines()
    return out[-1].strip() if out else ""


def _branch_exists(toplevel: Path, branch: str) -> bool:
    try:
        _git(toplevel, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    except HarnessError:
        return False
    return True


class PrFailed(HarnessError):
    """`open_pr` stopped part way: `state` says how far it got -- "none" (nothing was
    made), "branch" (the branch is made here, not pushed) or "pushed" (no PR) -- and
    `branch`/`commit` what was made."""

    def __init__(self, message: str, *, state: str, branch: str, commit: str | None = None):
        super().__init__(message)
        self.state, self.branch, self.commit = state, branch, commit


_IDENT = ("Please tell me who you are", "unable to auto-detect email address", "empty ident name")


def _refused(exc: Exception) -> str:
    """Why `git commit` refused the PR's commit, in a sentence: the engineer's hooks,
    signing and identity apply to it (decision D15)."""
    said = str(exc)
    if any(mark in said for mark in _IDENT):
        return f"git does not know who you are: set user.name and user.email ({said})"
    if "gpg" in said.lower() or "sign" in said.lower():
        return f"the commit could not be signed: {said}"
    return f"the commit was refused by the repository's hook: {said}"


def _record(plan: PrPlan, done: dict[str, Any]) -> None:
    """What was made so far, in the run's folder (`jobs.PR_RECORD`): the Study page and `study status`
    say "Branch <name> pushed to <remote>" or "made, not pushed" from it."""
    from evolvekit.app.jobs import PR_RECORD

    record = {"branch": done["branch"], "commit": done["commit"], "remote": plan.remote if done["pushed"] else None,
              "pushed": bool(done["pushed"]), "url": done.get("url")}
    (plan.run_dir / PR_RECORD).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")


def open_pr(plan: PrPlan) -> dict[str, Any]:
    """Make the branch, push it, open the PR: `{"branch", "commit", "pushed", "url", "note"}`.
    Raises `PrFailed` with how far it got."""
    from evolvekit import tree

    top, branch = plan.toplevel, plan.branch
    if _branch_exists(top, branch):
        raise PrFailed(f"the branch {branch} exists already; delete it or start a new run", state="none", branch=branch)
    try:
        made = tree.materialise(plan.run_dir, repo=tree.Repo(plan.repo_path), base=plan.base_commit, diff="",
                                candidate_id=PR_TREE)
        tree.apply_in(made, plan.block)
        try:
            # The engineer's identity, hooks and signing (no `identity=True`, no --no-verify): decision D15.
            tree.git(made.worktree, "commit", "-q", "-m", f"{plan.title}\n\n{plan.line}".rstrip())
        except tree.TreeError as exc:
            raise PrFailed(f"the branch {branch} was not made: {_refused(exc)}", state="none", branch=branch) from None
        commit = tree.git(made.worktree, "rev-parse", "HEAD").strip()
        tree.git(top, "branch", branch, commit)
    except tree.TreeError as exc:
        raise PrFailed(f"the branch {branch} was not made: {exc}", state="none", branch=branch) from None
    finally:
        if not tree.remove(plan.run_dir, PR_TREE):  # held open by another program: its entry stays too
            print(f"evolvekit: {tree.tree_dir(plan.run_dir, PR_TREE)} could not be removed: remove it, then run "
                  "git worktree prune in your repository", file=sys.stderr)
    done: dict[str, Any] = {"branch": branch, "commit": commit, "pushed": False, "url": None, "note": ""}
    _record(plan, done)
    if plan.remote:
        try:
            push_branch(top, plan.remote, branch)
        except (HarnessError, tree.TreeError) as exc:
            raise PrFailed(f"the branch {branch} is made here ({commit[:12]}), but pushing it to "
                           f"{plan.remote} failed: {exc}", state="branch", branch=branch, commit=commit) from None
        done["pushed"] = True
        _record(plan, done)
    if plan.gh:
        try:
            done["url"] = create_pr(top, base=plan.base_branch, branch=branch, title=plan.title,
                                    body_path=plan.body_path, draft=plan.draft)
            _record(plan, done)
        except HarnessError as exc:
            raise PrFailed(f"the branch {branch} is pushed to {plan.remote}, but gh pr create failed: {exc}; "
                           f"the body is at {plan.body_path}", state="pushed", branch=branch, commit=commit) from None
    elif plan.why_not == "no remote":
        done["note"] = (f"no remote: the branch {plan.branch} stays in {top}; push it and open the PR from it; "
                        f"the body is at {plan.body_path}")
    elif plan.why_not == "the remote is not on GitHub":
        done["note"] = (f"the remote is not on GitHub: open the PR there from {plan.branch}; "
                        f"the body is at {plan.body_path}")
    else:
        done["note"] = (f"gh is not installed: open the PR on GitHub from {plan.branch}; "
                        f"the body is at {plan.body_path}")
    return done
