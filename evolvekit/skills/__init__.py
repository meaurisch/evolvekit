"""The skills that ship with evolvekit, and the code that puts them in a project.

A skill is a folder with a `SKILL.md`. `install` writes each one to
`<dir>/.agents/skills/<name>/` and a pointer to `<dir>/.claude/skills/<name>/`
for the tool that does not read the generic path yet.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from evolvekit.resources import package_root

SHARED = "shared"
REFERENCE = "evolvekit.md"


def _root() -> Path:
    return package_root() / "skills"


def available() -> list[str]:
    return sorted(
        item.name
        for item in _root().iterdir()
        if item.name != SHARED and item.is_dir() and item.joinpath("SKILL.md").is_file()
    )


def frontmatter(text: str) -> dict:
    """The `---` block at the top of a file as a dict; {} when there is none.
    A block that is never closed is an error, not an empty answer."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    for end, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            data = yaml.safe_load("\n".join(lines[1:end]))
            return data if isinstance(data, dict) else {}
    raise ValueError("the frontmatter is not closed by a second ---")


def _checked(folder: str, text: str) -> tuple[str, str]:
    try:
        meta = frontmatter(text)
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"skill {folder}: bad frontmatter: {exc}") from exc
    if re.search(r"^description:\s*[>|][-+0-9]*\s*$", text, re.MULTILINE):
        raise ValueError(
            f"skill {folder}: the description must be one line "
            "(multi-line values with > or | are not supported)")
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"skill {folder}: the frontmatter has no name")
    if name != folder:
        raise ValueError(f"skill {folder}: the name {name!r} differs from the folder name")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"skill {folder}: the frontmatter has no description")
    if "\n" in description.strip():
        raise ValueError(f"skill {folder}: the description must be one line")
    return name, description.strip()


def _pointer(name: str, description: str) -> str:
    quoted = json.dumps(description, ensure_ascii=False)
    return (
        f"---\nname: {name}\ndescription: {quoted}\n---\n\n"
        f"Read and follow `.agents/skills/{name}/SKILL.md` in this repository.\n"
    )


def install(target: Path, *, force: bool = False) -> dict:
    target = Path(target)
    shared = _root().joinpath(SHARED, REFERENCE).read_bytes().replace(b"\r\n", b"\n")
    names = available()
    texts = {}
    for name in names:
        text = _root().joinpath(name, "SKILL.md").read_bytes().decode("utf-8").replace("\r\n", "\n")
        texts[name] = (text, *_checked(name, text))
    written: list[Path] = []
    skipped: list[Path] = []

    def put(path: Path, data: bytes) -> None:
        if path.exists() and not force:
            skipped.append(path)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(path)

    for name in names:
        text, skill_name, description = texts[name]
        put(target / ".agents" / "skills" / name / "SKILL.md", text.encode("utf-8"))
        put(target / ".agents" / "skills" / name / "references" / REFERENCE, shared)
        put(target / ".claude" / "skills" / name / "SKILL.md",
            _pointer(skill_name, description).encode("utf-8"))
    return {"written": written, "skipped": skipped}
