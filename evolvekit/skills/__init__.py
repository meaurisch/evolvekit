"""The skills that ship with evolvekit, and the code that puts them in a project.

A skill is a folder with a `SKILL.md`. `install` writes each one to
`<dir>/.agents/skills/<name>/` and a pointer to `<dir>/.claude/skills/<name>/`
for the tool that does not read the generic path yet.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

SHARED = "shared"
REFERENCE = "evolvekit.md"


def _root():
    return resources.files("evolvekit.skills")


def available() -> list[str]:
    return sorted(
        item.name
        for item in _root().iterdir()
        if item.name != SHARED and item.is_dir() and item.joinpath("SKILL.md").is_file()
    )


def frontmatter(text: str) -> dict:
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return out
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return {}


def _pointer(name: str, description: str) -> str:
    return (
        f"---\nname: {name}\ndescription: {description}\n---\n\n"
        f"Read and follow `.agents/skills/{name}/SKILL.md` in this repository.\n"
    )


def install(target: Path, *, force: bool = False) -> dict:
    target = Path(target)
    shared = _root().joinpath(SHARED, REFERENCE).read_bytes()
    written: list[Path] = []
    skipped: list[Path] = []

    def put(path: Path, data: bytes) -> None:
        if path.exists() and not force:
            skipped.append(path)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written.append(path)

    for name in available():
        text = _root().joinpath(name, "SKILL.md").read_bytes().decode("utf-8")
        text = text.replace("\r\n", "\n")
        meta = frontmatter(text)
        put(target / ".agents" / "skills" / name / "SKILL.md", text.encode("utf-8"))
        put(target / ".agents" / "skills" / name / "references" / REFERENCE,
            shared.replace(b"\r\n", b"\n"))
        put(target / ".claude" / "skills" / name / "SKILL.md",
            _pointer(meta.get("name", name), meta.get("description", "")).encode("utf-8"))
    return {"written": written, "skipped": skipped}
