"""The library home on disk: the app's settings and keys, the harness library,
and the studies -- everything the app knows.

    <home>/settings.yaml    provider, models, recently used applications
    <home>/.env             API keys: written here, never read back out
    <home>/harnesses/       installed harnesses
    <home>/studies/<slug>/  one folder per study (evolvekit/harness/study.py)

Nothing here is kept in memory between requests, so the app can be closed and
opened again at any time, and two tabs see the same thing.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

import yaml

from evolvekit.app import AppError
from evolvekit.env import parse_env
from evolvekit.harness import HarnessError
from evolvekit.harness.execute import runner_command
from evolvekit.harness.library import HarnessEntry, create_study, find_harnesses, install_harness, slugify
from evolvekit.harness.manifest import Harness, load_harness
from evolvekit.harness.study import Study, load_study, save_study, study_problems
from evolvekit.ledger import _atomic_write

__all__ = ["STEPS", "Home", "safe_name", "split_words"]

STEPS = ("Question", "Application", "Cases", "What may change", "Goal", "Limits and budget", "Review and start")
_STEP_OF = {
    "name": 1, "harness": 1, "template": 1, "application": 2, "cases": 3, "inputs": 4, "vary": 4,
    "constraints": 4, "kpis": 5, "goal": 5, "guardrails": 5, "limits": 6, "budget": 6, "plan": 6,
}
PROVIDERS = ("openrouter", "azure", "claude-cli", "copilot-cli")
KEY_NAMES = {
    "openrouter": ("OPENROUTER_API_KEY",),
    "azure": ("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"),
    "claude-cli": (),
    "copilot-cli": (),
}
DEFAULT_MODEL_FREE = ("claude-cli", "copilot-cli")
"""The providers whose model may be left empty: the CLI's own default model."""
DEFAULT_MODEL = "anthropic/claude-sonnet-5"
DEFAULT_SETTINGS: dict[str, Any] = {
    "provider": "openrouter",
    "models": {"assistant": DEFAULT_MODEL, "search": DEFAULT_MODEL},
    "prices": {},
    "recent_applications": [],
}
"""`prices`: `{model: [in, out]}`, USD per million tokens, for models the app
does not know the price of (`evolvekit/app/prices.py`). `models.<role>` may be
empty for `claude-cli` and `copilot-cli` only: the CLI's own default model."""
_SET_BY_APP: set[str] = set()
"""The key names `Home.export_keys` put into this process's environment, so
that a key the app no longer keeps is taken out again."""
RECENT = 10
MAX_UPLOAD = 200 * 1024 * 1024
INSPECT_TIMEOUT_S = 60
INSPECTED = ".inspected.json"
TRASH = ".trash"
"""`studies/.trash/<slug>~<YYYYMMDD-HHMMSS>` holds a deleted study; `<study>/.trash/cases/` a removed case."""
TRASH_DAYS = 7
ARCHIVED = ".archived"
"""The marker file of an archived study: a fact about the library, not a field of the study."""
STUDY_PARTS = ("study.yaml", "harness", "cases", "inputs")
"""What a study is made of, as against what it produced: `runs/`, `preview/`, `assistant.jsonl`."""
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
_TRASH_ENTRY = re.compile(r"^(?P<slug>[a-z0-9][a-z0-9-]{0,79})~(?P<stamp>\d{8}-\d{6})(?:-\d+)?$")
_STAMP = "%Y%m%d-%H%M%S"
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(name: str, formats: tuple[str, ...] | None = None) -> str:
    """The name an uploaded file is stored under: its own name with anything
    but letters, digits, dots, dashes and underscores made a dash. No folder,
    drive, `..` or device name gets through, and the extension must be one the
    harness reads."""
    text = str(name or "").strip()
    if not text or "\x00" in text or any(sep in text for sep in ("/", "\\", ":")) or ".." in text:
        raise AppError(f"{name!r} is not a file name an upload may have: no folders, drives or '..'")
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")
    if not cleaned or len(cleaned) > 120:
        raise AppError(f"{name!r} is not a file name an upload may have")
    if cleaned.split(".")[0].lower() in _RESERVED:
        raise AppError(f"{name!r} is a name Windows keeps for a device; rename the file")
    if formats and not cleaned.lower().endswith(tuple(f.lower() for f in formats)):
        raise AppError(f"{cleaned}: this harness reads {', '.join(formats)} files")
    return cleaned


def _problem(sentence: str) -> dict[str, Any]:
    key, _, text = sentence.partition(": ")
    head = re.split(r"[.\[]", key, maxsplit=1)[0]
    step = _STEP_OF.get(head, 7)
    return {"step": step, "key": key, "text": text or sentence}


class Home:
    """One library home."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.studies_dir = self.root / "studies"
        self.harnesses_dir = self.root / "harnesses"
        for folder in (self.root, self.studies_dir, self.harnesses_dir):
            folder.mkdir(parents=True, exist_ok=True)

    # -- settings and keys ----------------------------------------------------

    def settings(self) -> dict[str, Any]:
        stored: dict[str, Any] = {}
        path = self.root / "settings.yaml"
        if path.is_file():
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
                stored = loaded if isinstance(loaded, dict) else {}
            except yaml.YAMLError:
                stored = {}
        merged = json.loads(json.dumps(DEFAULT_SETTINGS))
        if stored.get("provider") in PROVIDERS:
            merged["provider"] = stored["provider"]
        for role in ("assistant", "search"):
            value = (stored.get("models") or {}).get(role)
            if isinstance(value, str) and (value.strip() or merged["provider"] in DEFAULT_MODEL_FREE):
                merged["models"][role] = value.strip()
        prices = stored.get("prices")
        if isinstance(prices, dict):
            for model, pair in prices.items():
                checked = self._price_pair(model, pair)
                if checked is not None:
                    merged["prices"][str(model).strip()] = checked
        recent = stored.get("recent_applications")
        if isinstance(recent, list):
            merged["recent_applications"] = [str(p) for p in recent if isinstance(p, str)][:RECENT]
        return merged

    def save_settings(self, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.settings()
        if "provider" in changes:
            if changes["provider"] not in PROVIDERS:
                raise AppError(f"provider: one of {', '.join(PROVIDERS)}")
            current["provider"] = changes["provider"]
        for role, value in (changes.get("models") or {}).items():
            if role not in ("assistant", "search") or not isinstance(value, str):
                raise AppError(f"models.{role}: the model's name, such as {DEFAULT_MODEL}")
            if not value.strip() and current["provider"] == "azure":
                raise AppError(f"models.{role}: the deployment name on your Azure OpenAI resource")
            if not value.strip() and current["provider"] not in DEFAULT_MODEL_FREE:
                raise AppError(f"models.{role}: the model's name, such as {DEFAULT_MODEL}")
            current["models"][role] = value.strip()
        if "prices" in changes:
            if not isinstance(changes["prices"], dict):
                raise AppError("prices: a mapping of model name to [price in, price out], USD per million tokens")
            current["prices"] = {}
            for model, pair in changes["prices"].items():
                checked = self._price_pair(model, pair)
                if checked is None:
                    raise AppError(f"prices: {model!r} wants [price in, price out], two numbers of 0 or more, "
                                   "USD per million tokens")
                current["prices"][str(model).strip()] = checked
        if "keys" in changes:
            self.set_keys(changes["keys"])
        self._write_yaml(self.root / "settings.yaml", current)
        return current

    @staticmethod
    def _price_pair(model: Any, pair: Any) -> list[float] | None:
        if not isinstance(model, str) or not model.strip() or not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return None
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 for v in pair):
            return None
        return [float(pair[0]), float(pair[1])]

    def remember_application(self, path: str) -> None:
        current = self.settings()
        recent = [path] + [p for p in current["recent_applications"] if p != path]
        current["recent_applications"] = recent[:RECENT]
        self._write_yaml(self.root / "settings.yaml", current)

    def _env_values(self) -> dict[str, str]:
        path = self.root / ".env"
        if not path.is_file():
            return {}
        try:
            return parse_env(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError):
            return {}

    def keys(self) -> dict[str, bool]:
        """Which keys are set: by name, never their values."""
        values = self._env_values()
        names = sorted({n for group in KEY_NAMES.values() for n in group})
        return {name: bool(values.get(name) or os.environ.get(name)) for name in names}

    def set_keys(self, keys: Any) -> None:
        if not isinstance(keys, dict):
            raise AppError("keys: a mapping of key names to values")
        allowed = {n for group in KEY_NAMES.values() for n in group}
        values = self._env_values()
        for name, value in keys.items():
            if name not in allowed:
                raise AppError(f"keys: {name!r} is not a key the app keeps; it keeps {', '.join(sorted(allowed))}")
            if value is None or str(value).strip() == "":
                values.pop(name, None)
            elif any(c in str(value) for c in "\r\n\x00"):
                raise AppError(f"keys: {name} must be one line")
            else:
                values[name] = str(value).strip()
        lines = ["# API keys for evolvekit's app. Written by the app; it never shows them again."]
        lines += [f"{name}={value}" for name, value in sorted(values.items())]
        _atomic_write(self.root / ".env", "\n".join(lines) + "\n")
        if os.name == "posix":  # the user's eyes only; Windows has no mode bits to set
            os.chmod(self.root / ".env", 0o600)
        self.export_keys()

    def export_keys(self) -> None:
        """Put the app's keys into this process's environment, where the
        providers read them: the app's file wins over a shell variable, and a
        key the app set earlier and no longer keeps is taken out again. So a
        key changed in Settings is used by the very next question, and a
        removed one stops the assistant at once."""
        values = self._env_values()
        for name in sorted({n for group in KEY_NAMES.values() for n in group}):
            if values.get(name):
                os.environ[name] = values[name]
                _SET_BY_APP.add(name)
            elif name in _SET_BY_APP:
                os.environ.pop(name, None)
                _SET_BY_APP.discard(name)

    def job_environment(self) -> dict[str, str]:
        """The keys a run needs, for its environment: passed explicitly, so the
        run never reads files above its study."""
        allowed = {n for group in KEY_NAMES.values() for n in group}
        return {name: value for name, value in self._env_values().items() if name in allowed and value}

    @staticmethod
    def _write_yaml(path: Path, data: Any) -> None:
        _atomic_write(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))

    # -- harnesses --------------------------------------------------------------

    def harness_entries(self) -> list[HarnessEntry]:
        return find_harnesses(self.root)

    def harness_entry(self, harness_id: str, version: str | None = None) -> HarnessEntry:
        matches = [e for e in self.harness_entries() if e.harness is not None and e.harness.id == harness_id
                   and (version is None or e.harness.version == version)]
        if not matches:
            raise AppError(f"there is no harness {harness_id!r}{' ' + version if version else ''} in the library", 404)
        return max(matches, key=lambda e: [int(p) for p in e.harness.version.split(".") if p.isdigit()])  # type: ignore[union-attr]

    def install(self, data: bytes, filename: str = "harness.zip") -> Harness:
        if len(data) > MAX_UPLOAD:
            raise AppError("the file is larger than 200 MB; a harness is much smaller", 413)
        incoming = self.harnesses_dir / (".upload-" + safe_name(filename or "harness.zip", (".zip",)))
        incoming.write_bytes(data)
        try:
            return install_harness(incoming, self.root)
        except HarnessError as exc:
            raise AppError(str(exc)) from None
        finally:
            incoming.unlink(missing_ok=True)

    # -- studies ----------------------------------------------------------------

    def study_root(self, slug: str) -> Path:
        if not isinstance(slug, str) or not _SLUG.match(slug):
            raise AppError(f"{slug!r} is not the name of a study", 404)
        root = self.studies_dir / slug
        if not (root / "study.yaml").is_file():
            raise AppError(f"there is no study {slug!r}", 404)
        return root

    def slugs(self) -> list[str]:
        return sorted(p.name for p in self.studies_dir.iterdir() if (p / "study.yaml").is_file() and _SLUG.match(p.name))

    def active_run(self) -> tuple[str, str] | None:
        """The `(slug, run)` that has the computer now: the oldest run across
        the home that is starting, searching or checking; None when nothing
        runs. The rule is `jobs.active_run`'s (imported here, not above:
        `jobs` reads studies through this module)."""
        from evolvekit.app import jobs

        return jobs.active_run(self)

    def _free_slug(self, name: str) -> str:
        """The slug a new study of this name gets: its name's, or `-2`, `-3`… when taken."""
        base = slugify(name)
        slug, number = base, 2
        while (self.studies_dir / slug).exists():
            slug, number = f"{base}-{number}", number + 1
        return slug

    def create_study(self, harness_id: str, template: str | None, name: str) -> str:
        name = str(name or "").strip()
        if not name:
            raise AppError("name: give the study a name")
        folder = Path(harness_id)
        source = folder if folder.is_dir() else self.harness_entry(harness_id).path
        slug = self._free_slug(name)
        try:
            create_study(self.studies_dir / slug, source, name, template or None)
        except HarnessError as exc:
            shutil.rmtree(self.studies_dir / slug, ignore_errors=True)
            raise AppError(str(exc)) from None
        return slug

    def load(self, slug: str) -> tuple[Path, Study, Harness]:
        root = self.study_root(slug)
        try:
            return root, load_study(root), load_harness(root / "harness")
        except HarnessError as exc:
            raise AppError(f"the study {slug!r} cannot be read: {exc}", 500) from None

    def problems(self, slug: str) -> list[dict[str, Any]]:
        root, study, harness = self.load(slug)
        return [_problem(p) for p in study_problems(study, harness, root=root)]

    def save_step(self, slug: str, changes: dict[str, Any]) -> None:
        """Merge `changes` -- top-level parts of `study.yaml` -- into the study
        and save it. The shape is checked; what is missing is not (that is
        what the problems list says, step by step)."""
        root, study, _ = self.load(slug)
        document = study.to_yaml()
        protected = {"study", "harness"}
        for key, value in (changes or {}).items():
            if key in protected:
                raise AppError(f"{key}: a study keeps its harness; start a new study for another one")
            if key not in document and key not in ("template", "step"):
                raise AppError(f"{key}: not part of a study")
            document[key] = value
        try:
            updated = Study.parse(document)
        except HarnessError as exc:
            raise AppError(str(exc)) from None
        # Every path that enters study.yaml is judged as text first: a case
        # lives under cases/, an input under inputs/, nothing points outside.
        for label, paths, folder in (("cases.training", updated.training, "cases/"),
                                     ("cases.test", updated.test, "cases/"),
                                     ("inputs", updated.inputs.values(), "inputs/")):
            for value in paths:
                _study_file(root, label, value, folder)
        save_study(updated, root)

    # -- managing studies: the trash, the archive, copies, the zip ---------------------

    def trash_dir(self) -> Path:
        return self.studies_dir / TRASH

    def delete_study(self, slug: str) -> str:
        """Move the study to the trash; the entry's name, `<slug>~<stamp>`.
        Nothing is deleted at once: `restore_study` brings it back until
        `clean_trash` empties it after `TRASH_DAYS`."""
        root = self.study_root(slug)
        self.trash_dir().mkdir(exist_ok=True)
        base = f"{slug}~{datetime.now().strftime(_STAMP)}"
        entry, number = base, 2
        while (self.trash_dir() / entry).exists():  # a second delete within the second
            entry, number = f"{base}-{number}", number + 1
        shutil.move(str(root), str(self.trash_dir() / entry))
        return entry

    def _trash_entry(self, entry: str) -> tuple[Path, str, datetime]:
        """`(folder, slug, deleted_at)` of a trash entry, judged by name first."""
        match = _TRASH_ENTRY.match(entry) if isinstance(entry, str) else None
        if match is None:
            raise AppError(f"{entry!r} is not an entry of the trash", 404)
        folder = self.trash_dir() / entry
        if not (folder / "study.yaml").is_file():
            raise AppError(f"there is no {entry!r} in the trash", 404)
        return folder, match.group("slug"), datetime.strptime(match.group("stamp"), _STAMP)

    def trash(self) -> list[dict[str, Any]]:
        """What waits in the trash, newest first: `{entry, name, deleted_at}`."""
        if not self.trash_dir().is_dir():
            return []
        listed = []
        for folder in self.trash_dir().iterdir():
            try:
                _, slug, deleted_at = self._trash_entry(folder.name)
            except AppError:
                continue
            name = slug
            try:
                loaded = yaml.safe_load((folder / "study.yaml").read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and str(loaded.get("name") or "").strip():
                    name = str(loaded["name"]).strip()
            except (OSError, yaml.YAMLError):
                pass
            listed.append({"entry": folder.name, "name": name, "deleted_at": deleted_at.isoformat(timespec="seconds")})
        return sorted(listed, key=lambda e: e["deleted_at"], reverse=True)

    def restore_study(self, entry: str) -> str:
        """The study back under its slug -- or `-2` when the slug was taken meanwhile."""
        folder, slug, _ = self._trash_entry(entry)
        target = self._free_slug(slug)
        shutil.move(str(folder), str(self.studies_dir / target))
        return target

    def clean_trash(self, days: int = TRASH_DAYS) -> list[str]:
        """Delete for good what has waited in the trash longer than `days`;
        the entries removed. The app calls this once when it starts."""
        if not self.trash_dir().is_dir():
            return []
        limit = datetime.now() - timedelta(days=days)
        removed = []
        for folder in sorted(self.trash_dir().iterdir()):
            match = _TRASH_ENTRY.match(folder.name)
            if match is None or not folder.is_dir():
                continue
            if datetime.strptime(match.group("stamp"), _STAMP) < limit:
                shutil.rmtree(folder, ignore_errors=True)
                if not folder.exists():
                    removed.append(folder.name)
        return removed

    def is_archived(self, slug: str) -> bool:
        return (self.study_root(slug) / ARCHIVED).is_file()

    def set_archived(self, slug: str, archived: bool) -> None:
        marker = self.study_root(slug) / ARCHIVED
        if archived:
            marker.write_text("archived\n", encoding="utf-8")
        else:
            marker.unlink(missing_ok=True)

    def duplicate_study(self, slug: str, name: str = "") -> str:
        """A copy of the study's setup -- `study.yaml`, its harness, cases
        (with their inspections) and inputs -- under a new slug and name; not
        its runs, previews, assistant history or downloads."""
        root, study, _ = self.load(slug)
        name = str(name or "").strip() or f"{study.name} (copy)"
        new_slug = self._free_slug(name)
        new_root = self.studies_dir / new_slug
        try:
            new_root.mkdir()
            for part in STUDY_PARTS[1:]:
                if (root / part).is_dir():
                    shutil.copytree(root / part, new_root / part, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                else:
                    (new_root / part).mkdir()
            study.name = name
            save_study(study, new_root)
        except OSError as exc:
            shutil.rmtree(new_root, ignore_errors=True)
            raise AppError(f"the study could not be copied: {exc}", 500) from None
        return new_slug

    def export_study(self, slug: str) -> bytes:
        """The study as one zip: `study.yaml`, `harness/`, `cases/`, `inputs/`
        -- what `import_study` reads; not what the study produced."""
        root = self.study_root(slug)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(root / "study.yaml", "study.yaml")
            for part in STUDY_PARTS[1:]:
                folder = root / part
                if not folder.is_dir():
                    continue
                for path in sorted(folder.rglob("*")):
                    relative = path.relative_to(root)
                    if path.is_dir() or path.suffix == ".pyc" or any(p.startswith(".") or p == "__pycache__" for p in relative.parts):
                        continue
                    archive.write(path, relative.as_posix())
        return buffer.getvalue()

    def import_study(self, data: bytes, filename: str = "study.zip") -> str:
        """A study from a zip `export_study` wrote (or a folder zipped by
        hand, with or without a top folder): its slug, new, from the name in
        its `study.yaml`. A member that would land outside the study's folder
        is refused, as is a zip without `study.yaml`; nothing is written
        until every name has been judged."""
        if len(data) > MAX_UPLOAD:
            raise AppError("the file is larger than 200 MB", 413)
        safe_name(filename or "study.zip", (".zip",))
        try:
            bundle = zipfile.ZipFile(io.BytesIO(data))
            names = [n for n in bundle.namelist() if not n.endswith("/")]
        except zipfile.BadZipFile:
            raise AppError(f"{filename}: not a zip file") from None
        tops = {n.split("/", 1)[0] for n in names}
        strip = len(tops) == 1 and all("/" in n for n in names)
        members: list[tuple[str, str]] = []
        for name in names:
            inner = name.split("/", 1)[1] if strip else name
            parts = PureWindowsPath(inner)
            if (not inner or "\x00" in inner or parts.drive or parts.root or inner.startswith(("/", "\\"))
                    or any(p in ("..", ".") for p in parts.parts)):
                raise AppError(f"{filename}: {name!r} would land outside the study's folder")
            # Only what a study is made of comes in: the file study.yaml, and files under the three folders.
            if (parts.parts[0] == "study.yaml") == (len(parts.parts) == 1) and parts.parts[0] in STUDY_PARTS:
                members.append((name, "/".join(parts.parts)))
        if "study.yaml" not in {inner for _, inner in members}:
            raise AppError(f"{filename}: there is no study.yaml in it, so it is not a study")
        try:
            study = Study.parse(yaml.safe_load(bundle.read(next(n for n, i in members if i == "study.yaml"))))
        except (yaml.YAMLError, HarnessError) as exc:
            raise AppError(f"{filename}: its study.yaml cannot be read: {exc}") from None
        slug = self._free_slug(study.name)
        staging = self.studies_dir / f".incoming-{slug}"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            for name, inner in members:
                target = staging.joinpath(*inner.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(name))
            for part in STUDY_PARTS[2:]:
                (staging / part).mkdir(exist_ok=True)
            try:
                load_harness(staging / "harness")
            except HarnessError as exc:
                raise AppError(f"{filename}: its harness cannot be read: {exc}") from None
            save_study(study, staging)  # written back as parsed: a normal study file
            staging.rename(self.studies_dir / slug)
        except AppError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            raise AppError(f"{filename}: the study could not be unpacked: {exc}", 500) from None
        return slug

    # -- cases and inputs -------------------------------------------------------

    def case_names(self, slug: str) -> list[str]:
        root = self.study_root(slug)
        folder = root / "cases"
        return sorted(p.name for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")) if folder.is_dir() else []

    def put_case(self, slug: str, filename: str, data: bytes) -> dict[str, Any]:
        root, study, harness = self.load(slug)
        if len(data) > MAX_UPLOAD:
            raise AppError("the file is larger than 200 MB", 413)
        name = safe_name(filename, harness.cases.formats)
        (root / "cases").mkdir(exist_ok=True)
        (root / "cases" / name).write_bytes(data)
        relative = f"cases/{name}"
        if relative not in study.training and relative not in study.test:
            study.training.append(relative)
            save_study(study, root)
        return {"name": name, "inspected": self.inspect_case(slug, name, force=True)}

    def delete_case(self, slug: str, filename: str) -> None:
        """Take the case out of the study: the file waits in `<study>/.trash/cases/`
        for `restore_case`, and its inspection is kept, so no re-reading then."""
        root, study, _ = self.load(slug)
        name = safe_name(filename)
        target = root / "cases" / name
        if not target.is_file():
            raise AppError(f"there is no case {name!r} in this study", 404)
        trash = root / TRASH / "cases"
        trash.mkdir(parents=True, exist_ok=True)
        (trash / name).unlink(missing_ok=True)
        shutil.move(str(target), str(trash / name))
        relative = f"cases/{name}"
        study.training = [c for c in study.training if c != relative]
        study.test = [c for c in study.test if c != relative]
        save_study(study, root)

    def restore_case(self, slug: str, filename: str) -> None:
        """A removed case back into the study, in training."""
        root, study, _ = self.load(slug)
        name = safe_name(filename)
        waiting = root / TRASH / "cases" / name
        if not waiting.is_file():
            raise AppError(f"there is no removed case {name!r} to restore", 404)
        target = root / "cases" / name
        if target.exists():
            raise AppError(f"there is a case {name!r} in the study again already", 409)
        target.parent.mkdir(exist_ok=True)
        shutil.move(str(waiting), str(target))
        relative = f"cases/{name}"
        if relative not in study.training and relative not in study.test:
            study.training.append(relative)
        save_study(study, root)

    def use_samples(self, slug: str) -> list[str]:
        root, study, harness = self.load(slug)
        entry = self.harness_entry(harness.id, harness.version)
        samples = sorted(p for p in (entry.path / "samples").glob("*") if p.is_file() and p.suffix.lower() in harness.cases.formats)
        if not samples:
            raise AppError(f"the harness {harness.id} has no sample cases")
        added = []
        for sample in samples:
            target = root / "cases" / sample.name
            shutil.copyfile(sample, target)
            relative = f"cases/{sample.name}"
            if relative not in study.training and relative not in study.test:
                study.training.append(relative)
            added.append(sample.name)
        save_study(study, root)
        for name in added:
            self.inspect_case(slug, name)
        return added

    def import_folder(self, slug: str, folder: str) -> dict[str, Any]:
        """Copy every case file in `folder` (and below it) into the study: the
        app runs on this computer, so a folder can be named instead of uploaded."""
        root, study, harness = self.load(slug)
        source = Path(str(folder or "").strip().strip('"')).expanduser()
        if not str(folder or "").strip() or not source.is_absolute():
            raise AppError("give the full path of the folder, such as C:\\Users\\you\\Documents\\cases")
        if not source.is_dir():
            raise AppError(f"there is no folder at {source}")
        found = sorted(p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in harness.cases.formats)
        if not found:
            raise AppError(f"{source} holds no {' or '.join(harness.cases.formats)} files")
        if len(found) > 500:
            raise AppError(f"{source} holds {len(found)} case files; a study needs a few, not hundreds -- choose a smaller folder")
        added, skipped = [], []
        for path in found:
            try:
                name = safe_name(path.name, harness.cases.formats)
            except AppError:
                skipped.append(path.name)
                continue
            if path.stat().st_size > MAX_UPLOAD:
                skipped.append(path.name)
                continue
            shutil.copyfile(path, root / "cases" / name)
            relative = f"cases/{name}"
            if relative not in study.training and relative not in study.test:
                study.training.append(relative)
            added.append(name)
        save_study(study, root)
        for name in added:
            self.inspect_case(slug, name, force=True)
        return {"added": added, "skipped": skipped}

    def put_input(self, slug: str, input_name: str, filename: str, data: bytes) -> str:
        root, study, harness = self.load(slug)
        spec = harness.inputs.get(input_name)
        if spec is None:
            raise AppError(f"the harness takes no input called {input_name!r}", 404)
        if len(data) > MAX_UPLOAD:
            raise AppError("the file is larger than 200 MB", 413)
        name = safe_name(filename, spec.formats)
        if spec.provides == "settings":
            try:
                loaded = json.loads(data.decode("utf-8-sig"))
            except (UnicodeDecodeError, ValueError):
                raise AppError(f"{name}: not a JSON file") from None
            if not isinstance(loaded, dict):
                raise AppError(f"{name}: a settings file holds a JSON object of setting names and values")
            unknown = sorted(str(k).lstrip("-").replace("-", "_") for k in loaded
                             if str(k).lstrip("-").replace("-", "_") not in harness.settings)
            if unknown:
                raise AppError(f"{name}: {', '.join(unknown[:5])} {'is not a setting' if len(unknown) == 1 else 'are not settings'} of {harness.title}")
            for key, value in loaded.items():
                setting = harness.settings[str(key).lstrip("-").replace("-", "_")]
                problem = setting.parameter.problem_with(value)
                if problem:
                    raise AppError(f"{name}: {setting.name}: {problem}")
        (root / "inputs").mkdir(exist_ok=True)
        (root / "inputs" / name).write_bytes(data)
        study.inputs[input_name] = f"inputs/{name}"
        save_study(study, root)
        return name

    def remove_input(self, slug: str, input_name: str) -> None:
        root, study, _ = self.load(slug)
        path = study.inputs.pop(input_name, None)
        if path:
            _study_file(root, "inputs", path, "inputs/").unlink(missing_ok=True)
        save_study(study, root)

    @staticmethod
    def _inspected(root: Path) -> dict[str, Any]:
        path = root / "cases" / INSPECTED
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write_inspected(root: Path, cache: dict[str, Any]) -> None:
        _atomic_write(root / "cases" / INSPECTED, json.dumps(cache, indent=1))

    def inspect_case(self, slug: str, name: str, *, force: bool = False) -> dict[str, Any]:
        """What the harness's runner says about a case ("1,200 tasks, 18
        vehicle types", or why it cannot be read), kept until the file changes."""
        root, study, harness = self.load(slug)
        path = root / "cases" / name
        if not path.is_file():
            raise AppError(f"there is no case {name!r} in this study", 404)
        stat = path.stat()
        stamp = [stat.st_size, stat.st_mtime_ns, study.application_path]
        with _lock(str(root)):
            cached = self._inspected(root).get(name)
        if cached and cached.get("stamp") == stamp and not force:
            return cached["result"]
        if not study.application_path:
            return {"ok": None, "summary": "", "error": "choose the application first: it is what reads the cases"}
        try:
            argv = runner_command(study, harness, str(root / "harness" / "runner.py")) + ["inspect", "--case", str(path)]
            done = subprocess.run(argv, cwd=root, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=INSPECT_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            result = {"ok": False, "summary": "", "error": f"reading it took longer than {INSPECT_TIMEOUT_S} s"}
        except (OSError, HarnessError) as exc:
            result = {"ok": False, "summary": "", "error": f"the application could not be started: {exc}"}
        else:
            answer = None
            for line in reversed(done.stdout.splitlines()):
                if line.strip().startswith("{"):
                    try:
                        answer = json.loads(line)
                    except ValueError:
                        answer = None
                    break
            if done.returncode == 0 and answer and answer.get("ok"):
                result = {"ok": True, "summary": answer.get("summary", ""), "tables": answer.get("tables") or {}}
            else:
                said = (answer or {}).get("error") or (done.stderr.strip().splitlines() or ["no message"])[-1]
                result = {"ok": False, "summary": "", "error": said}
        with _lock(str(root)):
            cache = self._inspected(root)
            cache[name] = {"stamp": stamp, "result": result}
            self._write_inspected(root, cache)
        return result

    def cases(self, slug: str) -> list[dict[str, Any]]:
        root, study, _ = self.load(slug)
        with _lock(str(root)):
            cache = self._inspected(root)
        listed = []
        for name in self.case_names(slug):
            relative = f"cases/{name}"
            stat = (root / relative).stat()
            cached = cache.get(name) or {}
            current = cached.get("stamp") == [stat.st_size, stat.st_mtime_ns, study.application_path]
            listed.append({
                "name": name, "bytes": stat.st_size,
                "set": "test" if relative in study.test else "training" if relative in study.training else "",
                "inspected": cached.get("result") if current else None,
            })
        return listed

    def suggested_test_count(self, slug: str) -> int:
        """How many cases to hold back for the final check: the harness's
        share of them, at least 3 once there are 6, and 40 % once there are
        10 (more held back makes a firmer answer); never every case."""
        _, _, harness = self.load(slug)
        n = len(self.case_names(slug))
        if n < 2:
            return 0
        share = round(harness.defaults.test_share * n)
        if n >= 10:
            share = max(3, round(0.4 * n))
        elif n >= 6:
            share = max(share, 3)  # fewer make a weak final check
        return max(0, min(share, n - 1))

    def split(self, slug: str, test: int) -> list[str]:
        """Hold `test` cases back for the final check, spread over the sizes;
        the smallest case always stays in training (the preview solves it)."""
        root, study, _ = self.load(slug)
        names = self.case_names(slug)
        if not isinstance(test, int) or isinstance(test, bool) or test < 0:
            raise AppError("test: a whole number of cases, 0 or more")
        if names and test > len(names) - 1:
            raise AppError(f"test: at most {len(names) - 1}, so the search keeps at least one case to learn from")

        inspected = {c["name"]: c["inspected"] or {} for c in self.cases(slug)}

        def size(name: str) -> tuple[float, str]:
            counts = [v for v in (inspected[name].get("tables") or {}).values() if isinstance(v, (int, float))]
            return (max(counts) if counts else (root / "cases" / name).stat().st_size, name)

        ordered = sorted(names, key=size)
        picks: list[int] = []
        candidates = len(ordered) - 1  # everything but the smallest
        if test == 1:
            picks = [1 + (candidates - 1) // 2]
        elif test > 1:
            picks = sorted({1 + round(k * (candidates - 1) / (test - 1)) for k in range(test)})
        held = {f"cases/{ordered[i]}" for i in picks}
        study.test = [f"cases/{n}" for n in ordered if f"cases/{n}" in held]
        study.training = [f"cases/{n}" for n in ordered if f"cases/{n}" not in held]
        save_study(study, root)
        return study.test

    # -- the study as the page sees it ----------------------------------------------

    def document(self, slug: str) -> dict[str, Any]:
        root, study, harness = self.load(slug)
        problems = [_problem(p) for p in study_problems(study, harness, root=root)]
        cases = self.cases(slug)
        return {
            "slug": slug,
            "study": study.to_yaml(),
            "problems": problems,
            "steps": [
                {"number": i + 1, "title": title, "problems": sum(1 for p in problems if p["step"] == i + 1)}
                for i, title in enumerate(STEPS)
            ],
            "cases": cases,
            "suggested_test": self.suggested_test_count(slug),
            "split_words": split_words(len(study.test), len(cases)),
            "inputs": dict(study.inputs),
        }


def split_words(held: int, total: int) -> str:
    """One sentence on how firm the final check's answer can be with `held`
    of `total` cases held back (the page shows it under the count)."""
    if held <= 0:
        return "Nothing held back: there will be no final check, so no answer about cases the search never saw."
    if held == 1:
        return "1 held back: the final check can give a number, but no range and no verdict."
    if held == 2:
        return "2 held back: a rough answer; the range will be wide."
    if held < 5:
        return f"{held} held back: a fair answer for a clear, consistent gain."
    return f"{held} held back: a firm answer."


def _study_file(root: Path, label: str, relative: str, folder: str) -> Path:
    """`relative`, a path from study.yaml, resolved inside `root` and under
    `folder`; refused as a 400 that names the field otherwise."""
    refusal = AppError(f"{label}: {relative!r} is not a file of this study")
    if not isinstance(relative, str) or not relative.startswith(folder):
        raise refusal
    try:
        return safe_relative(root, relative)
    except AppError:
        raise refusal from None


def safe_relative(root: Path, relative: str) -> Path:
    """`relative` inside `root`, judged as text first (see the dashboard's
    `_safe_path`): nothing absolute, no drive, no `..`."""
    parts = PureWindowsPath(relative)
    if not relative or "\x00" in relative or parts.drive or parts.root or relative.startswith(("/", "\\")) or ".." in parts.parts:
        raise AppError("not a file of this study", 404)
    target = root.joinpath(*parts.parts).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        raise AppError("not a file of this study", 404) from None
    return target
