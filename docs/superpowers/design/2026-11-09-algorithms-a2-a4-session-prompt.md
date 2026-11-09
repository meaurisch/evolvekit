# The algorithms programme, projects A2–A4: the session prompt

The first session (2026-11-09) built projects A0 and A1 and stopped there, at the owner's request.
This prompt starts a new session for the remaining three projects. Paste everything below the line
into a new Claude Code session opened in the evolvekit checkout (`C:\Users\norri\studio\evolvekit`),
once no other session is working in it.

What exists when you start:

| Project | Branch | PR | State |
|---|---|---|---|
| A0 correctness | `algo/0-correctness` | [#78](https://github.com/meaurisch/evolvekit/pull/78) | built, gate green, open |
| A1 the verdict | `algo/1-verdict` (from A0) | [#79](https://github.com/meaurisch/evolvekit/pull/79) | built, gate green, open |
| A2 noise-aware search | — | — | not started |
| A3 what the model is told | — | — | not started |
| A4 allocation and memory | — | — | not started |

---

You are building the last three projects of evolvekit's algorithms programme — A2, A3 and A4 —
in that order, each ending in a pull request. You run autonomously. The owner has approved the
design and will not be in this session: do not ask them anything; take every decision from the
design as amended below, and where it is silent decide the way a careful colleague would, write
the decision down, and go on. Quality matters more than speed. Correctness of the statistics
matters more than anything: when a formula in the design and a test disagree, re-derive the
formula before touching the test — and measure by simulation before you assert a rate.

## What to read first

1. `AGENTS.md`: the repository's rules.
2. `docs/superpowers/design/2026-11-08-algorithms-session-prompt.md`: the original session
   prompt. **Its loop, its rules "easy to forget on this machine" and its "When you are done"
   hold for you unchanged**, except where this prompt says otherwise. Read it whole.
3. `docs/superpowers/specs/2026-11-08-algorithms-design.md` §2 (the review), §5 (A2), §6 (A3),
   §7 (A4), §8–§10. Sections 3–4 are built; read them only for what A2–A4 build on.
4. `docs/superpowers/reviews/2026-11-08-algorithms-build-log.md`: what A0 and A1 decided and
   measured. **Keep the same log format; add your entries to the same file.**
5. The two built plans' "Decisions" sections — they changed the design, and A2–A4 must build on
   what was built, not on the design's text where they differ:
   `docs/superpowers/plans/2026-11-08-algo-0-correctness.md` and
   `docs/superpowers/plans/2026-11-08-algo-1-verdict.md` (decisions 11–14 especially).

## Step 0: a clean start

- `git status --porcelain` must be empty (if a tracked file shows modified, compare
  `git hash-object <file>` with `git rev-parse HEAD:<file>` before anything else: on this machine
  `core.autocrlf=true` can leave a byte-identical file flagged; refresh it with `git add <file>`
  only if the hashes are equal; otherwise stop and report).
- **Base branch.** If A0 (#78) and A1 are merged into `master`, branch A2 from `master`;
  otherwise from the tip of `algo/1-verdict`. Write the choice and the hash into the build log.
- `git config --local user.email` = `26815119+meaurisch@users.noreply.github.com`, `user.name`
  = `meaurisch`.
- `python tasks.py check` on the base must be green before you change anything (it takes about
  30 minutes; run it in the background with the project's `.venv/Scripts/python.exe`). If it is
  red, stop and report.

## What A0 and A1 changed that A2–A4 must respect

These are binding. Each plan you write carries them into its Global Constraints or Decisions.

1. **The verdict is one function.** `confirm.verdict_summary` / `decide` / `decision_of` decide;
   every page, command and report renders from the comparison and verdict documents. Never
   compute a chance, an interval or a decision anywhere else. A4.3's "against the harness
   defaults" line is a second comparison: it goes through the same functions and is labelled.
2. **The rule** (A1 decisions 11–14, which replace design §4's conjugate prior and §8
   decision 3's "conjugate prior"): the posterior is the reference prior,
   μ | d ~ t(n − 1, mean, sd/√n) over per-case log-ratio effects; use when the chance of a gain
   ≥ 0.95 ("19 in 20"), worse when ≤ 0.05, else keep; a candidate that failed more runs than the
   starting point is never use; Holm (one leg, p = 1 − chance, 0.05) wherever a choice is made
   among branches, candidates, kinds of case or levels. Do not reintroduce κ0, a "prior scale",
   the 9-in-10 threshold or a 1 % margin leg.
3. **A comparison is read at most twice.** The final check reads its answer, and if the answer is
   keep and runs can help (A1.3's within/between split, threshold 0.25), adds all its extra runs
   at once and reads once more. The app refuses a third read. **A4.1** says the saved time goes
   to the final check "as extra seeds only while A1.3 says runs help": implement that as a larger
   allowance for the one extension at the first read — never as more reads. The measured
   false-"use" rates under these reads are in the build log (worst case about 9 %); any change you
   make to the final check must keep a seeded null simulation of the real procedure under 10 %
   (fast test, plus a slow one asserting rate + 2 SE < 10 %).
4. **The hold-out veto has no noise margin yet** (A0 decision 7). **A2 must add it**: a candidate
   is kept out only when `private < seed_private − 2·σ̂_obj/√seeds` once A2.1's noise estimate
   exists (σ̂ = 0 keeps today's literal rule). Update the docs' sentence that today says "a
   candidate exactly as good as the seed is kept out about half the time".
5. **Two big-step counts** (A0.6b): attempts feed patience's relief; applied big steps feed the
   economics guard and every reported count. A3.2's spacing counts applied big steps.
6. **The near gate compares a child's change against its parent** (A0.5); A2.2's behaviour twins
   are a different filter (by measured behaviour) — do not merge them.
7. **Status schema 2, verdict schema 2**, `comparison.json` with `prior: "reference"`, `looks`,
   `posterior`, `holm`, `limited_by`, `numbers_from`. A new key in either document is documented
   in `docs/app.md`, `docs/reference.md` and `evolvekit/skills/shared/evolvekit.md` in the same
   PR, and a key that changes meaning bumps the schema.
8. **Legacy files**: documents written before A1 carry no `posterior` key and keep their own rule
   and words. Anything you add that reads a comparison must keep that split.

## The loop (as in the original prompt, with what the first session learned)

- **Plan, then a builder check, then rulings.** Write the plan with `superpowers:writing-plans`
  (a subagent can write it while you do other read-only work); dispatch the builder check
  (`builder-check-prompt.md` of `superpowers:brainstorming`) on the design section and the plan;
  ask it to re-derive every formula and to estimate any rate a test asserts. Record each ruling
  in the plan's "Decisions", commit the plan. In A1 the builder check found that the design's
  prior capped the chance and that its threshold failed its own null test — expect the same kind
  of thing in A2's race, promotion and twin rules.
- **Briefs.** Task headings are `### Task A2.1: …`; the skill's `task-brief` script only matches
  numeric task ids. Extract each brief with awk into `.superpowers/sdd/<plan>/task-<id>-full.md`
  as plan header + task + the Decisions section, and re-extract after every new ruling. The
  first session's contracts for implementers and reviewers are at
  `.superpowers/sdd/implementer-contract-algo.md` and `.superpowers/sdd/reviewer-contract-algo.md`
  (git-ignored, on this machine); the same text, with the environment notes, is committed at
  `docs/superpowers/design/2026-11-09-algorithms-agent-contracts.md`. Reuse them.
- **Agents one or two at a time**; a finished subagent that still has background test runs is
  stopped before the next heavy step (they load the machine and the gate is timing-sensitive).
- **Statistician review** (A2, as A1): a fresh subagent on the section, the changed modules and
  their tests, repeated until a round finds nothing Critical or Important. Give it the plan's
  Decisions so it does not flag deliberate departures, and ask it to **simulate the product's
  real procedure** — in A1 every round found a test that simulated a simpler procedure than the
  product runs.
- **A2's benchmark**: `python benchmarks/operator_mixes.py --seeds 5` before (on the base) and
  after; the "mature" regime's mean true cost of the reported best must not rise; both tables in
  the build log and the PR body; the README's operator numbers updated from the script. It takes
  long: run it in the background, never beside the gate.
- **A4's acceptance**: the demo-tour samples give one group; the pyvrp tagged samples keep their
  tags.
- **PRs**: push the branch, `gh pr create --base master` with the goal, the review items closed
  (S…, Q…, P… of design §2), the decisions, the gate counts, the benchmark tables, the
  statistician rounds, the plan's path, and the attribution line. Note in the body that the PR is
  stacked on the previous one until it merges. Then bind it with the app's PR tools.

## When you are done

As the original prompt says: `python tasks.py check` once more on the last branch, the final
entry of the build log (per project: PR link, PASS or what stopped it, gate count, benchmark
delta for A2, review rounds), one message, and stop. Do not merge anything.
