# Agent guide — evolvekit

Conventions for ANY coding agent (vendor-neutral).

- Never push to `master`: branch, push, open a PR, and let a maintainer merge.
  The `.githooks/pre-push` hook enforces it — enable it once per clone with
  `git config core.hooksPath .githooks` (needs `gitleaks` on `PATH`).
- Entry points: `python tasks.py test | full | run | check | lint` — never invent
  others. `check` (lint, then every test) is the CI gate; run it before a PR.
- Never commit secrets. Copy `.env.example` to `.env` (gitignored) and fill it
  in; evolvekit reads secrets from the environment only.
- The README is the manual. To set up a new experiment, start from
  [docs/new-experiment.md](docs/new-experiment.md) and copy the closest
  directory under `examples/`.
