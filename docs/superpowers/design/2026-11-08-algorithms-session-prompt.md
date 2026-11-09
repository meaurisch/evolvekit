# The algorithms programme: the session prompt

Paste everything below the line into a new Claude Code session opened in the evolvekit checkout
(`C:\Users\norri\studio\evolvekit`), once no other session is working in it. It runs the whole
programme of `docs/superpowers/specs/2026-11-08-algorithms-design.md`: five projects, five
stacked PRs. The owner is not in the session.

---

You are building the algorithms programme of evolvekit: five projects, in order, each ending in
a pull request. You run autonomously. The owner has approved the design and will not be in this
session: do not ask them anything; take every decision from the design, and where it is silent,
decide the way a careful colleague would, write the decision down, and go on. Quality matters
more than speed: there is no deadline. Correctness of the statistics matters more than anything:
when a formula in the design and a test disagree, re-derive the formula before touching the test.

## What to read first

1. `AGENTS.md`: the repository's rules (never push to master, `python tasks.py check` is the
   gate, no secrets, `tasks.py` is the only entry point).
2. `docs/superpowers/specs/2026-11-08-algorithms-design.md`: the design. Section 2 is the review
   that motivates every change, with file and line references into the code as it was on
   2026-11-08 (lines may have moved; the function names have not). Sections 3–7 are the five
   projects, A0 to A4, each with its files, formulas, tests and acceptance. Section 8 holds the
   decisions already taken; section 10 the testing standards.
3. `docs/superpowers/specs/2026-11-07-agent-path-design.md` §2 and §9: the rules that hold
   everywhere in this repository (the vocabulary for people versus `--json`, Windows first,
   stdlib first, the quality process) and the testing standards.
4. `docs/superpowers/design/2026-11-07-agent-path-session-prompt.md` and
   `docs/superpowers/reviews/2026-11-07-agent-path-build-log.md`: how the previous autonomous
   programme ran and logged itself. Keep the same log format.
5. README sections a project points you to, found by their headings: "The novelty filters",
   "The hold-out penalty", "After the run: is the improvement real?", "Goals in order of
   importance", "One run per instance", "Stochastic evaluators", "Operators", "Adaptive breadth",
   "Economics", "A study from the command line" (the `--warm` and `per_group` paragraphs), and
   `docs/app.md` "The Study page". Do not read the README end to end.
6. `benchmarks/operator_mixes.py`: the benchmark project A2 must not regress; its docstring says
   how the README's operator numbers were made.

## Step 0: a clean start

- `git status --porcelain` must be empty. If tracked files are modified, stop and report: another
  session may still own them. Two untracked files are expected and are yours to commit with
  project A0: `docs/superpowers/specs/2026-11-08-algorithms-design.md` and
  `docs/superpowers/design/2026-11-08-algorithms-session-prompt.md`.
- Choose the base branch as design §8 decision 1 says: `master` if the agent-path PRs (#72 and
  those after it) are merged; otherwise the tip of the latest `agent/*` branch in the clone
  (`git branch --list 'agent/*'`). Write the choice and the commit hash into the build log.
- `git config --local user.email` must be `26815119+meaurisch@users.noreply.github.com` and
  `user.name` `meaurisch`.
- `python tasks.py check` must be green on the base before you change anything. If it is not,
  stop and report; do not fix the suite as a side quest.
- Start the build log at `docs/superpowers/reviews/2026-11-08-algorithms-build-log.md`: one dated
  entry per project with the decisions taken, the gate counts, the benchmark tables where the
  project has them, the statistician review's rounds and the PR link. Commit it with each project.

## The loop, for each project in order

1. **Branch.** `algo/0-correctness` from the base; every later project from the previous one
   (`algo/1-verdict` from `algo/0-correctness`, then `algo/2-noise`, `algo/3-prompt`,
   `algo/4-allocation`). Stacked PRs, merge commits, a maintainer merges; you never merge.
2. **Plan to step level.** Re-read the code the project's section names as it is now (the earlier
   projects changed it). Expand the section into a plan at `docs/superpowers/plans/2026-11-08-algo-<n>-<name>.md`
   with the `superpowers:writing-plans` skill: keep every file, interface, formula and test the
   design gives; add the steps, exact signatures and test bodies. Then the builder check: dispatch
   one fresh subagent with the brainstorming skill's `builder-check-prompt.md`
   (`C:\Users\norri\.claude\plugins\cache\claude-plugins-official\superpowers\7.0.0\skills\brainstorming\builder-check-prompt.md`,
   or wherever `superpowers:brainstorming` is installed now) on the design's section and the
   expanded plan; answer its questions from the design, decide the rest yourself and record each
   call in a "Decisions" section at the end of the plan. Commit the plan.
3. **Build with `superpowers:subagent-driven-development`.** One task at a time, in the plan's
   order. Per task: an implementer subagent that sees only its task, the plan's header and the
   design's section (`studio-executor` for well-specified single-concern tasks; a general-purpose
   subagent on the most capable model for the statistics and the search core: A0.1, A0.2, A1.2,
   A2.1–A2.5, A4.1, A4.3); then a fresh reviewer subagent on the diff against the task's
   requirements and the testing standards. A task is done when its tests pass, the reviewer has
   no blocker, and it is committed with the plan's message. Never implement on the main thread
   beyond one-line fixes a reviewer asks for; never read source files on the main thread beyond
   what planning needs.
4. **Agents one or two at a time.** Never more than two subagents at once: wider fan-outs have
   exhausted this machine's session window before. Tasks that share a file run one after the
   other.
5. **A task that will not go green.** Two attempts by fresh implementers, the first's failure in
   the second's brief. Still red: revert the task's changes so the tree is clean, write what
   failed and why in the build log, and continue only when no later task of the project depends
   on it; otherwise stop the project there, open the PR as a draft with what exists, and go on to
   report (do not start the next project on a broken base).
6. **The gate.** `python tasks.py check` on the project's branch, green, with nothing else running
   on the machine: the full suite has timing-sensitive tests, so no demo study and no app may run
   beside it. Paste the counts into the build log.
7. **The acceptance the design names.** A0: `python tasks.py run` and the README quickstart counts
   re-derived. A1 and A2: the statistician review (design §4 end, §5 end): a fresh general-purpose
   subagent given only the design's section, the new or changed modules and their tests, asked
   where the decision, the sentence and the numbers can disagree or a formula is misused; fix what
   it finds; repeat until it finds nothing; log every round. A2: `python benchmarks/operator_mixes.py --seeds 5`
   before (on the previous branch) and after, both tables in the build log and the PR body, the
   README's operator numbers updated from the script; the "mature" regime must not get worse.
   A4: the demo-tour samples give one group; the pyvrp tagged samples keep their tags. A1 also
   gets one persona round on the Study page (a planner who reads only the page: is the sentence
   the same quantity as the number?), logged as the intern log does.
8. **Docs are part of the project**: the README sections and `docs/app.md` rows the section
   names, in the same PR, in the README's voice (the app's vocabulary for people; the engine's
   key names in `--json`).
9. **The PR.** Push the branch; `gh pr create` against `master` with: the project's goal in one
   line, the review items it closes (D1…, S1…, Q1…, P1… from design §2), the decisions taken
   while building, the gate counts, the benchmark tables where there are any, the statistician
   review's final round, the plan's path, and the attribution line this environment's rules
   require. Then the next project.

## Rules that are easy to forget on this machine

- Windows is the first platform. Test paths with spaces, CRLF and the absence of symlinks here;
  CI runs the same suite on Linux.
- This Bash tool collapses backslashes in heredocs: write files with the Write tool, never with
  a heredoc, when a backslash matters.
- Never edit source with PowerShell `Get-Content`/`Set-Content` (BOM and code-page damage); use
  the Edit and Write tools.
- Run anything likely to exceed two minutes in the background and wait for its notification;
  never `Start-Sleep` to wait. The benchmark and the full gate both exceed two minutes.
- Never run `git stash`; a temporary WIP commit sets work aside.
- Generic names everywhere; no vendor names in new files, commands or folders.
- The verdict is the only claim: nothing you build, write or log says "better" before the final
  check does. The new rule (design A1.2) is the one place that decides; every page, command and
  report renders from its document.
- Every statistical rule gets a simulation test with a seeded RNG whose name states the error
  rate it asserts (design §10).
- `tasks.py` is the only entry point: `python tasks.py test | full | run | check | lint`. Never
  invent others.

## What is not this session's

The design's part "Programme B: the go-to tool" (sections 12–19) is a second programme that
runs after this one, in a session of its own with its own prompt. Do not start it, do not
plan it, and do not let its sections widen a task here; where a project of programme A says
a later programme takes something up, leave it.

## When you are done

Run `python tasks.py check` once more on the last branch, then write the final entry of the
build log: per project, the PR link, PASS or the task that stopped it, the gate count, the
benchmark delta (A2) and the review rounds (A1, A2). Say it in one message, and stop. Do not
merge anything, do not start further work, do not tidy the sidebar.
