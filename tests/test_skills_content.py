"""What the skills say: the static half of their tests (design §4.4)."""

from __future__ import annotations

import argparse
import re
import shlex
from pathlib import Path

import pytest

from evolvekit.app.store import Home
from evolvekit.app.verdict import DECISIONS
from evolvekit.cli import build_parser
from evolvekit.skills import frontmatter

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "evolvekit" / "skills"
REFERENCE = SKILLS / "shared" / "evolvekit.md"
STUDY = SKILLS / "evolvekit-study" / "SKILL.md"
HARNESS = SKILLS / "evolvekit-harness" / "SKILL.md"

SECTIONS = ["Words", "Where things live", "Commands", "The verdict document", "Rules"]
NEVER_SAID = re.compile(r"\b(kpis?|seeds?|generations?|instances?|configs?|baselines?)\b", re.I)
PLACEHOLDERS = {"SLUG": "gate-study", "NAME": "Gate", "ID": "demo-tour", "T": "tune", "PATH": "solver.py",
                "DIR": "harness", "FILE": "cases/a.json", "N": "2", "H": "2", "S": "600"}
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


# The study skill (Task 2).

SAMPLES = sorted((ROOT / "harnesses" / "demo-tour" / "samples").glob("*.json"))
VENDORS = ("claude", "anthropic", "copilot", "github", "openai", "codex", "cursor", "gemini", "chatgpt",
           "microsoft", "azure", "openrouter")
STUDY_STEPS = ["1. Ground", "2. Interview", "3. Make the study", "4. Show the plan", "5. Start and hand over",
               "6. Report the verdict", "When it goes wrong"]
COMPLETENESS = (
    "Ask only what the request did not say.",
    "A request that answers every question is complete: go on without asking.",
    "One unanswered question stops you with that question, in one message.",
    "In a one-shot run, where no answer can come, say what is missing and end.",
)
HAND_OVER = ("Your study is running: <url>. It tries about N combinations in R rounds and ends around HH:MM; "
             "the answer is on the Study page.")
HAND_OVER_QUEUED = ("Your study is queued behind <name>: <url>. It starts when that study finishes; "
                    "the answer is on the Study page.")
MEASURES_RULE = "Measures come from the harness by name, `{from: harness}`, or from its templates; never write SQL for a measure."
THIN_PLAN_RULE = ("A plan with fewer than 3 rounds is thin: show its three ways out, start nothing, and wait for "
                  "the engineer's choice; in a one-shot run, say so and end.")
THIN_COMPILE = ("On a thin plan `study compile` exits 2 with `plan: … fewer than 3 rounds …`; the sentences after "
                "it are the ways out — relay them, change nothing in `study.yaml` yourself.")
THIN_WARNING = ("K is the number of entries under `problem.parameters` in the compiled `runs/<id>/evolvekit.yaml` "
                "(the settings tuned). When N is under 6 × K, add: \"The app will call this plan thin: K settings in N "
                "combinations; more time or fewer settings would sharpen it.\"")
STUDY_PATH = re.compile(r"`((?:study\.yaml|cases/|inputs/|runs/)[^`\s]*)`")
RUN_FILES = ("job.json", "job.log", "downloads/", "confirm/final/", "evolvekit.yaml")


def test_study_skill_frontmatter():
    _check_frontmatter(STUDY, "evolvekit-study", ("use evolvekit", "tune", "start"))


def test_study_skill_length():
    _check_length(STUDY)


def test_study_skill_commands_parse(capsys):
    _check_commands(STUDY, capsys)
    named = {tuple(w[:2]) for c in commands_in(_body(STUDY)) for w in command_variants(c)}
    wanted = {("doctor",)} | {("study", step) for step in ("new", "application", "cases", "check", "preview",
                                                           "compile", "start", "open", "wait", "verdict",
                                                           "report", "status")}
    assert wanted - named == set()


def test_study_skill_has_the_six_steps():
    assert _headings(STUDY) == STUDY_STEPS


def test_study_skill_paths_exist_in_a_fresh_study(tmp_path):
    home = Home(tmp_path / "home")
    slug = home.create_study("demo-tour", "tune", "Gate")
    home.add_cases(slug, SAMPLES, hold_back=2)
    root = home.study_root(slug)
    named = STUDY_PATH.findall(_body(STUDY))
    assert "study.yaml" in named and "cases/" in named and "runs/<id>/job.log" in named
    run_file = re.compile(r"runs/<id>/(" + "|".join(map(re.escape, RUN_FILES)) + ")")
    for path in named:
        if path.startswith("runs/"):
            assert run_file.fullmatch(path), f"{path} is not a file a run writes"
        else:
            assert (root / path).exists(), f"{path} is not in a fresh study"


def test_study_skill_has_the_reporting_rule():
    body = _body(STUDY)
    assert REPORTING_RULE in body and PRE_VERDICT_RULE in body
    assert "on the cases it learned from" in body
    assert REPORTING_RULE in _text(REFERENCE)


def test_study_skill_has_the_completeness_rule():
    interview = _section(_body(STUDY), "2. Interview")
    for sentence in COMPLETENESS:
        assert sentence in interview
    assert len(re.findall(r"^\s*- \[ \] ", interview, re.M)) == 8, "the interview is a checklist of eight questions"


def test_study_skill_has_the_hand_over():
    hand_over = _section(_body(STUDY), "5. Start and hand over")
    assert HAND_OVER in hand_over and HAND_OVER_QUEUED in hand_over
    assert "study wait SLUG --timeout 600" in hand_over


def test_study_skill_takes_measures_from_the_harness():
    body = _body(STUDY)
    assert MEASURES_RULE in body and "evolvekit study check SLUG" in body


def test_study_skill_stops_on_a_thin_plan():
    assert THIN_PLAN_RULE in _section(_body(STUDY), "4. Show the plan")
    assert THIN_COMPILE in _section(_body(STUDY), "4. Show the plan")
    assert "fewer than 3 rounds" in _section(_body(STUDY), "When it goes wrong")


def test_study_skill_says_where_the_thin_warning_comes_from():
    assert THIN_WARNING in _section(_body(STUDY), "5. Start and hand over")


def test_study_skill_says_what_to_do_when_it_goes_wrong():
    wrong = _section(_body(STUDY), "When it goes wrong")
    for phrase in ("evolvekit-harness", "evolvekit study check SLUG", "ways out", "evolvekit doctor",
                   "evolvekit study status SLUG", "runs/<id>/job.log"):
        assert phrase in wrong, phrase


def test_skills_use_the_apps_words():
    for path in (STUDY, HARNESS):
        assert NEVER_SAID.findall(_prose(_body(path))) == [], path.parent.name


@pytest.mark.parametrize("path", sorted(SKILLS.rglob("*.md")), ids=lambda p: p.relative_to(SKILLS).as_posix())
def test_no_vendor_names(path):
    text = _text(path).lower()
    assert [v for v in VENDORS if v in text] == []


def _check_frontmatter(path: Path, name: str, triggers: tuple[str, ...]) -> None:
    meta = frontmatter(_text(path))
    assert set(meta) == {"name", "description"}
    assert meta["name"] == name == path.parent.name
    description = meta["description"]
    assert len(description) < 1024
    assert "\n" not in description
    assert not description.startswith("Use when"), "the description starts with what the skill is for"
    assert description.rsplit(". ", 1)[-1].startswith("Use when"), "the description ends with 'Use when …'"
    for trigger in triggers:
        assert trigger in description.lower(), trigger


def _check_length(path: Path) -> None:
    lines = len(_body(path).splitlines())
    assert lines <= 150, f"{path.parent.name}: the body is {lines} lines"


def _check_commands(path: Path, capsys) -> None:
    commands = commands_in(_body(path))
    assert commands, f"{path.parent.name} names no command"
    for command in commands:
        for words in command_variants(command):
            _assert_parses(words, capsys)


def _headings(path: Path) -> list[str]:
    return re.findall(r"^## (.+?)\s*$", _body(path), re.M)


# The harness skill (Task 3).

HARNESS_STEPS = ["1. Look first", "2. Interview", "3. Start from something that passes", "4. Write it, in this order",
                 "5. Check until it exits 0", "6. Smoke test", "7. Every later change", "Two rules"]
CHECK_RULE = "Never call the harness done before `evolvekit harness check harness/ --app PATH --json` exits 0."


def test_harness_skill_frontmatter():
    _check_frontmatter(HARNESS, "evolvekit-harness", ("behind evolvekit", "write a harness", "harness check"))


def test_harness_skill_length():
    _check_length(HARNESS)


def test_harness_skill_commands_parse(capsys):
    _check_commands(HARNESS, capsys)
    named = {tuple(w[:2]) for c in commands_in(_body(HARNESS)) for w in command_variants(c)}
    assert {("harness", "discover"), ("harness", "new"), ("harness", "check"), ("study", "new"),
            ("study", "cases"), ("study", "check"), ("study", "preview")} <= named


def test_harness_skill_has_the_steps_in_order():
    assert _headings(HARNESS) == HARNESS_STEPS


def test_harness_skill_forbids_editing_the_sdk():
    body = _body(HARNESS)
    assert SDK_RULE in body and CHECK_RULE in body
    assert "evolvekit harness check harness/ --app PATH --json" in body
    rules = _section(body, "Two rules")
    assert "#the-comparability-trap" in rules and "#settings-every-value-in-range-must-be-valid" in rules
    assert "`private: true`" in _section(body, "4. Write it, in this order")
    assert "`version:`" in _section(body, "7. Every later change")
