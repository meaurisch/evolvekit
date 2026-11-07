"""Where harnesses and studies live, and how they are made.

    ~/evolvekit/                   the library home: EVOLVEKIT_HOME, or `evolvekit app --home`
      settings.yaml  .env
      harnesses/<id>-<version>/    installed harnesses
      studies/<slug>/              one folder per study

The harnesses in this repository's `harnesses/` folder are found as built-ins
when evolvekit runs from a checkout; everywhere else a harness arrives as a
`.zip` (`evolvekit harness pack`). A study copies its harness in, so an updated
harness never changes a study that already exists.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

import yaml

from evolvekit.harness import HarnessError
from evolvekit.harness.manifest import Harness, load_harness, resolve_code_repo
from evolvekit.harness.sdk import SDK_PATH
from evolvekit.harness.study import Study, save_study, study_from_template

__all__ = [
    "BUILT_IN",
    "HarnessEntry",
    "copy_harness",
    "create_study",
    "default_home",
    "find_harnesses",
    "install_harness",
    "new_harness",
    "pack_harness",
    "point_code_repo",
    "slugify",
]

MAX_MEMBERS = 2000
MAX_UNPACKED = 500 * 1024 * 1024
"""What a harness zip may hold: a harness is a runner, a manifest and some
samples. Judged from the archive's directory, before anything is written."""

BUILT_IN = Path(__file__).resolve().parents[2] / "harnesses"
"""`harnesses/` of the checkout evolvekit runs from (absent in an installed package)."""

_IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".DS_Store", "*.egg-info")


def default_home() -> Path:
    """The library home: `EVOLVEKIT_HOME`, else `~/evolvekit`."""
    return Path(os.environ.get("EVOLVEKIT_HOME") or (Path.home() / "evolvekit")).expanduser()


def slugify(name: str) -> str:
    """A folder name for a study: lower case, dashes, never empty."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:60].strip("-") or "study"


@dataclass(frozen=True)
class HarnessEntry:
    path: Path
    harness: Harness | None
    problem: str = ""
    built_in: bool = False


def find_harnesses(home: Path | None = None) -> list[HarnessEntry]:
    """Every harness evolvekit can offer: the installed ones, then the
    built-ins of a checkout. One that does not load is listed with why."""
    folders: list[tuple[Path, bool]] = []
    installed = (home or default_home()) / "harnesses"
    if installed.is_dir():
        folders += [(p, False) for p in sorted(installed.iterdir()) if (p / "harness.yaml").is_file()]
    if BUILT_IN.is_dir():
        folders += [(p, True) for p in sorted(BUILT_IN.iterdir()) if (p / "harness.yaml").is_file()]
    entries = []
    seen: set[str] = set()
    for folder, built_in in folders:
        try:
            harness = load_harness(folder)
        except HarnessError as exc:
            entries.append(HarnessEntry(folder, None, str(exc), built_in))
            continue
        if harness.key in seen:
            continue  # an installed copy wins over the built-in of the same version
        seen.add(harness.key)
        entries.append(HarnessEntry(folder, harness, "", built_in))
    return entries


def copy_harness(source: Path, target: Path) -> None:
    """A harness's folder, copied without its samples and caches: what a
    study pins."""
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("samples", "__pycache__", "*.pyc", ".pytest_cache"))


def create_study(folder: str | Path, harness_dir: str | Path, name: str, template: str | None = None) -> Study:
    """A new study in `folder` (which must not exist yet, or be empty): the
    harness copied in, empty `cases/` and `inputs/`, and `study.yaml` from the
    template (a blank study without one)."""
    folder, harness_dir = Path(folder), Path(harness_dir)
    if folder.exists() and any(folder.iterdir()):
        raise HarnessError(f"{folder}: a study folder must be new or empty")
    harness = load_harness(harness_dir)
    study = study_from_template(harness, template, name)
    folder.mkdir(parents=True, exist_ok=True)
    copy_harness(harness_dir, folder / "harness")
    for sub in ("cases", "inputs"):
        (folder / sub).mkdir(exist_ok=True)
    save_study(study, folder)
    return study


def pack_harness(folder: str | Path, out_dir: str | Path | None = None) -> Path:
    """Check the harness and write `<id>-<version>.zip` (samples included,
    caches not) next to it, or into `out_dir`."""
    folder = Path(folder)
    harness = load_harness(folder)
    target = Path(out_dir or folder.parent) / f"{harness.key}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder)
            if path.is_dir() or any(part in ("__pycache__", ".pytest_cache") for part in relative.parts):
                continue
            if path.suffix == ".pyc":
                continue
            archive.write(path, (Path(harness.key) / relative).as_posix())
    return target


def install_harness(archive: str | Path, home: Path | None = None) -> Harness:
    """Unpack a harness `.zip` into the library home and check it. A path in
    the archive that would land outside its folder is refused; so is an
    archive with more than `MAX_MEMBERS` files or more than `MAX_UNPACKED`
    bytes once unpacked, before anything of it is written. Whatever else a
    zip can do wrong -- it is not one, it is encrypted, compressed in a way
    Python cannot undo, or corrupt -- is a `HarnessError` with the reason."""
    archive = Path(archive)
    root = (home or default_home()) / "harnesses"
    root.mkdir(parents=True, exist_ok=True)
    staging = root / (".incoming-" + archive.stem)
    if staging.exists():
        shutil.rmtree(staging)
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            unpacked = sum(info.file_size for info in members)
            if len(members) > MAX_MEMBERS or unpacked > MAX_UNPACKED:
                raise HarnessError(f"{archive.name}: {len(members)} files / {round(unpacked / 2**20)} MB unpacked "
                                   "is more than a harness can be")
            names = [n for n in bundle.namelist() if not n.endswith("/")]
            tops = {n.split("/", 1)[0] for n in names}
            strip = len(tops) == 1 and all("/" in n for n in names)
            for name in names:
                inner = name.split("/", 1)[1] if strip else name
                target = (staging / inner).resolve()
                if not str(target).startswith(str(staging.resolve()) + os.sep):
                    raise HarnessError(f"{archive.name}: {name!r} would land outside the harness folder")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(name))
    except zipfile.BadZipFile as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise HarnessError(f"{archive.name}: not a zip file ({exc})") from None
    except (RuntimeError, NotImplementedError, zlib.error, OSError) as exc:
        # Encrypted, an unsupported compression, a corrupt stream, a disk that is full.
        shutil.rmtree(staging, ignore_errors=True)
        raise HarnessError(f"{archive.name}: cannot be unpacked: {exc}") from None
    except HarnessError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        harness = load_harness(staging)
    except HarnessError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    final = root / harness.key
    if final.exists():
        shutil.rmtree(final)
    staging.rename(final)
    return load_harness(final)


_CODE_REPO = re.compile(r"^(?P<lead>[ \t]+repo:[ \t]*)(?P<value>[^#\r\n]*?)(?P<tail>[ \t]*(?:#.*)?)$")


def point_code_repo(folder: str | Path, repo: str) -> None:
    """Set `code.repo` in `folder/harness.yaml` to `repo`, keeping the file's
    comments and layout (one line changes). A harness without `code:` is left
    as it is; a `code:` written in flow style is rewritten whole."""
    manifest = Path(folder) / "harness.yaml"
    raw = manifest.read_bytes().decode("utf-8")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data.get("code"), dict) or "repo" not in data["code"]:
        return
    lines = raw.splitlines(keepends=True)
    inside = False
    for index, line in enumerate(lines):
        if line[:1] not in (" ", "\t", "\r", "\n", "#", ""):
            inside = line.rstrip("\r\n").rstrip() == "code:"
            continue
        match = _CODE_REPO.match(line.rstrip("\r\n")) if inside else None
        if match:
            ending = line[len(line.rstrip("\r\n")):]
            lines[index] = f"{match['lead']}{json.dumps(repo)}{match['tail']}{ending}"
            manifest.write_bytes("".join(lines).encode("utf-8"))
            return
    data["code"]["repo"] = repo
    manifest.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _keep_code_repo(source: Path, target: Path) -> None:
    """A copied harness's relative `code.repo` would name another folder from its
    new place: write it as the absolute folder it named from `source`."""
    try:
        harness = load_harness(source, check_templates=False)
    except HarnessError:
        return  # the copy is checked when it is loaded; nothing to keep
    code = harness.code
    if code is None or Path(code.repo).is_absolute() or PureWindowsPath(code.repo).is_absolute():
        return
    point_code_repo(target, str(resolve_code_repo(harness.root, code)))


def new_harness(target: str | Path, *, source: str | Path | None = None, kind: str = "python") -> Path:
    """A new harness folder: a copy of `source` (the closest existing harness)
    or a skeleton of `kind`, with the current SDK and a fresh AGENTS.md."""
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise HarnessError(f"{target}: the folder for a new harness must be new or empty")
    if source is not None:
        shutil.copytree(Path(source), target, ignore=_IGNORED, dirs_exist_ok=True)
        _keep_code_repo(Path(source), target)
    else:
        from evolvekit.harness.skeleton import write_skeleton

        write_skeleton(target, kind)
    shutil.copyfile(SDK_PATH, target / "evk_harness.py")
    shutil.copyfile(Path(__file__).with_name("templates") / "AGENTS.md", target / "AGENTS.md")
    return target
