# Engineer gate log — the skills and the agent gate

The gate of the agent-path design, §4.4 and §8, for project 2: a fresh
reviewer each round, one at a time, who never sees the skills' source, the
design or earlier reviews, plays an engineer with a problem description and
works through the skill in a temporary repository through their coding agent;
8 or more with no blocker passes. The protocol is the intern review log's
([2026-09-26-intern-review-log.md](2026-09-26-intern-review-log.md)). The
gate script's own runs are logged first; their transcripts are under
[agent-gate/](agent-gate/).

## The set-up of every round

- **The reviewer:** a fresh general-purpose agent, told only the persona,
  the materials and the note. It drives a real shell and the in-app browser
  and never opens `.agents/`, `.claude/` or evolvekit's sources.
- **Materials:** `python tests/agent_gate.py --fixture <folder>` (with
  `--no-harness` for round B): a git repository with the skills installed,
  the demo-tour harness in `harness/` (round A only), `solver.py` at the
  root (for round B a program evolvekit does not know: another name and
  version, a scrubbed docstring), its four samples in `cases/` (round B:
  under neutral request names), a two-line README, and a clean library
  home; `--fixture` prints the lines to set (`EVOLVEKIT_HOME`, a PATH with
  evolvekit's venv first, `cd`, the session variables to unset) and the
  isolated `claude -p` command (`--allowedTools …`, `--add-dir <home>`,
  `--setting-sources project`, `--strict-mcp-config`, `--permission-prompts
  none`). The reviewer runs that command for the first message and the same
  with `--continue` for every reply. The reviewers' transcripts are kept,
  redacted, under [agent-gate/](agent-gate/) as `round-<n>-transcript.md`.
- **Round A, the note (verbatim):**

  > You are an engineer at a logistics company. Your tour solver is `solver.py` in the folder `<repo>`; your requests are in `cases/`. You have never used evolvekit; a colleague installed it and said your coding agent knows how. Work only through the agent: in a shell in `<repo>`, with `EVOLVEKIT_HOME=<home>` set, send your first message with `claude -p "<message>"` and every reply with `claude -p --continue "<reply>"`. Do not open `.agents/`, `.claude/` or any evolvekit source. Your first message is: "Use evolvekit to get shorter tours out of my solver, solver.py, on the requests in cases/." Answer its questions as an engineer would: hold two requests back; today's settings are the starting point; the first time it asks how long in total, say 30 seconds; when it shows you a plan, ask for ten minutes in total instead. When it gives you an address, open it in the in-app browser and look. When the study has ended (about ten minutes), ask the agent "Is it done? What did it find?". Then report: an overall score from 1 to 10 for "How easy was it to get from one request to a running study you could see, and to its answer?"; sub-scores for the questions it asked (did it ask what you had already said?), the plan before the start (did it wait when the plan was thin?), the hand-over, and the answer (did it claim anything the Study page's verdict does not?); the worst frictions (where, what happened, why it hurt, what would fix it, severity blocker / major / minor); what would make it a 10.

- **Round B, the note (verbatim):**

  > You are an engineer whose application is `solver.py` in the folder `<repo>`; your requests are in `cases/`. evolvekit does not know it yet. Work only through your coding agent, as in this note: in a shell in `<repo>`, with `EVOLVEKIT_HOME=<home>` set, `claude -p "<message>"` first and `claude -p --continue "<reply>"` after. Do not open `.agents/`, `.claude/`, `harness/evk_harness.py` or any evolvekit source. Your first message is exactly these two sentences: "Put solver.py behind evolvekit. It is a tour solver with a command line; I want to tune its settings on my requests." Answer its questions from what you see in `solver.py --help` and `cases/`. Report: an overall score from 1 to 10 for "How easy was it to get your application behind evolvekit?"; sub-scores for the questions it asked, what it wrote, the check loop, and the smoke test; the worst frictions with severity; what would make it a 10.

- **The controller's checks after a round:** round A — no question about
  something already said; with 30 seconds the plan was shown with its ways
  out and nothing started; the final answer holds the verdict's `line` and
  no other percentage. Round B — `harness check <repo>/harness --app
  <repo>/solver.py --json` exits 0; `harness/evk_harness.py` is identical to
  the SDK's; the transcript shows the smoke test's `study preview` line.

## The gate script

### Run 1 — Claude Code, the complete sentence (2026-11-07): PASS

`python tests/agent_gate.py --tool claude`. The agent made the study
`shorter-tours`, set the application, added the four requests with two held
back, started it and gave its address; the study ran 18 rounds in ten
minutes; the second, fresh run reported the verdict's line and nothing more.

```
PASS study_exists
PASS application_set
PASS study_started_or_queued
PASS output_names_the_url
PASS no_claim_of_improvement
PASS report_equals_verdict_line
```

The verdict: "Probably better: 8.5 % lower tour length on 2 held-back cases,
a chance of about 20 in 20; at worst about the same as your starting point
(Demo tour's own defaults)." Transcripts:
[agent-gate/20261107-174739-claude-1.txt](agent-gate/20261107-174739-claude-1.txt),
[agent-gate/20261107-174739-claude-2.txt](agent-gate/20261107-174739-claude-2.txt).

### Copilot CLI

Copilot CLI: not installed on this machine (2026-11-07; `where copilot`
finds nothing); the gate was not run with it. `python tests/agent_gate.py
--tool copilot` prints `FAIL tool: copilot is not installed` and exits 2.

### Run 2 — Claude Code, the sentence without the total time (2026-11-07): FAIL, then fixed

`python tests/agent_gate.py --tool claude --expect-question --sentence "…;
hold two back; today's settings are the starting point."` (no "ten minutes
in total").

```
FAIL no_study_made: shorter-tours
PASS asks_the_missing_question
```

The agent asked the one right question ("how much time in total should the
study get?") but had already made the study, set the application, added the
cases, checked and previewed it. The skill said "before anything runs" only
in its step's title; it now says, before step 3, that with one answer
missing nothing is made and the whole message is that question. Transcript:
[agent-gate/20261107-175829-claude-1.txt](agent-gate/20261107-175829-claude-1.txt).
The run is repeated after the fix (below).

### Run 2, repeated after the fix (2026-11-07): PASS

```
PASS no_study_made
PASS asks_the_missing_question
```

The agent's whole answer: the one question ("How much time in total should
the study get?"), with what the request had settled listed for context, and
nothing made. Transcript:
[agent-gate/20261107-180107-claude-1.txt](agent-gate/20261107-180107-claude-1.txt).

## Round A1 — 8/10, no blocker: the study skill's gate passes (2026-11-07)

| Questions it asked | The plan before the start | The hand-over | The answer |
|---|---|---|---|
| 9 | 8 | 8 | 9 |

**The run.** The first message named the solver, the goal and the cases; the
agent asked three things (the total time, what may change, how many cases to
hold back) and nothing already said. With 30 seconds it did not start: it
relayed evolvekit's refusal ("fewer than 3 rounds, too few to learn
anything") with its ways out. With ten minutes it made and started the study
`shorter-tours` (18 rounds), gave the address, and the engineer watched it in
the app. Asked "Is it done? What did it find?", it quoted the Study page's
verdict word for word ("Probably better: 8.5 % lower tour length on 2
held-back cases …") and claimed nothing beyond it. Transcript:
[agent-gate/round-a1-transcript.md](agent-gate/round-a1-transcript.md).

**Frictions, and what changed.**

- **(major) The Study page showed a literal `&#39;`** in the verdict ("Demo
  tour&#39;s own defaults"): project 1's headline-from-the-document change
  escaped the text twice. Fixed (escaped once), on project 1's branch too, so
  its PR carries it.
- **(major) The starting point is named "Demo tour's own defaults"** while
  the engineer said "today's settings". The app names the starting point by
  its source (the intern log's round 2 asked for exactly that); for a program
  the engineer owns whose settings are the harness's defaults, the two names
  mean the same thing. Left for the owner.
- **(minor) It promised to show the new plan and started without showing
  it.** Now the skill says to put the plan line in the message before
  `study start`, whether or not it must wait.
- **(minor) The Tries counter stayed at 8 while the study ran; "Past runs
  (1)" on a first run; the tool's trust warning on every call.** Left for the
  owner (the first two are the app's, the third the tool's).

## Round B1 — 7/10, no blocker (2026-11-07)

| Questions it asked | What it wrote | The check loop | The smoke test |
|---|---|---|---|
| 5 | 7 | 6 | 8 |

**The run.** Two sentences ("Put solver.py behind evolvekit. It is a tour
solver with a command line; I want to tune its settings on my requests.").
The agent asked nothing: it noticed that evolvekit's built-in demo-tour
harness was written for this very program (the fixture's `solver.py` is the
example solver, its cases the samples) and copied it, keeping a lever, a
template, a request format and a retry the engineer never asked for; it
removed them when pushed back (v1.1.0). It said "`harness check` exits 0"
after running it without `--app` (which exits 2 before checking anything),
and gave the real command only when asked. The smoke test's values (2 s per
request, 0.1 h, one of four held back) were chosen silently. The
controller's checks: `harness check harness/ --app solver.py --json` exits 0
(20 checks, no failure); `harness/evk_harness.py` is identical to the SDK's.

**Frictions, and what changed.**

- **(high) No interview, and a shortcut nobody asked for.** The skill now
  says: ask the five questions in one message before writing or copying
  anything (unless the request answers them); an application evolvekit
  already has a harness for is a finding to tell the engineer, not a
  shortcut to take; write only what the interview named — no lever,
  template, data change, request format or retry the engineer did not ask
  for. The gate's no-harness fixture now makes the program unknown to the
  built-in harness (a different program name and version), so round B
  exercises writing a harness.
- **(medium) "exits 0" without `--app`.** The skill now says to run the
  check with `--app PATH` (without it, exit 2 before any check) and to
  quote the exact command and its exit code.
- **(low) The smoke test's values chosen silently.** The skill now says
  which values the smoke test used, that they are for the smoke test only,
  and to quote the `study preview` line.
- **(low) `study compile` said `python -m evolvekit run --config …`.** On a
  study in the home it now says `next: evolvekit study start <slug>`.

## Round B2 — 7/10, no blocker (2026-11-07)

| Questions it asked | What it wrote | The check loop | The smoke test |
|---|---|---|---|
| 3 | 7 | 9 | 9 |

**The run.** The same two sentences, a program whose `--version` now says
"tour-solver 0.9". The agent asked nothing again: it read what it could from
`--help` and a case file and wrote the whole harness on guesses; asked why, it
said the skill had told it to stop and ask, and it chose to guess. The
program still gave the demo away (its docstring named `harnesses/demo-tour/`,
the cases were the samples under their own names), so the agent copied the
demo's km units and `private: true` coordinates and fixed them when told
(v1.1.0). `harness check --app` exited 0 at the end (17 checks, no
failure); the smoke test's four commands worked as written; the SDK file is
identical.

**Frictions, and what changed.**

- **(high) No interview in a one-shot run.** The skill now separates the
  questions a file can answer (1–3: say what you read) from the two only
  the engineer can (what people call things; what must never reach a
  model), and makes those two the whole first answer, with nothing written
  until they are answered.
- **(high) The "unknown" program was the demo.** The fixture now scrubs
  the solver's docstring of every mention of the demo and evolvekit and
  copies the cases under neutral request names.
- **(medium) Units and privacy copied from the demo.** Follows from the
  two above; the "write only what the interview named" rule stands.
- **(low) The tool's trust warning on every call.** The fixture no longer
  writes the tool's project settings file (it was ignored anyway; the flags
  carry the run). The check command shown to the engineer loses `--json`.

## Round B3 — 8/10, no blocker: the harness skill's gate passes (2026-11-07)

| Questions it asked | What it wrote | The check loop | The smoke test |
|---|---|---|---|
| 9 | 8 | 10 | 7 |

**The run.** The same two sentences, a program evolvekit did not know
(`tour-solver 0.9`, a scrubbed docstring, requests under neutral names). The
agent asked, in one message, the two things only the engineer can answer
and said what it had read for the rest; wrote `harness/` with the engineer's
words; ran `harness check harness/ --app solver.py` until it exited 0 (17
checks, no failure, no warning — the engineer confirmed it); then first told
the engineer to "follow the evolvekit-study skill" for the smoke test and,
asked again, ran it (`study new`, `cases`, `check`, `preview` worked as
written). The controller's checks: `harness check --app` exit 0; the SDK
file identical; the `study preview` line in the transcript.

**Frictions, and what changed.**

- **(medium) The hand-off to "follow a skill".** The skill now says to run
  the smoke test itself and quote each command and its line, never to send
  the engineer to a skill.
- **(medium) The engineer's own requests became the harness's samples**,
  byte for byte under invented names, although the interview had just
  settled that their coordinates must never reach a model, and nobody said
  so. The skill now says samples are small made-up cases in the engineer's
  format, never copies of the engineer's files.
- **(low) "All four requests are about 50 stops"** (they are 30, 40, 50
  and 60); the budget only by editing `study.yaml`. Left as they are.

The study skill's gate passed at round A1 (8/10) and the harness skill's at
round B3 (8/10), both with no blocker; the gate script's two runs pass.

### The gate script on the shipped skills (2026-11-07)

Both runs repeated on the final tree (d14d097), after the persona rounds'
fixes and the whole-branch review's:

- Run 2 (the sentence without the total time): `PASS no_study_made`, `PASS
  asks_the_missing_question`
  ([agent-gate/20261107-191257-claude-1.txt](agent-gate/20261107-191257-claude-1.txt)).
- Run 1 (the complete sentence), first attempt: five checks PASS and
  `FAIL no_claim_of_improvement: better` — the agent's summary of the
  interview said "tour length, lower is better", the bare word before any
  verdict ([agent-gate/20261107-191256-claude-1.txt](agent-gate/20261107-191256-claude-1.txt),
  [-2.txt](agent-gate/20261107-191256-claude-2.txt); the study itself ran
  and the second answer carried the verdict's line). The skill now gives
  the agent the phrase for a measure's direction, "lower is the goal",
  never "lower is better". Repeated below.
- Run 1, second attempt: invalid — the tool stopped on this account's usage
  limit for its default model before it read anything
  ([agent-gate/20261107-192429-claude-1.txt](agent-gate/20261107-192429-claude-1.txt)).
  The runs after it set `ANTHROPIC_MODEL=claude-opus-5-5` (the gate keeps
  every `ANTHROPIC_*` variable).
- Run 1, third attempt: five checks PASS and `FAIL report_equals_verdict_line:
  other percentages: 0.3 %` — the second answer quoted the verdict's line
  verbatim and added one sentence of its own that repeated the line's
  "0.3 %"
  ([agent-gate/20261107-210101-claude-2.txt](agent-gate/20261107-210101-claude-2.txt)).
  The design allows one sentence clearly the agent's own and forbids any
  *other* percentage claim; a figure the verdict's line or advice gives is
  not another claim, and the check now says so.
- Run 1, fourth attempt (the shipped tree): **every check PASS**.

```
PASS study_exists
PASS application_set
PASS study_started_or_queued
PASS output_names_the_url
PASS no_claim_of_improvement
PASS report_equals_verdict_line
```

  The verdict: "Probably better: 8.5 % lower tour length on 2 held-back
  cases, a chance of about 20 in 20; at worst about the same as your
  starting point (Demo tour's own defaults)." Transcripts:
  [agent-gate/20261107-211210-claude-1.txt](agent-gate/20261107-211210-claude-1.txt),
  [-2.txt](agent-gate/20261107-211210-claude-2.txt).
