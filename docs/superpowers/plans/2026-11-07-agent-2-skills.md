# Agent path, project 2: Skills and the agent gate — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Before executing:** this plan is at task level. Re-read the code as it is after project 1 landed, expand every task to step level with superpowers:writing-plans (keep the tasks, files, interfaces and test names below), and run the builder check on the result.

**Goal:** two skills a coding agent follows — one to run a study from a problem description, one to put an application behind evolvekit — and a gate in which a fresh agent turns one sentence into a running, visible study.

**Architecture:** the skills are Markdown shipped in the package (`evolvekit/skills/`), installed by project 1's `skill install`. They are interview scripts plus the CLI; everything deterministic is a command. Their tests are static (the text) and behavioural (a gate script that drives the real tool, run by hand; a persona review loop).

**Tech stack:** Markdown in the Agent Skills format (`SKILL.md` with `name` and `description` frontmatter, `references/`); Python for the tests and the gate script.

**Spec:** [docs/superpowers/specs/2026-11-07-agent-path-design.md](../specs/2026-11-07-agent-path-design.md), §2 and §4.

## Global constraints

- Each `SKILL.md` body ≤ 150 lines; frontmatter has `name` (= folder) and `description` (starts with what it is for, ends with "Use when …", < 1024 characters), nothing else (spec §4.1).
- No vendor name in any skill file; the skills name only commands that exist (spec §2, §4.4).
- The study skill never reports more than the verdict's `line`; the training gain is named "on the cases it learned from" (spec §4.2 step 6).
- The plan and the cost are shown and a yes is waited for before `study start` (spec §4.2 step 4).
- Branch `agent/2-skills` from `agent/1-one-world`; PR against `master`, stacked; merge commit.

## Review focus

1. The engineer's one sentence already contains the measure, the time and the cases: the skill must not re-ask what was said (persona round 1 checks it; Task 2's text has the rule "ask only what the request did not say").
2. The harness has no template and the study needs a goal: the skill writes `kpis`/`goal` from the harness's measures by name, never invents SQL (Task 2; static test that the skill names `{from: harness}` and `study check`).
3. `study start` fails because the plan is thin or impossible: the skill shows the three ways out and stops for the engineer (Task 2; gate check "no start without a yes").
4. The tool's non-interactive mode cannot ask: the gate's sentence carries every answer, so under the completeness rule the skill reaches `study start` without a question; a sentence with one answer missing must instead end with that question and no study started (Task 5's `study_started_or_queued`, and a second gate run with `--sentence` lacking the time, expecting `study_exists` false and the question in the output).
5. An agent that edits `evk_harness.py` to make `harness check` pass: the harness skill forbids it and the no-harness gate round diffs the SDK file against the package's (Task 3 text; Task 6 round).

---

## File map

| File | Responsibility |
|---|---|
| `evolvekit/skills/shared/evolvekit.md` | the shared reference: vocabulary, layouts, commands with exit codes, the verdict document's keys, the rules |
| `evolvekit/skills/evolvekit-study/SKILL.md` | the study skill |
| `evolvekit/skills/evolvekit-harness/SKILL.md` | the harness skill |
| `tests/test_skills_content.py` | the static tests |
| `tests/agent_gate.py` | the gate script (run by hand, not collected) |
| `docs/superpowers/reviews/2026-11-07-engineer-gate-log.md` | the persona rounds |
| `README.md`, `docs/harness-authoring.md` | "For AI coding tools": the skills, `skill install`, the gate |

### Task 1: The shared reference

**Files:** create `evolvekit/skills/shared/evolvekit.md`; test `tests/test_skills_content.py`.

**Produces:** sections, in order: *Words* (the vocabulary table of the app, say / meaning / not); *Where things live* (the library home, a study folder, a harness folder, the run folder's files); *Commands* (one table: command, what it does, exit codes — every row copied from the CLI's help, spec §3.2); *The verdict document* (its keys, spec §3.4); *Rules* (verdict-only reporting; plan before start; never edit the SDK; the comparability trap; every value in range is valid). ≤ 200 lines.

**Tests:** `test_reference_commands_parse` (every backticked `evolvekit …` line, with `SLUG`/`NAME`/`PATH`/`ID`/`FILE`/`N` replaced by sample values, parses with `build_parser().parse_args` and `--help` never raises); `test_reference_has_the_sections_in_order`; `test_reference_vocabulary_matches_app` (the "never on screen" words — KPI, seed, generation, instance, config, baseline — appear only in the Words table's "Not" column).

### Task 2: The study skill

**Files:** `evolvekit/skills/evolvekit-study/SKILL.md`.

**Produces:** the six steps of spec §4.2 as headed sections with the exact commands (`study application` among them); the interview as a checklist of eight questions with the completeness rule of spec §4.2 ("a request that answers every question is complete: go on without asking; one unanswered question stops you with that question, in one message; in a one-shot run say what is missing and end"); the hand-over sentence template ("Your study is running: <url>. It tries about N combinations in R rounds and ends around HH:MM; the answer is on the Study page."); the reporting rule with its wording; a *When it goes wrong* list (no harness → the harness skill; `study check` problems; thin plan; provider missing → `doctor`'s fix; the run failed → `study status` and the failure's log path). Body ≤ 150 lines.

**Tests** (in `test_skills_content.py`): `test_study_skill_frontmatter`, `test_study_skill_length`, `test_study_skill_commands_parse`, `test_study_skill_paths_exist_in_a_fresh_study` (every `study.yaml`, `cases/`, `runs/` path named exists after `study new` + `study cases`), `test_study_skill_has_the_reporting_rule` (the sentence "report only the verdict's line" verbatim), `test_no_vendor_names` (none of a fixed list of vendor and product names in any skill file).

### Task 3: The harness skill

**Files:** `evolvekit/skills/evolvekit-harness/SKILL.md`.

**Produces:** spec §4.3 as headed steps: `harness discover`; the interview (five questions); `harness new --kind … harness/`; tables first, then `runner.py`, settings with `help`/`explain`, levers, measures, templates, samples; the check loop with "never declare done before exit 0"; "never edit `evk_harness.py`"; the comparability trap and the valid-range rule, each in one paragraph pointing to `docs/harness-authoring.md`; the smoke test (the study skill's step 3 on the samples); version bump on change. Body ≤ 150 lines.

**Tests:** as Task 2's, plus `test_harness_skill_forbids_editing_the_sdk`.

### Task 4: The static test suite is complete and runs in the gate

**Files:** `tests/test_skills_content.py`.

**Produces:** the tests of Tasks 1–3 plus `test_installed_copies_equal_package` (after `skill install` into a temp dir, every installed file equals its package source byte for byte, pointers aside) and `test_pointer_frontmatter_equals_skill`. All run under `python tasks.py test`.

### Task 5: The gate script

**Files:** create `tests/agent_gate.py` (a script with `argparse`; `pyproject`'s `testpaths` keeps pytest from collecting it since it does not start with `test_`).

**Produces:**
- `build_fixture(dir: Path, *, home: Path) -> None`: a git repository with `skill install` run in it, `harnesses/demo-tour` copied to `harness/`, `examples/cli-solver/solver.py` copied to the root as `solver.py`, its four samples in `cases/`, a `README.md` of two lines, and `EVOLVEKIT_HOME` pointed at `home` (empty).
- `run_tool(tool: "claude" | "copilot", cwd: Path, sentence: str, *, timeout_s: int) -> str`: `claude -p <sentence> --allowedTools "Bash Read Edit Write Glob Grep"` or `copilot -p <sentence> --allow-all-tools` (the exact flags are the tools' as of the build; keep them in one place), captured output, written beside the log. Each call is a fresh, one-shot run; the gate never resumes a session.
- `checks(home: Path, output: str) -> list[dict]`: `study_exists`, `application_set` (`study.yaml` has `application.path`), `study_started_or_queued` (`job.json` phase in `queued`/`starting`/`search`/`check`), `output_names_the_url` (`http://127.0.0.1:` and `#/study/`), `no_claim_of_improvement` (none of "better", "improved", "faster", "cheaper", "% lower" in the output before the verdict exists); then the gate runs `study wait`, runs the tool again with "The evolvekit study <slug> has finished; report its result.", and checks `report_equals_verdict_line` (the verdict's `line` appears verbatim in that second output, and no other percentage claim does).
- `main(argv)`: `--tool`, `--sentence` (default spec §4.4's), `--timeout`, `--keep`; prints PASS/FAIL per check and exits 0/1. The default sentence is complete under the skill's completeness rule: "Use evolvekit to find solver settings that give shorter tours on the requests in cases/; the solver is solver.py in this folder; hold two back; ten minutes in total; today's settings are the starting point."

**Tests** (pytest, in `tests/test_agent_gate_script.py`): `test_build_fixture_layout`, `test_checks_on_a_fake_output` (a hand-written output and home: each check's truth table), `test_no_claim_detector_ignores_the_verdict_line`.

### Task 6: The persona gate and the no-harness round

- Round A (the study skill), as spec §4.4's persona gate, in Claude Code; once more in Copilot CLI when it is installed on the machine (record "not installed" otherwise). Repeat until 8+ with no blocker.
- Round B (the harness skill): the reviewer plays an engineer whose application is `examples/cli-solver/solver.py` and whose instruction is two sentences ("It is a tour solver with a command line; I want to tune its settings on my requests."); it must produce a harness in `harness/` that passes `harness check --app <python> --json` with exit 0, without editing `evk_harness.py` (the round diffs it against `evolvekit/harness/sdk/evk_harness.py`). Repeat until 8+.
- Log every round in `docs/superpowers/reviews/2026-11-07-engineer-gate-log.md` (persona, materials, note, transcript summary, score, blockers, fixes).

### Task 7: Docs and the PR

- README: a section "For AI coding tools" under *Harnesses and studies*: `evolvekit skill install`, where the files land, what each skill does, the gate script's command; `docs/harness-authoring.md`: the harness skill as the recommended way, with the same install line.
- `python tasks.py check` green (nothing else running); PR `agent/2-skills` against `master`, stacked on project 1, with the decisions taken, the gate log's scores and the plan's path.
