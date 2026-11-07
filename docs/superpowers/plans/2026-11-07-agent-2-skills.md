# Agent path, project 2: Skills and the agent gate — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> Expanded to step level on 2026-11-07 against the code of `agent/1-one-world` (e8ab740). The builder check runs on this version.

**Goal:** two skills a coding agent follows — one to run a study from a problem description, one to put an application behind evolvekit — and a gate in which a fresh agent turns one sentence into a running, visible study.

**Architecture:** the skills are Markdown shipped in the package (`evolvekit/skills/`), installed by project 1's `skill install` (`evolvekit/skills/__init__.py`: `install` copies each `SKILL.md`, copies `shared/evolvekit.md` into every skill's `references/`, writes the `.claude/skills/<name>/SKILL.md` pointer). They are interview scripts plus the CLI; everything deterministic is a command. Their tests are static (`tests/test_skills_content.py`, the text against `build_parser()` and a fresh study) and behavioural (`tests/agent_gate.py`, run by hand, drives the real tool; and a persona review loop).

**Tech stack:** Markdown in the Agent Skills format (`SKILL.md` with `name` and `description` frontmatter, `references/`); Python 3.11 standard library plus pytest for the tests and the gate script.

**Spec:** [docs/superpowers/specs/2026-11-07-agent-path-design.md](../specs/2026-11-07-agent-path-design.md), §2 and §4 (with §3.2 for the commands and §3.4 for the verdict document).

## Global constraints

- Each `SKILL.md` body ≤ 150 lines; the frontmatter has `name` (= folder) and `description` (starts with what it is for, ends with a sentence that begins "Use when", < 1024 characters, one line — `install` refuses `>`/`|` blocks), nothing else (spec §4.1). The shared reference ≤ 200 lines.
- No vendor name in any skill file, `.claude` included; the skills name only commands that exist in `build_parser()` (spec §2, §4.4).
- The app's words in every sentence of the skills: study, case, held-back case, starting point, measure, goal, rule, combination, round, final check; never KPI, seed, generation, instance, config, baseline outside code spans (spec §2).
- The study skill never reports more than the verdict's `line` and `advice`; the training gain is named "on the cases it learned from" only (spec §4.2 step 6). Before the verdict exists it writes no "better", "improved", "faster", "cheaper" and no percentage.
- The plan and its time are shown (`study compile`) before `study start`. A complete request with a plan that fits starts without a further question; a thin or impossible plan, or an incomplete request, waits for the engineer (spec §4.2 steps 2 and 4).
- Commands in the skills start with `evolvekit`; the reference says `python -m evolvekit` is the same when `evolvekit` is not on PATH. **A code span or fenced line that starts with `evolvekit ` is a whole command and must parse** (the static tests parse every one); a command named in prose drops the prefix (`study start`, `harness new`).
- Branch `agent/2-skills` from `agent/1-one-world`; PR against `master`, stacked; merge commit. Never run `python tasks.py check` beside a demo study or the gate.

## Review focus

1. The engineer's one sentence already contains the measure, the time and the cases: the skill must not re-ask what was said. Pinned by `test_study_skill_has_the_completeness_rule` (Task 2: "Ask only what the request did not say." and the three completeness sentences verbatim) and by persona round A (Task 6).
2. The harness has no template and the study needs a goal: the skill writes `kpis`/`goal` from the harness's measures by name, never SQL. Pinned by `test_study_skill_takes_measures_from_the_harness` (Task 2: `{from: harness}`, `study check`, the measures sentence verbatim).
3. `study compile` says the plan is thin (fewer than 3 rounds): the skill shows the three ways out, starts nothing, and waits. Pinned by `test_study_skill_stops_on_a_thin_plan` (Task 2) and persona round A's 30-second answer (Task 6).
4. The tool's one-shot mode cannot ask: the default gate sentence carries every answer, so the skill reaches `study start` without a question; the same sentence without the total time must end with that question and no study made. Pinned by `checks` and `question_checks` with their truth tables (Task 5) and the gate's `--expect-question` run (Task 6).
5. An agent edits `evk_harness.py` to make `harness check` pass: the harness skill forbids it and round B diffs the SDK file against the package's. Pinned by `test_harness_skill_forbids_editing_the_sdk` (Task 3) and the round B `cmp` (Task 6).

---

## File map

| File | Responsibility |
|---|---|
| `evolvekit/skills/shared/evolvekit.md` | the shared reference: words, where things live, the commands with exit codes, the verdict document's keys, the rules (replaces the placeholder) |
| `evolvekit/skills/evolvekit-study/SKILL.md` | the study skill (replaces the placeholder) |
| `evolvekit/skills/evolvekit-harness/SKILL.md` | the harness skill (replaces the placeholder) |
| `tests/test_skills_content.py` | the static tests (new) |
| `tests/agent_gate.py` | the gate script, run by hand; not collected (pytest collects `test_*.py` only) |
| `tests/test_agent_gate_script.py` | the gate script's own tests (new) |
| `docs/superpowers/reviews/2026-11-07-engineer-gate-log.md` | the persona rounds (new) |
| `docs/superpowers/reviews/agent-gate/` | the gate's transcripts, token redacted (new) |
| `README.md` (§ "For coding agents"), `docs/harness-authoring.md` (new § "With an AI coding tool") | the skills, `skill install`, the gate |

`evolvekit/skills/__init__.py`, `tests/test_skills_install.py` and `pyproject.toml` are not changed: package data already covers `skills/*/SKILL.md` and `skills/shared/*`, and `evolvekit/skills/**` is `eol=lf` in `.gitattributes`.

---

### Task 1: The shared reference

**Files:**
- Modify: `evolvekit/skills/shared/evolvekit.md` (the placeholder, whole file)
- Create: `tests/test_skills_content.py`

**Interfaces:**
- Consumes: `evolvekit.cli.build_parser() -> argparse.ArgumentParser`; `evolvekit.app.verdict.DECISIONS: tuple[str, ...]`.
- Produces (test helpers Tasks 2–4 reuse, in `tests/test_skills_content.py`):
  - `_text(path: Path) -> str`; `_body(path: Path) -> str` (the text after the frontmatter's closing `---`; the whole text for the reference);
  - `_prose(text: str) -> str` (fenced blocks and code spans removed);
  - `_section(text: str, title: str) -> str` (from `## <title>` to the next `## `);
  - `commands_in(text: str) -> list[str]` (every backticked span and every fenced-block line that starts with `evolvekit ` or `python -m evolvekit `);
  - `command_variants(command: str) -> list[list[str]]` (usage syntax expanded to argv lists, placeholders substituted);
  - `_assert_parses(words: list[str], capsys) -> None`;
  - constants `ROOT`, `SKILLS`, `REFERENCE`, `STUDY`, `HARNESS`, `NEVER_SAID`, `PLACEHOLDERS`, `REPORTING_RULE`, `PRE_VERDICT_RULE`, `SDK_RULE`, `EXIT_CODES`.

- [ ] **Step 1: Branch**

```bash
git switch -c agent/2-skills agent/1-one-world
```

- [ ] **Step 2: Write the failing tests**

```python
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
```

Below the tests, the helpers with the signatures of the Interfaces block. `command_variants` is the one algorithm the tests do not fully determine:

```python
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
```

`_assert_parses(words, capsys)`: `build_parser().parse_args(words)` must not raise `SystemExit` (`pytest.fail` with the words and `capsys.readouterr().err`); then `parse_args([*prefix, "--help"])` with `prefix = words[:2] if words[0] in GROUPS else words[:1]` raises `SystemExit` with code 0; drain `capsys` after. `_body(path)` is the text after the frontmatter's closing `---` when the file starts with `---`, else the whole text. `_choices(parser, *path) -> set[str]` walks `argparse._SubParsersAction` choices (`next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))`).

- [ ] **Step 3: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_skills_content.py -q`
Expected: `test_command_variants_expands_usage` PASS; the reference tests FAIL (the placeholder has no `## ` sections, no commands).

- [ ] **Step 4: Write `evolvekit/skills/shared/evolvekit.md`**

Title `# evolvekit reference`, one line saying both skills read it, then exactly these five `##` sections, ≤ 200 lines in all:

- **`## Words`** — a table with the header `| Say | Meaning | Not |`. Rows, in this order: study (Not: config), case (Not: instance), held-back case (Not: test instance), starting point (Not: baseline), measure (Not: KPI), goal, rule, combination (Not: candidate), round (Not: generation), final check (Not: confirm), runs per case (Not: seeds). One sentence after it: `--json` documents and `study.yaml` keep the engine's key names (`kpis`, `seeds`, …); sentences for people never do.
- **`## Where things live`** — four short blocks, paths in backticks:
  - the library home: `EVOLVEKIT_HOME`, else `~/evolvekit`; holds `studies/<slug>/`, `harnesses/` (installed), `settings.yaml`, `.env` (keys, never printed), `app.json` (the running app's address);
  - a study folder `studies/<slug>/`: `study.yaml`, `harness/` (the pinned copy), `cases/`, `inputs/`, `preview/`, `runs/<id>/`;
  - a run folder `runs/<id>/`: `job.json` (the phase), `job.log` (what a failed run said), `queued.json`, `launch.json`, `confirm/final/` (the final check), `downloads/` (the exports);
  - a harness in an application's repository: `harness/` (or `harness/<id>/` when it holds several): `harness.yaml`, `runner.py`, `evk_harness.py` (the SDK), `AGENTS.md`, `templates/`, `samples/`; the skills in `.agents/skills/<name>/` with this file as `references/evolvekit.md`.
- **`## Commands`** — one sentence: every command starts with `evolvekit`, and `python -m evolvekit` is the same when `evolvekit` is not on PATH (both spans end there: a span that goes on after `evolvekit ` is parsed as a command); every `study` command takes `--home DIR`. Then one table `| Command | Does | Exit |`, one command per row, written in usage syntax (`[…]` optional, `(a \| b)` alternatives, `\|` escaped in the table), placeholders only from `SLUG NAME ID T PATH DIR FILE N S`, the "Does" text taken from the CLI's help. Rows (each is a test input):
  `evolvekit doctor [--app PATH] [--json]` (exit `0 when every line is ✓, else 1`); `evolvekit harness list`; `evolvekit harness discover (--program PATH \| --python PATH --module NAME) [--json]`; `evolvekit harness new DIR --from ID`; `evolvekit harness new DIR --kind program` (two rows: `program` and `python` are the kinds); `evolvekit harness check DIR --app PATH --json` (exit `0 all pass, 1 warnings only, 2 failures`); `evolvekit study new NAME --harness ID [--template T] [--app PATH] [--json]`; `evolvekit study application SLUG PATH`; `evolvekit study application SLUG --detect`; `evolvekit study list [--json]`; `evolvekit study cases SLUG --add FILE... [--hold-back N] [--json]`; `evolvekit study check SLUG` (0 ready, 2 problems); `evolvekit study preview SLUG`; `evolvekit study compile SLUG` (prints the plan: rounds, combinations, the final check, "done around HH:MM", or the thin plan with its three ways out); `evolvekit study run SLUG`; `evolvekit study start SLUG [--wait [--timeout S]] [--json]`; `evolvekit study stop SLUG [--json]`; `evolvekit study status SLUG [--run ID] [--json]`; `evolvekit study watch SLUG [--every S] [--json]`; `evolvekit study wait SLUG [--timeout S] [--json]` (0 done, 1 stopped or failed, 4 timeout); `evolvekit study verdict SLUG [--run ID] [--json]`; `evolvekit study report SLUG [--run ID] [--markdown \| --json]`; `evolvekit study open SLUG [--no-browser]`; `evolvekit skill list`; `evolvekit skill install [--dir DIR] [--force]`.
  After the table, the sentence `EXIT_CODES` verbatim, and: "`harness check` and `doctor` have their own, in the table."
- **`## The verdict document`** — what `study verdict --json` prints (spec §3.4): a table `| Key | Holds |` with every key of `VERDICT_KEYS` in backticks (the statistics may share a row), the `decision` values each in backticks (`use`, `keep`, `worse`, `none`, `single`, `pending`, `stopped`, `skipped`) with their meaning, and the `phase` values. One sentence: `line` and `advice` are the only sentences an agent repeats.
- **`## Rules`** — a numbered list, one rule per item:
  1. `REPORTING_RULE` verbatim, then: the gain on the training cases is named only as "on the cases it learned from" (`learned_from`), and only when asked.
  2. `PRE_VERDICT_RULE` verbatim.
  3. The plan before the start: `study compile`'s line is shown before `study start`; a complete request with a plan that fits starts; anything else waits for the engineer.
  4. Measures come from the harness by name (`{from: harness}`) or from its templates; never SQL in a study.
  5. "Never edit `evk_harness.py`: it is evolvekit's SDK, copied in by `harness new`; fix a failing check in `runner.py` or `harness.yaml`." (starts with `SDK_RULE`).
  6. The comparability trap, one paragraph: a data change that alters what the application sees (costs, weights) alters every measure computed from it; such measures are `changes_with_levers: true`, and a data study is judged by a measure of the untouched request.
  7. Every value in range is valid, one paragraph: the search tries anything in a setting's range; ranges that cannot break by construction, else a `{says, expr}` constraint, else an SQL constraint.

- [ ] **Step 5: Run the tests to see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_skills_content.py tests/test_skills_install.py -q`
Expected: all PASS (`test_skills_install.py` still passes: the reference is copied byte-for-byte).

- [ ] **Step 6: Commit**

```bash
git add evolvekit/skills/shared/evolvekit.md tests/test_skills_content.py
git commit -m "skills: the shared reference: words, layouts, commands, the verdict, the rules"
```

---

### Task 2: The study skill

**Files:**
- Modify: `evolvekit/skills/evolvekit-study/SKILL.md` (the placeholder, whole file)
- Modify: `tests/test_skills_content.py` (append)

**Interfaces:**
- Consumes: Task 1's helpers and constants; `evolvekit.skills.frontmatter(text: str) -> dict`; `evolvekit.app.store.Home(root)` with `create_study(harness_id, template, name) -> str`, `add_cases(slug, files, *, hold_back) -> dict`, `study_root(slug) -> Path`.
- Produces: `_check_frontmatter(path: Path, name: str, triggers: tuple[str, ...]) -> None`, `_check_length(path: Path) -> None`, `_check_commands(path: Path, capsys) -> None`, `_headings(path: Path) -> list[str]` (reused by Task 3); the constants `COMPLETENESS`, `HAND_OVER`, `HAND_OVER_QUEUED`, `MEASURES_RULE`, `THIN_PLAN_RULE` (the gate's prompts in Task 5 rely on the skill saying these).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_skills_content.py`; the new imports join the others at the top of the file)

```python
from evolvekit.app.store import Home
from evolvekit.skills import frontmatter

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
```

Helpers (signatures as in the Interfaces block):
- `_check_frontmatter(path, name, triggers)`: `meta = frontmatter(_text(path))`; `assert set(meta) == {"name", "description"}`; `meta["name"] == name == path.parent.name`; `d = meta["description"]`; `len(d) < 1024`; `"\n" not in d`; `not d.startswith("Use when")`; `d.rsplit(". ", 1)[-1].startswith("Use when")`; every trigger in `d.lower()`.
- `_check_length(path)`: `len(_body(path).splitlines()) <= 150`.
- `_check_commands(path, capsys)`: `commands_in(_body(path))` is not empty, and `_assert_parses` passes for every variant.
- `_headings(path) -> list[str]`: `re.findall(r"^## (.+?)\s*$", _body(path), re.M)`.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_skills_content.py -q -k "study_skill or apps_words or vendor"`
Expected: FAIL — the placeholder's description has no "Use when", no commands, no headings. `test_no_vendor_names` passes on all three files already.

- [ ] **Step 3: Write `evolvekit/skills/evolvekit-study/SKILL.md`** (body ≤ 150 lines)

Frontmatter, the description on one line in double quotes (inner quotes single):

```yaml
---
name: evolvekit-study
description: "Runs an evolvekit study from a problem description: asks only what the request did not say, makes the study with the evolvekit command line, shows the plan and its time, starts it, hands over the study's address in the app, and reports the final check's verdict word for word. Use when someone says 'use evolvekit', asks to improve, tune or find better settings or data for an application with evolvekit, or asks to start, watch or report on a study."
---
```

Body: one line "Read `references/evolvekit.md` first: its words, commands and rules hold here.", then the `##` headings of `STUDY_STEPS`, in order:

- **`## 1. Ground`** — `evolvekit doctor` (as a code span: the tests look for it); follow every ✗'s `fix:` line, except that the provider line matters only when the study turns AI help on (`budget.ai.enabled`, off unless the engineer asks). The harness: `harness/harness.yaml` in the repository (or `harness/<id>/`), else `evolvekit harness list`; none fits → follow the `evolvekit-harness` skill, then come back here.
- **`## 2. Interview`** — `COMPLETENESS[0]` first, then exactly eight checklist lines `- [ ] …`, each naming what in the request answers it and where the answer lands:
  1. Where is the application? → `--app PATH`.
  2. What may change: settings, data? → `vary.settings` (`tune`, `{fixed: value}`, `default`), `vary.data`.
  3. What stays fixed? → `{fixed: value}`; what the request does not name stays as the template has it.
  4. Where are the cases, which are representative, how many are held back? → `study cases --add … --hold-back N`; "the requests in cases/" means all of them.
  5. The measure and its direction? → `kpis` and `goal.levels`: the harness measure whose label or help says what the engineer said ("shorter tours" → the tour length, `lower`); when two fit, ask.
  6. How long per case? → `limits.time_per_case_s`; when the request does not say, the harness's default, named in the plan line.
  7. How much time in total? → `budget.hours` = minutes ÷ 60, three decimals (ten minutes → `0.167`). Never chosen by you.
  8. The starting point: today's settings or a settings file? → today's settings are the harness's defaults (nothing to write); a file goes into `inputs` when the harness takes one.
  Then `COMPLETENESS[1:]` verbatim, and "Decide nothing about the goal or the total time alone."
- **`## 3. Make the study`** — a fenced block:

  ```
  evolvekit study new NAME --harness harness/ --template T --app PATH --json
  evolvekit study cases SLUG --add "cases/*.json" --hold-back N
  evolvekit study application SLUG PATH
  ```
  (the third only when `study new` said the application was not accepted). Then: the `--json` line gives `slug` and `root`; edit `study.yaml` in `root` for `vary`, `kpis`, `goal`, `guardrails` (only when the request names one), `limits`, `budget`; `MEASURES_RULE` verbatim; then `evolvekit study check SLUG` (exit 2: each line names a key of `study.yaml`; fix and check again) and `evolvekit study preview SLUG`. Paths named: `study.yaml`, `cases/`, `inputs/`.
- **`## 4. Show the plan`** — `evolvekit study compile SLUG`; its line says the rounds, the combinations, the final check and "done around HH:MM". Show that line. A plan that fits, after a complete request, goes on to step 5 without a question; otherwise wait for a yes. `THIN_PLAN_RULE` verbatim; the three ways out are the ones the line names (fewer cases, a shorter time per case, more time).
- **`## 5. Start and hand over`** — `evolvekit study start SLUG`, then `evolvekit study open SLUG` (prints the address and opens the app). Fill in and say one of `HAND_OVER` / `HAND_OVER_QUEUED` verbatim as templates (N, R, HH:MM from step 4's line; `<name>` from `study start`'s "queued behind"). `PRE_VERDICT_RULE` verbatim. Then end, unless the engineer asked to wait: waiting is `evolvekit study wait SLUG --timeout 600`, again while it exits 4, until it exits 0 or 1.
- **`## 6. Report the verdict`** — `evolvekit study verdict SLUG` prints the line and the advice; `evolvekit study report SLUG --markdown > evolvekit-report.md` writes the report: name the file, never paste its numbers. `REPORTING_RULE` verbatim; "The gain on the cases it learned from is said only when asked, and named so." Interpretation, if any, is one sentence below, marked as yours.
- **`## When it goes wrong`** — a list: no harness → `evolvekit-harness`; `evolvekit study check SLUG` exits 2 → fix the keys it names; a thin plan → its ways out (step 4); a ✗ from `evolvekit doctor` → its fix line; `study start` says the study is already running → `evolvekit study status SLUG`; the run failed (`study wait` exit 1) → say the error line from `evolvekit study status SLUG` and the log `runs/<id>/job.log`, and stop.

- [ ] **Step 4: Run the tests to see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_skills_content.py tests/test_skills_install.py -q`
Expected: all PASS, `test_harness_*` not yet present.

- [ ] **Step 5: Commit**

```bash
git add evolvekit/skills/evolvekit-study/SKILL.md tests/test_skills_content.py
git commit -m "skills: the study skill: interview, plan, start, hand over, the verdict only"
```

---

### Task 3: The harness skill

**Files:**
- Modify: `evolvekit/skills/evolvekit-harness/SKILL.md` (the placeholder, whole file)
- Modify: `tests/test_skills_content.py` (append)

**Interfaces:**
- Consumes: Task 2's `_check_frontmatter`, `_check_length`, `_check_commands`, `_headings`; Task 1's `SDK_RULE`, `COMPLETENESS` from Task 2.
- Produces: the constants `HARNESS_STEPS`, `CHECK_RULE` (round B's note relies on the skill saying them).

- [ ] **Step 1: Write the failing tests** (append)

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_skills_content.py -q -k harness_skill`
Expected: FAIL (placeholder: no "Use when", no commands, no headings).

- [ ] **Step 3: Write `evolvekit/skills/evolvekit-harness/SKILL.md`** (body ≤ 150 lines)

```yaml
---
name: evolvekit-harness
description: "Puts an application behind evolvekit by writing a harness in harness/: reads what the application exposes, asks the engineer only what it cannot read, writes harness.yaml and runner.py, runs evolvekit harness check until it exits 0, and smoke-tests a study on the samples. Use when someone asks to put an application behind evolvekit or to write a harness, when harness check fails, or when evolvekit does not know an application."
---
```

Body: "Read `references/evolvekit.md` first." then the `##` headings of `HARNESS_STEPS`, in order:

- **`## 1. Look first`** — `evolvekit harness discover --program PATH --json` (a program) or `evolvekit harness discover --python PATH --module NAME --json` (a Python package): candidate settings, a pointer, not a harness. `evolvekit harness list`: a harness for a similar application is the place to start. Read one case file and the application's `--help` or docs.
- **`## 2. Interview`** — five checklist lines: what a case is and its file format; what the application takes as settings and how it is called; what a solution contains; what people call things (the words the labels use); what must never reach a model (those columns get `private: true`). Then "Answer from the application and its files first; ask the engineer only what neither says." and `COMPLETENESS[1:]` verbatim.
- **`## 3. Start from something that passes`** — `evolvekit harness new harness/ --from ID` when step 1 found a close harness, else `evolvekit harness new harness/ --kind program` (or `--kind python`); `harness/<id>/` when the repository holds several. The new folder passes its first check.
- **`## 4. Write it, in this order`** — numbered: `harness.yaml` tables first (every column with type, unit, description; `private: true` for what must never reach a model); `runner.py` (`read_case`, `solve`, `solution_tables`; it runs under the application's Python and never imports evolvekit); settings with `help` (for people) and `explain` (for a model), ranges, `recommended`; levers and measures (a measure is SQL over the tables that returns one number); templates (`templates/<id>.yaml`); samples (3–10 small cases that solve in a second or two, with the script that makes them). Check after each.
- **`## 5. Check until it exits 0`** — `evolvekit harness check harness/ --app PATH --json`: read every check with `"ok": false`, fix its `fix`, failures first, then warnings; exit 1 (warnings) is not done. `CHECK_RULE` verbatim. `SDK_RULE` followed by ": it is evolvekit's SDK, copied in by `harness new`; a check that fails inside it is fixed in `runner.py` or `harness.yaml`."
- **`## 6. Smoke test`** — the study skill's step 3 on the samples, in a fenced block:

  ```
  evolvekit study new "Smoke test" --harness harness/ --app PATH --json
  evolvekit study cases SLUG --add "harness/samples/*" --hold-back 1
  evolvekit study check SLUG
  evolvekit study preview SLUG
  ```
  and: say the preview's line to the engineer; the study stays in the library home as a draft.
- **`## 7. Every later change`** — bump `version:` in `harness.yaml`, check again; studies already made keep their pinned copy.
- **`## Two rules`** — two paragraphs: the comparability trap (as the reference's rule 6, plus `changes_with_levers: true` and "give the harness a measure of the untouched request"), ending "(evolvekit's `docs/harness-authoring.md#the-comparability-trap`)"; every value in range is valid (by construction, a `{says, expr}` constraint, an SQL constraint), ending "(evolvekit's `docs/harness-authoring.md#settings-every-value-in-range-must-be-valid`)".

- [ ] **Step 4: Run the tests to see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_skills_content.py tests/test_skills_install.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add evolvekit/skills/evolvekit-harness/SKILL.md tests/test_skills_content.py
git commit -m "skills: the harness skill: discover, interview, write, check until exit 0, smoke test"
```

---

### Task 4: The static test suite is complete and runs in the gate

**Files:**
- Modify: `tests/test_skills_content.py` (append)

**Interfaces:**
- Consumes: `evolvekit.skills.install(target: Path, *, force: bool = False) -> dict`, `available() -> list[str]`, `frontmatter`.
- Produces: nothing new for later tasks.

- [ ] **Step 1: Write the tests** (append; `available`, `install` join the imports at the top)

```python
from evolvekit.skills import available, install


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
```

- [ ] **Step 2: Run them**

Run: `.venv/Scripts/python.exe -m pytest tests/test_skills_content.py tests/test_skills_install.py -q`
Expected: PASS. These pin project 1's install behaviour against the real skills (they would have failed before project 1); no code changes.

- [ ] **Step 3: Confirm none is `slow` and the file runs in the fast loop**

Run: `.venv/Scripts/python.exe -m pytest tests/test_skills_content.py -q -m "not slow"` — Expected: the same count as Step 2 (nothing deselected).

- [ ] **Step 4: Commit**

```bash
git add tests/test_skills_content.py
git commit -m "skills: installed copies equal the package's, pointers carry the same frontmatter"
```

---

### Task 5: The gate script

**Files:**
- Create: `tests/agent_gate.py`
- Create: `tests/test_agent_gate_script.py`

**Interfaces:**
- Consumes: `evolvekit.skills.install`; `evolvekit.app.store.Home` (`slugs() -> list[str]`, `study_root(slug) -> Path`); `evolvekit.app.jobs.latest_run(root) -> str | None`, `jobs.phase(run_dir: Path) -> dict`; `evolvekit.harness.study.load_study(root)` (`.application_path`); `evolvekit.app.verdict.build_verdict(root: Path, run: str | None = None) -> dict`; `evolvekit.app.running.read_running(home_root: Path) -> dict | None`.
- Produces (module `agent_gate`, importable from the tests because `tests/` is on `sys.path` as for `harness_toy`):
  - `DEFAULT_SENTENCE = "Use evolvekit to find solver settings that give shorter tours on the requests in cases/; the solver is solver.py in this folder; hold two back; ten minutes in total; today's settings are the starting point."`
  - `MISSING_TIME_SENTENCE` = the same without "ten minutes in total; ".
  - `REPORT_PROMPT = "The evolvekit study {slug} has finished; report its result."`
  - `TOOLS: dict[str, list[str]]` — the one place the tools' flags live (see Step 3).
  - `build_fixture(repo: Path, *, home: Path, harness: bool = True) -> None`
  - `gate_env(home: Path) -> dict[str, str]`
  - `tool_command(tool: str, sentence: str, home: Path) -> tuple[list[str], str | None]` (argv, stdin text); raises `ToolMissing` when the tool is not on PATH.
  - `run_tool(tool: str, cwd: Path, sentence: str, *, timeout_s: int, env: Mapping[str, str], transcript: Path) -> str`
  - `redact(text: str) -> str`
  - `claims(text: str, allowed: Sequence[str] = ()) -> list[str]`
  - `the_study(home: Path) -> tuple[str, Path] | None`
  - `checks(home: Path, output: str) -> list[dict]`, `question_checks(home: Path, output: str) -> list[dict]`, `report_checks(document: dict, output: str) -> list[dict]` — every check `{"name": str, "ok": bool, "detail": str}`.
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing tests** (`tests/test_agent_gate_script.py`)

```python
import shutil
from pathlib import Path

import pytest

import agent_gate
from agent_gate import (DEFAULT_SENTENCE, MISSING_TIME_SENTENCE, build_fixture, checks, claims, question_checks,
                        redact, report_checks, tool_command)
from evolvekit.app.store import Home
from evolvekit.harness.manifest import load_harness
from evolvekit.harness.study import load_study, save_study

ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"
SAMPLES = sorted(p.name for p in (ROOT / "harnesses" / "demo-tour" / "samples").glob("*.json"))
URL = "http://127.0.0.1:8780/?token=abc123#/study/gate"
LINE = ("Probably better: 1.3 % lower tour length on 2 held-back cases, a chance of about 19 in 20; "
        "at worst 0.3 % worse than your starting point.")
ADVICE = "Use the new settings: they are probably better on cases the search never saw."


def _ok(results):
    return {r["name"]: r["ok"] for r in results}


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_build_fixture_layout(tmp_path):
    repo, home = tmp_path / "a repo", tmp_path / "home"
    build_fixture(repo, home=home)
    assert (repo / ".git").is_dir()
    for name in ("evolvekit-study", "evolvekit-harness"):
        assert (repo / ".agents" / "skills" / name / "SKILL.md").is_file()
        assert (repo / ".agents" / "skills" / name / "references" / "evolvekit.md").is_file()
        assert (repo / ".claude" / "skills" / name / "SKILL.md").is_file()
    assert load_harness(repo / "harness").id == "demo-tour"
    assert not list((repo / "harness").rglob("__pycache__"))
    assert (repo / "solver.py").read_bytes() == SOLVER.read_bytes()
    assert sorted(p.name for p in (repo / "cases").iterdir()) == SAMPLES
    assert len((repo / "README.md").read_text(encoding="utf-8").splitlines()) == 2
    assert home.is_dir() and list(home.iterdir()) == []
    bare = tmp_path / "bare"
    build_fixture(bare, home=tmp_path / "home2", harness=False)
    assert not (bare / "harness").exists() and (bare / "solver.py").is_file()


def test_sentences():
    assert "ten minutes in total" in DEFAULT_SENTENCE
    assert MISSING_TIME_SENTENCE == DEFAULT_SENTENCE.replace("ten minutes in total; ", "")


def test_checks_on_a_fake_output(tmp_path):
    home = tmp_path / "home"
    assert _ok(checks(home, "")) == {"study_exists": False, "application_set": False,
                                     "study_started_or_queued": False, "output_names_the_url": False,
                                     "no_claim_of_improvement": True}
    store = Home(home)
    slug = store.create_study("demo-tour", "tune", "Gate")
    root = store.study_root(slug)
    said = f"Your study is running: {URL}. It tries about 12 combinations in 4 rounds and ends around 14:20."
    assert _ok(checks(home, said)) == {"study_exists": True, "application_set": False,
                                       "study_started_or_queued": False, "output_names_the_url": True,
                                       "no_claim_of_improvement": True}
    study = load_study(root)
    study.application_path = str(SOLVER)
    save_study(study, root)
    run = root / "runs" / "20261107-120000"
    run.mkdir(parents=True)
    (run / "queued.json").write_text('{"queued_at": "2026-11-07T12:00:00+00:00"}', encoding="utf-8")
    assert all(_ok(checks(home, said)).values())
    for claim in ("It found better settings.", "Tours are 3.1 % shorter.", "It improved.", "faster", "cheaper"):
        assert _ok(checks(home, f"{said} {claim}"))["no_claim_of_improvement"] is False, claim
    assert _ok(checks(home, "http://127.0.0.1:8780/"))["output_names_the_url"] is False
    (run / "queued.json").unlink()
    (run / "job.json").write_text('{"phase": "done"}', encoding="utf-8")
    assert _ok(checks(home, said))["study_started_or_queued"] is False  # a one-shot run that waited is a fault


def test_question_checks_truth_table(tmp_path):
    home = tmp_path / "home"
    asked = "I have everything but one answer. How much time may the whole study take, in minutes?"
    assert _ok(question_checks(home, asked)) == {"no_study_made": True, "asks_the_missing_question": True}
    assert _ok(question_checks(home, "Starting the study now."))["asks_the_missing_question"] is False
    assert _ok(question_checks(home, "Which cases should I hold back?"))["asks_the_missing_question"] is False
    Home(home).create_study("demo-tour", "tune", "Gate")
    assert _ok(question_checks(home, asked))["no_study_made"] is False


def test_no_claim_detector_ignores_the_verdict_line():
    document = {"line": LINE, "advice": ADVICE}
    said = f"The study has finished.\n\n{LINE}\n{ADVICE}\n\nThe report is in evolvekit-report.md."
    assert claims(LINE) != []
    assert claims(said, allowed=(LINE, ADVICE)) == []
    assert _ok(report_checks(document, said)) == {"report_equals_verdict_line": True}
    extra = said + "\nOn the cases it learned from it was 4.2 % lower."
    assert _ok(report_checks(document, extra))["report_equals_verdict_line"] is False
    assert _ok(report_checks(document, "Probably better: 1.3 % lower."))["report_equals_verdict_line"] is False


def test_tool_command_is_one_shot(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_gate.shutil, "which", lambda name: f"/bin/{name}")
    home = tmp_path / "a home"
    argv, stdin = tool_command("claude", "Use evolvekit.", home)
    assert argv[0] == "/bin/claude" and "-p" in argv and stdin == "Use evolvekit."
    assert argv[argv.index("--add-dir") + 1] == str(home)
    assert "--no-session-persistence" in argv
    assert not {"-c", "--continue", "-r", "--resume"} & set(argv)
    argv, stdin = tool_command("copilot", "Use evolvekit.", home)
    assert stdin is None and argv[argv.index("-p") + 1] == "Use evolvekit."
    monkeypatch.setattr(agent_gate.shutil, "which", lambda name: None)
    with pytest.raises(agent_gate.ToolMissing):
        tool_command("copilot", "x", home)
    assert redact(f"see {URL}") == "see http://127.0.0.1:8780/?token=…#/study/gate"
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_agent_gate_script.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_gate'`.

- [ ] **Step 3: Write `tests/agent_gate.py`**

Module docstring: what the gate is (design §4.4), that it is run by hand, never by pytest, never beside `python tasks.py check`, and the command line. `ROOT = Path(__file__).resolve().parents[1]`.

The tools, in one place. Verified on this machine 2026-11-07: `claude` 2.1.304 at `C:\Users\norri\AppData\Roaming\npm\claude.cmd` — `-p/--print` (prompt from stdin when no positional is given), `--allowedTools` (comma or space separated), `--add-dir`, `--no-session-persistence`, `--output-format text`. `copilot` is **not installed** (`where copilot`: not found); its flags are the ones the plan names and are unverified — `tool_command` refuses it with `ToolMissing` until it is on PATH.

```python
TOOLS = {
    "claude": ["-p", "--allowedTools", "Bash,PowerShell,Read,Edit,Write,Glob,Grep", "--add-dir", "{home}",
               "--no-session-persistence", "--output-format", "text"],
    "copilot": ["-p", "{sentence}", "--allow-all-tools", "--add-dir", "{home}"],
}
"""Each tool's one-shot flags. claude reads the sentence on stdin (a .cmd on Windows mangles quotes in
arguments); `--add-dir` lets it edit study.yaml in the library home, outside the repository."""
```

- `tool_command(tool, sentence, home)`: `exe = shutil.which(tool)`, else `raise ToolMissing(f"{tool} is not installed")`; argv = `[exe, *(a.format(home=home, sentence=sentence) for a in TOOLS[tool])]`; stdin = `sentence` when `"{sentence}" not in TOOLS[tool]`, else `None`. `class ToolMissing(Exception)`.
- `build_fixture(repo, *, home, harness=True)`: `repo.mkdir(parents=True)`, `home.mkdir(parents=True)`; `git init -q <repo>` (`check=True`; no commit, so no git identity or hooks are needed); `skills.install(repo)`; when `harness`, `shutil.copytree(ROOT/"harnesses"/"demo-tour", repo/"harness", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))`; `shutil.copy2` of `examples/cli-solver/solver.py` to `repo/"solver.py"`; the four `harnesses/demo-tour/samples/*.json` into `repo/"cases"`; `README.md` = `"# Tour solver\nsolver.py plans a tour through the stops of each request in cases/.\n"`.
- `gate_env(home)`: `{**os.environ, "EVOLVEKIT_HOME": str(home), "PYTHONIOENCODING": "utf-8", "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"}` — the venv's `Scripts` first, so `evolvekit` resolves in the tool's shell.
- `run_tool(tool, cwd, sentence, *, timeout_s, env, transcript)`: `subprocess.run(argv, cwd=cwd, env=dict(env), input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_s)`; on `TimeoutExpired` take the partial output and append `"\n[the gate stopped the tool after {timeout_s} s]"`; output = stdout + stderr; write `redact(f"$ {tool} <<< {sentence}\n\n{output}")` to `transcript` (parents made); return the unredacted output. Each call is a fresh run; the gate never resumes a session.
- `redact(text)`: `re.sub(r"token=[^\s&#\"')]+", "token=…", text)`.
- `claims(text, allowed=())`: remove every non-empty `allowed` string from `text`; return the matches of `\b(better|improved|faster|cheaper)\b` (case-insensitive) followed by those of `\d+(?:[.,]\d+)?\s?%`.
- `the_study(home)`: `Home(home).slugs()`; none → `None`; else the slug whose `jobs.latest_run(root)` is newest (a smoke study without a run loses), ties → the first; returns `(slug, root)`.
- `checks(home, output)`, in this order: `study_exists` (`the_study` is not None); `application_set` (`load_study(root).application_path` is truthy); `study_started_or_queued` (`jobs.phase(root/"runs"/run)["phase"] in ("queued", "starting", "search", "check")` for the latest run); `output_names_the_url` (`"http://127.0.0.1:" in output and "#/study/" in output`); `no_claim_of_improvement` (`claims(output) == []`, detail = the claims). Missing prerequisites make a check `ok: False` with the reason as detail, never an exception.
- `question_checks(home, output)`: `no_study_made` (`Home(home).slugs() == []`); `asks_the_missing_question` (some sentence of `output` that ends in `?` matches `\b(time|minutes?|hours?|how long)\b`, case-insensitive; sentences split on `(?<=[.?!])\s+`).
- `report_checks(document, output)`: one check `report_equals_verdict_line`: `document["line"] in output` and `claims(output, allowed=(document["line"], document.get("advice", "")))` has no percentage (the words are judged by the persona, the percentages here — spec §4.4); detail names what was missing or extra.
- `main(argv)`: `argparse` with `--tool {claude,copilot}` (default `claude`), `--sentence` (default `DEFAULT_SENTENCE`), `--expect-question` (judge with `question_checks`; use with `--sentence "<MISSING_TIME_SENTENCE>"`), `--timeout` (seconds per tool run, default 900), `--wait-timeout` (default 1800), `--log-dir` (default `ROOT/"docs"/"superpowers"/"reviews"/"agent-gate"`), `--keep` (leave the folder, the app and the run), `--fixture DIR` (build `DIR/repo` and `DIR/home`, print `EVOLVEKIT_HOME=<DIR/home>` and `cd <DIR/repo>`, exit 0 — the persona rounds' materials), `--no-harness` (with `--fixture`: no `harness/`). Flow:
  1. `ToolMissing` → print `FAIL tool: <tool> is not installed` and return 2 (the gate did not run).
  2. `work = Path(tempfile.mkdtemp(prefix="evk gate "))` (a space in the path on purpose); `build_fixture(work/"repo", home=work/"home")`; `env = gate_env(work/"home")`; `stamp = datetime.now().strftime("%Y%m%d-%H%M%S")`.
  3. `out1 = run_tool(..., transcript=log_dir/f"{stamp}-{tool}-1.txt")`; results = `question_checks` with `--expect-question`, else `checks`.
  4. Without `--expect-question` and with `study_started_or_queued` ok: `[sys.executable, "-m", "evolvekit", "study", "wait", slug, "--home", str(home), "--timeout", str(wait_timeout)]` as a subprocess with `env`; exit ≠ 0 → add `{"name": "study_finished", "ok": False, "detail": "study wait exited N"}`; exit 0 → `document = build_verdict(root)`, `out2 = run_tool(tool, repo, REPORT_PROMPT.format(slug=slug), ..., transcript=log_dir/f"{stamp}-{tool}-2.txt")`, results += `report_checks(document, out2)`.
  5. Print `PASS <name>` / `FAIL <name>: <detail>` per check and the transcripts' paths.
  6. Unless `--keep`: `evolvekit study stop <slug>` when the run has not ended; stop the app `study open` started (`read_running(home)` → `os.kill(pid, signal.SIGTERM)`, errors ignored); `shutil.rmtree(work, ignore_errors=True)`.
  7. Return 0 when every check is ok, else 1. `if __name__ == "__main__": sys.exit(main())`.

- [ ] **Step 4: Run the tests to see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_agent_gate_script.py -q` — Expected: PASS.
Run: `.venv/Scripts/python.exe -m pytest tests -q --collect-only -m "not slow" | grep -c agent_gate.py` — Expected: `0` (the script itself is not collected).
Run: `.venv/Scripts/python.exe -m ruff check --select F,E9 tests/agent_gate.py tests/test_agent_gate_script.py` — Expected: no findings.

- [ ] **Step 5: Commit**

```bash
git add tests/agent_gate.py tests/test_agent_gate_script.py
git commit -m "gate: a fresh agent turns one sentence into a running study, and reports only the verdict"
```

---

### Task 6: The persona gate and the no-harness round

Process, not code. The controller runs it, one reviewer at a time (never beside `python tasks.py check`). Every reviewer is a fresh general-purpose agent told only the persona, the materials and the note below; it never opens `.agents/`, `.claude/` or the evolvekit repository's sources.

- [ ] **The gate script, Claude Code, complete sentence:** `.venv/Scripts/python.exe tests/agent_gate.py --tool claude` → every check PASS (about 25 minutes: the tool, a 10-minute study, the tool again). Log the output.
- [ ] **The gate script, the missing answer:** `.venv/Scripts/python.exe tests/agent_gate.py --tool claude --expect-question --sentence "Use evolvekit to find solver settings that give shorter tours on the requests in cases/; the solver is solver.py in this folder; hold two back; today's settings are the starting point."` → `no_study_made` and `asks_the_missing_question` PASS.
- [ ] **Copilot CLI:** `where copilot`; installed → both gate runs with `--tool copilot`; absent → log "Copilot CLI: not installed on this machine (2026-11-07); the gate was not run with it". (Not installed at plan time.)
- [ ] **Round A (the study skill), materials:** `.venv/Scripts/python.exe tests/agent_gate.py --fixture "<scratch>/round-aN"`: a repository with the skills installed, the demo-tour harness in `harness/`, `solver.py`, `cases/`, and a clean library home.
- [ ] **Round A, persona and note** (give exactly this):

  > You are an engineer at a logistics company. Your tour solver is `solver.py` in the folder `<repo>`; your requests are in `cases/`. You have never used evolvekit; a colleague installed it and said your coding agent knows how. Work only through the agent: in a shell in `<repo>`, with `EVOLVEKIT_HOME=<home>` set, send your first message with `claude -p "<message>"` and every reply with `claude -p --continue "<reply>"`. Do not open `.agents/`, `.claude/` or any evolvekit source. Your first message is: "Use evolvekit to get shorter tours out of my solver, solver.py, on the requests in cases/." Answer its questions as an engineer would: hold two requests back; today's settings are the starting point; the first time it asks how long in total, say 30 seconds; when it shows you a plan, ask for ten minutes in total instead. When it gives you an address, open it in the in-app browser and look. When the study has ended (about ten minutes), ask the agent "Is it done? What did it find?". Then report: an overall score from 1 to 10 for "How easy was it to get from one request to a running study you could see, and to its answer?"; sub-scores for the questions it asked (did it ask what you had already said?), the plan before the start (did it wait when the plan was thin?), the hand-over, and the answer (did it claim anything the Study page's verdict does not?); the worst frictions (where, what happened, why it hurt, what would fix it, severity blocker / major / minor); what would make it a 10.

- [ ] **Round A, controller's checks after the round:** the transcript shows no question about something the first message or an earlier answer said; with 30 seconds the plan was shown with its ways out and nothing started (or, if that plan had 3+ rounds, the round notes it); the final answer contains the verdict's `line` (`evolvekit study verdict <slug> --home <home>`) and no other percentage.
- [ ] **Round B (the harness skill), materials:** `.venv/Scripts/python.exe tests/agent_gate.py --fixture "<scratch>/round-bN" --no-harness`.
- [ ] **Round B, persona and note** (give exactly this):

  > You are an engineer whose application is `solver.py` in the folder `<repo>`; your requests are in `cases/`. evolvekit does not know it yet. Work only through your coding agent, as in this note: in a shell in `<repo>`, with `EVOLVEKIT_HOME=<home>` set, `claude -p "<message>"` first and `claude -p --continue "<reply>"` after. Do not open `.agents/`, `.claude/`, `harness/evk_harness.py` or any evolvekit source. Your first message is exactly these two sentences: "Put solver.py behind evolvekit. It is a tour solver with a command line; I want to tune its settings on my requests." Answer its questions from what you see in `solver.py --help` and `cases/`. Report: an overall score from 1 to 10 for "How easy was it to get your application behind evolvekit?"; sub-scores for the questions it asked, what it wrote, the check loop, and the smoke test; the worst frictions with severity; what would make it a 10.

- [ ] **Round B, controller's checks after the round:** `.venv/Scripts/python.exe -m evolvekit harness check "<repo>/harness" --app "<repo>/solver.py" --json` exits 0; `cmp "<repo>/harness/evk_harness.py" evolvekit/harness/sdk/evk_harness.py` reports no difference; the transcript shows the smoke test's `study preview` line.
- [ ] **Loop:** a round below 8, or with a blocker, gets its fixes in the skills (Tasks 1–3's files), the static tests re-run (`.venv/Scripts/python.exe -m pytest tests/test_skills_content.py tests/test_skills_install.py -q`), a commit per round (`skills: round A<N> fixes: …`), and a new round with a fresh reviewer and fresh materials. Stop each of A and B at 8+ with no blocker.
- [ ] **Log** every round in `docs/superpowers/reviews/2026-11-07-engineer-gate-log.md`, in the intern log's shape: a header (the gate of design §4.4/§8, the set-up of every round: reviewer, persona, materials, note — the notes above verbatim); per round `## Round A<N> — <score>/10, <blockers or "no blocker">`: the sub-score table, what happened (the sentence, the questions, the plan, the study, the answer), frictions and what changed, the transcript's path; the gate script's runs with their PASS/FAIL lines and transcript paths.
- [ ] **Commit:** `git add docs/superpowers/reviews/2026-11-07-engineer-gate-log.md docs/superpowers/reviews/agent-gate/` and `git commit -m "gate: the engineer gate's rounds"`.

---

### Task 7: Docs and the PR

- [ ] **README, `### For coding agents`** (under *Harnesses and studies*; the heading is kept, the command table links `#for-coding-agents`). Replace the paragraph that says both skills are placeholders with:
  - the install block as it is (`skill list`, `skill install`) and where the files land (`.agents/skills/<name>/` with `references/evolvekit.md`; the `.claude/skills/` pointer);
  - one paragraph per skill: `evolvekit-study` — tell your coding agent, in one sentence, what to tune, on which cases, against what and for how long; it asks only what you did not say, makes the study, shows the plan, starts it, gives you the study's address in the app, and later reports the verdict's sentence and nothing more. `evolvekit-harness` — for an application evolvekit does not know yet: it reads what the application exposes, writes `harness/`, runs `harness check` until it exits 0, and never edits `evk_harness.py`;
  - "Both are Markdown: read them in `.agents/skills/`.";
  - the gate: `python tests/agent_gate.py --tool claude` (about 25 minutes; never beside `python tasks.py check`), what it checks in one sentence, and `--expect-question`.
- [ ] **`docs/harness-authoring.md`**: a new `## With an AI coding tool` between the introduction and `## First: what the application exposes`: `python -m evolvekit skill install` in the application's repository, then ask the tool to put the application behind evolvekit; the `evolvekit-harness` skill follows this page's steps and the check loop, into `harness/`.
- [ ] **Docs check:** `.venv/Scripts/python.exe -m pytest tests/test_skills_content.py tests/test_skills_install.py tests/test_agent_gate_script.py -q` PASS; every command the new README text names is in the reference's table.
- [ ] **Commit:** `git add README.md docs/harness-authoring.md` and `git commit -m "docs: the skills and the agent gate"`.
- [ ] **The gate:** with nothing else running (no demo study, no agent gate), `python tasks.py check` → exit 0.
- [ ] **PR:** `git push -u origin agent/2-skills`; `gh pr create --base master --head agent/2-skills --title "Agent path 2: skills and the agent gate"` with a body: what ships (the two skills, the reference, the static tests, the gate script), stacked on project 1's PR, the gate script's results, the persona rounds' scores (A and B, final round and count), Copilot's status, this plan's path, the Decisions below, and the attribution line. A maintainer merges, with a merge commit.

---

## Decisions

1. **Exact sentences pinned by the tests** (the spec fixes their meaning, not their words): `REPORTING_RULE`, `PRE_VERDICT_RULE`, the four `COMPLETENESS` sentences, `HAND_OVER`, `HAND_OVER_QUEUED`, `MEASURES_RULE`, `THIN_PLAN_RULE`, `CHECK_RULE`, `SDK_RULE`, `EXIT_CODES`. The plan's "report only the verdict's line" became the capitalised full sentence `REPORTING_RULE`.
2. **A queued hand-over** (`HAND_OVER_QUEUED`): the plan's template assumes a started run; a queued one has no honest "ends around".
3. **The per-case time defaults to the harness's** (`limits.time_per_case_s` from the template) when the request does not say, and the plan line names it; only the goal and the total time are never chosen by the skill. Without this the spec's own gate sentence would be incomplete (it gives no per-case time).
4. **The report is written to a file, never pasted:** `study report SLUG --markdown > evolvekit-report.md`, and the answer is the line and the advice. Pasting it would break the gate's "no other percentage claim".
5. **Gate checks:** `study_started_or_queued` accepts `queued`/`starting`/`search`/`check` only — a `done` run in a one-shot run means the agent waited; `report_equals_verdict_line` allows the verdict's `line` **and** `advice` (the advice says "probably better" by design) and judges percentages only, as the spec says; claim words in the second answer are left to the persona.
6. **Gate mechanics:** claude reads the sentence on stdin (a `.cmd` on Windows mangles quoted arguments); `--add-dir <home>` so it can edit `study.yaml` outside the repository; `--no-session-persistence`; the work folder has a space in its name; the fixture is `git init` without a commit; the gate stops the run and the app `study open` started, unless `--keep`; transcripts are redacted (`token=…`) and committed under `docs/superpowers/reviews/agent-gate/`; a missing tool exits 2, not 1. Added flags beyond the plan: `--expect-question`, `--wait-timeout`, `--log-dir`, `--fixture`, `--no-harness` (the persona rounds' materials come from the same fixture).
7. **Interfaces sharpened:** `build_fixture(repo, *, home, harness=True)` (the plan's `dir` shadows a builtin; `harness` for round B); `run_tool` takes `env` and `transcript`; `checks` split into `checks`, `question_checks`, `report_checks`, plus `claims`, `the_study`, `tool_command`, `gate_env`, `redact`.
8. **Tests added beyond the plan's names:** `test_command_variants_expands_usage`, `test_reference_covers_every_command`, `test_reference_states_the_exit_codes`, `test_reference_names_every_verdict_key` (also every `DECISIONS` value, from the code), `test_reference_has_the_rules`, `test_study_skill_has_the_six_steps`, `test_study_skill_has_the_completeness_rule`, `test_study_skill_has_the_hand_over`, `test_study_skill_takes_measures_from_the_harness`, `test_study_skill_stops_on_a_thin_plan`, `test_study_skill_says_what_to_do_when_it_goes_wrong`, `test_skills_use_the_apps_words`, `test_harness_skill_has_the_steps_in_order`, `test_sentences`, `test_question_checks_truth_table`, `test_tool_command_is_one_shot`.
9. **The path test:** spans starting `study.yaml`, `cases/`, `inputs/` must exist in a fresh study (`study new` + `study cases`; `harness/` is left out because the skill also names the repository's `harness/<id>/`); `runs/<id>/…` paths, which no fresh study has, must name a file a run writes (`job.json`, `job.log`, `downloads/`, `confirm/final/`, `evolvekit.yaml`).
10. **The vocabulary test** reads prose only (code spans and fenced blocks are engine names) and also covers both `SKILL.md` bodies; the Words table's last column is the only place the six words may appear.
11. **Vendor list** (case-insensitive substrings, `.claude` included): claude, anthropic, copilot, github, openai, codex, cursor, gemini, chatgpt, microsoft, azure, openrouter.
12. **`doctor`'s provider ✗ does not stop the study skill** unless the study turns AI help on: a fresh home's default provider has no key, so `doctor` exits 1 on every clean machine.
13. **Harness skill → harness-authoring:** the repository of the engineer has no `docs/`; the two rules' paragraphs stand on their own and cite "evolvekit's `docs/harness-authoring.md#…`".
14. **README:** the existing `### For coding agents` is rewritten in place (its anchor is linked from the command table) instead of adding a second section "For AI coding tools".
15. **The shared reference is copied at install only**, as project 1 built it; no build-time copy into each skill's `references/` (spec §4.1 also names "the package build").
16. **Persona round A** starts from an incomplete message and includes a 30-second total to exercise the thin plan; the complete sentence is the gate script's job.

Taken by the controller after the builder check (a fresh engineer's questions on this plan):

17. **The persona's tool runs like the gate's.** `build_fixture` writes the fixture repository's own tool settings (`.claude/settings.json` with `permissions.allow` for Bash, PowerShell, Read, Edit, Write, Glob, Grep and `permissions.additionalDirectories: [<home>]`, the one vendor-named file the design excepts, since it is the tool's own), and `--fixture` prints the three lines to set before working there: `EVOLVEKIT_HOME=…`, `PATH=<the venv's Scripts folder>;…` and `cd …`. The persona notes tell the reviewer to set those three lines and then run plain `claude -p "<message>"` in the fixture; otherwise both persona rounds would fail on setup, not on the skills.
18. **"Thin" is what `study compile` blocks on** (fewer than 3 rounds): the skill stops there with the three ways out. A plan with 3+ rounds goes ahead; when it has fewer than 6 combinations per setting tuned (the Study page's "thin" warning), the hand-over adds one sentence: "The app will call this plan thin: N settings in M combinations; more time or fewer settings would sharpen it." The numbers come from `study compile`'s output.
19. **Templates come from the harness folder.** The reference says where: `harness/<id>/templates/*.yaml` in the repository, or the study's pinned copy `<study>/harness/templates/` after `study new`. The skill picks the template whose goal matches the request's measure and direction (`--template <name>`); with no fitting template it makes a blank study and writes `vary.settings: recommended`, `kpis` from the harness's measures by name (`{from: harness}`) and `goal` from the interview — never invented SQL.
20. **The harness skill's smoke test uses the template the skill just wrote** (`study new "Smoke test" --harness harness/ --template <it> --app PATH`), so `study check` exits 0 and `study preview` solves a sample; a check that fails there is the harness's fault and the loop goes on.
21. **The persona reviewer has the in-app browser** (the project 1 rounds used it); the note keeps "open it in the in-app browser".
