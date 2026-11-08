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
python's forms (python's own `-u`, `-B`, `-X OPT`, `-W OPT` before them are
accepted and ignored); a command of the CLI, `--version`, `-h`, `--help` are
the CLI (a bare `stop` on `evolvekit-app.exe` is the app's `stop`); anything
else, nothing included, is the app (`evolvekit app ...`). Frozen, a CLI command
that would write into the app's own folder is refused, and so is the app form
whose library home is inside it: the app's folder unzipped as the default
home, `<profile>/evolvekit` (`%USERPROFILE%` on Windows), cannot start (the
README says not to unzip it there).

`evolvekit.cli` is imported for the CLI and the app forms only: python's forms
(every helper process the app starts) do not pay for it.
"""

from __future__ import annotations

import argparse
import builtins
import contextlib
import io
import os
import re
import runpy
import sys
from pathlib import Path

from evolvekit import resources

__all__ = ["WRITE_TARGETS", "main", "writes_into_bundle"]

WRITE_TARGETS: dict[tuple[str, str | None], tuple[str, ...]] = {
    ("init", None): ("directory",),
    ("run", None): ("run_dir",),
    ("confirm", None): ("run_dir",),
    ("stop", None): ("run_dir",),
    ("export", None): ("out",),
    ("dashboard", None): ("export",),
    ("leaderboard", None): ("run_dir", "html"),  # its ledger makes the run folder
    ("skill", "install"): ("dir",),
    ("harness", "new"): ("directory",),
    ("harness", "pack"): ("out",),
    ("study", "new"): ("dir",),
    ("study", "preview"): ("directory",),
    ("study", "compile"): ("directory",),
    ("study", "run"): ("directory",),
}
"""The parsed attributes each command writes to; the key's second part is the
subcommand (`harness_command`, `skill_command`, `study_command`). Besides
these, a `--home` given to any command is a target (a library home makes its
folders), and `harness pack` with no `--out` writes beside the harness. A new
command that writes beside the working folder belongs here."""

_FOLDER_OR_NAME = {("study", "preview"), ("study", "compile"), ("study", "run")}
"""Commands whose `directory` is a study folder when one is there, else a study's
name in the library home (`cli._study_root`): only a folder is a target."""

_HOME_COMMANDS = {"app", "doctor", "harness", "study"}
_TARGET_COMMANDS = {command for command, _ in WRITE_TARGETS} | _HOME_COMMANDS

_REFUSED = ("error: this is the app's own folder: run evolvekit-cli.exe from a folder of yours "
            "(its full path works from anywhere)")
_HOME_IN_BUNDLE = ("The library home {home} is inside the app's own folder. Move the app's folder somewhere else "
                   "(for example to Downloads or Desktop), or start it with --home <a folder of yours>.")
_NO_STDIN ="evolvekit-cli.exe cannot read a program from its input; give a file"
_PYTHON_USAGE = "[-u] [-B] [-X OPT] [-W OPT] (-m MODULE | -c CODE | --script FILE | FILE.py) [args]"
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


def _inside(path: Path, bundle: Path) -> bool:
    target = path.resolve()
    return target == bundle or bundle in target.parents


def _targets(args: argparse.Namespace) -> list[Path]:
    """The paths a parsed command writes to, relative ones against the working folder."""
    command = args.command
    key = (command, getattr(args, f"{command}_command", None))
    values = [value for name in WRITE_TARGETS.get(key, ()) if isinstance(value := getattr(args, name, None), str)]
    if key in _FOLDER_OR_NAME:
        values = [value for value in values if (Path.cwd() / value).is_dir()]
    if key == ("harness", "pack") and not isinstance(args.out, str):
        values.append(str((Path.cwd() / args.directory).resolve().parent))  # the zip lands beside the harness
    paths = [Path.cwd() / value for value in values]
    if isinstance(home := getattr(args, "home", None), str):
        paths.append(Path.cwd() / Path(home).expanduser())
    return paths


def writes_into_bundle(argv: list[str]) -> bool:
    """Frozen: would this CLI command write into the app's own folder?"""
    if not resources.is_frozen() or not argv or argv[0] not in _TARGET_COMMANDS:
        return False
    if any(flag in argv for flag in _CLI_FLAGS):  # parsing would print them, and cli.main again
        return False
    from evolvekit import cli

    bundle = resources.bundle_dir().resolve()  # type: ignore[union-attr]
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            args, _ = _relaxed(cli.build_parser()).parse_known_args(argv)
        return any(_inside(path, bundle) for path in _targets(args))
    except SystemExit:  # cli.main says what is wrong, once
        return False
    except Exception:  # the relaxed parser failed (argparse's insides changed): judge the working folder
        return _inside(Path.cwd(), bundle)


def _is_app_exe() -> bool:
    return resources.exe_name().lower() == resources.APP_EXE.lower()


def _frozen_process() -> list[tuple[str, io.TextIOBase]]:
    """No bytecode in the bundle, and streams for the windowless exe (CLI code
    flushes them); the streams opened, to be closed."""
    sys.dont_write_bytecode = True
    opened = []
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            handle = open(os.devnull, "w", encoding="utf-8")
            setattr(sys, name, handle)
            opened.append((name, handle))
    return opened


def _message_box(text: str) -> None:
    """Say `text` where a windowless exe can: a Windows message box."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, "evolvekit", 0x10)  # MB_ICONERROR


def _usage(form: str) -> int:
    print(f"usage: {form}", file=sys.stderr)
    return 2


def _python_flags(argv: list[str]) -> int | None:
    """How many leading arguments are python's own flags (`-u`, `-B`, `-X OPT`,
    `-W OPT`, and `-uB`, `-Xutf8`, `-Wignore`); None when one lacks its value."""
    index = 0
    while index < len(argv):
        token = argv[index]
        if re.fullmatch(r"-[uB]+", token) or (token[:2] in ("-X", "-W") and len(token) > 2):
            index += 1
        elif token in ("-X", "-W"):
            if index + 1 >= len(argv):
                return None
            index += 2
        else:
            break
    return index


def _is_python_form(first: str) -> bool:
    return first in ("-m", "-c", "--script", "-") or first.lower().endswith(".py")


def _run_python(argv: list[str]) -> int:
    first, rest = argv[0], argv[1:]
    if first == "-":
        print(_NO_STDIN, file=sys.stderr)
        return 2
    if first == "-m":
        if not rest:
            return _usage("-m MODULE [args]")
        sys.argv = [rest[0], *rest[1:]]
        sys.path.insert(0, os.getcwd())  # as `python -m`: the current folder first
        runpy.run_module(rest[0], run_name="__main__", alter_sys=True)
        return 0
    if first == "-c":
        if not rest:
            return _usage("-c CODE [args]")
        sys.argv = ["-c", *rest[1:]]
        sys.path.insert(0, "")  # as `python -c`: the current folder, as python names it
        exec(compile(rest[0], "<string>", "exec"), {"__name__": "__main__", "__builtins__": builtins})
        return 0
    if first == "--script":
        if not rest:
            return _usage("--script FILE [args]")
        first, rest = rest[0], rest[1:]
    sys.argv = [first, *rest]
    sys.path.insert(0, os.path.dirname(os.path.abspath(first)))  # as `python FILE`: a sibling module imports
    runpy.run_path(first, run_name="__main__")
    return 0


def _with_absolute_home(argv: list[str]) -> tuple[Path, list[str]]:
    """The home the app form serves, made absolute against the starting folder,
    and `argv` with that absolute value in place of each `--home` value given
    (`--home H`, `--home=H`, or a prefix argparse takes for it, as `--hom H`)."""
    from evolvekit.harness.library import default_home

    out, value, spots = list(argv), None, []
    index = 0
    while index < len(out):
        token = out[index]
        if token == "--":
            break
        name, equals, given = token.partition("=")
        if len(name) >= 4 and "--home".startswith(name):
            if equals:
                spots.append((index, name))
                value = given
            elif index + 1 < len(out) and not out[index + 1].startswith("-"):
                spots.append((index + 1, None))
                value = out[index + 1]
                index += 1
        index += 1
    home = Path(value).expanduser().resolve() if value else default_home()
    for spot, name in spots:
        out[spot] = f"{name}={home}" if name else str(home)
    if not home.is_absolute():  # a relative EVOLVEKIT_HOME: read once, here, not again inside the home
        home = home.resolve()
        out += ["--home", str(home)]
    return home, out


def _say(text: str, *, windowless: bool) -> None:
    """An error of the app form: in a message box when windowless, else on stderr."""
    if windowless:
        _message_box(text)
    else:
        print(text, file=sys.stderr)


def _enter_home(argv: list[str], *, windowless: bool) -> list[str] | int:
    """Frozen, the app form works in its home (D5): `argv` with the home made
    absolute, or the exit code when the home is inside the app's own folder (2:
    the default home, `EVOLVEKIT_HOME` or `--home` alike; the app's folder
    unzipped as `<profile>/evolvekit` is the default home) or cannot be made or
    entered (1), said in a message box when windowless."""
    home, argv = _with_absolute_home(argv)
    if _inside(home, resources.bundle_dir().resolve()):  # type: ignore[union-attr]
        _say(_HOME_IN_BUNDLE.format(home=home), windowless=windowless)
        return 2
    try:
        home.mkdir(parents=True, exist_ok=True)
        os.chdir(home)
    except OSError as exc:
        _say(f"error: the library home {home} cannot be used: {exc.strerror or exc}", windowless=windowless)
        return 1
    return argv


def _dispatch(argv: list[str], *, frozen: bool, windowless: bool) -> int:
    flags = _python_flags(argv)
    if flags is None or (flags and not (len(argv) > flags and _is_python_form(argv[flags]))):
        return _usage(_PYTHON_USAGE)
    if flags or (argv and _is_python_form(argv[0])):
        return _run_python(argv[flags:])
    from evolvekit import cli  # only now: python's forms never import the CLI

    first = argv[0] if argv else ""
    bare_app_stop = argv == ["stop"] and _is_app_exe()
    if (first in cli._COMMANDS or first in _CLI_FLAGS) and not bare_app_stop:
        if frozen and writes_into_bundle(argv):
            print(_REFUSED, file=sys.stderr)
            return 2
        return cli.main(argv)
    if frozen:  # the app works in the home: nothing relative lands in the app's folder
        entered = _enter_home(argv, windowless=windowless)
        if isinstance(entered, int):
            return entered
        argv = entered
    return cli.main(["app", *argv])


def main(argv: list[str] | None = None) -> int:
    """Dispatch one start of an exe; a `SystemExit` of what runs passes through."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not resources.is_frozen():
        return _dispatch(argv, frozen=False, windowless=False)
    windowless = sys.stdout is None or sys.stderr is None
    opened = _frozen_process()
    try:
        return _dispatch(argv, frozen=True, windowless=windowless)
    finally:
        for name, handle in opened:
            if getattr(sys, name) is handle:
                setattr(sys, name, None)
            handle.close()


if __name__ == "__main__":
    sys.exit(main())
