"""Is this the application a harness needs? Asked of the application itself.

A `python` application is an interpreter in which the harness's module
imports: it is asked, in a child process with a 10 s timeout, for its own
version and the module's. A `program` is run with the harness's probe command
(`{app} --version`), and its output must match the harness's `expect`, whose
first group is the version. A `.py` program is run with the interpreter that
runs evolvekit. Either way the answer is one line a person can read:
"PyVRP 0.14.0 found", "Python 3.13, no pyvrp".
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from evolvekit.harness.manifest import Harness

__all__ = ["Probe", "probe_application", "version_matches", "version_words", "program_command"]

PROBE_TIMEOUT_S = 10.0

_SNIPPET = r"""
import json, sys
out = {"python": "%d.%d.%d" % tuple(sys.version_info[:3]), "executable": sys.executable}
name = sys.argv[1]
if name:
    try:
        import importlib
        module = importlib.import_module(name)
        version = None
        try:
            import importlib.metadata as metadata
            version = metadata.version(name)
        except Exception:
            version = getattr(module, "__version__", None)
        out.update(found=True, version=version)
    except Exception as exc:
        out.update(found=False, error="%s: %s" % (type(exc).__name__, exc))
print(json.dumps(out))
"""


@dataclass(frozen=True)
class Probe:
    ok: bool
    message: str
    """One line for people: what was found, or why it is not the application."""
    version: str | None = None
    python: str | None = None


def _version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for piece in re.split(r"[.+-]", text):
        match = re.match(r"\d+", piece)
        if not match:
            break
        parts.append(int(match.group()))
    return tuple(parts)


def version_words(specifier: str) -> str:
    """A specifier in words: ">=0.14,<0.15" -> "0.14.x", "==1.2.*" -> "1.2.x",
    ">=2" -> "2 or later"; anything else as it is."""
    clauses = [c.strip() for c in specifier.split(",") if c.strip()]
    ops = {}
    for clause in clauses:
        match = re.match(r"^(~=|==|!=|>=|<=|>|<)\s*([0-9][0-9.*]*)$", clause)
        if not match:
            return specifier
        ops[match.group(1)] = match.group(2)
    if set(ops) == {">=", "<"}:
        low, high = _version_tuple(ops[">="]), _version_tuple(ops["<"])
        if len(low) >= 2 and len(high) == 2 and low[0] == high[0] and high[1] == low[1] + 1 and not any(low[2:]):
            return f"{low[0]}.{low[1]}.x"
        return f"{ops['>=']} or later, before {ops['<']}"
    if set(ops) == {"=="} and ops["=="].endswith(".*"):
        return ops["=="][:-2] + ".x"
    if set(ops) == {">="}:
        return f"{ops['>=']} or later"
    return specifier


def version_matches(version: str, specifier: str) -> bool:
    """`version` against a specifier such as ">=0.14,<0.15" or "==1.2.*".
    Numbers only: pre-release tags are ignored, which errs on the side of
    accepting an application rather than refusing it."""
    if not specifier.strip():
        return True
    have = _version_tuple(version)
    for clause in specifier.split(","):
        clause = clause.strip()
        match = re.match(r"^(~=|==|!=|>=|<=|>|<)\s*([0-9][0-9.*]*)$", clause)
        if not match:
            return False
        op, raw = match.groups()
        if raw.endswith(".*"):
            prefix = _version_tuple(raw[:-2])
            same = have[: len(prefix)] == prefix
            if (op == "==" and not same) or (op == "!=" and same):
                return False
            continue
        want = _version_tuple(raw)
        width = max(len(have), len(want))
        a, b = have + (0,) * (width - len(have)), want + (0,) * (width - len(want))
        if op == "~=":
            if not (a >= b and a[: len(want) - 1] == want[: len(want) - 1]):
                return False
        elif not {"==": a == b, "!=": a != b, ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[op]:
            return False
    return True


def program_command(app: str) -> list[str]:
    """How to start a program: a `.py` file with evolvekit's interpreter."""
    return [sys.executable, app] if app.lower().endswith(".py") else [app]


def probe_application(harness: Harness, app: str, *, timeout: float = PROBE_TIMEOUT_S) -> Probe:
    """Ask `app` whether it is the application `harness` needs."""
    spec = harness.application
    if not app or not Path(app).exists():
        return Probe(False, f"there is nothing at {app or '(no path)'}")
    if spec.kind == "python":
        try:
            done = subprocess.run(
                [app, "-c", _SNIPPET, spec.module], capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return Probe(False, f"{Path(app).name} did not answer within {timeout:g} s")
        except OSError as exc:
            return Probe(False, f"{Path(app).name} cannot be started: {exc.strerror or exc}")
        try:
            answer = json.loads(done.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError):
            return Probe(False, f"{Path(app).name} is not a Python interpreter")
        python = answer.get("python")
        short = ".".join(str(python).split(".")[:2])
        if not spec.module:
            return Probe(True, f"Python {python}", python=python)
        if not answer.get("found"):
            return Probe(False, f"Python {short}, no {spec.module}", python=python)
        version = answer.get("version") or "?"
        if spec.version and not version_matches(version, spec.version):
            return Probe(False, f"{harness.title} {version} found, and this harness needs {harness.title} {version_words(spec.version)}",
                         version=version, python=python)
        return Probe(True, f"{harness.title} {version} found", version=version, python=python)
    tokens = shlex.split(spec.probe, posix=True)
    argv: list[str] = []
    for token in tokens:
        argv += program_command(app) if token == "{app}" else [token.replace("{app}", app)]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return Probe(False, f"{Path(app).name} did not answer `{spec.probe}` within {timeout:g} s")
    except OSError as exc:
        return Probe(False, f"{Path(app).name} cannot be started: {exc.strerror or exc}")
    said = (done.stdout or "") + (done.stderr or "")
    if spec.expect:
        match = re.search(spec.expect, said)
        if not match:
            first = said.strip().splitlines()[0] if said.strip() else "nothing"
            return Probe(False, f"{Path(app).name} does not look like {spec.label}: it said {first[:80]!r}")
        return Probe(True, f"{spec.label} {match.group(1)} found", version=match.group(1))
    if done.returncode != 0:
        return Probe(False, f"{Path(app).name} answered `{spec.probe}` with exit code {done.returncode}")
    return Probe(True, f"{spec.label} found")
