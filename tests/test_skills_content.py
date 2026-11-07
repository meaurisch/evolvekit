"""What the skills say: the static half of their tests (design §4.4)."""

from __future__ import annotations

import argparse
import re
import shlex
from pathlib import Path

import pytest

from evolvekit.app.verdict import DECISIONS
from evolvekit.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "evolvekit" / "skills"
REFERENCE = SKILLS / "shared" / "evolvekit.md"
STUDY = SKILLS / "evolvekit-study" / "SKILL.md"
HARNESS = SKILLS / "evolvekit-harness" / "SKILL.md"

SECTIONS = ["Words", "Where things live", "Commands", "The verdict document", "Rules"]
NEVER_SAID = re.compile(r"\b(kpis?|seeds?|generations?|instances?|configs?|baselines?)\b", re.I)
PLACEHOLDERS = {"SLUG": "gate-study", "NAME": "Gate", "ID": "demo-tour", "T": "tune", "PATH": "solver.py",
                "DIR": "harness", "FILE": "cases/a.json", "N": "2", "S": "600"}
GROUPS = ("study", "harness", "skill")
VERDICT_KEYS = ("schema", "study", "run", "phase", "decision", "reason", "line", "advice", "measure", "held_back",
                "mean_pct", "ci95_pct", "worst_pct", "chance", "chance_words", "levels", "learned_from", "rule",
                "by_case", "changed", "files", "started_at", "finished_at", "elapsed_s", "rounds", "combinations")
REPORTING_RULE = "Report only the verdict's line and its advice, word for word, and add no sentence that claims more."
PRE_VERDICT_RULE = 'Until the verdict exists, write no "better", "improved", "faster" or "cheaper", and no percentage.'
SDK_RULE = "Never edit `evk_harness.py`"
EXIT_CODES = ("Exit codes: 0 success; 1 a problem the message names; 2 a configuration or validation error, "
              "and `study check` with problems; 4 a timeout.")


def test_command_variants_expands_usage():
    assert command_variants("evolvekit study start SLUG [--wait [--timeout S]] [--json]") == [
        ["study", "start", "gate-study", "--wait", "--timeout", "600", "--json"]]
    assert sorted(command_variants("python -m evolvekit harness discover (--program PATH | --python PATH --module NAME)")) == [
        ["harness", "discover", "--program", "solver.py"],
        ["harness", "discover", "--python", "solver.py", "--module", "Gate"]]
    assert sorted(command_variants("evolvekit study report SLUG [--run ID] [--markdown \\| --json]")) == [
        ["study", "report", "gate-study", "--run", "demo-tour", "--json"],
        ["study", "report", "gate-study", "--run", "demo-tour", "--markdown"]]
    assert command_variants('evolvekit study cases SLUG --add "cases/*.json" --hold-back N  # all of them') == [
        ["study", "cases", "gate-study", "--add", "cases/*.json", "--hold-back", "2"]]
    assert command_variants("evolvekit study report SLUG --markdown > evolvekit-report.md") == [
        ["study", "report", "gate-study", "--markdown"]]
    assert command_variants("evolvekit study cases SLUG --add FILE...") == [
        ["study", "cases", "gate-study", "--add", "cases/a.json"]]


def test_reference_has_the_sections_in_order():
    text = _text(REFERENCE)
    assert re.findall(r"^## (.+?)\s*$", text, re.M) == SECTIONS
    assert len(text.splitlines()) <= 200


def test_reference_commands_parse(capsys):
    commands = commands_in(_section(_text(REFERENCE), "Commands"))
    assert len(commands) >= 20, "the Commands table names too few commands"
    for command in commands:
        for words in command_variants(command):
            _assert_parses(words, capsys)


def test_reference_covers_every_command():
    named = {tuple(words[:2] if words[0] in GROUPS else words[:1])
             for command in commands_in(_text(REFERENCE)) for words in command_variants(command)}
    parser = build_parser()
    wanted = {("study", name) for name in _choices(parser, "study")}
    wanted |= {("doctor",), ("harness", "discover"), ("harness", "new"), ("harness", "check"),
               ("harness", "list"), ("skill", "list"), ("skill", "install")}
    assert wanted - named == set()


def test_reference_states_the_exit_codes():
    commands = _section(_text(REFERENCE), "Commands")
    assert EXIT_CODES in commands
    assert "0 all pass, 1 warnings only, 2 failures" in commands  # harness check
    assert "0 when every line is ✓, else 1" in commands          # doctor


def test_reference_names_every_verdict_key():
    section = _section(_text(REFERENCE), "The verdict document")
    spans = set(re.findall(r"`([^`]+)`", section))
    assert set(VERDICT_KEYS) - spans == set()
    assert set(DECISIONS) - spans == set()


def test_reference_vocabulary_matches_app():
    text = _text(REFERENCE)
    words = _section(text, "Words")
    rows = [line for line in words.splitlines() if line.startswith("|") and not set(line) <= set("|-: ")]
    assert [c.strip() for c in rows[0].strip().strip("|").split("|")] == ["Say", "Meaning", "Not"]
    nots = " ".join(row.strip().strip("|").split("|")[-1] for row in rows[1:])
    for word in ("KPI", "seed", "generation", "instance", "config", "baseline"):
        assert re.search(rf"\b{word}\b", nots, re.I), f"the Words table does not say to avoid {word!r}"
    for row in rows:
        text = text.replace(row, row.strip().strip("|").rsplit("|", 1)[0])
    assert NEVER_SAID.findall(_prose(text)) == []


def test_reference_has_the_rules():
    rules = _section(_text(REFERENCE), "Rules")
    for sentence in (REPORTING_RULE, PRE_VERDICT_RULE, SDK_RULE):
        assert sentence in rules
    assert "comparability trap" in rules and "every value in range" in rules.lower()


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _body(path: Path) -> str:
    text = _text(path)
    if not text.startswith("---"):
        return text
    return text.split("---", 2)[2]


def _prose(text: str) -> str:
    text = re.sub(r"^```.*?^```[ \t]*$", "", text, flags=re.S | re.M)
    return re.sub(r"`[^`\n]*`", "", text)


def _section(text: str, title: str) -> str:
    found = re.search(rf"^## {re.escape(title)}\s*$(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert found, f"no section {title!r}"
    return found.group(1)


def commands_in(text: str) -> list[str]:
    starts = ("evolvekit ", "python -m evolvekit ")
    found = [span for span in re.findall(r"`([^`\n]+)`", text) if span.startswith(starts)]
    for block in re.findall(r"^```[^\n]*\n(.*?)^```", text, re.S | re.M):
        found += [line.strip() for line in block.splitlines() if line.strip().startswith(starts)]
    return found


def command_variants(command: str) -> list[list[str]]:
    command = command.replace("\\|", "|")
    command = re.split(r"\s+[#>]\s", command, maxsplit=1)[0].strip()
    command = re.sub(r"^(python -m )?evolvekit\s+", "", command)
    group = re.compile(r"[\[(]([^\[\]()]*)[\])]")  # an innermost [...] or (...)
    pending, done = [command], []
    while pending:
        text = pending.pop()
        found = group.search(text)
        if found is None:
            done.append(text)
            continue
        for alternative in found.group(1).split("|"):
            pending.append(text[:found.start()] + alternative.strip() + text[found.end():])
    return [[PLACEHOLDERS.get(word.removesuffix("..."), word) for word in shlex.split(text)] for text in reversed(done)]


def _choices(parser: argparse.ArgumentParser, *path: str) -> set[str]:
    for name in path:
        action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        parser = action.choices[name]
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return set(action.choices)


def _assert_parses(words: list[str], capsys) -> None:
    try:
        build_parser().parse_args(words)
    except SystemExit:
        pytest.fail(f"does not parse: {words}\n{capsys.readouterr().err}")
    prefix = words[:2] if words[0] in GROUPS else words[:1]
    with pytest.raises(SystemExit) as exit_:
        build_parser().parse_args([*prefix, "--help"])
    assert exit_.value.code == 0
    capsys.readouterr()
