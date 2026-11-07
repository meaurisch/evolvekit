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
from evolvekit.skills import available, frontmatter, install

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
STOP_BEFORE_MAKING = ("Do not go on to step 3 until every question has an answer: with one missing, nothing is made "
                      "— no `study new`, no file — and your whole message is that question.")
THIN_COMPILE = ("On a thin plan `study compile` exits 2 with `plan: … fewer than 3 rounds …`; the sentences after "
                "it are the ways out — relay them, change nothing in `study.yaml` yourself.")
SHOW_PLAN_FIRST = ("Put the plan line (rounds, combinations, held-back cases, done around) in your message before "
                   "`study start`, whether or not you must wait for a yes.")
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


def test_study_skill_makes_nothing_before_the_interview_is_complete():
    body = _body(STUDY)
    assert STOP_BEFORE_MAKING in _section(body, "2. Interview")
    assert _section(body, "3. Make the study").lstrip().startswith("Only when the interview is complete")
    first_new = min(body.find(f"`{c}") if f"`{c}" in body else body.find(c)
                    for c in commands_in(body) if command_variants(c)[0][:2] == ["study", "new"])
    assert 0 <= body.find(STOP_BEFORE_MAKING) < first_new, "the stop comes before the first `study new` command"


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


def test_study_skill_shows_the_plan_line_before_it_starts():
    assert SHOW_PLAN_FIRST in _section(_body(STUDY), "4. Show the plan")


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


FORMAT_POINTER = ("Read `harness/AGENTS.md` (copied in by `harness new`: the file table, the `runner.py` hooks and "
                  "their signatures, the `kit/` folder) and evolvekit's `docs/harness-authoring.md` for the full "
                  "format; its section 'What `harness check` runs' says what each check means.")
CHECK_EXITS = "Exit 2 is failures, exit 1 is warnings only: neither is done."


def test_harness_skill_points_to_the_full_format():
    assert FORMAT_POINTER in _section(_body(HARNESS), "3. Start from something that passes")


def test_harness_skill_pins_the_interview_smoke_test_and_exits():
    body = _body(HARNESS)
    interview = _section(body, "2. Interview")
    assert len(re.findall(r"^\s*- \[ \] ", interview, re.M)) == 5, "the interview is a checklist of five questions"
    assert "--template T" in _section(body, "6. Smoke test")
    assert CHECK_EXITS in _section(body, "5. Check until it exits 0")
    assert "(when `runner.py` has `discover()`)" in _section(body, "7. Every later change")


# Round B1: interview first, write only what was asked, check with --app, the smoke test says its values.
# Round B2: the two questions only the engineer can answer come first; the check shown to people has no --json.

ENGINEER_ONLY = ("Questions 1–3 may be answered from the application's `--help` and a case file: say what you read. "
                 "Questions 4 and 5 only the engineer can answer, in the request or a reply; never from `--help`, a "
                 "case file or a harness you copy. Ask those in one message and write nothing until they are "
                 "answered: in a one-shot run your whole answer is those questions.")
REUSE_FINDING = ("An application that evolvekit already has a harness for (`evolvekit harness list`, or "
                 "`evolvekit doctor --app PATH`, which shows which harness takes it) is a finding to tell the "
                 "engineer, not a shortcut to take: ask whether to reuse it, and if yes hand over to "
                 "`evolvekit-study`.")
ONLY_WHAT_WAS_ASKED = ("Write only what the interview named: no lever, data change, request format or retry the "
                       "engineer did not ask for, and no template beyond the one the smoke test needs; a smaller "
                       "harness that passes beats a fuller one.")
CHECK_WITH_APP = ("Run `evolvekit harness check harness/ --app PATH --json` — without `--app` it exits 2 before "
                  "checking anything — and quote the command and its exit code in your answer.")
CHECK_FOR_PEOPLE = ("Show the engineer the command without `--json`, `evolvekit harness check harness/ --app PATH`: "
                    "the `--json` form is for you.")
SMOKE_VALUES = ("Say which values the smoke test used (the harness's default time per case, the template's budget, "
                "the cases held back), that they are for the smoke test only, and quote the `study preview` line.")


def test_harness_skill_round_b_sentences():
    body = _body(HARNESS)
    interview = _section(body, "2. Interview")
    assert ENGINEER_ONLY in interview and REUSE_FINDING in interview
    assert "go on without asking" not in interview, "no request is complete without questions 4 and 5"
    assert ONLY_WHAT_WAS_ASKED in _section(body, "4. Write it, in this order")
    assert CHECK_WITH_APP in _section(body, "5. Check until it exits 0")
    assert CHECK_FOR_PEOPLE in _section(body, "5. Check until it exits 0")
    assert SMOKE_VALUES in _section(body, "6. Smoke test")


# Round B3: the smoke test is the agent's to run; samples are never the engineer's files.

SMOKE_YOURSELF = ("Run the smoke test yourself and quote each command and its line; never tell the engineer to "
                  "follow a skill — they are talking to you.")
SAMPLES_NOT_PRIVATE = ("Samples are small made-up cases in the engineer's format, never copies of the engineer's own "
                       "files: a harness folder travels, and what must never reach a model must not sit in it.")


def test_harness_skill_round_b3_sentences():
    body = _body(HARNESS)
    assert SMOKE_YOURSELF in _section(body, "6. Smoke test")
    assert "evolvekit-study" not in _section(body, "6. Smoke test"), "the smoke test is said in its own words"
    assert SAMPLES_NOT_PRIVATE in _section(body, "4. Write it, in this order")
    interview = _section(body, "2. Interview")
    assert "(questions 1–3)" in interview.split("- [ ]", 1)[0], "reading the files first is for questions 1–3"
    checklist = re.findall(r"^\s*- \[ \] .*$", interview, re.M)
    assert all("→ goes into:" in line for line in checklist[3:]), "questions 4 and 5 say where the answer goes"


# The suite is complete (Task 4).

PHASES = ("queued", "starting", "search", "check", "done", "stopped", "failed")


def test_installed_copies_equal_package(tmp_path):
    install(tmp_path)
    shared = REFERENCE.read_bytes().replace(b"\r\n", b"\n")
    for name in available():
        generic = tmp_path / ".agents" / "skills" / name
        assert (generic / "SKILL.md").read_bytes() == (SKILLS / name / "SKILL.md").read_bytes().replace(b"\r\n", b"\n")
        assert (generic / "references" / "evolvekit.md").read_bytes() == shared
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert files == sorted(f"{where}/skills/{name}/{leaf}" for name in available()
                           for where, leaf in ((".agents", "SKILL.md"), (".agents", "references/evolvekit.md"),
                                               (".claude", "SKILL.md")))


def test_pointer_frontmatter_equals_skill(tmp_path):
    install(tmp_path)
    for name in available():
        own = frontmatter(_text(SKILLS / name / "SKILL.md"))
        pointer = frontmatter((tmp_path / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8"))
        assert pointer == own and set(own) == {"name", "description"}


def test_vendor_names_are_absent_from_the_reference_and_both_skills():
    for path in (REFERENCE, STUDY, HARNESS):
        text = _text(path).lower()
        assert [v for v in VENDORS if v in text] == [], path.name


def test_reference_rules_three_and_four():
    rules = _section(_text(REFERENCE), "Rules")
    three = re.search(r"^3\. (.+)$", rules, re.M)
    four = re.search(r"^4\. (.+)$", rules, re.M)
    assert three and "study compile" in three.group(1) and "before `study start`" in three.group(1)
    assert four and "{from: harness}" in four.group(1) and "never SQL" in four.group(1)


def test_reference_lists_the_phases_and_the_nested_keys():
    section = _section(_text(REFERENCE), "The verdict document")
    spans = set(re.findall(r"`([^`]+)`", section))
    assert set(PHASES) <= spans
    assert {"cases", "runs_per_case", "pct", "name", "start", "best", "change_pct", "outcome"} <= spans


def test_reference_names_the_extra_commands_and_the_lock_exit():
    text = _text(REFERENCE)
    commands = _section(text, "Commands")
    assert "--dir PATH" in commands
    named = {tuple(w[:2]) for c in commands_in(commands) for w in command_variants(c)}
    assert {("harness", "pack"), ("harness", "install")} <= named
    assert "exits 3 when another run holds the run lock" in commands


# The whole-branch review's fixes.

START_WITHOUT_A_QUESTION = ("A plan that fits, for a request complete as first sent, goes on to step 5 without a "
                            "further question; for a request completed by the engineer's answers, wait for a yes "
                            "after the plan line.")
COMPILE_AGAIN = ("When time has passed since step 4 (the start waited for a yes), compile again just before "
                 "`study start`, and quote the new line.")
POWERSHELL_REPORT = ("In Windows PowerShell, write it with `evolvekit study report SLUG --markdown` piped to "
                     "`Out-File -Encoding utf8 evolvekit-report.md`, since `>` writes UTF-16 there.")
LOCK_EXIT = "A command that runs a study exits 3 when another run holds the run lock."
HARNESS_PATHS = ("AGENTS.md", "harness.yaml", "runner.py", "evk_harness.py", "templates/", "samples/")


def test_study_skill_says_which_request_starts_without_a_question():
    body = _body(STUDY)
    plan = _section(body, "4. Show the plan")
    assert START_WITHOUT_A_QUESTION in plan and THIN_PLAN_RULE in plan and SHOW_PLAN_FIRST in plan
    assert "after a complete request" not in body
    assert COMPILE_AGAIN in _section(body, "5. Start and hand over")
    assert POWERSHELL_REPORT in _section(body, "6. Report the verdict")


def test_reference_compile_new_and_lock_rows():
    commands = _section(_text(REFERENCE), "Commands")
    compile_row = next(line for line in commands.splitlines() if line.startswith("| `evolvekit study compile"))
    assert compile_row.rstrip().endswith("| 0 a plan; 2 a thin plan or a problem |")
    new_row = next(line for line in commands.splitlines() if line.startswith("| `evolvekit study new"))
    assert "ID, or a harness folder" in new_row
    assert LOCK_EXIT in commands and "`main()`" not in commands


def test_harness_new_makes_every_path_the_harness_skill_names(tmp_path, capsys):
    from evolvekit.cli import main

    body = _body(HARNESS)
    named = set(re.findall(r"`harness/([^`\s<*]+)`", body))
    assert named and named <= set(HARNESS_PATHS), named
    for leaf in HARNESS_PATHS:
        assert re.search(rf"`(harness/)?{re.escape(leaf.rstrip('/'))}", body), f"the harness skill names no {leaf}"
    assert main(["harness", "new", str(tmp_path / "harness"), "--kind", "program"]) == 0
    capsys.readouterr()
    for leaf in HARNESS_PATHS:
        path = tmp_path / "harness" / leaf
        assert (path.is_dir() if leaf.endswith("/") else path.is_file()), f"harness new made no {leaf}"
