"""The frozen entry: what both exes of the installed app run.

The app's folder holds two exes, and PyInstaller runs this module as the
script of both:

- `evolvekit-app.exe`, windowless: a double-click and the logon task. With no
  arguments it starts the app (its home under the user profile, the browser
  opened); when an app already runs for that home, its browser is opened
  instead of a second app.
- `evolvekit-cli.exe`, with a console: the command line (every `evolvekit ...`
  command works as `evolvekit-cli.exe ...`), and evolvekit's Python, which
  runs `-m MODULE`, `-c CODE`, `--script FILE` and `FILE.py` as python does.

The rule, on the first argument: `-m` / `-c` / `--script` / a `*.py` file are
python's forms; a command of the CLI, `--version`, `-h`, `--help` are the CLI
(a bare `stop` on `evolvekit-app.exe` is the app's `stop`); anything else,
nothing included, is the app (`evolvekit app ...`). Frozen, a CLI command that
would write into the app's own folder is refused.
"""

from __future__ import annotations

import argparse
import builtins
import contextlib
import io
import os
import runpy
import sys
from pathlib import Path

from evolvekit import cli
from evolvekit.resources import APP_EXE, bundle_dir, is_frozen

__all__ = ["WRITE_TARGETS", "main", "writes_into_bundle"]

WRITE_TARGETS: dict[tuple[str, str | None], tuple[str, ...]] = {
    ("init", None): ("directory",),
    ("run", None): ("run_dir",),
    ("confirm", None): ("run_dir",),
    ("stop", None): ("run_dir",),
    ("export", None): ("out",),
    ("dashboard", None): ("export",),
    ("skill", "install"): ("dir",),
    ("harness", "new"): ("directory",),
    ("harness", "pack"): ("out",),
    ("study", "new"): ("dir",),
}
"""The parsed attributes each command writes to; the key's second part is the
subcommand (`harness_command`, `skill_command`, `study_command`). A new command
that writes beside the working folder belongs here."""

_REFUSED = ("error: this is the app's own folder: run evolvekit-cli.exe from a folder of yours "
            "(its full path works from anywhere)")
_CLI_FLAGS = ("--version", "-h", "--help")


def _relaxed(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """`parser` with no option required, in every subparser too: a target is
    judged although a required option (`confirm --seeds`) is missing, and
    `cli.main` then says what is missing, in a folder of the user's."""
    for action in parser._actions:
        if action.option_strings:
            action.required = False
        if isinstance(action, argparse._SubParsersAction):
            for child in action.choices.values():
                _relaxed(child)
    return parser


def writes_into_bundle(argv: list[str]) -> bool:
    """Frozen: would this CLI command write into the app's own folder?"""
    if not is_frozen() or not argv or argv[0] not in {command for command, _ in WRITE_TARGETS}:
        return False
    if any(flag in argv for flag in _CLI_FLAGS):  # parsing would print them, and cli.main again
        return False
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            args, _ = _relaxed(cli.build_parser()).parse_known_args(argv)
    except SystemExit:  # cli.main says what is wrong, once
        return False
    command = args.command
    attributes = WRITE_TARGETS.get((command, getattr(args, f"{command}_command", None)))
    if attributes is None:
        return False
    targets = [value for name in attributes if isinstance(value := getattr(args, name, None), str)]
    if (command, getattr(args, "harness_command", None)) == ("harness", "pack") and not isinstance(args.out, str):
        targets.append(str((Path.cwd() / args.directory).resolve().parent))  # the zip lands beside the harness
    bundle = bundle_dir().resolve()  # type: ignore[union-attr]
    for value in targets:
        target = (Path.cwd() / value).resolve()
        if target == bundle or bundle in target.parents:
            return True
    return False


def _is_app_exe() -> bool:
    return Path(sys.executable).name.lower() == APP_EXE.lower()


def _frozen_process() -> None:
    """No bytecode in the bundle, and streams for the windowless exe (CLI code flushes them)."""
    sys.dont_write_bytecode = True
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _usage(form: str) -> int:
    print(f"usage: {form}", file=sys.stderr)
    return 2


def _run_module(argv: list[str]) -> int:
    if not argv:
        return _usage("-m MODULE [args]")
    sys.argv = [argv[0], *argv[1:]]
    runpy.run_module(argv[0], run_name="__main__", alter_sys=True)
    return 0


def _run_code(argv: list[str]) -> int:
    if not argv:
        return _usage("-c CODE [args]")
    sys.argv = ["-c", *argv[1:]]
    exec(compile(argv[0], "<string>", "exec"), {"__name__": "__main__", "__builtins__": builtins})
    return 0


def _run_script(argv: list[str]) -> int:
    if not argv:
        return _usage("--script FILE [args]")
    script = argv[0]
    sys.argv = [script, *argv[1:]]
    sys.path.insert(0, os.path.dirname(os.path.abspath(script)))  # as `python FILE`: a sibling module imports
    runpy.run_path(script, run_name="__main__")
    return 0


def _app_home(argv: list[str]) -> Path:
    """The home the app form serves: `--home`'s value, else the default home."""
    from evolvekit.harness.library import default_home

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--home")
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            known, _ = parser.parse_known_args(argv)
    except SystemExit:  # `--home` without a value: cli.main says so
        known = argparse.Namespace(home=None)
    return Path(known.home).expanduser() if known.home else default_home()


def main(argv: list[str] | None = None) -> int:
    """Dispatch one start of an exe; a `SystemExit` of what runs passes through."""
    argv = list(sys.argv[1:] if argv is None else argv)
    frozen = is_frozen()
    if frozen:
        _frozen_process()
    first = argv[0] if argv else ""
    if first == "-m":
        return _run_module(argv[1:])
    if first == "-c":
        return _run_code(argv[1:])
    if first == "--script":
        return _run_script(argv[1:])
    if first.lower().endswith(".py"):
        return _run_script(argv)
    bare_app_stop = argv == ["stop"] and _is_app_exe()
    if (first in cli._COMMANDS or first in _CLI_FLAGS) and not bare_app_stop:
        if frozen and writes_into_bundle(argv):
            print(_REFUSED, file=sys.stderr)
            return 2
        return cli.main(argv)
    if frozen:  # the app works in the home: nothing relative lands in the app's folder
        home = _app_home(argv)
        home.mkdir(parents=True, exist_ok=True)
        os.chdir(home)
    return cli.main(["app", *argv])


if __name__ == "__main__":
    sys.exit(main())
