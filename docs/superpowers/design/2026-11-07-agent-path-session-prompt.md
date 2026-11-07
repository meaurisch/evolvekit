# The agent path: the session prompt

Paste everything below the line into a new Claude Code session opened in the evolvekit checkout (`C:\Users\norri\studio\evolvekit`), on `master`, up to date, with `docs/superpowers/specs/2026-11-07-agent-path-design.md` and the five plans merged. It runs the whole programme: five projects, five stacked PRs. The owner is not in the session.

---

You are building the agent path of evolvekit: five projects, in order, each ending in a pull request. You run autonomously. The owner has approved the design and will not be in this session: do not ask them anything; take every decision from the design, and where it is silent, decide the way a careful colleague would, write the decision down, and go on. Quality matters more than speed: there is no deadline.

## What to read first

1. `AGENTS.md` (the repository's rules: never push to master, `python tasks.py check` is the gate, no secrets).
2. `docs/superpowers/specs/2026-11-07-agent-path-design.md` — the design. Every decision is there; §2 holds the rules that apply to every project, §8 the persona gates, §9 the testing standards.
3. The plan of the project you are on, in `docs/superpowers/plans/`: `2026-11-07-agent-1-one-world.md`, `…-agent-2-skills.md`, `…-agent-3-code.md`, `…-agent-4-over-time.md`, `…-agent-5-installer.md`.
4. `docs/superpowers/reviews/2026-09-26-intern-review-log.md` — how a persona gate is run and logged.

Do not read the README end to end; read the sections a task points you to.

## Step 0: a clean start

- `git status --porcelain` must be empty on `master`, up to date with `origin/master`. If tracked files are modified, commit them as one isolated commit first; leave untracked scratch alone. Never push to `master`.
- `git config --local user.email` must be `26815119+meaurisch@users.noreply.github.com` and `user.name` `meaurisch` (GitHub refuses pushes with the private address).
- `python tasks.py check` must be green before you change anything. If it is not, stop and report; do not fix the suite as a side quest.
- Keep a running log at `docs/superpowers/reviews/2026-11-07-agent-path-build-log.md`: one dated entry per project with the decisions taken, the gate counts, the persona scores and the PR link. Commit it with each project.

## The loop, for each project in order

1. **Branch.** `agent/1-one-world` from `master`; every later project's branch from the previous project's branch (`agent/2-skills` from `agent/1-one-world`, and so on). Stacked PRs, merge commits, a maintainer merges; you never merge.
2. **Plan to step level.** Project 1's plan is already at step level: use it as it is. For projects 2–5: re-read the code as it is now (the earlier projects changed it), expand the plan to step level with the `superpowers:writing-plans` skill — keep every task, file, interface and test name the plan gives; add the steps, exact signatures and the test bodies — and save it in place. Then the builder check: dispatch one fresh subagent with the brainstorming skill's `builder-check-prompt.md` (`C:\Users\norri\.claude\plugins\cache\claude-plugins-official\superpowers\7.0.0\skills\brainstorming\builder-check-prompt.md`) on the design's section and the expanded plan; answer its questions from the design, and for the rest decide yourself and record each call in a "Decisions" section at the end of the plan. Commit the expanded plan.
3. **Build with `superpowers:subagent-driven-development`.** One task at a time, in the plan's order. Per task: an implementer subagent that sees only its task, the plan's header and the design's section (use `studio-executor` for well-specified single-concern tasks, a general-purpose subagent on the most capable model for multi-file design tasks such as plan 1's Task 2, plan 3's Tasks 2, 5 and 9, plan 4's Tasks 2, 6 and 9); then a fresh reviewer subagent on the diff against the task's requirements and the testing standards (design §9). A task is done when its tests pass, the reviewer has no blocker, and it is committed with the plan's message. Never implement on the main thread beyond one-line fixes a reviewer asks for; never read source files on the main thread beyond what planning needs.
4. **Agents one or two at a time.** Never more than two subagents at once: wider fan-outs have exhausted this machine's session window before. Tasks that share a file (the plan's shared-file map) run strictly one after the other.
5. **A task that will not go green.** Two attempts by fresh implementers, with the first's failure in the second's brief. Still red: revert the task's changes so the tree is clean, write what failed and why in the build log, and continue only when no later task of the project depends on it; otherwise stop the project there, open the PR as a draft with what exists, and go on to report (do not start the next project on a broken base).
6. **The gate.** `python tasks.py check` on the project's branch, green, with nothing else running on the machine: the full suite has timing-sensitive tests, so no demo study and no app may run beside it. Paste the counts into the build log.
7. **The persona gate** of design §8 for the project: a fresh general-purpose subagent that is told only the persona, the materials and the note (never the code, the design or earlier reviews); it drives the real thing — the terminal, and the in-app browser for the app — and scores out of 10 with blockers named. Fix what it found, repeat until 8 or more with no blocker; log every round in `docs/superpowers/reviews/` as the intern log does. Project 2's gate also runs `tests/agent_gate.py` with `--tool claude`; with `--tool copilot` only when `copilot` is on `PATH` (record "not installed" otherwise, never fake it).
8. **Docs** are part of the project: the README, `docs/app.md` and `docs/harness-authoring.md` rows and sections the plan names, in the same PR.
9. **The PR.** Push the branch; `gh pr create` against `master` with: the project's goal in one line, the decisions taken while building, the gate counts, the persona log's final score, the plan's path, and the attribution line this environment's rules require. Then the next project.

## Rules that are easy to forget on this machine

- Windows is the first platform. Test detached processes, paths with spaces, CRLF and the absence of symlinks on this machine; CI will run the same suite on Linux.
- This Bash tool collapses backslashes in heredocs: write files with the Write tool, never with a heredoc, when a backslash matters.
- Never edit source with PowerShell `Get-Content`/`Set-Content` (BOM and code-page damage); use the Edit and Write tools.
- Run anything likely to exceed two minutes in the background and wait for its notification; never `Start-Sleep` to wait.
- Never run `git stash`; a temporary WIP commit sets work aside.
- Generic names everywhere: `.agents/skills/`, `harness/`; no vendor names in new files, commands or folders.
- The verdict is the only claim: nothing you build, write or log says "better" before the final check does. The skills, the report and the PR body are generated from the verdict document.
- No GitHub Actions, no remote workers, no server: everything runs on this computer.
- `tasks.py` is the only entry point: `python tasks.py test | full | run | check | lint` (and `dist` once project 5 adds it). Never invent others.

## When you are done

Run `python tasks.py check` once more on the last branch, then write the final entry of the build log: per project, the PR link, PASS or the task that stopped it, the gate count and the persona score. Say it in one message, and stop. Do not merge anything, do not start further work, do not tidy the sidebar.
