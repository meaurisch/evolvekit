"""`.env`: where the keys go, read the way the README always said they were.

"Secrets are read from environment variables only. Copy `.env.example` to
`.env` and fill in what you need" -- and then nothing read the file, so a key
placed there never reached a provider, and the provider's error told the user
to do what they had just done.

Every command now loads the nearest `.env` going up from the config file's
directory, and the nearest going up from the working directory -- but never
out of the project. Four rules:

* **The environment wins.** A variable that is already set is never overridden,
  so CI, a shell `export` or a secrets manager keeps the last word.
* **Values are never shown.** What was loaded is reported by *name* only. There
  is no debug switch that prints a value, because there must not be one.
* **The search stops at the project.** Everything loaded is inherited by every
  evaluator, so a `.env` somebody left in a parent directory -- the home
  directory, a shared drive -- must not be able to point a provider's base URL
  somewhere else while the user's real key is in the shell. The search goes up
  to the first directory that holds `.git` or `pyproject.toml` and no further;
  outside any project only the directory itself is read, and the home
  directory is never read unless the search starts there.
* **Nothing that changes what runs.** `PATH`, `PYTHON*`, `LD_*`, `DYLD_*`,
  `NODE_OPTIONS` and the like decide which program starts or which code it
  loads first; a file of keys has no business setting them. They are refused
  and reported by name.

Standard library only; the format is the usual one: `NAME=value`, an optional
`export`, `#` comments, optional single or double quotes.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, NamedTuple

__all__ = ["load_env_files", "parse_env", "find_env_file", "LoadedEnv"]

PROJECT_MARKERS = (".git", "pyproject.toml")
"""What makes a directory the root of a project, for the purposes of the search."""

_REFUSED = re.compile(
    r"^(PATH|PATHEXT|COMSPEC|SYSTEMROOT|WINDIR|PYTHON\w*|LD_\w+|DYLD_\w+|NODE_OPTIONS|NODE_PATH"
    r"|PERL5LIB|PERL5OPT|RUBYOPT|RUBYLIB|JAVA_TOOL_OPTIONS|_JAVA_OPTIONS|BASH_ENV|ENV"
    r"|EVOLVEKIT_NO_DOTENV)$",
    re.IGNORECASE,  # Windows environment names are case-insensitive
)
"""Variables that decide which program starts, or which code it loads before
its own. Every evaluator inherits the environment, so these are never taken
from a `.env`."""

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
        elif value and value[0] in "\"'":  # `"" in "..."` is true: an empty value must not get here
            # Quoted, with something after the closing quote: a trailing comment.
            closing = value.find(value[0], 1)
            if closing > 0:
                value = value[1:closing]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[name] = value
    return values


class LoadedEnv(NamedTuple):
    path: Path
    names: list[str]
    """What the file set, by name."""
    refused: list[str]
    """What the file tried to set and may not (`_REFUSED`), by name."""


def _home() -> Path | None:
    try:
        return Path.home().resolve()
    except (OSError, RuntimeError):
        return None


def _is_project_root(directory: Path) -> bool:
    return any((directory / marker).exists() for marker in PROJECT_MARKERS)


def find_env_file(start: Path) -> Path | None:
    """The nearest `.env` in `start` or above it, inside `start`'s project.

    The project is the nearest directory at or above `start` that holds `.git`
    or `pyproject.toml`, looking no higher than just below the home directory.
    With no project, `start` alone is searched.
    """
    try:
        here = Path(start).resolve()
    except OSError:
        return None
    home = _home()
    chain = [here, *here.parents]
    searched = chain[:1]
    for depth, directory in enumerate(chain):
        if depth and home is not None and (directory == home or directory in home.parents):
            break  # never read the home directory, or above it, from below
        if _is_project_root(directory):
            searched = chain[: depth + 1]
            break
    for directory in searched:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_env_files(starts: Iterable[str | Path]) -> list[LoadedEnv]:
    """Load the nearest `.env` above each of `starts` into `os.environ`.

    Returns a `LoadedEnv` for every file that set or tried to set something. A
    variable that already exists is left alone, and so is one whose value in
    the file is empty -- an untouched copy of `.env.example` sets nothing.
    """
    loaded: list[LoadedEnv] = []
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
        names: list[str] = []
        refused: list[str] = []
        for name, value in values.items():
            if not value:
                continue
            if _REFUSED.match(name):
                refused.append(name)
            elif name not in os.environ:
                os.environ[name] = value
                names.append(name)
        if names or refused:
            loaded.append(LoadedEnv(path, names, refused))
    return loaded


