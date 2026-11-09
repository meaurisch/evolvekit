# Agent guide — evolvekit

Conventions for ANY coding agent (vendor-neutral).

- Never push to `master`: branch, push, open a PR, and let a maintainer merge.
  The `.githooks/pre-push` hook enforces it — enable it once per clone with
  `git config core.hooksPath .githooks` (needs `gitleaks` on `PATH`).
- Entry points: `python tasks.py test | full | run | check | lint | dist` — never invent
  others. `check` (lint, then every test) is the CI gate; run it before a PR.
- Never commit secrets. Copy `.env.example` to `.env` (gitignored) and fill it
  in; evolvekit reads secrets from the environment only.
- The README is the front page: what evolvekit is, what it can do, how to
  start. Keep it short. Every command, `study.yaml` key and config option is
  documented in [docs/reference.md](docs/reference.md), the manual; a new
  command or key goes there, and in the README only when it changes the
  overview. To set up a new experiment, start from
  [docs/new-experiment.md](docs/new-experiment.md) and copy the closest
  directory under `examples/`.
- To put an application behind evolvekit for studies, write a harness:
  [docs/harness-authoring.md](docs/harness-authoring.md). Inside a harness
  folder, its own `AGENTS.md` governs, and `evolvekit harness check` must pass.
