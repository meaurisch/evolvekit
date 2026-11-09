"""`evolvekit harness discover`: what an application exposes, before a harness exists.

A pointer to where to look, labelled as such: for a program, the flags its
`--help` lists; for a Python package, the parameters with defaults of its
public functions and classes. Nothing of the application runs beyond
`--version`, `--help` and an import.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from evolvekit.harness.probe import program_command

_SKIPPED = {"help", "version"}
_DOTTED = re.compile(r"\d+(?:\.\d+)+")
_DEFAULT = re.compile(r"[(\[]\s*(?:default|defaults to)\s*[:=]?\s*([^)\]]*?)\s*[)\]]", re.IGNORECASE)
_FLAG = re.compile(r"^(--?[A-Za-z0-9][\w.-]*)(.*)$")
_USAGE_FLAG = re.compile(r"(?<![\w-])(--?[A-Za-z0-9][\w-]*)")
_WORD = re.compile(r"(?<![\w-])([A-Za-z_][\w-]*)")

POINTER_NOTE = ("candidate settings read from {what}: a pointer to where to look, not a harness; "
                "check each one before a harness relies on it")


# --------------------------------------------------------------------------- help text


def _strip_brackets(text: str) -> str:
    """The usage text with everything inside [...] removed (nesting counted)."""
    out, depth = [], 0
    for char in text:
        if char == "[":
            depth += 1
        elif char == "]":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
    return "".join(out)


def _usage(lines: list[str]) -> str:
    """The usage block: the `usage:` line and its indented continuation lines."""
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("usage:"):
            block = [line.strip()[len("usage:"):]]
            for more in lines[i + 1:]:
                if not more.strip() or not more[:1].isspace():
                    break
                block.append(more.strip())
            return " ".join(block)
    return ""


def _value(raw: str, *, flag: bool) -> object:
    """A default as the help printed it, read as the value it most likely is."""
    raw = raw.strip().strip("'\"")
    if raw in ("None", "null", ""):
        return None
    if flag and raw in ("True", "False", "true", "false"):
        return raw.lower() == "true"
    for kind in (int, float):
        try:
            return kind(raw)
        except ValueError:
            pass
    return raw


def _type_of(takes_value: bool, choices: list[str] | None, default: object) -> str:
    if choices:
        return "choice"
    if not takes_value:
        return "bool"
    if isinstance(default, bool):
        return "text"
    if isinstance(default, int):
        return "int"
    if isinstance(default, float):
        return "float"
    return "text"


def _spec(spec: str) -> tuple[list[str], str]:
    """`-c, --crates=COUNT` -> (["-c", "--crates"], "COUNT")."""
    flags, value = [], ""
    for part in re.split(r",\s*(?=-)", spec):  # never inside {a,b}
        match = _FLAG.match(part.strip())
        if not match:
            continue
        flags.append(match.group(1))
        rest = match.group(2).strip().lstrip("[").lstrip("=").rstrip("]").strip()
        value = value or rest
    return flags, value


def parse_help(text: str) -> list[dict]:
    """Candidate settings from an argparse- or GNU-style `--help` text (pure).

    An option line starts with `-` (after indentation); its help follows after
    two spaces or on the more-indented lines below it. Positionals are read
    from an argparse `positional arguments:` section only."""
    lines = text.expandtabs().splitlines()
    usage = _usage(lines)
    outside = _strip_brackets(usage)
    required_flags = set(_USAGE_FLAG.findall(outside))
    required_words = set(_WORD.findall(outside))
    settings: list[dict] = []
    seen: set[str] = set()
    section = ""
    current: dict | None = None

    in_usage = False  # the usage block, wrapped or not, is read by _usage only
    for line in lines:
        stripped = line.strip()
        if not stripped:
            current, in_usage = None, False
            continue
        if stripped.lower().startswith("usage:"):
            current, in_usage = None, True
            continue
        if not line[:1].isspace():
            current, in_usage = None, False
            if stripped.endswith(":"):
                section = stripped[:-1].lower()
            continue
        if in_usage:
            continue
        parts = re.split(r"\s{2,}", stripped, maxsplit=1)
        head, help_text = parts[0], (parts[1] if len(parts) > 1 else "")
        if head.startswith("-"):
            flags, value = _spec(head)
            if not flags:
                current = None
                continue
            longs = [f for f in flags if f.startswith("--")]
            name = (longs[0] if longs else flags[0]).lstrip("-")
            current = {"name": name, "flags": flags, "value": value, "help": help_text,
                       "required": any(f in required_flags for f in flags)}
        elif section.startswith("positional") and re.fullmatch(r"[A-Za-z_][\w-]*|\{[^}]*\}", head):
            current = {"name": head, "flags": [], "value": head, "help": help_text,
                       "required": head in required_words, "positional": True}
        elif current is not None:
            current["help"] = (current["help"] + " " + stripped).strip()
            continue
        else:
            continue
        if current["name"] in _SKIPPED or current["name"] in seen or "-h" in current["flags"]:
            current["skip"] = True
        seen.add(current["name"])
        settings.append(current)

    found = []
    for item in settings:
        if item.get("skip"):
            continue
        value = item["value"]
        choices = None
        braces = re.fullmatch(r"\{(.+)\}", value)
        if braces:
            choices = [c.strip() for c in braces.group(1).split(",")]
        takes_value = bool(value)
        match = _DEFAULT.search(item["help"])
        default = _value(match.group(1), flag=not takes_value) if match else None
        if choices is None and item.get("positional"):
            takes_value = True
        found.append({
            "name": item["name"],
            "type": _type_of(takes_value, choices, default),
            "default": default,
            "choices": choices,
            "required": bool(item["required"]),
            "help": item["help"] or None,
            "source": "--help",
        })
    return found


# --------------------------------------------------------------------------- program


def _exists(path: str) -> bool:
    return Path(path).exists() or shutil.which(path) is not None


def _ask(argv: list[str], flag: str, timeout_s: float, name: str) -> tuple[str | None, str | None]:
    """The text a program printed for `flag`, or a note saying why there is none."""
    try:
        done = subprocess.run(argv + [flag], capture_output=True, text=True, timeout=timeout_s,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, f"{name} did not answer {flag} within {timeout_s:g} s"
    except OSError as exc:
        return None, f"{name} cannot be started: {exc.strerror or exc}"
    return (done.stdout if done.stdout.strip() else done.stderr), None


def _first_line(text: str | None) -> str | None:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return None


def _version_number(version: str | None) -> str | None:
    match = _DOTTED.search(version or "")
    return match.group(0) if match else None


def discover_program(path: str, *, timeout_s: float = 10.0) -> dict:
    """What the program at `path` says about itself in `--version` and `--help`."""
    if not _exists(path):
        raise ValueError(f"there is nothing at {path}")
    # resolved: a relative path such as tools/solve.exe is not found by Windows' CreateProcess
    argv, name = program_command(str(Path(path).resolve()) if Path(path).exists() else path), Path(path).name
    notes: list[str] = []
    version_text, note = _ask(argv, "--version", timeout_s, name)
    if note:
        notes.append(note)
    version = _first_line(version_text)
    if note is None and version is None:
        notes.append(f"{name} printed nothing for --version")
    help_text, note = _ask(argv, "--help", timeout_s, name)
    settings: list[dict] = []
    if note:
        notes.append(note)
    else:
        settings = parse_help(help_text or "")
        notes.append(POINTER_NOTE.format(what="--help") if settings else
                     f"found no settings in the --help text of {name}: read its own documentation for what it accepts")
    return {
        "application": {"kind": "program", "path": path, "version": version,
                        "version_number": _version_number(version)},
        "settings": settings,
        "notes": notes,
    }


# --------------------------------------------------------------------------- python

_SCRIPT = r'''
import dataclasses, inspect, json, sys, typing

MARK = "evolvekit-discover:"
name = sys.argv[1]
out = {"version": None, "settings": [], "error": None}
real_stdout, sys.stdout = sys.stdout, sys.stderr

def safe(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else repr(value)
    if isinstance(value, (list, tuple)):
        return [safe(v) for v in value]
    try:
        json.dumps(value)
        return value
    except Exception:
        return repr(value)

def words(annotation):
    if annotation is inspect.Parameter.empty or annotation is dataclasses.MISSING:
        return None
    if isinstance(annotation, str):
        return annotation
    if isinstance(annotation, type):
        return annotation.__name__
    return inspect.formatannotation(annotation)

def literal(annotation):
    try:
        if typing.get_origin(annotation) is typing.Literal:
            return [safe(a) for a in typing.get_args(annotation)]
    except Exception:
        pass
    return None

def doc(obj):
    text = (inspect.getdoc(obj) or "").strip()
    return " ".join(text.split("\n\n")[0].split()) if text else None

def add(qualname, param, *, default, has_default, annotation, help):
    out["settings"].append({
        "name": qualname + "." + param, "source": name + "." + qualname,
        "default": safe(default) if has_default else None, "required": not has_default,
        "annotation": words(annotation), "choices": literal(annotation), "help": help,
    })

try:
    import importlib
    module = importlib.import_module(name)
    version = getattr(module, "__version__", None)
    if version is None:
        try:
            import importlib.metadata as metadata
            version = metadata.version(name.split(".")[0])
        except Exception:
            version = None
    out["version"] = None if version is None else str(version)
    for attr in sorted(vars(module)):
        obj = getattr(module, attr)
        owner = getattr(obj, "__module__", None) or ""
        if attr.startswith("_") or not (owner == name or owner.startswith(name + ".")):
            continue
        qualname = getattr(obj, "__qualname__", attr)
        try:
            if inspect.isclass(obj) and dataclasses.is_dataclass(obj):
                for field in dataclasses.fields(obj):
                    if not field.init:
                        continue
                    if field.default is not dataclasses.MISSING:
                        add(qualname, field.name, default=field.default, has_default=True,
                            annotation=field.type, help=doc(obj))
                    elif field.default_factory is not dataclasses.MISSING:
                        factory = getattr(field.default_factory, "__name__", "a factory")
                        add(qualname, field.name, default=None, has_default=True, annotation=field.type,
                            help="default made by " + factory)
                    else:
                        add(qualname, field.name, default=None, has_default=False, annotation=field.type,
                            help=doc(obj))
            elif inspect.isclass(obj):
                params = [p for p in inspect.signature(obj).parameters.values() if p.kind is p.KEYWORD_ONLY]
                for p in params:
                    has = p.default is not p.empty
                    add(qualname, p.name, default=p.default if has else None, has_default=has,
                        annotation=p.annotation, help=doc(obj))
            elif inspect.isfunction(obj) or inspect.isbuiltin(obj):
                for p in inspect.signature(obj).parameters.values():
                    if p.default is p.empty or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                        continue
                    add(qualname, p.name, default=p.default, has_default=True,
                        annotation=p.annotation, help=doc(obj))
        except (TypeError, ValueError):
            continue
except BaseException as exc:
    out["error"] = "%s: %s" % (type(exc).__name__, exc)

sys.stdout = real_stdout
print(MARK + json.dumps(out))
'''

_MARK = "evolvekit-discover:"


def _python_type(annotation: str | None, choices: list | None, default: object) -> str:
    if choices:
        return "choice"
    words = (annotation or "").replace(" ", "")
    words = re.sub(r"^Optional\[(.*)\]$", r"\1", words)
    words = re.sub(r"\|None$|^None\|", "", words)
    named = {"bool": "bool", "int": "int", "float": "float", "str": "text"}
    if words in named:
        return named[words]
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int):
        return "int"
    if isinstance(default, float):
        return "float"
    return "text"


def discover_python(python: str, module: str, *, timeout_s: float = 30.0) -> dict:
    """What `module`, imported by the interpreter `python`, exposes as parameters with defaults."""
    if not _exists(python):
        raise ValueError(f"there is nothing at {python}")
    name = Path(python).name
    notes: list[str] = []
    answer: dict = {}
    # a relative path such as .venv/Scripts/python.exe is not found by Windows' CreateProcess
    executable = str(Path(python).resolve()) if Path(python).exists() else python
    try:
        done = subprocess.run([executable, "-c", _SCRIPT, module], capture_output=True, text=True,
                              timeout=timeout_s, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL)
        lines = [line for line in done.stdout.splitlines() if line.startswith(_MARK)]
        if lines:
            answer = json.loads(lines[-1][len(_MARK):])
        else:
            notes.append(f"{name} is not a Python interpreter, or it stopped before it answered")
    except subprocess.TimeoutExpired:
        notes.append(f"{name} did not finish importing {module} within {timeout_s:g} s")
    except OSError as exc:
        notes.append(f"{name} cannot be started: {exc.strerror or exc}")
    except ValueError:
        notes.append(f"{name} answered in a form evolvekit cannot read")
    if answer.get("error"):
        notes.append(f"{name} cannot import {module}: {answer['error']}")
    settings = [{
        "name": s["name"],
        "type": _python_type(s.get("annotation"), s.get("choices"), s.get("default")),
        "default": s.get("default"),
        "choices": s.get("choices"),
        "required": bool(s.get("required")),
        "help": s.get("help"),
        "source": s["source"],
        "annotation": s.get("annotation"),
    } for s in answer.get("settings", [])]
    if answer and not answer.get("error"):
        notes.append(POINTER_NOTE.format(what=f"the public functions and classes of {module}") if settings else
                     f"found no functions or classes with parameters that have defaults in {module}")
    version = answer.get("version")
    return {
        "application": {"kind": "python", "path": python, "module": module, "version": version,
                        "version_number": _version_number(version)},
        "settings": settings,
        "notes": notes,
    }


# --------------------------------------------------------------------------- words


def render(found: dict) -> str:
    """The document for people: the application, one line per setting, the notes."""
    app = found["application"]
    what = app["path"] + (f" -m {app['module']}" if app.get("module") else "")
    lines = [f"{app['kind']} {what}: {app['version'] or 'no version'}"]
    width = max([len(s["name"]) for s in found["settings"]] + [4])
    for s in found["settings"]:
        bits = [s["type"] + (" " + "|".join(map(str, s["choices"])) if s["choices"] else "")]
        if s["default"] is not None:
            bits.append(f"default {s['default']}")
        if s["required"]:
            bits.append("required")
        line = f"  {s['name']:<{width}}  " + ", ".join(bits)
        lines.append(line + (f"  -- {s['help']}" if s["help"] else ""))
    lines += [f"note: {note}" for note in found["notes"]]
    return "\n".join(lines)
