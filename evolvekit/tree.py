"""A code study's candidate is a change to a source tree (design §5.3).

A candidate's `block` is a unified diff against a base commit. `materialise`
checks the base out as a detached git worktree at `<run>/work/trees/<id>`,
applies the diff and commits it, so every candidate has a commit id; `remove`
deletes a tree again. The diff helpers normalise a diff for dedup, list the
files it changes, refuse files outside `open`, and count its lines.

The engineer's checkout is never touched: worktrees, temporary index files and
`refs/evolvekit/*` only. Every git call goes through `_git`, which pins the
settings that would otherwise differ between machines (a Windows checkout's
`core.autocrlf=true` would refuse LF diffs), so a tree holds its commit's files
byte for byte. Only a candidate's change is added with the repository's own
line-ending rules (`staged_diff`), so its block is what the engineer's git would
commit. Nothing here is imported unless a study varies code, so settings
studies never need git.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "AUTHOR", "FIXED_DATE", "GIT_FLAGS", "GIT_TIMEOUT_S", "REMOVE_TRIES", "REMOVE_WAIT_S", "Repo", "Tree", "TreeError", "applies",
    "apply_in",
    "changed_files", "diff_between", "diff_stat", "git", "git_version", "glob_pattern", "keep_endings", "materialise",
    "normalise_diff", "relative_diff", "remove", "remove_all", "resolve_base", "resolve_commit", "snapshot_working", "staged_diff", "staged_diff_without_strays",
    "touches_only",
    "tree_dir",
]

GIT_FLAGS = ("-c", "core.autocrlf=false", "-c", "core.quotepath=false", "-c", "core.longpaths=true")
AUTHOR = ("evolvekit", "evolvekit@localhost")
FIXED_DATE = "2000-01-01T00:00:00+0000"
REMOVE_TRIES = 3
REMOVE_WAIT_S = 1.0
GIT_TIMEOUT_S = 300.0

_MISSING = "git is not installed: code studies need git on PATH (git-scm.com)"
# Inherited from a git hook (or a shell that set them), these would point every
# call at another repository than the folder the call runs in.
_LOCATING = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY",
             "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX")
# evolvekit's own commits and checkouts: a fixed identity, no signing, none of the
# repository's hooks (the folder named does not exist, so no hook is found), and
# no background gc or maintenance started in the engineer's repository.
_INTERNAL = ("-c", f"user.name={AUTHOR[0]}", "-c", f"user.email={AUTHOR[1]}", "-c", "commit.gpgsign=false",
             "-c", "core.hooksPath=.evolvekit-no-hooks", "-c", "gc.auto=0", "-c", "maintenance.auto=false")
# Messages in English whatever the machine's language; never a prompt for credentials.
_QUIET = {"LC_ALL": "C", "LANGUAGE": "C", "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
# A diff `git apply` takes back whatever the engineer's diff settings say.
_DIFF = ("--binary", "--full-index", "--no-color", "--no-ext-diff", "--no-textconv", "--no-relative",
         "--submodule=short", "--unified=3", "--src-prefix=a/", "--dst-prefix=b/")


class TreeError(RuntimeError):
    """A tree operation failed; the message is one sentence for people."""


def _plain(line: str) -> str:
    for lead in ("fatal: ", "error: "):
        if line.startswith(lead):
            return line[len(lead):]
    return line


def _git(cwd: Path | None, *args: str, identity: bool = False, env: Mapping[str, str] | None = None,
         checkout_eol: bool = False, timeout: float | None = GIT_TIMEOUT_S) -> str:
    """Run git and return its stdout as it is: no newline translation, and bytes that
    are not UTF-8 kept as surrogates (`surrogateescape`), so a diff round-trips.

    `identity`: one of evolvekit's own commits or checkouts (fixed author and date,
    no hooks, no signing). `checkout_eol`: the call reads the engineer's own
    checkout, so it keeps the repository's line-ending settings instead of
    `core.autocrlf=false` (a CRLF checkout is not a change to every file).
    """
    if cwd is not None and not Path(cwd).is_dir():
        raise TreeError(f"{cwd}: no such folder")
    flags = GIT_FLAGS[2:] if checkout_eol else GIT_FLAGS
    full = {k: v for k, v in os.environ.items() if k not in _LOCATING}
    full.update(_QUIET)
    if identity:
        flags += _INTERNAL
        for role in ("AUTHOR", "COMMITTER"):
            full[f"GIT_{role}_NAME"], full[f"GIT_{role}_EMAIL"] = AUTHOR
            full[f"GIT_{role}_DATE"] = FIXED_DATE
    if env:
        full.update(env)
    try:
        done = subprocess.run(["git", *flags, *args], cwd=cwd, capture_output=True, env=full, check=False,
                              stdin=subprocess.DEVNULL, timeout=timeout)
    except FileNotFoundError:
        raise TreeError(_MISSING) from None
    except subprocess.TimeoutExpired:
        words = [a for a in args if not a.startswith("-")]
        command = " ".join(words[:2] if words[:1] == ["worktree"] else words[:1])
        raise TreeError(f"git {command} did not finish in {timeout:g} s") from None
    if done.returncode:
        lines = done.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise TreeError(_plain(lines[-1].strip()) if lines else f"git {args[0]} stopped with exit code {done.returncode}")
    return done.stdout.decode("utf-8", errors="surrogateescape")


git = _git
"""The one way to call git (`_git`), for evolvekit's other modules: `harness check` lists a source's files."""


class Repo:
    """The folder a harness's `code.repo` names, inside a git repository."""

    def __init__(self, path: str | Path):
        given = Path(path)
        if not given.is_dir():
            raise TreeError(f"{given}: no such folder")
        self.path = given.resolve()
        try:
            out = _git(self.path, "rev-parse", "--show-toplevel", "--show-prefix")
        except TreeError as exc:
            if str(exc) == _MISSING:
                raise
            reason = "" if "not a git repository" in str(exc) else f" ({exc})"
            raise TreeError(f"{self.path} is not inside a git repository{reason}") from None
        lines = out.split("\n")
        self.toplevel = Path(lines[0].strip()).resolve()
        self.prefix = lines[1].rstrip("\r") if len(lines) > 1 else ""

    def __repr__(self) -> str:
        return f"Repo({str(self.path)!r})"


@dataclass(frozen=True)
class Tree:
    """A candidate's worktree; `path` is `{tree}`, the repo's folder inside it."""

    worktree: Path
    path: Path
    commit: str


def tree_dir(run_dir: Path, candidate_id: str) -> Path:
    return Path(run_dir) / "work" / "trees" / candidate_id


def git_version() -> str | None:
    try:
        out = _git(None, "--version").strip()
    except TreeError:
        return None
    return out.removeprefix("git version ").strip() or None


def resolve_commit(repo: Repo, ref: str) -> str:
    try:
        out = _git(repo.toplevel, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}").strip()
    except TreeError as exc:
        if str(exc) == _MISSING:
            raise
        out = ""
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", out):
        raise TreeError(f"{ref}: no such branch, tag or commit in {repo.toplevel}")
    return out


def resolve_base(repo: Repo, ref: str) -> str:
    return snapshot_working(repo) if ref == "working" else resolve_commit(repo, ref)


def snapshot_working(repo: Repo) -> str:
    """The working tree as it is, as a commit on HEAD (HEAD itself when nothing changed).

    The commit is made from a temporary index (`git add -A`: untracked files that
    are not ignored go into it too) and kept alive for later re-checks by the ref
    `refs/evolvekit/base-<commit>`; the engineer's index, working tree and HEAD
    are not written.
    """
    top = repo.toplevel
    head = resolve_commit(repo, "HEAD")
    with tempfile.TemporaryDirectory(prefix="evolvekit-index-") as scratch:
        env = {"GIT_INDEX_FILE": str(Path(scratch) / "index")}
        _git(top, "read-tree", head, env=env)
        _git(top, "add", "-A", "--", ".", env=env, checkout_eol=True)
        snapshot = _git(top, "write-tree", env=env).strip()
    if snapshot == _git(top, "rev-parse", f"{head}^{{tree}}").strip():
        return head
    commit = _git(top, "commit-tree", snapshot, "-p", head, "-m", "evolvekit: the working tree as it was",
                  identity=True).strip()
    # Named by the commit: the same files on the same HEAD make the same commit
    # (fixed author and date), so an identical snapshot reuses its ref.
    _git(top, "update-ref", f"refs/evolvekit/base-{commit}", commit)
    return commit


def materialise(run_dir: Path, *, repo: Repo, base: str, diff: str, candidate_id: str) -> Tree:
    """Check `base` out at `tree_dir(run_dir, candidate_id)`, apply `diff` and commit it.

    The same change on the same base is the same commit (fixed author and date).
    A diff that does not apply raises `TreeError` and leaves no tree behind.

    Every `diff` that reaches here is git's own diff from the base, as
    `staged_diff` and `diff_between` write it (an operator's or the agent's
    change, a branch in a bake-off, the seed's empty diff): `diff --git`
    headers, `a/`/`b/` prefixes, toplevel paths, exact counts and `-U3`
    context. So `--recount` without `--unidiff-zero` is correct here, and the
    repairs `apply_in` makes for a model's reply (`_prepare`) are not needed.
    """
    worktree, note = tree_dir(run_dir, candidate_id), _note(run_dir, candidate_id)
    if (worktree.exists() or note.exists()) and not remove(run_dir, candidate_id):  # a crashed earlier attempt
        raise TreeError(f"{worktree} is still in use by another program; close it and try again")
    worktree.parent.mkdir(parents=True, exist_ok=True)
    # --force: the folder is gone, but a tree deleted by hand may still be registered.
    _git(repo.toplevel, "worktree", "add", "--detach", "--force", "--quiet", os.path.abspath(worktree), base,
         identity=True)
    entry = _entry_of(worktree)
    if entry is not None:  # so `remove` can delete this entry, and only this one
        note.write_text(str(entry), encoding="utf-8")
    path = worktree / repo.prefix if repo.prefix else worktree
    if diff.strip():
        patch = worktree.parent / f"{candidate_id}.patch"
        patch.write_text(diff if diff.endswith("\n") else diff + "\n", encoding="utf-8",
                         errors="surrogateescape", newline="\n")
        try:
            _git(worktree, "apply", "--index", "--whitespace=nowarn", "--recount", os.path.abspath(patch))
            _git(worktree, "commit", "--quiet", "--no-verify", "-m", f"evolvekit candidate {candidate_id}",
                 identity=True)
        except TreeError as exc:
            remove(run_dir, candidate_id)
            raise TreeError(f"the change does not apply to {base[:12]}: {exc}") from None
        finally:
            patch.unlink(missing_ok=True)
    return Tree(worktree, path, _git(worktree, "rev-parse", "HEAD").strip())


def _long(path: Path) -> str:
    """Windows: the extended-length form, so a deep tree git made can be deleted."""
    full = os.path.abspath(path)
    if sys.platform != "win32" or full.startswith("\\\\?\\"):
        return full
    return "\\\\?\\UNC\\" + full[2:] if full.startswith("\\\\") else "\\\\?\\" + full


def _rmtree(path: str) -> None:
    def writable(func, target, _info):  # a read-only file: clear the bit, try once more
        os.chmod(target, stat.S_IWRITE)
        func(target)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=writable)
    else:
        shutil.rmtree(path, onerror=writable)


def _note(run_dir: Path, candidate_id: str) -> Path:
    """Beside a tree: the path of its entry in the repository (`<common>/worktrees/<name>`)."""
    return tree_dir(run_dir, candidate_id).parent / f"{candidate_id}.gitdir"


def _entry_of(worktree: Path) -> Path | None:
    """A worktree's entry in its repository, from its `.git` file."""
    try:
        line = (worktree / ".git").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    entry = Path(line[len("gitdir:"):].strip())
    return Path(os.path.abspath(entry if entry.is_absolute() else worktree / entry))


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _drop_entry(entry: Path, worktree: Path) -> None:
    """Delete evolvekit's own entry, and only when it still names this tree.

    This is what `git worktree prune` does for one entry; prune itself would also
    drop the engineer's entries whose folders are missing (an unplugged drive).
    """
    try:
        named = Path((entry / "gitdir").read_text(encoding="utf-8").strip())
    except OSError:
        return  # gone already, or not a worktree entry: nothing of ours to delete
    if _same(named if named.is_absolute() else entry / named, worktree / ".git"):
        _rmtree(_long(entry))


def remove(run_dir: Path, candidate_id: str) -> bool:
    """Delete a candidate's tree and its entry in the repository.

    False when the tree is still held open: the folder is kept, and a later call
    finishes the job. The repository's other worktree entries are never touched.
    """
    worktree, note = tree_dir(run_dir, candidate_id), _note(run_dir, candidate_id)
    if not worktree.exists() and not note.exists():
        return True
    try:
        entry: Path | None = Path(note.read_text(encoding="utf-8").strip())
    except OSError:
        entry = _entry_of(worktree)
    attempt = 0
    while worktree.exists():
        try:
            _rmtree(_long(worktree))
        except OSError:
            pass
        attempt += 1
        if worktree.exists():
            if attempt >= REMOVE_TRIES:
                return False
            time.sleep(REMOVE_WAIT_S)
    if entry is not None:
        try:
            _drop_entry(entry, worktree)
        except OSError:
            return True  # the tree is gone; the note stays, so the next call deletes the entry
    note.unlink(missing_ok=True)
    return True


def remove_all(run_dir: Path, *, keep: Collection[str] = ()) -> list[str]:
    """Remove every tree of a run except `keep`; the ids removed, sorted."""
    root = tree_dir(run_dir, "-").parent
    if not root.is_dir():
        return []
    ids = {entry.name for entry in root.iterdir() if entry.is_dir()}
    ids |= {entry.name.removesuffix(".gitdir") for entry in root.glob("*.gitdir") if entry.is_file()}
    return [name for name in sorted(ids) if name not in keep and remove(run_dir, name)]


def diff_between(repo: Repo, base: str, ref: str) -> str:
    """`ref`'s own change, from its merge-base with `base` (`git diff base...ref`)."""
    old, new = resolve_commit(repo, base), resolve_commit(repo, ref)
    if old == new:
        return ""
    return _git(repo.toplevel, "diff", *_DIFF, f"{old}...{new}")


def applies(repo: Repo, base: str, diff: str, *, directory: str = "") -> bool:
    """Whether `diff` applies on `base` exactly as `apply_in` would apply it there (the
    same recount, path handling and CRLF retry, `_prepare` and `_attempts`): checked
    against a temporary index, so no tree is made and the engineer's index is not written."""
    if not diff.strip():
        return True
    commit = resolve_commit(repo, base)
    top = repo.toplevel

    def exists(rel: str) -> bool:
        try:
            _git(top, "cat-file", "-e", f"{commit}:{rel}")
        except TreeError as exc:
            if str(exc) == _MISSING:
                raise
            return False
        return True

    def is_crlf(rel: str) -> bool:
        if not exists(rel):
            return False
        data = _git(top, "cat-file", "blob", f"{commit}:{rel}")
        crlf = data.count("\r\n")
        return crlf > 0 and crlf * 2 > data.count("\n")

    prepared, extra, root = _prepare(diff, directory, exists)
    with tempfile.TemporaryDirectory(prefix="evolvekit-apply-") as scratch:
        env = {"GIT_INDEX_FILE": str(Path(scratch) / "index")}
        _git(top, "read-tree", commit, env=env)

        def attempt(text: str) -> None:
            patch = Path(scratch) / "change.patch"
            patch.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8",
                             errors="surrogateescape", newline="\n")
            _git(top, "apply", "--cached", "--check", *extra, str(patch), env=env)

        try:
            _attempts(prepared, root, attempt, is_crlf)
        except TreeError as exc:
            if str(exc) == _MISSING:
                raise
            return False
    return True


def _sides_prefixed(diff: str) -> bool:
    """Whether the diff's file headers carry git's `a/` and `b/` (else `-p0`)."""
    names = [_side_raw(text[4:]) for kind, text in _lines(diff)
             if kind == "h" and text.startswith(("--- ", "+++ "))]
    names = [name for name in names if name != "/dev/null"]
    return not names or any(name[:2] in ("a/", "b/") for name in names)


def _side_raw(path: str) -> str:
    if path.startswith('"'):
        return _unquote(_quoted_prefix(path)[0])
    return path.split("\t")[0].strip()


def _mostly_crlf(path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    crlf = data.count(b"\r\n")
    return crlf > 0 and crlf * 2 > data.count(b"\n")


def _with_crlf(diff: str, is_crlf: Callable[[str], bool]) -> str | None:
    """The diff with `\\r` added to the hunk lines (context, removed, added) of
    every file `is_crlf` names; `None` when it names none of the diff's files."""
    norm = diff.replace("\r\n", "\n").replace("\r", "")
    out: list[str] = []
    crlf, minus, changed = False, "", False
    for line, (kind, text) in zip(norm.split("\n"), _lines(norm)):
        if kind == "h":
            if text.startswith("diff --git "):
                crlf = False
            elif text.startswith("--- "):
                minus = _side(text[4:])
            elif text.startswith("+++ "):
                plus = _side(text[4:])
                crlf = is_crlf(minus if plus == "/dev/null" else plus)
        elif kind in ("+", "-", " ") and crlf:
            out.append((line or " ") + "\r")
            changed = True
            continue
        out.append(line)
    return "\n".join(out) if changed else None


def _starts_section(lines: list[str], index: int) -> bool:
    """A hunk header, a `diff --git` line, or a `--- ` line followed by `+++ `."""
    line = lines[index]
    return bool(_HUNK.match(line)) or line.startswith("diff --git ") or (
        line.startswith("--- ") and index + 1 < len(lines) and lines[index + 1].startswith("+++ "))


def _recount(diff: str) -> str:
    """The diff with every hunk header's line counts made true, as a model's reply
    needs. A hunk ends at the next hunk or file header (`diff --git`, or a `--- `
    line followed by a `+++ ` line) or at a line no hunk holds; an empty line
    inside it is an empty context line whose space was lost, and empty lines at
    its end are dropped."""
    lines = diff.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        hunk = _HUNK.match(lines[i])
        if hunk is None:
            out.append(lines[i])
            i += 1
            continue
        body: list[str] = []
        j = i + 1
        while j < len(lines):
            line = lines[j]
            if _starts_section(lines, j):
                break
            if line and line[0] not in " +-\\":
                # The hunk's end, or a context line whose space was lost: when hunk
                # lines follow before the next header, the diff is refused, so the
                # changes after it are not dropped as trailing text.
                k = j + 1
                while k < len(lines) and not _starts_section(lines, k):
                    if lines[k][:1] in (" ", "+", "-"):
                        raise TreeError(f"the diff does not apply: a hunk line has no ' ', '+' or '-' mark: {line[:80]!r}")
                    k += 1
                break
            body.append(line)
            j += 1
        while body and body[-1] == "":
            body.pop()
        body = [line or " " for line in body]
        old = sum(line[0] in " -" for line in body)
        new = sum(line[0] in " +" for line in body)
        out.append(f"@@ -{hunk.group(1)},{old} +{hunk.group(3)},{new} @@{lines[i][hunk.end():]}")
        out.extend(body)
        i = j
    return "\n".join(out)


def apply_in(made: Tree, diff: str, *, directory: str = "") -> None:
    """Apply a diff (an operator's reply) to a tree's worktree and its index.

    Paths are relative to `directory` (the repo's folder, `Repo.prefix`), or
    already from the toplevel (every path starts with `directory`), with or
    without git's `a/`/`b/`. Hunk counts are recounted, and a hunk without
    context lines is accepted (`--unidiff-zero`): models write both. Such a hunk
    is anchored by its line number only, so it lands wherever its removed lines
    match there. `git apply` without `--reject` is atomic: a diff that fails
    leaves the tree as it was. A file committed with CRLF endings is shown
    without its CRs, so when the diff fails as it is, it is tried once more with
    `\\r` on the hunk lines of every file that is mostly CRLF (never with
    `--ignore-whitespace`). Raises `TreeError("the diff does not apply: …")`.
    """
    prepared, extra, root = _prepare(diff, directory, lambda rel: (made.worktree / rel).exists())

    def attempt(text: str) -> None:
        with tempfile.TemporaryDirectory(prefix="evolvekit-reply-") as scratch:
            patch = Path(scratch) / "reply.patch"
            patch.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8",
                             errors="surrogateescape", newline="\n")
            _git(made.worktree, "apply", "--index", *extra, str(patch))

    _attempts(prepared, root, attempt, lambda rel: _mostly_crlf(made.worktree / rel))


def _prepare(diff: str, directory: str, exists: Callable[[str], bool]) -> tuple[str, list[str], str]:
    """What `apply_in` and `applies` both apply: the diff recounted, the `git apply`
    options after the mode (`--index` or `--cached --check`), and the folder its paths
    are relative to ("" for the toplevel). `exists(path)`: a toplevel path is in the base."""
    # Counted here, not by `--recount`: git's recount takes the next file's
    # `--- a/x` for a removed line when a diff has no `diff --git` headers.
    diff = _recount(diff)
    args = ["--whitespace=nowarn", "--unidiff-zero"]
    names = changed_files(diff)
    root = directory.rstrip("/")
    # Toplevel paths only when they say so: every path starts with the folder, the
    # tree has some of them as written, and none of them under the folder (so
    # `src/x.py` inside a folder `src` that holds its own `src/` stays relative).
    toplevel = bool(root and names and all(name.startswith(root + "/") for name in names)
                    and any(exists(name) for name in names)
                    and not any(exists(f"{root}/{name}") for name in names))
    if root and not toplevel:
        args.append(f"--directory={root}")
    else:
        root = ""
    if not _sides_prefixed(diff):
        args.append("-p0")
    return diff, args, root


def _attempts(diff: str, root: str, attempt: Callable[[str], None], is_crlf: Callable[[str], bool]) -> None:
    """`attempt(diff)`, then once more with `\\r` on the hunk lines of every file that
    `is_crlf` (a toplevel path) says is mostly CRLF; `TreeError("the diff does not apply: …")`."""
    try:
        attempt(diff)
        return
    except TreeError as exc:
        if str(exc) == _MISSING:
            raise
        first = exc
    crlf = _with_crlf(diff, lambda name: is_crlf(f"{root}/{name}" if root else name))
    if crlf is not None:
        try:
            attempt(crlf)
            return
        except TreeError:
            pass
    raise TreeError(f"the diff does not apply: {first}") from None


def relative_diff(diff: str, prefix: str) -> str:
    """A candidate's diff (toplevel paths) with `prefix` (`Repo.prefix`) taken off
    its file paths, as the open files are listed in a prompt."""
    if not prefix:
        return diff
    out: list[str] = []
    for kind, text in _lines(diff):
        if kind in ("+", "-", " "):
            out.append(kind + text)
            continue
        if kind == "h":
            if text.startswith(("diff --git ", "--- ", "+++ ")):
                for side in ("a/", "b/"):
                    text = text.replace(f" {side}{prefix}", f" {side}").replace(f' "{side}{prefix}', f' "{side}')
            else:
                for lead in ("rename from ", "rename to ", "copy from ", "copy to "):
                    if text.startswith(lead + prefix):
                        text = lead + text[len(lead + prefix):]
        out.append(text)
    return "\n".join(out)


def _ending(data: bytes) -> bytes | None:
    """`\\r\\n` when every line of `data` ends so, `\\n` when none does; `None` when
    they are mixed or there is no line end."""
    lf, crlf = data.count(b"\n"), data.count(b"\r\n")
    if not lf:
        return None
    return b"\r\n" if crlf == lf else b"\n" if not crlf else None


def _unset_text(made: Tree, names: list[str]) -> set[str]:
    """The paths among `names` whose `text` attribute is unset (`-text`, `binary`):
    git does not treat them as text, so their bytes are not ours to touch."""
    unset: set[str] = set()
    for start in range(0, len(names), UNSTAGE_BATCH):
        out = _git(made.worktree, "check-attr", "text", "-z", "--", *names[start:start + UNSTAGE_BATCH])
        fields = out.split("\0")
        unset.update(fields[i] for i in range(0, len(fields) - 2, 3) if fields[i + 2] == "unset")
    return unset


def keep_endings(made: Tree) -> list[str]:
    """Give every tracked file an operator rewrote the line endings the tree's
    commit holds it with: a file that was all LF is LF again, one that was all
    CRLF is CRLF again (a file with mixed endings, a new file and a binary file
    stay as written). A coding agent's editor may write a whole file with CRLF,
    and a marked block's rewrite comes back LF: either would otherwise record a
    rewrite of every line. The toplevel paths changed, sorted."""
    listed = _git(made.worktree, "diff", "--name-only", "-z", "--no-renames", "--diff-filter=M", "HEAD", "--")
    fixed: list[str] = []
    names = sorted(name for name in listed.split("\0") if name)
    unset = _unset_text(made, names)
    for rel in names:
        target = made.worktree / rel
        try:
            data = target.read_bytes()
            old = _git(made.worktree, "cat-file", "blob", f"HEAD:{rel}").encode("utf-8", errors="surrogateescape")
        except (OSError, TreeError):
            continue
        want = _ending(old)
        if want is None or (want == b"\n" and rel in unset) or b"\0" in data or b"\0" in old:
            continue
        lf = data.replace(b"\r\n", b"\n")
        kept = lf if want == b"\n" else lf.replace(b"\n", b"\r\n")
        if kept != data:
            target.write_bytes(kept)
            fixed.append(rel)
    return fixed


def _add_all(made: Tree) -> None:
    """`git add -A` with the repository's own line-ending rules (`core.autocrlf`,
    `core.eol`, `.gitattributes`), so the block is what the engineer's git would
    commit: under `core.autocrlf=true` a CRLF file is recorded LF. Never refused
    for a conversion it could not undo (`core.safecrlf`): nothing is checked out."""
    _git(made.worktree, "-c", "core.safecrlf=false", "add", "-A", "--", ".", checkout_eol=True)


def staged_diff(made: Tree, base: str) -> str:
    """Everything the tree's worktree now holds against `base`, as a candidate's block
    (cumulative, toplevel paths with `a/`/`b/`): the rewritten files' line endings
    kept (`keep_endings`), `git add -A` with the repository's own rules, then the
    index's diff."""
    keep_endings(made)
    _add_all(made)
    return _git(made.worktree, "diff", "--cached", *_DIFF, base)


UNSTAGE_BATCH = 100
"""Paths per `git rm --cached` call: a command line stays well under Windows' limit."""


def staged_diff_without_strays(made: Tree, base: str, open: Sequence[str], *, prefix: str) -> tuple[str, list[str]]:
    """`staged_diff`, with the files *added* outside `open` left out: `(block, the
    dropped paths, toplevel-relative)`. For an agent's tree, where a scratch file
    or a cache it leaves behind is not its change. A tracked file outside `open`
    that is modified, deleted or has its mode changed stays in the block (a rename
    is a deletion and an addition: `--no-renames`), so `touches_only` still refuses it."""
    keep_endings(made)
    _add_all(made)
    listed = _git(made.worktree, "diff", "--cached", "--name-status", "-z", "--no-renames", "--diff-filter=A",
                  base)
    parts = [part for part in listed.split("\0") if part]
    added = parts[1::2]  # -z: the status, then the path
    dropped = [name for name in added if _outside(name, [_glob(p) for p in open], prefix)]
    for start in range(0, len(dropped), UNSTAGE_BATCH):
        _git(made.worktree, "rm", "--cached", "-q", "--", *dropped[start:start + UNSTAGE_BATCH],
             env={"GIT_LITERAL_PATHSPECS": "1"})
    return _git(made.worktree, "diff", "--cached", *_DIFF, base), dropped


# --- reading diffs -------------------------------------------------------------

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DROP = ("diff --git ", "index ", "old mode ", "new mode ", "similarity index ", "dissimilarity index ", "\\ ")
_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


def _lines(diff: str) -> Iterator[tuple[str, str]]:
    """(kind, text) per line: `@` a hunk header, `+`/`-`/` ` a hunk's line without
    its mark, `h` anything else (file and extended headers, binary patches).

    The hunk header's counts say where a hunk ends, so a removed `-- x` line is
    not taken for a `--- ` file header; lines past a short count are still read
    as the hunk's, as `git apply --recount` does.
    """
    old_left = new_left = 0
    in_hunk = False
    for raw in diff.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        hunk = _HUNK.match(raw)
        if hunk:
            old_left = int(hunk.group(2) if hunk.group(2) is not None else 1)
            new_left = int(hunk.group(4) if hunk.group(4) is not None else 1)
            in_hunk = True
            yield "@", raw
            continue
        counted = old_left > 0 or new_left > 0
        mark = raw[:1]
        if counted and (mark in ("+", "-", " ") or not raw):
            old_left -= mark != "+"
            new_left -= mark != "-"
            yield (mark or " "), raw[1:]
            continue
        if in_hunk and mark in ("+", "-", " ") and not raw.startswith(("--- ", "+++ ")):
            yield mark, raw[1:]
            continue
        if mark != "\\":
            old_left = new_left = 0
            in_hunk = False
        yield "h", raw


def _unquote(text: str) -> str:
    """git's C-style quoted path (`"a/caf\\303\\251.py"`) as text."""
    if len(text) < 2 or not (text.startswith('"') and text.endswith('"')):
        return text
    out, body, i = bytearray(), text[1:-1], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if re.fullmatch(r"[0-7]{3}", body[i + 1:i + 4]):
                out.append(int(body[i + 1:i + 4], 8))
                i += 4
                continue
            out.append(_ESCAPES.get(nxt, ord(nxt)))
            i += 2
            continue
        out.extend(ch.encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")


def _quoted_prefix(text: str) -> tuple[str, str]:
    """A leading quoted name, and what follows it."""
    i = 1
    while i < len(text) and text[i] != '"':
        i += 2 if text[i] == "\\" else 1
    return text[:i + 1], text[i + 1:]


def _strip_side(name: str) -> str:
    return name[2:] if name[:2] in ("a/", "b/") else name


def _side(path: str) -> str:
    """`--- a/x` / `+++ b/x` -> `x` (a timestamp or git's trailing tab dropped); `/dev/null` stays."""
    if path.startswith('"'):
        return _strip_side(_unquote(_quoted_prefix(path)[0]))
    return _strip_side(path.split("\t")[0].strip())


def _header_paths(rest: str) -> list[str]:
    """The paths of `diff --git a/x b/y` (when they can be told apart)."""
    if rest.startswith('"'):
        first, tail = _quoted_prefix(rest)
        tail = tail.strip()
        return [_strip_side(_unquote(first)), _strip_side(_unquote(tail))] if tail else [_strip_side(_unquote(first))]
    if ' "b/' in rest:
        first, tail = rest.split(' "b/', 1)
        return [_strip_side(first), _unquote('"b/' + tail)[2:]]
    half = (len(rest) - 1) // 2
    if len(rest) % 2 and rest[half] == " " and rest[:2] == "a/" and rest[half + 1:half + 3] == "b/" \
            and rest[2:half] == rest[half + 3:]:
        return [rest[2:half]]
    return []  # a rename: its `rename from` / `rename to` lines name both sides


def changed_files(diff: str) -> list[str]:
    """Every path a diff changes, toplevel-relative and sorted (both sides of a rename)."""
    files: set[str] = set()
    for kind, text in _lines(diff):
        if kind != "h":
            continue
        if text.startswith("diff --git "):
            files.update(_header_paths(text[len("diff --git "):].rstrip()))
        elif text.startswith(("--- ", "+++ ")):
            files.add(_side(text[4:]))
        else:
            for lead in ("rename from ", "rename to ", "copy to "):
                if text.startswith(lead):
                    files.add(_unquote(text[len(lead):].rstrip()))
    files.discard("/dev/null")
    files.discard("")
    return sorted(files)


def _glob(pattern: str) -> re.Pattern[str]:
    """`**/` any folders (or none), `**` anything, `*` and `?` within one folder."""
    pattern = pattern.replace("\\", "/").removeprefix("./")
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


glob_pattern = _glob
"""`code.open`'s glob as a pattern for a path relative to the repo folder, as `touches_only` matches it."""


def touches_only(diff: str, open: Sequence[str], *, prefix: str) -> list[str]:
    """The changed files outside `open` (globs relative to `prefix`), toplevel-relative.

    A path with a `..` part is always outside, whatever it would match.
    """
    patterns = [_glob(p) for p in open]
    return [name for name in changed_files(diff) if _outside(name, patterns, prefix)]


def _outside(name: str, patterns: Sequence[re.Pattern[str]], prefix: str) -> bool:
    return (".." in name.replace("\\", "/").split("/")
            or not (name.startswith(prefix) and any(p.fullmatch(name[len(prefix):]) for p in patterns)))


def diff_stat(diff: str) -> dict[str, Any]:
    added = removed = 0
    for kind, _ in _lines(diff):
        added += kind == "+"
        removed += kind == "-"
    return {"added": added, "removed": removed, "files": changed_files(diff)}


def _squeeze(text: str) -> str:
    """Indentation kept, inner runs of whitespace collapsed, the end stripped."""
    lead = text[: len(text) - len(text.lstrip())]
    return lead + " ".join(text.split())


def _ignorable(text: str, path: str) -> bool:
    return not text.strip() or (path.endswith(".py") and text.lstrip().startswith("#"))


def normalise_diff(diff: str) -> str:
    """The diff as dedup compares it.

    Two diffs against the same base with the same changes at the same base lines
    normalise alike, whatever their context width, `index` and mode lines,
    timestamps, line endings, trailing or inner whitespace, blank lines, or (in
    `.py` files) full-line comments. Each group of changed lines is anchored at
    the base line it starts at (a new file's at 0).
    """
    out: list[str] = []
    group: list[str] = []
    anchor: int | None = None
    old, minus, path = 0, "", ""
    # A `diff --git` header's path, held until a `---`/`+++` pair names the file
    # instead; a binary patch or a pure rename has none, and is then named by it.
    pending: str | None = None
    held: list[str] = []

    def flush() -> None:
        nonlocal group, anchor
        if group:
            out.append(f"@ {anchor}")
            out.extend(group)
        group, anchor = [], None

    def release(named: bool) -> None:
        nonlocal pending, held
        if named and pending is not None:
            out.append(pending)
        out.extend(held)
        pending, held = None, []

    for kind, text in _lines(diff):
        if kind == "@":
            flush()
            release(True)
            old = int(_HUNK.match(text).group(1))
        elif kind in ("+", "-"):
            squeezed = _squeeze(text)
            if not _ignorable(squeezed, path):
                anchor = old if anchor is None else anchor
                group.append(kind + squeezed)
            old += kind == "-"
        elif kind == " ":
            flush()
            old += 1
        else:
            flush()
            line = text.rstrip()
            if line.startswith("diff --git "):
                release(True)
                paths = _header_paths(line[len("diff --git "):])
                pending = f"F {paths[-1]}" if paths else None
                path = paths[-1] if paths else path
                continue
            if not line or line.startswith(_DROP):
                continue
            if line.startswith("--- "):
                release(False)
                minus = _side(line[4:])
            elif line.startswith("+++ "):
                plus = _side(line[4:])
                path = minus if plus == "/dev/null" else plus
                out.append(f"F {path}")
            elif pending is not None:
                held.append(line)
            else:
                out.append(line)  # binary patches, new/deleted/rename lines: as they are
    flush()
    release(True)
    return "\n".join(out) + ("\n" if out else "")
