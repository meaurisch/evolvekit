# Algorithms programme — build log

The running log of the session that builds the five projects of
[the algorithms design](../specs/2026-11-08-algorithms-design.md): one entry
per project with the decisions taken, the gate counts, the benchmark tables,
the statistician review's rounds and the PR link. The session prompt is
[docs/superpowers/design/2026-11-08-algorithms-session-prompt.md](../design/2026-11-08-algorithms-session-prompt.md).

## Step 0 — a clean start (2026-11-09)

- The clone was on `agent/5-installer`. `git status` listed
  `tests/test_app_store.py` as modified, but `git hash-object` of the working
  file equalled the committed blob (9bb1be8): the content was unchanged and
  only git's cached file stats were stale (`core.autocrlf=true`, file on disk
  with LF). Decision: no session's work could be in a byte-identical file, so
  the index entry was refreshed with `git add` of the identical file (the file
  itself was not touched) and the tree read clean. Cost if wrong: none; the
  bytes are the committed bytes.
- Base branch (design §8 decision 1): PRs #72–#76 and the README #77 are all
  merged, so the base is **`master` at 68b579e** ("Merge pull request #77"),
  fast-forwarded to `origin/master`.
- `user.email` and `user.name` as required.
- As in the agent-path build, "python" on this machine is the project's
  `.venv/Scripts/python.exe`; every gate and test in this log ran with it.
- `python tasks.py check` on master: **2711 passed, 5 skipped** in 27:35.
