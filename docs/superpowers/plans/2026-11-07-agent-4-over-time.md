# Agent path, project 4: Studies over time — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Before executing:** this plan is at task level. Re-read `evolvekit/app/jobs.py`, `server.py`, `evolvekit/harness/study.py`, `compile.py`, the sampler (`param_lhs`) and the app's Home and Cases pages as they are after project 3 landed, expand every task to step level with superpowers:writing-plans (keep the tasks, files, interfaces and test names below), and run the builder check on the result.

**Goal:** a study can run in nightly and weekend windows and keep its archive; what earlier studies learned about an application warm-starts the next; cases can be grouped and tuned per group with an exported rule.

**Architecture:** the app's ticker becomes a scheduler that reads `schedule` from `study.yaml` and starts, continues or stops runs at window edges; `app --service` keeps it alive without a browser. `memory.learned` reads finished studies in the home; `study new --warm` and a sampler prior use it. Groups are features (SQL over the request tables, or tags) plus a standard-library k-means; `per_group` compiles one search per group.

**Tech stack:** Python ≥ 3.11 standard library; `schtasks` on Windows.

**Spec:** [docs/superpowers/specs/2026-11-07-agent-path-design.md](../specs/2026-11-07-agent-path-design.md), §2 and §6.

## Global constraints

- No CI, no server, no remote machine: the scheduler is the app's own process on the planner's computer (spec §2, §6.2).
- A run in its final check is never stopped by a window's end (spec §6.1).
- A prior never excludes any value in range; it decays by half per study since the winner (spec §6.3).
- A group needs at least two training cases, else one group; tags win over features (spec §6.4).
- Branch `agent/4-over-time` from `agent/3-code`; PR against `master`, stacked; merge commit.

## Review focus

1. The computer sleeps through a window's start and wakes inside it: the scheduler starts the run late and stops at the window's end, never carrying the missed time over (Task 2's `test_wakes_inside_window_starts_late`).
2. A window that straddles midnight (`22:00`–`03:00`) and daylight-saving changes: windows are local wall-clock, computed with `zoneinfo`, and a straddling window belongs to the day it starts (Task 1's `test_window_straddles_midnight`, `test_dst_change_keeps_wall_clock`).
3. Two studies with windows that overlap: the queue rule holds (one at a time, the oldest schedule first), and the second gets the rest of the window (Task 2's `test_overlapping_campaigns_take_turns`).
4. Memory from a study whose harness version differs: a setting that no longer exists or changed range is ignored with a note, never applied (Task 5's `test_learned_ignores_settings_the_harness_no_longer_has`).
5. Per-group tuning where one group's final check has a single held-back case: that group's verdict is `single` and the exported rule says so (Task 9's `test_per_group_single_case_group`).

---

## File map

| File | Responsibility |
|---|---|
| `evolvekit/harness/schedule.py` | new: parse and validate `schedule`; `next_edge(now)`; window membership |
| `evolvekit/app/scheduler.py` | new: the tick that starts, continues and stops runs at edges |
| `evolvekit/app/server.py`, `jobs.py` | the ticker calls the scheduler; `--service`; `app.log` |
| `evolvekit/cli.py` | `study start --now`, `app --service`, `app install-startup`, `app uninstall-startup`, `study new --warm`, `study groups` |
| `evolvekit/harness/memory.py` | new: `learned(home, harness_id)` |
| `evolvekit/harness/compile.py`, the sampler module of `param_lhs` | `prior` on parameters |
| `evolvekit/harness/groups.py` | new: features, k-means, silhouette, tags, the rule stump |
| `evolvekit/harness/study.py`, `compile.py`, `execute.py` | `schedule`, `per_group`, per-group runs and checks |
| `evolvekit/app/verdict.py`, `api.py`, `static/js/pages/*.js` | `groups` in the verdict; Home's campaign line; "What we learned"; group badges |
| `tests/test_schedule.py`, `tests/test_scheduler.py`, `tests/test_app_service.py`, `tests/test_memory.py`, `tests/test_prior.py`, `tests/test_groups.py`, `tests/test_per_group.py`, `tests/test_app_learned.py` | new tests |

### Task 1: `schedule` in `study.yaml`

**Produces:** `Schedule.parse(raw) -> Schedule` with `windows: list[Window(days: set[int], start: time, end: time)]` and `until: date | None`; validation messages name the key path; `in_window(now: datetime) -> Window | None`; `next_edge(now) -> tuple[datetime, "start" | "end"]`; local wall-clock through `zoneinfo`/`datetime.astimezone()`.

**Tests:** `test_parse_windows_and_until`, `test_refuses_bad_day_and_time`, `test_in_window_weekday`, `test_window_straddles_midnight`, `test_dst_change_keeps_wall_clock`, `test_next_edge_from_outside_and_inside`.

### Task 2: The scheduler tick

**Produces:** `scheduler.tick(home, *, now: datetime, start=jobs.start, continue_run=jobs.continue_run, stop=jobs.stop) -> list[str]` (what it did, for the log): for every study with a schedule and not past `until`: inside a window and nothing of it running → no run yet: `start`; the latest run `stopped` short of its plan (`jobs.can_resume`): `start(resume=run)`; the latest run's plan done (`done`): `continue_run` with `hours = time left in the window`; outside every window and its run active and not in `check` → stop; one at a time across the home (the queue), the oldest schedule first. `budget.hours` of a scheduled study is the campaign's whole search time: the plan is made once from it, and windows only decide when it runs (the resume path); a window's stop writes the usual `stopped`. `AppServer._tick` calls it; `study wait`/`watch` call it too.

**Tests** (injected clock and patched `start`/`continue_run`/`stop` recorders): `test_starts_at_window_start`, `test_resumes_unfinished_plan_before_continuing`, `test_continues_finished_run_with_window_hours`, `test_stops_at_window_end`, `test_never_stops_final_check`, `test_wakes_inside_window_starts_late`, `test_overlapping_campaigns_take_turns`, `test_past_until_does_nothing`.

### Task 3: Start now, and the campaign line

**Produces:** `study start` on a scheduled study prints "scheduled: next window Tue 01:00" and submits nothing unless `--now`; `jobs.state_line` adds `campaign: {"next": iso, "edge": "start"|"end"}`; Home cards show "Campaign · next window Tue 01:00"; the API's study document carries `schedule`.

**Tests:** `test_start_scheduled_prints_next_window`, `test_start_now_overrides`, `test_home_card_campaign_line`.

### Task 4: The app as a service

**Produces:** `app --service` (no browser, no console reads, logging to `<home>/app.log` rotated at 5 MB with two backups, `app.json` written); `app install-startup` (Windows: `schtasks /Create /SC ONLOGON /TN "evolvekit app" /TR "<python> -m evolvekit app --service --home <home>"`, with the task name and the exact command printed; macOS/Linux: prints a launchd plist or a systemd user unit to stdout and where to put it, exit 0); `app uninstall-startup` reverses it (Windows) or prints the removal command.

**Tests:** `test_service_writes_log_and_app_json` (a short-lived server), `test_log_rotates`, `test_install_startup_windows_command` (subprocess patched, the argv asserted), `test_install_startup_other_platforms_print_unit`.

### Task 5: `memory.learned`

**Produces:** `learned(home: Home, harness_id: str) -> dict`: `{"studies": [{slug, name, finished_at, decision, line}], "settings": {name: {"tuned_in": n, "importance": float | None, "importance_n": n, "winners": [{"value", "study", "finished_at"}], "direction": "up"|"down"|"mixed"|None}}, "notes": [str]}` from every finished study on that harness (the run's `status` parameters section for importance; the verdict for decisions; the best's values for winners); settings the current harness version lacks or whose range changed are listed in `notes` and left out.

**Tests** (synthetic finished studies written by a helper): `test_learned_counts_and_importance_mean`, `test_winners_only_from_use_studies`, `test_learned_ignores_settings_the_harness_no_longer_has`, `test_learned_empty_home`.

### Task 6: Warm starts and the decaying prior

**Produces:** `study new --warm`: the start values of tuned settings = the latest `use` winner's; `recommended` re-ranked by importance when at least three studies exist; `study.step.warm = {"from": slug, "at": iso}`; the compile writes `problem.parameters[name].prior = {"value": v, "weight": 0.5 ** k}` where `k` = studies on this harness since the winner; the sampler: `param_lhs` draws a `weight` share of its samples from a narrow band around `value` (± 10 % of the range) and the rest as today; a prior with weight below 0.05 is dropped.

**Tests:** `test_warm_uses_latest_winner`, `test_warm_reranks_recommended_after_three`, `test_prior_weight_decays_by_half`, `test_prior_share_of_draws` (1000 draws; share within ± 5 points of the weight), `test_prior_never_excludes_range_ends`.

### Task 7: "What we learned" in the app

**Produces:** GET `/api/harnesses/{harness}` gains `learned` (Task 5's document); the harness page shows it: the studies with their verdict marks, and a table of settings (importance as a bar, winners) in the app's words.

**Tests:** `test_api_harness_learned`, `test_harness_page_shows_learned` (page test).

### Task 8: Features, tags and `study groups`

**Produces:** `harness.yaml` `features: {name: {sql, describe}}` (manifest); `groups.features_of(root, case) -> dict` (declared, else row counts and numeric means from the preview tables of that case, computed through the SDK's `tables` command); `groups.kmeans(points, k, restarts=20, seed=0)`, `groups.silhouette(points, labels)`; `groups.compute(root, *, k: int | None) -> dict` (`{"method": "tags"|"features"|"one", "groups": {name: [cases]}, "features": {...}}`, tags win, min two training cases per group) written to `groups.json`; `cases/<name>.tags` (one line) read by the app's Cases view, with a tag editor; `study groups SLUG [--k N] [--json]`.

**Tests:** `test_default_features`, `test_declared_features_sql`, `test_kmeans_recovers_two_clusters`, `test_silhouette_prefers_right_k`, `test_tags_win_over_features`, `test_small_group_collapses_to_one`, `test_cli_groups_json`.

### Task 9: `per_group`

**Produces:** `per_group: true` in `study.yaml`; the compile makes `runs/<id>/groups/<name>/` one search each, run in sequence by `run_study` under the budget split by case count; the final check per group; `build_verdict` gains `groups: {name: <a verdict document without groups>}` and the top-level decision is `use` only when every group with more than one case says `use`; the exports add `settings_by_group.json` and `rule.md` (a one-split stump over the features: "If <feature> is above <value> use A, else B"; with tags: "For cases tagged X use A"); the Cases view shows a badge per group.

**Tests:** `test_per_group_compiles_one_run_per_group`, `test_per_group_budget_split`, `test_per_group_verdict_requires_every_group`, `test_per_group_single_case_group`, `test_rule_stump_picks_best_split`, `test_cases_view_badges` (page test).

### Task 10: Docs, the gate, the PR

- README: *Campaigns* and *What evolvekit learned* and *Settings per kind of case* under *Harnesses and studies*; `docs/app.md`: the service, install-startup, the campaign line, the harness page, group badges; `docs/harness-authoring.md`: `features:`.
- The persona gate of spec §8 for project 4 (a planner who comes back on Monday; the scheduler driven with a simulated clock through the test helpers, and the "What we learned" page); log it.
- `python tasks.py check` green; PR `agent/4-over-time` against `master`, stacked.
