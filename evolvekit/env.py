"""`.env`: where the keys go, read the way the README always said they were.

"Secrets are read from environment variables only. Copy `.env.example` to
`.env` and fill in what you need" -- and then nothing read the file, so a key
placed there never reached a provider, and the provider's error told the user
to do what they had just done.

Every command now loads the nearest `.env` going up from the config file's
directory, and the nearest going up from the working directory. Two rules:

* **The environment wins.** A variable that is already set is never overridden,
  so CI, a shell `export` or a secrets manager keeps the last word.
* **Values are never shown.** What was loaded is reported by *name* only. There
  is no debug switch that prints a value, because there must not be one.

Standard library only; the format is the usual one: `NAME=value`, an optional
`export`, `#` comments, optional single or double quotes.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

__all__ = ["load_env_files", "parse_env", "find_env_file"]

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env(text: str) -> dict[str, str]:
    """`NAME=value` lines. Anything else -- comments, blanks, garbage -- is skipped."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        name, value = match.group(1), match.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif value[:1] in "\"'":
            # Quoted, with something after the closing quote: a trailing comment.
            closing = value.find(value[0], 1)
            if closing > 0:
                value = value[1:closing]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[name] = value
    return values


def find_env_file(start: Path) -> Path | None:
    """The nearest `.env` file in `start` or a directory above it."""
    try:
        here = Path(start).resolve()
    except OSError:
        return None
    for directory in (here, *here.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_env_files(starts: Iterable[str | Path]) -> list[tuple[Path, list[str]]]:
    """Load the nearest `.env` above each of `starts` into `os.environ`.

    Returns `(file, names it set)` for every file that set something. A
    variable that already exists is left alone, and so is one whose value in
    the file is empty -- an untouched copy of `.env.example` sets nothing.
    """
    loaded: list[tuple[Path, list[str]]] = []
    seen: set[Path] = set()
    for start in starts:
        path = find_env_file(Path(start))
        if path is None or path in seen:
            continue
        seen.add(path)
        try:
            values = parse_env(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError):
            continue
        names = []
        for name, value in values.items():
            if value and name not in os.environ:
                os.environ[name] = value
                names.append(name)
        if names:
            loaded.append((path, names))
    return loaded
