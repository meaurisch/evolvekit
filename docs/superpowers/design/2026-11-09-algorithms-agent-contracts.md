# Algorithms programme: the implementer and reviewer contracts

The contracts the first session (A0, A1) gave every implementer and task reviewer. A2–A4 reuse them.
Each dispatch names the contract, the environment notes and the task's brief (plan header + task + Decisions).

## Implementer contract (algorithms programme)

You implement ONE task of a plan in `C:\Users\norri\studio\evolvekit`, on the current branch.

1. Read the environment notes file named in your dispatch (venv Python, no full gate, Write/Edit
   tools only, LF endings, commit attribution, vocabulary rules). They bind you.
2. Read your brief (`task-<id>-full.md`): the plan header with Global Constraints, your task with
   exact values to use verbatim, and the controller's Decisions, which override the task text
   where they disagree.
3. TDD: write the failing tests first and run them to see them fail (RED), implement, run them to
   see them pass (GREEN). Then run every fast test file that imports a module you changed
   (`grep -rl` for it under tests/) with `.venv/Scripts/python.exe -m pytest <files> -q -m "not slow"`,
   and `.venv/Scripts/python.exe -m ruff check --select F,E9 evolvekit tests`. Run a `slow` test only
   if your brief names it, never two at once. Never run `python tasks.py check`.
4. When a formula in the design and a test disagree, re-derive the formula before touching the test,
   and say what you derived in the report.
5. Commit on the current branch with the brief's commit message plus a blank line and
   `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push, never `git stash`, never
   commit `.superpowers/`.
6. If the brief is unclear or contradicts the code in a way that matters, stop and reply
   NEEDS_CONTEXT with the specific question rather than guess. Small gaps: decide like a careful
   colleague and list the decision in the report.
7. You do not dispatch subagents — not helpers and never a reviewer. The controller reviews your diff.
8. Self-review your own diff: complete against the brief, nothing extra, tests that would fail on
   the old code, pristine test output.
9. Write the full report to the report path in your dispatch: what you implemented, decisions you
   took, tests and results, TDD evidence (RED command + failing output; GREEN command + passing
   output), files changed, self-review findings, concerns.
10. Reply with ONLY (under 15 lines): Status (DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT),
    commits (short SHA + subject), one-line test summary, concerns, report path.

If you are resumed with review findings: fix them, re-run the covering tests, APPEND a fix report
(what changed, tests, command, output) to the same report file, commit, reply with the same contract.

## Task reviewer contract (algorithms programme)

You review ONE task's implementation in `C:\Users\norri\studio\evolvekit`: first spec compliance,
then quality. Task-scoped gate, not a merge review.

- The brief (`task-<id>-full.md`) is what was requested: plan header + Global Constraints, the
  task, and the controller's Decisions (which override task text). The design section named in
  your dispatch is the binding authority behind it.
- The implementer's report is unverified claims; check them against the diff. Stated rationales
  never downgrade a finding.
- The diff file is your view of the change: read it once. Inspect code outside it only to
  evaluate a concrete risk you can name (a changed contract's call sites, resume/replay paths,
  shared state) — one focused check per risk, and say what you checked.
- Read-only: never modify the tree, index, HEAD or branches. Do not re-run the suite; run a
  focused test only for a specific doubt (`.venv/Scripts/python.exe -m pytest <file>::<test> -q`).
  Do not dispatch subagents.
- Statistics: re-derive every formula the task implements; a formula that disagrees with the
  design, or a test that asserts the wrong thing, is Critical.
- Testing standards: every function the task names has a unit test; tests would fail on the old
  code; statistical rules have a seeded simulation test whose name states the error rate it
  asserts; CLI tested through `main([...])`; output pristine; Windows-safe paths.
- Words: sentences for people use the app's vocabulary (study, case, held-back case, starting
  point, measure, goal, rule, combination, round, final check; never KPI, seed, generation,
  instance, config, baseline); `--json` keeps engine key names; nothing says "better" before the
  verdict does.

Report (under 50 lines):
- **Spec:** ✅ or ❌, with Missing / Extra / Misunderstood items.
- **Findings:** each labelled Critical / Important / Minor, with file:line and a one-line fix.
- **⚠️ Cannot verify from diff:** items that live in unchanged code or span tasks.
- **Task quality:** Approved or Not approved.

Scoped re-review (when your dispatch lists earlier findings): verdict each finding ADDRESSED or
NOT ADDRESSED with file:line evidence, and flag only NEW Critical/Important breakage in the fix
diff; anything else out of scope goes under "Deferred minors".

## Environment notes (this machine)


- Repository: `C:\Users\norri\studio\evolvekit`. Windows 11. Git Bash is the shell behind the Bash tool.
- **Python is the project venv:** run tests as `.venv/Scripts/python.exe -m pytest ...` (never bare `python`/`pytest`: the global Python has no ruff and no PyVRP). Lint: `.venv/Scripts/python.exe -m ruff check --select F,E9 evolvekit tests`.
- `pytest -m "not slow"` is the fast loop; tests marked `slow` run real demo-tour studies and take minutes — run them only when your task says so, and never two of them at once.
- Never run `python tasks.py check` (the full gate) yourself; the controller runs it once per project. Never start `evolvekit app` and leave it running.
- Write files with the Write/Edit tools, never with a shell heredoc (backslashes get mangled) and never with PowerShell `Set-Content` (BOM damage). Keep files LF-ended as the repository has them.
- Windows first: paths with spaces, drive letters, no symlinks, detached processes; CI also runs on Linux, so nothing Windows-only without a platform guard.
- The vocabulary for sentences meant for people: study, case, held-back case, starting point, measure, goal, rule, combination, round, final check — never KPI, seed, generation, instance, config, baseline. `--json` output keeps the engine's key names.
- Generic names: nothing new named after Claude, Copilot or any vendor.
- Nothing you build or write says "better" before the verdict does; the verdict's `line` is the only claim.
- Commit on the current branch with the message your brief gives, and end the commit message with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Never push. Never run `git stash`. Never commit `.superpowers/`.
- Do not edit the README or docs unless your brief names them.
