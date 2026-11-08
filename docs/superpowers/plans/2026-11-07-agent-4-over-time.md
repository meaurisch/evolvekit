# Agent path, project 4: Studies over time — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Before executing:** this plan is at task level. Re-read `evolvekit/app/jobs.py`, `server.py`, `evolvekit/harness/study.py`, `compile.py`, the sampler (`param_lhs`) and the app's Home and Cases pages as they are after project 3 landed, expand every task to step level with superpowers:writing-plans (keep the tasks, files, interfaces and test names below), and run the builder check on the result.
>
> Expanded to step level on 2026-11-08 against agent/3-code (021f834). The builder check runs on this version. Tasks run in order 1–10; Tasks 5–7 (memory) and 8–9 (groups) do not depend on Tasks 1–4 and may be reviewed apart. Where a step names a body, the tests are the contract and the body is a sketch.

**Goal:** a study can run in nightly and weekend windows and keep its archive; what earlier studies learned about an application warm-starts the next; cases can be grouped and tuned per group with an exported rule.

**Architecture:** the app's ticker becomes a scheduler that reads `schedule` from `study.yaml` and starts, continues or stops runs at window edges; `app --service` keeps it alive without a browser. `memory.learned` reads finished studies in the home; `study new --warm` and a sampler prior use it. Groups are features (SQL over the request tables, or tags) plus a standard-library k-means; `per_group` compiles one search per group.

**Tech stack:** Python ≥ 3.11 standard library; `schtasks` on Windows.

**Spec:** [docs/superpowers/specs/2026-11-07-agent-path-design.md](../specs/2026-11-07-agent-path-design.md), §2 and §6 (with §3.4 for the verdict document, §8 for the gate, §9 for testing, §10 and §11).

## Global constraints

- No CI, no server, no remote machine: the scheduler is the app's own process on the planner's computer (spec §2, §6.2).
- A run in its final check is never stopped by a window's end (spec §6.1).
- A prior never excludes any value in range; it decays by half per study since the winner (spec §6.3).
- A group needs at least two training cases, else one group; tags win over features (spec §6.4).
- Branch `agent/4-over-time` from `agent/3-code`; PR against `master`, stacked; merge commit.
- The harness SDK's contract does not change (spec §10): no new runner command; features come from what `solve --tables-out` already writes.
- Standard library only; no `tzdata` (this machine's venv has none, and Windows has no system zone database): local wall-clock through `datetime.astimezone()`, `zoneinfo` only where a test or a caller passes a zone.
- The app's words in every sentence for people (study, case, held-back case, starting point, measure, goal, rule, combination, round, final check; never KPI, seed, generation, instance, config, baseline); `--json` keeps the engine's keys. Nothing says "better" outside a verdict's `line`.
- Windows first: paths with spaces (every test home is `tmp_path / "my home"`), drive letters, CRLF, detached processes; Linux must pass too (platform guards, never Windows-only code without one).
- CLI tested through `main([...])` and `capsys`; tests that run real demo-tour studies are `@pytest.mark.slow`; never two slow tests or the gate beside a demo study.
- Python is `.venv/Scripts/python.exe`; files LF-ended, written with the Write/Edit tools; never `git stash`, never commit `.superpowers/`.
- Docs are part of each task: a command or key without its README row (or docs/app.md, docs/harness-authoring.md line) is not done.

## Review focus

1. The computer sleeps through a window's start and wakes inside it: the scheduler starts the run late and stops at the window's end, never carrying the missed time over (Task 2's `test_wakes_inside_window_starts_late`).
2. A window that straddles midnight (`22:00`–`03:00`) and daylight-saving changes: windows are local wall-clock, computed with `zoneinfo`, and a straddling window belongs to the day it starts (Task 1's `test_window_straddles_midnight`, `test_dst_change_keeps_wall_clock`).
3. Two studies with windows that overlap: the queue rule holds (one at a time, the oldest schedule first), and the second gets the rest of the window (Task 2's `test_overlapping_campaigns_take_turns`).
4. Memory from a study whose harness version differs: a setting that no longer exists or changed range is ignored with a note, never applied (Task 5's `test_learned_ignores_settings_the_harness_no_longer_has`).
5. Per-group tuning where one group's final check has a single held-back case: that group's verdict is `single` and the exported rule says so (Task 9's `test_per_group_single_case_group`).

Carried from project 3's persona gate (real-world failures the tests had missed), each with its test:

6. **What the file says is not what the planner wrote.** YAML 1.1 reads an unquoted `from: 01:00` as the integer 60 and `until: 2026-12-31` as a date; both read as meant (Task 1's `test_unquoted_times_read_as_yaml_writes_them`). An app edit of any Set up step rewrites `study.yaml` from `Study.to_yaml()`: `schedule`, `per_group` and `warm` survive it (Task 3's `test_app_edit_keeps_schedule_per_group_and_warm`, which Tasks 6 and 9 extend).
7. **Line endings.** A tags file saved by Notepad (BOM, CRLF, trailing spaces) is the same tag as one written by the app, and a `.tags` file is never listed as a case (Task 8's `test_tags_file_with_bom_and_crlf`, `test_tags_file_is_not_a_case`).
8. **A spend cap overshot.** Window continuations never add up to more than the campaign's search time, and per-group splits never add up to more than the study's hours or AI cap (Task 2's `test_campaign_never_exceeds_its_search_time`, Task 9's `test_per_group_budget_split`).
9. **Stale pages.** Home keeps polling while a campaign waits for its next window, and a session resumed a day later says its own time left, not "0 min" (Task 3's `test_home_polls_while_a_campaign_is_armed`, `test_a_resumed_campaign_session_says_its_own_time_left`).
10. **"Better" over unchecked figures.** "What we learned" shows importance and winning values in words that claim nothing, winners only from `use` studies; `rule.md` names today's settings for a group whose own verdict is not `use` (Task 7's `test_harness_page_never_says_better`, Task 9's `test_rule_md_claims_nothing_for_groups_not_use`).
11. **Defaults overriding.** A warm start's values become the study's `start` values, which win over an input settings file and the harness's defaults, and a winner outside the new study's range is not used (Task 6's `test_warm_start_values_inside_the_study_range`).

---

## File map

| File | Responsibility |
|---|---|
| `evolvekit/harness/schedule.py` | new: parse and validate `schedule`; window spans in local wall-clock; `in_window`, `span_at`, `next_edge`, `past_until`; the presets; the day words |
| `evolvekit/app/scheduler.py` | new: the `Clock` (real or rehearsal), `campaign.json` (arm, pause, read), the tick that starts, resumes, re-checks, continues and stops runs, the campaign's state and words, a person's start and stop |
| `evolvekit/app/server.py` | `AppServer.tick_once` (queue, then scheduler); the ticker calls it |
| `evolvekit/app/service.py` | new: the service's rotating log; the startup task (Windows XML, launchd plist, systemd unit); install and uninstall |
| `evolvekit/app/detach.py` | `python_for_runs()`: never `pythonw.exe` for a run |
| `evolvekit/app/jobs.py` | `state_line(campaign)`; time left of a resumed session; per-group runs (status, stop, resume rules); `_launch` through `python_for_runs` |
| `evolvekit/cli.py` | `study start --now`, campaign `stop`/`wait`/`status`, `app --service`, `app --clock/--clock-speed`, `app install-startup`, `app uninstall-startup`, `study new --warm`, `study groups`; `study open` starts the service |
| `evolvekit/harness/memory.py` | new: `finished_studies`, `learned`, `warm_start`, `priors_for` |
| `evolvekit/space.py`, `evolvekit/search/operators.py` | `Parameter.prior`; `param_lhs_typed` draws a share around it |
| `evolvekit/harness/groups.py` | new: tags, features, standardise, k-means, silhouette, `compute`, `groups.json`, the rule stump |
| `evolvekit/harness/manifest.py` | `features:` (`Feature`) |
| `evolvekit/harness/study.py` | `schedule`, `per_group`, `warm` keys; their problems |
| `evolvekit/harness/compile.py` | `compile_study(priors=, up=)`; `write_run(group=)` |
| `evolvekit/harness/execute.py` | `run_per_group`, the budget split, the group exports; `_final_check(up=)` |
| `evolvekit/app/verdict.py`, `report.py` | `groups` in the verdict document and the report |
| `evolvekit/app/store.py`, `api.py` | `save_step` keeps the new keys; tags; `campaign` and `schedule_presets` in the study document; `learned` on the harness route; `?group=` |
| `evolvekit/app/static/js/` — `words.js`, `pages/home.js`, `pages/setup.js`, `pages/study.js`, `pages/start.js`, `plots.js`, new `pages/harness.js`, `app.js`, `static/app.html` | the campaign card and poll, "When it runs", the rehearsal clock notice, "What we learned", tags, group badges, per-group verdicts |
| `tests/clock_helpers.py`, `tests/campaign_helpers.py` | new, not collected: a fixed-rule Amsterdam 2026 zone; campaign studies and hand-written run folders |
| `tests/test_schedule.py`, `tests/test_scheduler.py`, `tests/test_app_service.py`, `tests/test_memory.py`, `tests/test_prior.py`, `tests/test_groups.py`, `tests/test_per_group.py`, `tests/test_app_learned.py` | new tests |

---

### Task 1: `schedule` in `study.yaml`

**Files:**
- Create: `evolvekit/harness/schedule.py`, `tests/clock_helpers.py`, `tests/test_schedule.py`
- Modify: `evolvekit/harness/study.py` (`Study.schedule`; `Study.parse` known keys and parse; `to_yaml` writes `schedule` only when set), `tests/test_study.py` (one test)

**Interfaces:**
- Consumes: `evolvekit.harness.HarnessError`.
- Produces (`evolvekit/harness/schedule.py`):
  - `DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")` (index = `datetime.weekday()`); `DAY_WORDS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")`; `MONTH_WORDS` likewise — fixed English, never `strftime("%a")` (locale-dependent on Windows).
  - `JOIN_GAP_S = 60.0` — two occurrences this close are one span.
  - `@dataclass(frozen=True) class Window: days: frozenset[int]; start: time; end: time`; property `straddles -> bool` (`end <= start`); `to_yaml() -> {"days": [names], "from": "HH:MM", "to": "HH:MM"}`.
  - `@dataclass(frozen=True) class Schedule: windows: tuple[Window, ...]; until: date | None = None`, with
    - `parse(raw: Any, path: str = "schedule") -> Schedule` (raises `HarnessError` whose message starts with the key path);
    - `to_yaml() -> dict`;
    - `spans(around: datetime, *, tz: tzinfo | None = None) -> list[tuple[datetime, datetime]]` — every occurrence starting from two days before `around`'s local date to eight days after, aware, sorted, merged when the gap is ≤ `JOIN_GAP_S`, none starting on a date after `until`;
    - `span_at(now, *, tz=None) -> tuple[datetime, datetime] | None`; `in_window(now, *, tz=None) -> Window | None` (the window of the occurrence that holds `now`);
    - `next_edge(now, *, tz=None) -> tuple[datetime, str] | None` — `(end, "end")` inside a span, `(start, "start")` outside, `None` when no span holds `now` and none is left before `until`;
    - `past_until(now, *, tz=None) -> bool`.
  - `PRESETS: dict[str, dict]` — `"nights"` (mon–fri 01:00–06:00), `"weekends"` (sat, sun 00:00–23:59), `"nights-and-weekends"` (both), each `{"label": str, "schedule": <schedule YAML>}`.
  - `when_words(moment: datetime, now: datetime) -> str` — `"Tue 01:00"` within six days of `now`, else `"Tue 24 Nov 01:00"`.
  - `Study.schedule: Schedule | None = None` (study.py).
- `tests/clock_helpers.py` produces `class Amsterdam2026(tzinfo)` (CET/CEST with 2026's EU switches, 29 March 01:00 UTC and 25 October 01:00 UTC; PEP 495 `fold` for the repeated hour; `fromutc` implemented) and `at(tz, "2026-11-10 03:00") -> datetime` (a wall-clock time in that zone).

- [ ] **Step 1: Write the failing tests** (`tests/test_schedule.py`; `TZ = Amsterdam2026()`; `NIGHTS = {"days": ["mon","tue","wed","thu","fri"], "from": "01:00", "to": "06:00"}`, `WEEKEND = {"days": ["sat","sun"], "from": "00:00", "to": "23:59"}`)

```python
def test_parse_windows_and_until():
    s = Schedule.parse({"windows": [NIGHTS, WEEKEND], "until": date(2026, 12, 31)})
    assert s.windows[0] == Window(frozenset({0, 1, 2, 3, 4}), time(1, 0), time(6, 0))
    assert s.until == date(2026, 12, 31)
    assert Schedule.parse({"windows": [NIGHTS], "until": "2026-12-31"}).until == date(2026, 12, 31)
    assert Schedule.parse(s.to_yaml()) == s
    dumped = yaml.safe_dump(s.to_yaml())
    assert yaml.safe_load(dumped)["windows"][0]["from"] == "01:00", "the dumper quotes it: read back as text"

def test_unquoted_times_read_as_yaml_writes_them():
    raw = yaml.safe_load("windows:\n  - {days: [sat], from: 01:00, to: 23:59}\n")
    assert raw["windows"][0]["from"] == 60          # YAML 1.1 sexagesimal
    window = Schedule.parse(raw).windows[0]
    assert (window.start, window.end) == (time(1, 0), time(23, 59))

@pytest.mark.parametrize("raw, said", [
    ({"windows": [{**WEEKEND, "days": ["sat", "funday"]}]},
     "schedule.windows[0].days: 'funday' is not a day; use mon, tue, wed, thu, fri, sat, sun"),
    ({"windows": [{**WEEKEND, "days": []}]}, "schedule.windows[0].days: name at least one day"),
    ({"windows": [{**NIGHTS, "from": "25:00"}]}, "schedule.windows[0].from: '25:00' is not a time of day such as 01:00"),
    ({"windows": [{**NIGHTS, "to": "24:00"}]}, "schedule.windows[0].to: use 23:59 for the end of the day"),
    ({"windows": [{**NIGHTS, "to": "01:00"}]}, "schedule.windows[0]: from and to are the same time; a window needs a length"),
    ({"windows": []}, "schedule.windows: name at least one window"),
    ({"windows": [NIGHTS], "until": "next week"}, "schedule.until: a date such as 2026-12-31"),
    ({"windows": [{**NIGHTS, "start": "01:00"}]}, "schedule.windows[0]: unknown key(s) ['start']"),
])
def test_refuses_bad_day_and_time(raw, said):
    with pytest.raises(HarnessError, match=re.escape(said)):
        Schedule.parse(raw)

def test_in_window_weekday():
    s = Schedule.parse({"windows": [NIGHTS, WEEKEND]})
    assert s.in_window(at(TZ, "2026-11-10 03:00"), tz=TZ) == s.windows[0]      # a Tuesday
    assert s.in_window(at(TZ, "2026-11-10 07:00"), tz=TZ) is None
    assert s.in_window(at(TZ, "2026-11-14 12:00"), tz=TZ) == s.windows[1]      # a Saturday

def test_window_straddles_midnight():
    s = Schedule.parse({"windows": [{"days": ["fri"], "from": "22:00", "to": "03:00"}]})
    assert s.in_window(at(TZ, "2026-11-14 02:00"), tz=TZ) is not None, "Saturday 02:00 belongs to Friday's window"
    assert s.in_window(at(TZ, "2026-11-13 02:00"), tz=TZ) is None, "Friday 02:00 would be Thursday's"
    assert s.span_at(at(TZ, "2026-11-14 02:00"), tz=TZ) == (at(TZ, "2026-11-13 22:00"), at(TZ, "2026-11-14 03:00"))

def test_adjacent_windows_merge():
    s = Schedule.parse({"windows": [WEEKEND]})
    at = at(TZ, "2026-11-14 23:59") + timedelta(seconds=30)
    assert s.span_at(at, tz=TZ) == (at(TZ, "2026-11-14 00:00"), at(TZ, "2026-11-15 23:59"))
    assert s.next_edge(at, tz=TZ) == (at(TZ, "2026-11-15 23:59"), "end"), "no stop at midnight between Saturday and Sunday"

@pytest.mark.parametrize("zone", ZONES)   # [Amsterdam2026()] + [ZoneInfo("Europe/Amsterdam")] when it loads
def test_dst_change_keeps_wall_clock(zone):
    s = Schedule.parse({"windows": [{"days": list(DAYS), "from": "01:00", "to": "06:00"}]})
    spring = s.span_at(at(zone, "2026-03-29 03:30"), tz=zone)
    assert spring[0].astimezone(zone).strftime("%H:%M") == "01:00" and spring[1] - spring[0] == timedelta(hours=4)
    autumn = s.span_at(at(zone, "2026-10-25 04:00"), tz=zone)
    assert autumn[1] - autumn[0] == timedelta(hours=6)
    assert s.next_edge(at(zone, "2026-03-28 23:00"), tz=zone) == (at(zone, "2026-03-29 01:00"), "start")

def test_next_edge_from_outside_and_inside():
    s = Schedule.parse({"windows": [NIGHTS, WEEKEND]})
    assert s.next_edge(at(TZ, "2026-11-10 07:00"), tz=TZ) == (at(TZ, "2026-11-11 01:00"), "start")
    assert s.next_edge(at(TZ, "2026-11-10 03:00"), tz=TZ) == (at(TZ, "2026-11-10 06:00"), "end")
    assert s.next_edge(at(TZ, "2026-11-13 07:00"), tz=TZ) == (at(TZ, "2026-11-14 00:00"), "start")
    ending = Schedule.parse({"windows": [NIGHTS], "until": "2026-11-10"})
    assert ending.next_edge(at(TZ, "2026-11-10 07:00"), tz=TZ) is None
    assert ending.past_until(at(TZ, "2026-11-10 07:00"), tz=TZ)
    assert not ending.past_until(at(TZ, "2026-11-10 03:00"), tz=TZ)

def test_when_words():
    now = at(TZ, "2026-11-10 07:00")
    assert when_words(at(TZ, "2026-11-11 01:00"), now) == "Wed 01:00"
    assert when_words(at(TZ, "2026-11-19 01:00"), now) == "Thu 19 Nov 01:00"

def test_presets_parse():
    for preset in PRESETS.values():
        Schedule.parse(preset["schedule"])
```

  In `tests/test_study.py`: `test_study_yaml_keeps_its_schedule` — `Study.parse` of a document with `schedule: {windows: [NIGHTS]}` gives `study.schedule.windows[0].start == time(1, 0)` and `to_yaml()["schedule"] == Schedule.parse(...).to_yaml()`; a study without one has no `"schedule"` key in `to_yaml()`.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_schedule.py tests/test_study.py -q`
Expected: FAIL — `ModuleNotFoundError: evolvekit.harness.schedule`.

- [ ] **Step 3: Write `tests/clock_helpers.py` and `evolvekit/harness/schedule.py`**, then the `schedule` key in `study.py`.

  A time is a `"H:MM"`/`"HH:MM"` string or an int 0–1439 read as YAML's sexagesimal minutes (`60 * h + m`); `"24:00"` and 1440 get the "use 23:59" sentence. Day names are case-insensitive and deduplicated. An occurrence of a window on local date `d` runs from `_at(d, start)` to `_at(d + 1 day if straddles else d, end)`, where `_at(day, t, tz)` is `datetime.combine(day, t).replace(tzinfo=tz)` with a zone and `datetime.combine(day, t).astimezone()` without (the computer's own rules, DST included, on Windows too). `now` is turned aware the same way when naive. Compare in UTC.

- [ ] **Step 4: Run them to see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_schedule.py tests/test_study.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add evolvekit/harness/schedule.py evolvekit/harness/study.py tests/clock_helpers.py tests/test_schedule.py tests/test_study.py
git commit -m "schedule: windows in study.yaml, local wall-clock, straddling midnight and DST"
```

---

### Task 2: The scheduler tick

**Files:**
- Create: `evolvekit/app/scheduler.py`, `tests/campaign_helpers.py`, `tests/test_scheduler.py`
- Modify: `evolvekit/app/server.py` (`AppServer.tick_once()`; `_tick` loops over it), `evolvekit/cli.py` (`_tick_queue` also ticks the scheduler)

**Interfaces:**
- Consumes: Task 1's `Schedule.span_at`, `past_until`; `jobs.start`, `jobs.continue_run`, `jobs.stop`, `jobs.resume_final_check`, `jobs.can_resume`, `jobs.can_continue`, `jobs.phase`, `jobs.active_run`, `jobs._run_dirs`, `jobs.ACTIVE`, `jobs.MAX_CONTINUE_HOURS`; `execute.run_study_of`; `stopping.active_seconds`, `events.read_events`; `running.read_running`.
- Produces (`evolvekit/app/scheduler.py`; later tasks import these):
  - `CAMPAIGN = "campaign.json"` (in the study folder): `{"state": "armed"|"paused"|"complete"|"ended", "armed_at": iso, "reason": str|None, "by_hand": run|None, "stopped_by_window": run|None, "window": iso|None, "actions": int, "failures": int, "ticked_at": iso|None}`.
  - Constants `MIN_SESSION_S = 600.0`, `ACTIONS_PER_WINDOW = 3`, `FAILURES_TO_PAUSE = 3`, `TICK_WRITE_S = 60.0`, `LOCK = "scheduler.lock"` (in the home), `CLOCK_FILE = "clock.json"` (in the home).
  - `@dataclass(frozen=True) class Clock: start: datetime | None = None; speed: float = 1.0; origin: float = 0.0` with `now() -> datetime` (aware, local; `start + (time.time() - origin) * speed` when simulated), `simulated -> bool`, `words() -> str` (`"rehearsal clock: Sat 14 Nov 01:10, 60 × real time"`; `""` for the real clock).
  - `write_clock(home_root: Path, start: datetime, speed: float) -> Path`; `clear_clock(home_root: Path) -> None`; `clock_of(home) -> Clock` — `clock.json` while the app whose pid it names (`read_running`) still runs, else the real clock.
  - `read_campaign(root: Path) -> dict | None`; `arm(root: Path, *, now: datetime, by_hand: str | None = None) -> dict`; `pause(root: Path, *, reason: str, state: str = "paused") -> None`.
  - `search_left_s(run_dir: Path) -> float` — `plan.json`'s `max_hours * 3600` less `active_seconds(read_events(run_dir))`, at least 0.
  - `tick(home, *, now: datetime, speed: float = 1.0, start=jobs.start, continue_run=jobs.continue_run, stop=jobs.stop, recheck=jobs.resume_final_check) -> list[str]` — one line per thing it did, `"<slug>: <what>"`.
  - `AppServer.tick_once() -> None` (server.py): `jobs.launch_queued(home)`, then `scheduler.tick(home, now=clock.now(), speed=clock.speed)` with `clock = scheduler.clock_of(home)`; each in its own `try`, a fault printed to stderr with the traceback.
- `tests/campaign_helpers.py` produces: `local(text: str) -> datetime` (`"2026-11-14 00:00:05"` as the computer's wall-clock time, aware, through `astimezone()`); `WEEKEND`; `campaign_study(tmp_path, name="Tours", windows=(WEEKEND,), *, armed_at="2026-11-13T09:00:00+01:00") -> tuple[Home, Path]` (a demo-tour study in `Home(tmp_path / "my home")` -- the same home on every call -- from the `tune` template with the samples and one held back, the schedule saved and the campaign armed at `armed_at`); `write_run(root, run_id, phase, *, generations=2, planned=6, max_hours=1.0, used_s=0.0, final=None, check=False, error=None, alive=False) -> Path` (job.json, plan.json, runs.jsonl, events.jsonl with two events `used_s` apart; `alive=True` writes this process's pid and a fresh `launch.json`, so `jobs.phase` reads it as live); `Recorder(start_raises: Exception | None = None)` (callables `start`, `continue_run`, `stop`, `recheck` that append `(name, args, kwargs)` to `calls` and return what `jobs` would -- `{"run": "x"}`, `{"stopping": True}` -- or raise `start_raises`; `calls_for(only=None) -> dict` gives them as `tick`'s keyword arguments; `names() -> list[str]`).

The tick, for every study in the home whose campaign is `armed`, oldest `armed_at` first (the queue rule):

| State of the latest run | Inside a span | Outside every span |
|---|---|---|
| active (`starting`, `search`) | nothing | `stop` — unless it is the campaign's `by_hand` run or already has a stop request; records `stopped_by_window` |
| active (`check`) | nothing | nothing (spec §6.1) |
| none, or ended before its search began | `start(home, root)` | nothing |
| `jobs.can_resume` | `start(home, root, resume=run)` | nothing |
| search done, final check stopped or died (`final.stopped`, or `failed` with `check` in the job) | `recheck(home, root, run)` | nothing |
| `jobs.can_continue` | `continue_run(home, root, run, hours=h)` with `h = min((span_end - now) / speed, search_left_s, MAX_CONTINUE_HOURS * 3600) / 3600`, only when that is ≥ `MIN_SESSION_S`; when `search_left_s < MIN_SESSION_S` the campaign is `complete` | nothing |
| `failed` with an error not from a dead process | counts a failure; at `FAILURES_TO_PAUSE` the campaign is paused with the error | nothing |

Around every row: a study whose `schedule` is gone is paused ("the study has no schedule any more"); past `until` with no active run → `ended`; nothing is launched while any run in the home is active (`jobs.active_run`); at most `ACTIONS_PER_WINDOW` launches per span (`window` = the span's start; `actions` resets when it changes); after one launch the tick returns (one at a time). `AppError` from a call is logged as a line and counted as a failure. The whole tick runs under `<home>/scheduler.lock` created with `open(..., "x")` (`{"pid", "at"}`; a lock whose pid is dead or older than 60 s is taken over); when another live process holds it, the tick returns `[]`. `ticked_at` is written at most every `TICK_WRITE_S`.

- [ ] **Step 1: Write the failing tests** (`tests/test_scheduler.py`; times in the computer's zone through `datetime(...).astimezone()`; the campaign is `WEEKEND` unless said)

```python
def test_starts_at_window_start(tmp_path):
    home, root = campaign_study(tmp_path)
    rec = Recorder()
    assert scheduler.tick(home, now=local("2026-11-13 23:59"), **rec.calls_for()) == []
    said = scheduler.tick(home, now=local("2026-11-14 00:00:05"), **rec.calls_for())
    assert rec.names() == ["start"] and rec.calls[0][1] == (home, root) and rec.calls[0][2] == {}
    assert said == [f"{root.name}: started a run (window Sat 00:00 – Sun 23:59)"]

def test_unarmed_study_is_left_alone(tmp_path):
    home, root = campaign_study(tmp_path)
    (root / scheduler.CAMPAIGN).unlink()
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [], "a schedule alone never starts a draft"

def test_resumes_unfinished_plan_before_continuing(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "stopped", generations=2, planned=6)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [("start", (home, root), {"resume": "r1"})]

def test_continues_finished_run_with_window_hours(tmp_path):
    home, root = campaign_study(tmp_path, windows=({"days": ["sat"], "from": "00:00", "to": "06:00"},))
    write_run(root, "r1", "done", generations=6, planned=6, max_hours=1.0, used_s=900, final={"decision": "keep"})
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 03:00"), **rec.calls_for())
    assert rec.calls == [("continue_run", (home, root, "r1"), {"hours": pytest.approx(0.75)})], "the search time left, under 3 h of window"
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 03:00"), speed=6.0, **rec.calls_for())
    assert rec.calls[0][2]["hours"] == pytest.approx(0.5), "3 h of rehearsal clock at 6 x is half an hour"

def test_campaign_never_exceeds_its_search_time(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "done", generations=6, planned=6, max_hours=1.0, used_s=3600 - 300, final={"decision": "keep"})
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 03:00"), **rec.calls_for())
    assert rec.calls == [] and scheduler.read_campaign(root)["state"] == "complete"
    scheduler.tick(home, now=local("2026-11-15 03:00"), **rec.calls_for())
    assert rec.calls == []

def test_stops_at_window_end(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "search", alive=True)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-16 00:01"), **rec.calls_for())
    assert rec.calls == [("stop", (root, "r1"), {})]
    (root / "runs" / "r1" / STOP_REQUEST).write_text("stop\n", encoding="utf-8")
    scheduler.tick(home, now=local("2026-11-16 00:02"), **rec.calls_for())
    assert len(rec.calls) == 1, "asked once"
    assert scheduler.read_campaign(root)["stopped_by_window"] == "r1"

def test_never_stops_final_check(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "check", alive=True)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-16 00:01"), **rec.calls_for())
    assert rec.calls == []

def test_a_session_started_by_hand_is_not_stopped(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "search", alive=True)
    scheduler.arm(root, now=local("2026-11-16 00:00"), by_hand="r1")
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-16 00:01"), **rec.calls_for())
    assert rec.calls == []

def test_wakes_inside_window_starts_late(tmp_path):
    home, root = campaign_study(tmp_path)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-13 20:00"), **rec.calls_for())     # Friday: the last tick before the sleep
    scheduler.tick(home, now=local("2026-11-14 09:00"), **rec.calls_for())     # wakes on Saturday morning
    assert rec.names() == ["start"]
    write_run(root, "r1", "search", alive=True)
    scheduler.tick(home, now=local("2026-11-16 00:10"), **rec.calls_for())
    assert rec.names() == ["start", "stop"], "the window still ends at Sunday 23:59: no time carried over"

def test_overlapping_campaigns_take_turns(tmp_path):
    home, a = campaign_study(tmp_path, "A", windows=({"days": ["sat"], "from": "01:00", "to": "03:00"},),
                             armed_at="2026-11-13T09:00:00+01:00")
    _, b = campaign_study(tmp_path, "B", windows=({"days": ["sat"], "from": "01:00", "to": "06:00"},),
                          armed_at="2026-11-13T10:00:00+01:00")
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 01:00"), **rec.calls_for())
    assert rec.calls == [("start", (home, a), {})], "the oldest schedule first, one at a time"
    write_run(a, "a1", "search", alive=True)
    scheduler.tick(home, now=local("2026-11-14 02:00"), **rec.calls_for())
    assert len(rec.calls) == 1, "B waits while A has the computer"
    scheduler.tick(home, now=local("2026-11-14 03:00:05"), **rec.calls_for())
    assert rec.calls[-1] == ("stop", (a, "a1"), {})
    write_run(a, "a1", "stopped", generations=2, planned=6)
    scheduler.tick(home, now=local("2026-11-14 03:01"), **rec.calls_for())
    assert rec.calls[-1] == ("start", (home, b), {}), "B gets the rest of its window"

def test_waits_behind_another_studys_run(tmp_path):
    home, root = campaign_study(tmp_path)
    _, other = campaign_study(tmp_path, "Other")
    (other / scheduler.CAMPAIGN).unlink()
    write_run(other, "o1", "search", alive=True)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == []

def test_past_until_does_nothing(tmp_path):
    home, root = campaign_study(tmp_path)
    study = load_study(root)
    study.schedule = replace(study.schedule, until=date(2026, 11, 13))
    save_study(study, root)
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [] and scheduler.read_campaign(root)["state"] == "ended"

def test_rechecks_a_final_check_that_did_not_finish(tmp_path):
    home, root = campaign_study(tmp_path)
    write_run(root, "r1", "failed", generations=6, planned=6, check=True,
              error="the run's process ended without finishing: the computer restarted")
    rec = Recorder()
    scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for())
    assert rec.calls == [("recheck", (home, root, "r1"), {})]

def test_failing_campaign_pauses_after_three_failures(tmp_path):
    home, root = campaign_study(tmp_path)
    rec = Recorder(start_raises=AppError("the study is not ready yet: cases.test: hold back at least one case", 409))
    for hour in ("10:00", "10:01", "10:02", "10:03"):
        scheduler.tick(home, now=local(f"2026-11-14 {hour}"), **rec.calls_for())
    assert rec.names() == ["start"] * 3
    campaign = scheduler.read_campaign(root)
    assert campaign["state"] == "paused" and "hold back at least one case" in campaign["reason"]

def test_actions_per_window_are_capped(tmp_path):
    home, root = campaign_study(tmp_path)
    rec = Recorder()   # start "succeeds" but no run appears: as if each run died at once
    for minute in range(6):
        scheduler.tick(home, now=local(f"2026-11-14 10:0{minute}"), **rec.calls_for())
    assert rec.names() == ["start"] * scheduler.ACTIONS_PER_WINDOW

def test_one_tick_at_a_time_across_processes(tmp_path):
    home, root = campaign_study(tmp_path)
    (home.root / scheduler.LOCK).write_text(json.dumps({"pid": os.getppid(), "at": datetime.now(timezone.utc).isoformat()}),
                                            encoding="utf-8")
    rec = Recorder()
    assert scheduler.tick(home, now=local("2026-11-14 10:00"), **rec.calls_for()) == [] and rec.calls == []

def test_server_ticks_the_queue_then_the_scheduler(tmp_path, monkeypatch):
    order = []
    monkeypatch.setattr(jobs, "launch_queued", lambda home: order.append("queue"))
    def broken(home, **kw):
        order.append("schedule"); raise RuntimeError("a bad study")
    monkeypatch.setattr(scheduler, "tick", broken)
    server = AppServer(tmp_path / "my home", port=0)
    try:
        server.tick_once()          # never raises
    finally:
        server.stop()
    assert order == ["queue", "schedule"]

def test_wait_and_watch_tick_the_scheduler(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(scheduler, "tick", lambda home, **kw: seen.append(kw["now"]) or [])
    home, _ = campaign_study(tmp_path)
    cli._tick_queue(home)
    assert len(seen) == 1 and seen[0].tzinfo is not None
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scheduler.py -q`
Expected: FAIL — `ImportError: cannot import name 'scheduler'`.

- [ ] **Step 3: Write `tests/campaign_helpers.py`, then `evolvekit/app/scheduler.py` as the table and the paragraph under it say**; `AppServer.tick_once` and `_tick` (`while not self._stopping.wait(TICK_S): self.tick_once()`); `cli._tick_queue` calls `scheduler.tick(home, now=clock.now(), speed=clock.speed)` after `launch_queued`, a fault said in one line on stderr as the queue's is. `scheduler.py` imports `jobs` at module level; `jobs` imports `scheduler` only inside functions (Task 3).

- [ ] **Step 4: Run them to see them pass**, and the neighbours

Run: `.venv/Scripts/python.exe -m pytest tests/test_scheduler.py tests/test_app_server.py tests/test_cli_study.py -q -m "not slow"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add evolvekit/app/scheduler.py evolvekit/app/server.py evolvekit/cli.py tests/campaign_helpers.py tests/test_scheduler.py
git commit -m "scheduler: the ticker starts, resumes, re-checks, continues and stops campaign runs at window edges"
```

---

### Task 3: Start now, and the campaign line

**Files:**
- Modify: `evolvekit/app/scheduler.py` (`campaign_state`, `campaign_done`, `start_campaign`, `stop_by_person`), `evolvekit/app/jobs.py` (`state_line` gains `campaign` and the `campaign` card kind; `status` and `_run_line` time left of a resumed session), `evolvekit/cli.py` (`study start --now`, `study stop`, `study wait`, `study status` on a campaign), `evolvekit/app/store.py` (`save_step` accepts `schedule`), `evolvekit/app/api.py` (`start`, `stop`; the study document's `campaign` and `schedule_presets`), `evolvekit/app/static/js/words.js` (`EK.cardOf` for `campaign`), `pages/home.js` (poll), `pages/setup.js` (`whenSummary`, `whenDrawer`, the start act), `pages/study.js` (the campaign notice), `README.md` (`study start` and `study stop` rows; `study.yaml`'s `schedule:` in *A study from the command line*)
- Test: `tests/test_scheduler.py`, `tests/test_cli_study.py`, `tests/test_app_jobs.py`, `tests/test_app_store.py`, `tests/test_app_page.py`

**Interfaces:**
- Consumes: Task 1 (`Schedule`, `PRESETS`, `when_words`), Task 2 (`arm`, `pause`, `read_campaign`, `search_left_s`, `clock_of`, `tick`).
- Produces:
  - `scheduler.campaign_state(root: Path, study: Study, *, now: datetime) -> dict | None` — `None` without a schedule, else `{"state": "draft"|"armed"|"paused"|"complete"|"ended", "next": iso|None, "edge": "start"|"end"|None, "line": str, "tone": "warn"|None, "missed": iso|None}`; the lines, exactly:
    - draft: `"Campaign · not started: its first window would be Sat 00:00"`
    - armed, outside: `"Campaign · next window Sat 00:00"`; inside: `"Campaign · this window ends Sun 23:59"`
    - armed, a whole span since `armed_at` passed with no tick in it (`ticked_at` before its start, now after its end): `"Campaign · the window of Sat 00:00 was missed: the app was not running · next window Mon 01:00"`, tone `warn`
    - paused: `"Campaign · paused: <reason>"` (tone `warn`); complete: `"Campaign · done: its search time is spent"`; ended: `"Campaign · ended on 31 Dec"`
  - `scheduler.campaign_done(root, study, *, now) -> bool` — the campaign is `complete` or `ended`, or paused, and no run is active.
  - `scheduler.start_campaign(home, root, *, now: datetime, at_once: bool, start=jobs.start, continue_run=jobs.continue_run, recheck=jobs.resume_final_check) -> dict` — `at_once=False`: `arm` → `{"scheduled": True, "campaign": campaign_state(...)}`; `at_once=True`: does what the tick would do inside a window (start, resume, recheck or continue with the campaign's search time left), ignoring the window, and arms with `by_hand` = that run → `jobs.start`'s answer plus `"campaign"`.
  - `scheduler.stop_by_person(home, root, run: str | None) -> dict` — `pause(root, reason="stopped by you")`, then `jobs.stop` when the run is active or queued → `{"paused": True, **jobs.stop(...)}` or `{"paused": True, "stopping": False}`.
  - `jobs.state_line(root, study, document_of=None, *, now: datetime | None = None)` (`now` defaults to the home's clock, `scheduler.clock_of` of `root.parent.parent`) — `campaign` key whenever `campaign_state` is not `None`; card `kind: "campaign"` with `line` `"campaign · next window Sat 00:00"` (the state line's words, lower-case first word) when the campaign is armed and the latest run is not active and is absent or was stopped by the window (`stopped_by_window`).
  - Time left (`jobs.status`'s `left_s`/`finish_at` and the running card's words): a session after a resume or a continuation counts the search time left (`search_left_s`, as the engine's clock across sessions does), never from the run's first `started_at`; a campaign session's left is also at most its span's end (real seconds, by the clock's speed); `finish_at` then says the earlier of the two.
  - The study document (`api._document`) gains `campaign` (`campaign_state`) and `schedule_presets` (`schedule.PRESETS`); `study.schedule` is in `study`.
  - `POST /api/studies/{slug}/start` with a schedule: `{"now": true}` optional → `start_campaign`; `POST /stop` on an armed campaign → `stop_by_person`.
  - `study start SLUG [--now] [--wait] [--json]`; a scheduled study without `--now` prints `scheduled: next window Sat 00:00 (the app, or evolvekit study wait, starts it then)`, `--json` `{"scheduled": true, "next": iso}`; then, when no app runs for the home, a second line on stderr: `note: windows are kept while the app runs: evolvekit app --service, or evolvekit app install-startup to start it at logon`.
  - `study wait` on a study with an armed campaign waits until `campaign_done` (a window's stop is not an end; it says the campaign line on stderr when it changes); exit 0 when the latest run is `done`, 1 when the campaign paused or ended without one, 4 on timeout. It needs no run to exist yet.

- [ ] **Step 1: Write the failing tests**

  In `tests/test_scheduler.py`:

```python
def test_campaign_lines(tmp_path):
    home, root = campaign_study(tmp_path)
    study = load_study(root)
    assert scheduler.campaign_state(root, study, now=local("2026-11-13 18:00"))["line"] == "Campaign · next window Sat 00:00"
    assert scheduler.campaign_state(root, study, now=local("2026-11-14 10:00"))["line"] == "Campaign · this window ends Sun 23:59"
    scheduler.pause(root, reason="stopped by you")
    assert scheduler.campaign_state(root, study, now=local("2026-11-14 10:00"))["line"] == "Campaign · paused: stopped by you"
    (root / scheduler.CAMPAIGN).unlink()
    assert scheduler.campaign_state(root, study, now=local("2026-11-13 18:00"))["line"] == \
        "Campaign · not started: its first window would be Sat 00:00"

def test_missed_window_is_said(tmp_path):
    home, root = campaign_study(tmp_path, windows=({"days": ["sat"], "from": "00:00", "to": "06:00"},
                                                   {"days": ["mon"], "from": "01:00", "to": "06:00"}))
    scheduler.tick(home, now=local("2026-11-13 20:00"), **Recorder().calls_for())   # the last tick: Friday
    state = scheduler.campaign_state(root, load_study(root), now=local("2026-11-15 12:00"))
    assert state["line"] == "Campaign · the window of Sat 00:00 was missed: the app was not running · next window Mon 01:00"
    assert state["tone"] == "warn"

def test_start_now_overrides(tmp_path):
    home, root = campaign_study(tmp_path)
    (root / scheduler.CAMPAIGN).unlink()
    rec = Recorder()
    answer = scheduler.start_campaign(home, root, now=local("2026-11-13 18:00"), at_once=True, **rec.calls_for(only=("start",)))
    assert rec.calls == [("start", (home, root), {})] and scheduler.read_campaign(root)["by_hand"] == answer["run"]
```

  In `tests/test_cli_study.py` (the CLI through `main` and `capsys`, the clock patched with `monkeypatch.setattr(scheduler, "clock_of", lambda home: scheduler.Clock(start=local("2026-11-13 18:00"), origin=time.time()))`):

```python
def test_start_scheduled_prints_next_window(tmp_path, capsys, monkeypatch):
    home, root = campaign_study(tmp_path); (root / scheduler.CAMPAIGN).unlink()
    started = []
    monkeypatch.setattr(jobs, "start", lambda *a, **k: started.append(a) or {"run": "x"})
    assert main(["study", "start", root.name, "--home", str(home.root)]) == 0
    out, err = capsys.readouterr()
    assert out.splitlines()[0] == "scheduled: next window Sat 00:00 (the app, or evolvekit study wait, starts it then)"
    assert "evolvekit app install-startup" in err and started == []
    assert scheduler.read_campaign(root)["state"] == "armed"
    assert main(["study", "start", root.name, "--home", str(home.root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[0]) == {"scheduled": True, "next": local("2026-11-14 00:00").isoformat()}

def test_start_now_starts_at_once(tmp_path, capsys, monkeypatch):   # --now: jobs.start called once, the run printed,
    ...                                                              # campaign armed with by_hand = that run

def test_stop_pauses_the_campaign(tmp_path, capsys):
    home, root = campaign_study(tmp_path)
    assert main(["study", "stop", root.name, "--home", str(home.root)]) == 0
    assert capsys.readouterr().out.strip() == "paused the campaign (no run was running); evolvekit study start resumes it"
    assert scheduler.read_campaign(root)["state"] == "paused"

def test_wait_on_a_campaign_waits_through_window_stops(tmp_path, capsys, monkeypatch):
    # jobs.phase patched to give: stopped (window end), search, done; campaign_done false, false, true
    # -> exit 0 with the verdict; the stopped phase never ends the wait; the campaign line is said on stderr once per change
```

  In `tests/test_app_jobs.py`:

```python
def test_home_card_campaign_line(tmp_path):
    home, root = campaign_study(tmp_path)
    state = jobs.state_line(root, load_study(root), now=local("2026-11-13 18:00"))
    assert state["kind"] == "campaign" and state["line"] == "campaign · next window Sat 00:00"
    assert state["campaign"]["next"] == local("2026-11-14 00:00").isoformat()
    write_run(root, "r1", "stopped", generations=2, planned=6)
    scheduler.arm(root, now=local("2026-11-13 18:00"))
    campaign = json.loads((root / scheduler.CAMPAIGN).read_text(encoding="utf-8"))
    campaign["stopped_by_window"] = "r1"
    (root / scheduler.CAMPAIGN).write_text(json.dumps(campaign), encoding="utf-8")
    assert jobs.state_line(root, load_study(root), now=local("2026-11-16 08:00"))["kind"] == "campaign", \
        "a window's stop is not 'stopped' on Home"

def test_a_resumed_campaign_session_says_its_own_time_left(tmp_path):
    home, root = campaign_study(tmp_path, windows=({"days": ["sat"], "from": "00:00", "to": "06:00"},))
    run_dir = write_run(root, "r1", "search", alive=True, max_hours=1.0, used_s=1800)
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    job.update(started_at="2026-11-07T00:00:00+01:00", resumed_at=local("2026-11-14 05:50").isoformat())
    (run_dir / "job.json").write_text(json.dumps(job), encoding="utf-8")
    status = jobs.status(root, "r1", lambda d: {}, now=local("2026-11-14 05:50"))
    assert status["time"]["left_s"] == pytest.approx(600, abs=5), "the window's 10 min, not 0 from a week-old start"
```

  In `tests/test_app_store.py`: `test_app_edit_keeps_schedule_per_group_and_warm` — a study with a `schedule` saved through `home.save_step(slug, {"limits": {...}})` and `{"cases": {...}}` keeps it in `study.yaml`; `home.save_step(slug, {"schedule": PRESETS["weekends"]["schedule"]})` sets it and `{"schedule": None}` removes it. Task 6 extends the test with `warm` and Task 9 with `per_group: true`, each adding its key to `save_step`'s allowlist.

  In `tests/test_app_page.py`:

```python
def test_home_polls_while_a_campaign_is_armed():
    poll = _page_function(HOME_PAGE, "poll")
    assert 's.kind === "campaign"' in poll and "30000" in poll

def test_the_campaign_card_speaks_the_campaigns_words():
    out = _node("""process.stdout.write(JSON.stringify(EK.cardOf({slug: "t", name: "T", kind: "campaign",
        line: "campaign · next window Sat 00:00", campaign: {line: "Campaign · next window Sat 00:00"}, rounds: {done: 2, planned: 6}})));""")
    assert out["kicker"] == "Campaign · next window Sat 00:00" and out["head"] == "Between windows"
    assert out["sub"] == "2 of 6 rounds done · not checked yet"

def test_setup_offers_when_it_runs():
    drawer = _page_function(SETUP_PAGE, "whenDrawer")
    assert "schedule_presets" in drawer and '{ schedule: null }' in drawer
    assert "Schedule it" in _page_function(SETUP_PAGE, "draw")
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scheduler.py tests/test_cli_study.py tests/test_app_jobs.py tests/test_app_store.py tests/test_app_page.py -q -m "not slow"`
Expected: FAIL — `campaign_state` and the `--now` flag do not exist.

- [ ] **Step 3: Implement** the interfaces above. `jobs.status` gains `now: datetime | None = None` (for the test). The page: `EK.cardOf` `campaign` → kicker `s.campaign.line`, head `"Between windows"` (no run yet: `"Waits for its first window"`), sub `"N of M rounds done · not checked yet"` when rounds are known, tone from `s.campaign.tone`; Home's `poll` polls every 30 s while any study's kind is `campaign` (10 s while one is running or queued, as now); Set up's *Limits and budget* summary gains a line *When it runs* (`whenSummary`: "As soon as you start it", or the preset's label and "until 31 Dec"), its drawer `whenDrawer` offers the presets as a radio list plus "As soon as I start it" and an optional *Until* date, saved with `save({ schedule: … })` or `save({ schedule: null })`, and the sentence "It runs only inside these hours, and only while the app is running then." The start act on a scheduled study says `Scheduled: next window Sat 00:00` and goes Home; the Review step's start button reads "Schedule it" when the study has a schedule. The Study page shows `doc.state.campaign.line` as a notice under the header (tone from the state).

- [ ] **Step 4: Run them to see them pass**

Run: same command → PASS.

- [ ] **Step 5: README** — under *A study from the command line*, a paragraph **Run it in windows** with the YAML of design §6.1, that `budget.hours` is then the whole campaign's search time spread over as many windows as it takes, that `study start` waits for the first window (`--now` starts at once, and that session is not stopped at a window's end), that `study stop` pauses the campaign, and that the app (or `study wait`) must be running in the windows. The command table: `study start SLUG [--now] [--wait [--timeout S]] [--json]` (with the scheduled case) and `study stop` (pauses a campaign).

- [ ] **Step 6: Commit**

```bash
git add evolvekit tests README.md
git commit -m "campaigns: study start --now, the campaign line on Home, When it runs in Set up, a person's stop pauses"
```

---

### Task 4: The app as a service

**Files:**
- Create: `evolvekit/app/service.py`, `tests/test_app_service.py`
- Modify: `evolvekit/cli.py` (`p_app`: positional `action` in `{install-startup, uninstall-startup}`, `--service`, `--clock ISO`, `--clock-speed N`; `cmd_app`; `_study_open` starts `app --service`), `evolvekit/app/detach.py` (`python_for_runs`), `evolvekit/app/jobs.py` (`_launch`'s argv[0]), `evolvekit/app/api.py` (`home` gains `clock`), `pages/home.js` (the rehearsal clock notice), `README.md` (the `app` rows), `docs/app.md` (*Starting it*: the option table)

**Interfaces:**
- Consumes: Task 2 (`write_clock`, `clear_clock`, `clock_of`, `Clock.words`); `running.read_running`; `AppServer`.
- Produces (`evolvekit/app/service.py`):
  - `LOG = "app.log"`, `LOG_BYTES = 5 * 1024 * 1024`, `LOG_BACKUPS = 2`, `TASK_NAME = "evolvekit app"`, `TASK_XML = "evolvekit-app-task.xml"` (in the home), `UNIT_NAME = "evolvekit-app.service"`, `PLIST_NAME = "evolvekit.app.plist"`.
  - `start_logging(home_root: Path, *, redirect: bool = False) -> logging.Logger` — a `RotatingFileHandler(<home>/app.log, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8")` (the constants read when called, so a test can lower them), lines `"%(asctime)s %(message)s"`; with `redirect` (the service), `sys.stdout` and `sys.stderr` are replaced by writers that log each line, so the ticker's tracebacks land in the log.
  - `windowless(python: str) -> str` — on Windows `pythonw.exe` beside `python.exe` when it exists, else `python`.
  - `service_argv(home_root: Path, *, python: str | None = None) -> list[str]` — `[windowless(python or sys.executable), "-m", "evolvekit", "app", "--service", "--home", str(home_root)]`.
  - `task_xml(argv: list[str], user: str) -> str` — a Task Scheduler 1.2 definition: `LogonTrigger` for `user`, `Exec/Command` = `argv[0]`, `Exec/Arguments` = `subprocess.list2cmdline(argv[1:])`, `ExecutionTimeLimit PT0S`, `DisallowStartIfOnBatteries false`, `StopIfGoingOnBatteries false`, `MultipleInstancesPolicy IgnoreNew`, `LogonType InteractiveToken`, `RunLevel LeastPrivilege`; every value XML-escaped.
  - `install_startup(home_root: Path, *, platform: str = sys.platform, run=subprocess.run, out=print, err=<print to stderr>, python: str | None = None, user: str | None = None) -> int` and `uninstall_startup(home_root, *, platform, run, out, err) -> int` (`user` defaults to `USERDOMAIN\USERNAME`):
    - win32: writes `<home>/evolvekit-app-task.xml` as UTF-16 with a BOM; runs `["schtasks", "/Create", "/TN", "evolvekit app", "/XML", <that path>, "/F"]`; prints `registered the task "evolvekit app": at logon it runs` and the command line on the next line; on a non-zero exit, schtasks' last line and `you can also start it yourself: <command>` on stderr, exit 1. Uninstall: `["schtasks", "/Delete", "/TN", "evolvekit app", "/F"]`.
    - darwin: prints the plist (`Label evolvekit.app`, `ProgramArguments` = `service_argv`, `RunAtLoad true`), then `save it as ~/Library/LaunchAgents/evolvekit.app.plist, then run: launchctl load ~/Library/LaunchAgents/evolvekit.app.plist`; exit 0, nothing run. Uninstall prints `launchctl unload …` and `rm …`.
    - linux: prints the unit (`[Unit] Description=evolvekit app`, `[Service] ExecStart=<shlex.join(argv)>`, `Restart=on-failure`, `[Install] WantedBy=default.target`), then `save it as ~/.config/systemd/user/evolvekit-app.service, then run: systemctl --user enable --now evolvekit-app`; exit 0. Uninstall prints `systemctl --user disable --now evolvekit-app` and `rm …`.
  - `detach.python_for_runs() -> str` — `sys.executable`, or `python.exe` beside it when it is `pythonw.exe`; `spawn_detached`'s launcher and `jobs._launch` use it.
  - `cmd_app`:
    - another app already runs for the home (`read_running`): `--service` logs nothing new and exits 0 (`already running at <url>` to the log of that home); the browser form prints `the app is already running for this library: <url>`, opens it unless `--no-browser`, exits 0. One app per home, so two tickers never share a home.
    - `--service`: `start_logging`, no browser, no prints, no console reads; `AppServer` writes `app.json`; logs the address, the home and `Clock.words()`; serves until the process ends; `clear_clock` and `server.stop()` in `finally`.
    - `--clock ISO` (a naive time is local) with `--clock-speed N` (1 to 3600, default 1) writes `clock.json` for this app; `--clock-speed` without `--clock` exits 2; the browser form prints the clock's words as its third line.
  - `GET /api/home` gains `clock: {"line": Clock.words()}` while the clock is simulated; Home shows it as a warning notice above the cards.
  - `study open` starts `[python_for_runs(), "-m", "evolvekit", "app", "--home", <home>, "--service"]` with its launcher output in `<home>/app-start.log` (the service writes `app.log` itself, and rotation cannot rename a file another handle holds on Windows).

- [ ] **Step 1: Write the failing tests** (`tests/test_app_service.py`; `HOME = tmp_path / "my home"`)

```python
def test_service_writes_log_and_app_json(tmp_path):
    home = tmp_path / "my home"
    proc = subprocess.Popen([sys.executable, "-m", "evolvekit", "app", "--service", "--home", str(home), "--port", "0"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    try:
        running = _until(lambda: read_running(home), timeout=30)
        assert urllib.request.urlopen(running["url"]).status == 200
        log = (home / "app.log").read_text(encoding="utf-8")
        assert running["url"] in log and str(home.resolve()) in log
    finally:
        proc.terminate(); out, err = proc.communicate(timeout=30)
    assert out == b"", "nothing on the console"

def test_log_rotates(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "LOG_BYTES", 1000)
    logger = service.start_logging(tmp_path)
    for i in range(200):
        logger.info("line %d %s", i, "x" * 40)
    names = sorted(p.name for p in tmp_path.iterdir() if p.name.startswith("app.log"))
    assert names == ["app.log", "app.log.1", "app.log.2"]

def test_install_startup_windows_command(tmp_path):
    home = tmp_path / "my home"; home.mkdir()
    calls, printed = [], []
    code = service.install_startup(home, platform="win32", run=lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""),
                                   out=printed.append, python=r"C:\Program Files\Py\python.exe")
    assert code == 0
    assert calls == [["schtasks", "/Create", "/TN", "evolvekit app", "/XML", str(home / "evolvekit-app-task.xml"), "/F"]]
    xml = (home / "evolvekit-app-task.xml").read_bytes().decode("utf-16")
    assert r"<Command>C:\Program Files\Py\python.exe</Command>" in xml   # pythonw.exe when it exists beside it
    assert f'<Arguments>-m evolvekit app --service --home "{home}"</Arguments>' in xml
    assert "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in xml and "<DisallowStartIfOnBatteries>false" in xml
    assert printed[0] == 'registered the task "evolvekit app": at logon it runs'
    assert printed[1] == subprocess.list2cmdline([r"C:\Program Files\Py\python.exe", "-m", "evolvekit", "app", "--service",
                                                  "--home", str(home)])

def test_install_startup_reports_a_refusal(tmp_path, capsys):   # schtasks exit 1 "ERROR: Access is denied." -> 1,
    ...                                                          # that line and "you can also start it yourself:" on stderr

@pytest.mark.parametrize("platform, path", [("darwin", "~/Library/LaunchAgents/evolvekit.app.plist"),
                                            ("linux", "~/.config/systemd/user/evolvekit-app.service")])
def test_install_startup_other_platforms_print_unit(tmp_path, platform, path):
    printed = []
    assert service.install_startup(tmp_path, platform=platform, run=_never, out=printed.append) == 0
    text = "\n".join(printed)
    assert path in text and "--service" in text and str(tmp_path) in text

def test_uninstall_startup(tmp_path):   # win32: argv ["schtasks", "/Delete", "/TN", "evolvekit app", "/F"]; others print the removal

def test_python_for_runs_is_never_pythonw(tmp_path, monkeypatch):
    (tmp_path / "python.exe").write_bytes(b""); (tmp_path / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    assert detach.python_for_runs() == str(tmp_path / "python.exe")

def test_a_second_app_on_the_same_home_opens_the_first(tmp_path, capsys):
    home = tmp_path / "my home"; home.mkdir()
    write_running(home, pid=os.getpid(), port=8790, url="http://127.0.0.1:8790/?token=k")
    assert main(["app", "--home", str(home), "--no-browser"]) == 0
    assert capsys.readouterr().out.strip() == "the app is already running for this library: http://127.0.0.1:8790/?token=k"

def test_clock_flag_writes_and_the_home_reads_it(tmp_path):
    home = Home(tmp_path / "my home")
    scheduler.write_clock(home.root, datetime(2026, 11, 13, 17, 30).astimezone(), 60.0)
    assert scheduler.clock_of(home).start is None, "no app runs: the real clock"
    write_running(home.root, pid=os.getpid(), port=1, url="u")
    clock = scheduler.clock_of(home)
    assert clock.simulated and clock.speed == 60.0 and clock.words().startswith("rehearsal clock: Fri 13 Nov 17:30")
    assert main(["app", "--home", str(home.root), "--clock-speed", "60"]) == 2

def test_study_open_starts_the_service(tmp_path, monkeypatch):   # spawn_detached patched: argv ends with "--service",
    ...                                                           # log is <home>/app-start.log
```

  In `tests/test_app_page.py`: `test_home_shows_the_rehearsal_clock` — Home's `draw` renders `data.clock.line` in a warning notice when present.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_service.py tests/test_app_page.py -q`
Expected: FAIL — `evolvekit.app.service` does not exist.

- [ ] **Step 3: Implement** `service.py`, `detach.python_for_runs`, `jobs._launch`'s interpreter, `cmd_app`'s three forms and the flags, `_study_open`, the `clock` key and Home's notice.

- [ ] **Step 4: Run them to see them pass**, and the neighbours

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_service.py tests/test_app_page.py tests/test_app_server.py tests/test_cli_study.py -q -m "not slow"`
Expected: PASS.

- [ ] **Step 5: Docs** — README command table (the app's rows): `app --service` (no browser or console, logs to `<home>/app.log` rotated at 5 MB with two kept, keeps campaigns' windows), `app install-startup` / `app uninstall-startup` (Windows: the logon task "evolvekit app", printed; macOS and Linux: the unit to install, printed), `app --clock ISO --clock-speed N` (a rehearsal clock for campaigns: windows follow it, runs keep real time; Home says so). `docs/app.md` *Starting it*: the same three rows in the option table, and one sentence that a second `evolvekit app` on the same library opens the first.

- [ ] **Step 6: Commit**

```bash
git add evolvekit tests README.md docs/app.md
git commit -m "app --service: a rotated log, one app per home, the logon task, a rehearsal clock"
```

---

### Task 5: `memory.learned`

**Files:**
- Create: `evolvekit/harness/memory.py`, `tests/test_memory.py`

**Interfaces:**
- Consumes: `jobs._run_dirs`, `jobs.phase`, `jobs._kind_of`, `verdict.build_verdict`, `execute.run_study_of`, `status.build_status` (its `parameters.items[*]` — `name`, `default`, `best`, `importance.value`, `importance.n`), `library.find_harnesses`, `manifest.load_harness`. `home` is anything with `.root` and `.studies_dir` (the app's `Home`): `memory.py` does not import `evolvekit.app.store`.
- Produces:
  - `IMPORTANCE_MIN_N = 5`.
  - `@dataclass(frozen=True) class Finished: slug: str; name: str; root: Path; run: str; run_dir: Path; finished_at: datetime; decision: str; line: str; kind: str; study: Study; pinned: Harness` — `kind` in `"settings"|"data"|"both"|"code"|"bakeoff"|"per_group"`.
  - `finished_studies(home, harness_id: str) -> list[Finished]` — oldest first; per study in `home.studies_dir` (archived ones too; the trash is not there) whose pinned harness id is `harness_id`: its newest run whose phase is `done`, with the verdict's `decision` and `line`.
  - `current_harness(home, harness_id: str, finished: list[Finished]) -> Harness | None` — the library's newest of that id, else the newest pinned copy.
  - `learned(home, harness_id: str, *, document_of: Callable[[Path], dict] | None = None) -> dict` (`None`: `memory.build_status`, looked up at call time so a test can patch it; the same rule for every `document_of` in this module) — exactly:
    `{"studies": [{"slug", "name", "run", "finished_at", "decision", "line", "kind"}], "settings": {name: {"label", "tuned_in": int, "importance": float | None, "importance_n": int, "winners": [{"value", "study", "finished_at"}], "direction": "up"|"down"|"mixed"|None}}, "notes": [str]}`.
    Settings facts come only from settings-tuning studies (`kind` `settings` or `both`): `tuned_in` counts studies that tuned it; `importance` is the mean of `importance.value` over those whose `importance.n >= IMPORTANCE_MIN_N`, `importance_n` their number; `winners` are the best's values in studies whose decision is `use`; `direction` compares each winner with that study's starting value (`default` in the status item): all above `up`, all below `down`, both `mixed`, `None` for no winner or a non-numeric setting. A setting the current harness lacks, or whose declaration differs (type, low, high, log, choices), is left out for that study with a note: `"old_knob (in Depot spring) is not a setting of Demo tour 1.1.0 any more: left out"`, `"neighbours: its range changed since Depot spring (2–20, now 2–30): that study's figures are left out"`.

- [ ] **Step 1: Write the failing tests** (`tests/test_memory.py`; a helper `finished(home, docs, name, *, decision, best=None, importance=None, finished_at, start=None) -> Path` (the study's folder) makes a demo-tour study with `home.create_study("demo-tour", "tune", name)` and a run through `test_app_results.finished_run` with `best`'s values in the best's row; `docs` maps run folders to status documents whose `parameters.items` carry the given importance; `document_of=docs.get`)

```python
def test_learned_counts_and_importance_mean(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "One", decision="keep", importance={"neighbours": (0.6, 20)}, finished_at="2026-10-01")
    finished(home, docs, "Two", decision="keep", importance={"neighbours": (0.2, 20)}, finished_at="2026-10-08")
    finished(home, docs, "Three", decision="keep", importance={"neighbours": (0.9, 3)}, finished_at="2026-10-15")
    got = memory.learned(home, "demo-tour", document_of=docs.get)
    n = got["settings"]["neighbours"]
    assert n["tuned_in"] == 3 and n["importance"] == pytest.approx(0.4) and n["importance_n"] == 2
    assert [s["name"] for s in got["studies"]] == ["One", "Two", "Three"]

def test_winners_only_from_use_studies(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
    finished(home, docs, "Summer", decision="keep", best={"neighbours": 20}, finished_at="2026-10-08")
    n = memory.learned(home, "demo-tour", document_of=docs.get)["settings"]["neighbours"]
    assert n["winners"] == [{"value": 12, "study": "spring", "finished_at": "2026-10-01T00:12:00+00:00"}]
    assert n["direction"] == "up", "from the starting value 8"

def test_direction_mixed_and_none(tmp_path):   # winners 12 and 5 from start 8 -> "mixed"; `init` (a choice) -> None

def test_learned_ignores_settings_the_harness_no_longer_has(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    root = finished(home, docs, "Depot spring", decision="use", best={"neighbours": 12, "old_knob": 3}, finished_at="2026-10-01")
    pinned = yaml.safe_load((root / "harness" / "harness.yaml").read_text(encoding="utf-8"))
    pinned["version"] = "1.0.0"
    pinned["settings"]["old_knob"] = {"type": "int", "low": 1, "high": 9, "default": 2, "label": "Old knob"}
    pinned["settings"]["neighbours"]["high"] = 20
    (root / "harness" / "harness.yaml").write_text(yaml.safe_dump(pinned, sort_keys=False), encoding="utf-8")
    got = memory.learned(home, "demo-tour", document_of=docs.get)
    assert "old_knob" not in got["settings"]
    assert got["settings"]["neighbours"]["winners"] == [] and got["settings"]["neighbours"]["tuned_in"] == 0
    assert got["notes"] == ["old_knob (in Depot spring) is not a setting of Demo tour 1.1.0 any more: left out",
                            "neighbours: its range changed since Depot spring (2–20, now 2–30): that study's figures are left out"]

def test_learned_lists_code_studies_and_bakeoffs_without_settings_facts(tmp_path):
    # a code study (template faster) and a bake-off run finished on demo-tour: in `studies` with kind "code" /
    # "bakeoff" and their decision and line; nothing of theirs in `settings`

def test_learned_takes_each_studys_newest_done_run(tmp_path):
    # runs r1 done use, r2 done keep, r3 stopped -> the study's entry is r2's (keep), no winner

def test_learned_empty_home(tmp_path):
    assert memory.learned(Home(tmp_path / "my home"), "demo-tour") == {"studies": [], "settings": {}, "notes": []}
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_memory.py -q`
Expected: FAIL — `ModuleNotFoundError: evolvekit.harness.memory`.

- [ ] **Step 3: Implement `evolvekit/harness/memory.py`** as the interfaces say. The verdict and the status document of a run are read once per `jobs._finished_key` (a dict cache bounded like `jobs._EARLIER_LINES`): the harness page asks on every visit.

- [ ] **Step 4: Run them to see them pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_memory.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add evolvekit/harness/memory.py tests/test_memory.py
git commit -m "memory: what finished studies learned about an application's settings"
```

---

### Task 6: Warm starts and the decaying prior

**Files:**
- Modify: `evolvekit/space.py` (`Parameter.prior`; `prior` a known key; `describe` unchanged), `evolvekit/search/operators.py` (`param_lhs_typed`), `evolvekit/harness/study.py` (`Warm`, the `warm` key), `evolvekit/harness/memory.py` (`warm_start`, `priors_for`), `evolvekit/harness/compile.py` (`compile_study(priors=)`; `write_run` computes them for a warm study in a home), `evolvekit/app/store.py` (`Home.create_study(..., warm=False)`; `save_step` accepts `warm`), `evolvekit/cli.py` (`study new --warm`), `README.md` (the `study new` row)
- Create: `tests/test_prior.py`
- Test: `tests/test_app_store.py` (Task 3's keep-the-keys test gains `warm`)

**Interfaces:**
- Consumes: Task 5's `finished_studies`, `learned`, `current_harness`.
- Produces:
  - `space.PRIOR_BAND = 0.1` (of the unit range, either side); `Parameter.prior: tuple[Any, float] | None = None`, parsed from `prior: {value, weight}` (the value legal for the parameter, `0 < weight <= 1`, else `SpaceError` naming `<path>.prior`); `render_block` and `describe` ignore it, so the evaluation cache key does not change.
  - `param_lhs_typed(space, parent, *, seed, n_variants=6)`: after the hypercube, for each variant in order and each parameter with a prior in declaration order, one `rng.random() < weight` decides whether that value is drawn instead from the band (`from_unit(clamp(to_unit(value) + rng.uniform(-PRIOR_BAND, PRIOR_BAND)))`; a bool or choice takes the value itself); the result's `meta["prior"]` lists the names drawn so; the rest is as today.
  - `memory.MIN_PRIOR_WEIGHT = 0.05`.
  - `@dataclass class Warm: from_slug: str; run: str; at: str; ranked: list[str]` and `Study.warm: Warm | None = None`; YAML `warm: {from, run, at, ranked}` (`ranked` only when re-ranked).
  - `memory.warm_start(home, harness: Harness, study: Study, *, document_of=build_status) -> tuple[Study, list[str]]` — the study with `start` set on each tuned setting to the latest `use` study's winner (only winners inside the study's range for that setting), `warm` recorded, and the lines to say; with a template that tunes `recommended` and at least three finished settings studies, the tuned set is the `len(harness.recommended)` settings with the highest mean importance (unknown importance after, in the harness's rank order).
  - `memory.priors_for(home, study: Study, *, document_of=build_status) -> dict[str, dict]` — `{name: {"value", "weight"}}` for the study's tuned settings with a `use` winner: `weight = 0.5 ** k`, `k = 1 +` the finished studies on the harness after the winner's; dropped below `MIN_PRIOR_WEIGHT` and when the value is outside the study's range.
  - `compile_study(..., priors: dict[str, dict] | None = None)` writes `problem.parameters[name].prior`; `write_run` passes `memory.priors_for(Home-like(root.parent.parent), study)` when `study.warm` is set and `root.parent.name == "studies"`, else none.
  - `study new NAME --harness ID [--template T] --warm` — stderr: `warm start from Depot spring (use): neighbours 12, restart_after 13000` and, re-ranked, `tuned: the 3 settings that went most with the measure in 4 studies: …`; with no `use` study: `nothing to warm-start from: no finished study on demo-tour says use; a plain start`. `--warm --dir` exits 2: `--warm reads the library home's finished studies; leave out --dir`.

- [ ] **Step 1: Write the failing tests** (`tests/test_prior.py`; Task 5's `finished` helper imported from `test_memory`; `warm_study(home, docs) -> Study` makes a `tune` study with `warm` set from the latest `use` study; `seed_candidate(space) -> Candidate` is the starting point, `params = space.defaults()`)

```python
def test_warm_uses_latest_winner(tmp_path, capsys, monkeypatch):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
    finished(home, docs, "Summer", decision="use", best={"neighbours": 16}, finished_at="2026-10-08")
    finished(home, docs, "Autumn", decision="keep", best={"neighbours": 25}, finished_at="2026-10-15")
    monkeypatch.setattr(memory, "build_status", docs.get)   # what the CLI's warm start reads
    assert main(["study", "new", "Winter", "--harness", "demo-tour", "--template", "tune", "--warm", "--home", str(home.root)]) == 0
    study = load_study(home.study_root("winter"))
    assert study.settings["neighbours"].start == 16 and study.warm.from_slug == "summer"
    assert "warm start from Summer (use): neighbours 16" in capsys.readouterr().err

def test_warm_start_values_inside_the_study_range(tmp_path):
    # the winner 16 is outside a narrowed range 2-10 of the new study -> start stays the harness default 8, a line says so;
    # an input settings file giving neighbours 5 does not override the warm start's 16 (start wins over the file)

def test_warm_reranks_recommended_after_three(tmp_path):
    # three settings studies with importance accept_worse 0.7, neighbours 0.5, restart_after 0.1 and init 0.6
    # (init not recommended): tuned == {"accept_worse", "init", "neighbours"}; warm.ranked starts accept_worse, init;
    # with two studies the tuned set is the harness's recommended

def test_prior_weight_decays_by_half(tmp_path):
    home, docs = Home(tmp_path / "my home"), {}
    finished(home, docs, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
    study = warm_study(home, docs)
    assert memory.priors_for(home, study, document_of=docs.get) == {"neighbours": {"value": 12, "weight": 0.5}}
    finished(home, docs, "Summer", decision="keep", best={"neighbours": 20}, finished_at="2026-10-08")
    assert memory.priors_for(home, study, document_of=docs.get)["neighbours"]["weight"] == 0.25
    for day in ("15", "22", "29"):
        finished(home, docs, f"S{day}", decision="keep", best={"neighbours": 20}, finished_at=f"2026-10-{day}")
    assert memory.priors_for(home, study, document_of=docs.get) == {}, "0.5 ** 5 is below 0.05: dropped"

def test_prior_share_of_draws():
    space = ParameterSpace.parse({"n": {"type": "int", "low": 2, "high": 30, "default": 8, "prior": {"value": 12, "weight": 0.3}}})
    drawn = [param_lhs_typed(space, seed_candidate(space), seed=s) for s in range(1000)]
    share = sum("n" in r.meta.get("prior", []) for r in drawn) / 1000
    assert abs(share - 0.3) <= 0.05
    assert all(abs(space.parameters[0].to_unit(r.meta["params"]["n"]) - space.parameters[0].to_unit(12)) <= 0.1 + 1e-9
               for r in drawn if "n" in r.meta.get("prior", []))

def test_prior_never_excludes_range_ends():
    space = ParameterSpace.parse({"x": {"type": "float", "low": 0.0, "high": 1.0, "default": 0.5,
                                        "prior": {"value": 0.5, "weight": 0.5}}})
    values = [param_lhs_typed(space, seed_candidate(space), seed=s).meta["params"]["x"] for s in range(1000)]
    assert any(v < 0.05 for v in values) and any(v > 0.95 for v in values)

def test_prior_draws_are_seeded():   # the same seed gives the same values and the same meta["prior"], twice

def test_prior_is_not_part_of_the_cache_key():
    plain = ParameterSpace.parse({"n": {"type": "int", "low": 2, "high": 30, "default": 8}})
    with_prior = ParameterSpace.parse({"n": {"type": "int", "low": 2, "high": 30, "default": 8, "prior": {"value": 12, "weight": 0.5}}})
    assert plain.render_block({"n": 9}) == with_prior.render_block({"n": 9})

def test_compile_writes_prior_only_for_warm_studies(tmp_path):
    # write_run of a warm study in a home: evolvekit.yaml problem.parameters.neighbours.prior == {"value": 12, "weight": 0.5};
    # the same study without `warm`: no "prior" key anywhere in evolvekit.yaml

def test_warm_needs_the_home(tmp_path, capsys):
    assert main(["study", "new", "W", "--harness", "demo-tour", "--warm", "--dir", str(tmp_path / "w")]) == 2
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_prior.py -q`
Expected: FAIL — `--warm` is not an option; `prior` is an unknown key.

- [ ] **Step 3: Implement** the interfaces above (space, operator, study key, memory functions, compile, store, CLI).

- [ ] **Step 4: Run them to see them pass**, and the neighbours

Run: `.venv/Scripts/python.exe -m pytest tests/test_prior.py tests/test_memory.py tests/test_space.py tests/test_tuning_operators.py tests/test_typed_parameters.py tests/test_study_compile.py tests/test_app_store.py -q -m "not slow"`
Expected: PASS.

- [ ] **Step 5: README** — the `study new` row gains `[--warm]`: "start from what finished studies on this harness learned: the latest `use` winner's values as the starting point, and a prior around them for the search that halves with every study since". One paragraph in *A study from the command line* after **Make the study**: the warm start changes the starting point the final check compares with, so it suits a planner who uses the winner today.

- [ ] **Step 6: Commit**

```bash
git add evolvekit tests README.md
git commit -m "warm starts: study new --warm, and a decaying prior the sampler draws around"
```

---

### Task 7: "What we learned" in the app

**Files:**
- Create: `evolvekit/app/static/js/pages/harness.js`, `tests/test_app_learned.py`
- Modify: `evolvekit/app/api.py` (`harness` adds `learned`), `evolvekit/app/static/js/app.js` (route `#/harness/<id>`), `evolvekit/app/static/app.html` (the script), `pages/home.js` (the Applications drawer and the links row), `docs/app.md` (`## Your library`: *What we learned*)

**Interfaces:**
- Consumes: Task 5's `learned(home, harness_id, document_of=)`; `EK.markOf`, `EK.when`, `EK.mount`.
- Produces:
  - `GET /api/harnesses/{harness}` → `describe_harness(...)` plus `"learned": memory.learned(req.home, id, document_of=req.server.status_document)`.
  - `EK.pages.harness = { render }` at `#/harness/<id>`; its pure part `learnedView(h)` returns the page's body. Words, exactly:
    - title `What we learned · <harness title>`; intro `From <N> finished studies on this application. A winning value comes only from a study whose final check said use.`; empty: `No study on this application has finished yet.`
    - each study: its verdict mark, name (link to its Study page), `finished 14 Nov`, and the verdict's own `line`;
    - the table: columns *Setting*, *Tuned in*, *How strongly it went with the measure* (a bar of `importance`, then `0.42 · from 2 studies`, or `not enough tries to tell`), *Winning values* (`12 in Depot spring (1 Oct)`, or `none yet`), *Winners moved it* (`up`, `down`, `both ways`, `—`);
    - the notes as a list under *Left out*.
  - Home: the Applications drawer gives each harness a link `What we learned`; the links row gains `What we learned` (to the harness of the newest finished study; the drawer when there is none).

- [ ] **Step 1: Write the failing tests** (`tests/test_app_learned.py`)

```python
def test_api_harness_learned(tmp_path):
    server = AppServer(tmp_path / "my home", port=0); server.start()
    try:
        finished(server.home, {}, "Spring", decision="use", best={"neighbours": 12}, finished_at="2026-10-01")
        status, got = call(server, "GET", "/api/harnesses/demo-tour")   # test_app_server.call
    finally:
        server.stop()
    assert got["learned"]["studies"][0]["name"] == "Spring" and set(got["learned"]) == {"studies", "settings", "notes"}

LEARNED = {"title": "Demo tour", "learned": {"studies": [{"slug": "spring", "name": "Spring", "run": "r1",
    "finished_at": "2026-10-01T00:12:00+00:00", "decision": "use", "line": "Probably better: 3.1 % lower tour length …", "kind": "settings"}],
    "settings": {"neighbours": {"label": "Candidate moves per stop", "tuned_in": 2, "importance": 0.42, "importance_n": 2,
        "winners": [{"value": 12, "study": "spring", "finished_at": "2026-10-01T00:12:00+00:00"}], "direction": "up"}},
    "notes": ["old_knob (in Spring) is not a setting of Demo tour 1.1.0 any more: left out"]}}

def test_harness_page_shows_learned():
    text = _render_text(_node(_page_function(HARNESS_PAGE, "learnedView"),
                              f"process.stdout.write(JSON.stringify(String(learnedView({json.dumps(LEARNED)}))));"))
    for words in ("What we learned · Demo tour", "From 1 finished studies on this application.",
                  "Candidate moves per stop", "0.42 · from 2 studies", "12 in Spring (1 Oct)", "up", "Left out",
                  "Probably better: 3.1 % lower tour length"):
        assert words in text, words

def test_harness_page_never_says_better():
    strings = _visible_strings(HARNESS_PAGE)
    assert not [s for s in strings if "better" in s.lower()], "only a verdict's own line says it, from the data"

def test_the_harness_route_and_its_script_ship():   # app.js routes "harness"; app.html loads pages/harness.js (the
    ...                                             # existing test_the_page_loads_only_files_that_exist_and_ship holds)
```

  (`_render_text` strips tags from the rendered HTML string; `HARNESS_PAGE` is the file's text, as `STUDY_PAGE` is in `test_app_page.py`.)

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_learned.py -q`
Expected: FAIL — no `learned` key; no `pages/harness.js`.

- [ ] **Step 3: Implement** the route, the page (with the app's components: `EK.markOf` for the marks, the existing bar style of the Settings view for importance), Home's links.

- [ ] **Step 4: Run them to see them pass**, and the page's word rules

Run: `.venv/Scripts/python.exe -m pytest tests/test_app_learned.py tests/test_app_page.py -q`
Expected: PASS (no banned word reaches the screen; every script parses).

- [ ] **Step 5: docs/app.md** — under `## Your library`, *What we learned*: where it is (Home › Applications, or the link on Home), what the table says, that importance is how strongly a setting's values went with the measure in the searches (not proof of anything), and that a winning value comes only from a study whose final check said use.

- [ ] **Step 6: Commit**

```bash
git add evolvekit tests docs/app.md
git commit -m "app: What we learned, a harness's page of finished studies and their settings"
```

---

### Task 8: Features, tags and `study groups`

**Files:**
- Create: `evolvekit/harness/groups.py`, `tests/test_groups.py`
- Modify: `evolvekit/harness/manifest.py` (`Feature`, the top-level key `features`, its SQL checked at load), `evolvekit/app/store.py` (`case_names` skips `*.tags`; `Home.set_tag`; case rows gain `tag` and `group`; `delete_case` removes the tag file), `evolvekit/app/api.py` (`PUT /api/studies/{slug}/cases/{name}/tag`), `evolvekit/app/static/js/pages/start.js` (`casesDrawer`: a *Kind* column with the tag editor, and the group), `evolvekit/cli.py` (`study groups`), `README.md` (the `study groups` row), `docs/harness-authoring.md` (`## features:` after `## Tables: the vocabulary`)

**Interfaces:**
- Consumes: `execute._solve_once`, `execute.preview_time_limit`, `execute._starting_values`, `compile.base_settings`, `compile.runner_input`, `compile.runner_settings`, `sql.sql_problem`, `sql.schema_database`, `library.slugify`.
- Produces:
  - `@dataclass(frozen=True) class Feature: name: str; sql: str; describe: str = ""` and `Harness.features: dict[str, Feature]`; the SQL must run against the request tables alone (`sql_problem` on `schema_database` of the request tables), else `HarnessError("features.<name>.sql: <why>")`.
  - `groups.GROUPS = "groups.json"`, `TAGS = ".tags"`, `MIN_TRAINING = 2`, `KS = (2, 3)`, `RESTARTS = 20`, `TAG_MAX = 40`.
  - `read_tag(root: Path, case: str) -> str` (BOM, CR, surrounding space stripped; the first line; `""` when none) and `write_tag(root: Path, case: str, tag: str) -> None` (one line, LF; `""` removes the file; more than `TAG_MAX` characters or a line break → `HarnessError`). The file of `cases/a.json` is `cases/a.json.tags`.
  - `case_tables(root: Path, case: str) -> Path` — `solve --tables-out` at the starting values with `preview_time_limit`, through `execute._solve_once`, kept at `<study>/groups/tables/<case file name>.sqlite` with `<name>.json` holding `[size, mtime_ns, application_path]`; solved again only when that changes.
  - `features_of(root: Path, case: str, *, tables: Callable[[Path, str], Path] = case_tables) -> dict[str, float]` — the declared features (each SQL's first value as a float), else `rows_<table>` for every request table and `mean_<table>_<column>` for every `int`/`float` request column that is not `private`.
  - `standardise(points: list[list[float]]) -> list[list[float]]` (z-scores; a column with no spread is dropped).
  - `kmeans(points: list[list[float]], k: int, *, restarts: int = RESTARTS, seed: int = 0) -> list[int]` — k-means++ start, Lloyd to convergence or 100 steps, the restart with the least within-group sum of squares; labels renumbered by first appearance.
  - `silhouette(points, labels) -> float`.
  - `compute(root: Path, *, k: int | None = None, tables=case_tables) -> dict` — writes and returns `groups.json`: `{"format": 1, "method": "tags"|"features"|"one", "k": int, "silhouette": float | None, "groups": {name: {"training": [...], "test": [...]}}, "features": {case: {name: value}}, "describe": {feature: words}, "notes": [str], "cases": [every case]}`. Any tagged case → `tags` (untagged cases form `untagged`); else features with `k` given, or the best silhouette among `KS`, among the `k` whose every group has `MIN_TRAINING` training cases; a method whose groups break `MIN_TRAINING` → `one` (`{"all": …}`) with the note `a group needs at least two cases to learn from; all cases are one group`. Feature groups are named `group 1`, `group 2`, … in the order of their mean of the first feature.
  - `read_groups(root: Path, study: Study) -> dict | None` — `groups.json` when its `cases` equal the study's training and held-back cases, else `None`.
  - `slug_of(name: str) -> str` (a group's folder name: `library.slugify`).
  - `Home.set_tag(slug, name, tag) -> dict` (the study document); the case rows' `tag` (`read_tag`) and `group` (from `read_groups`, else `""`).
  - `study groups SLUG [--k N] [--json] [--home H]` — `--k` outside 2–5 exits 2; prints `2 groups by the cases' features (silhouette 0.62):`, one line per group `  group 1: 4 cases (3 to learn from, 1 held back)`, then `wrote groups.json`; `--json` prints the document.

- [ ] **Step 1: Write the failing tests** (`tests/test_groups.py`; `tables=` given a fake that writes a SQLite file with a `stops` table of `n` rows per case, so no solve runs except in the slow test)

```python
def test_default_features(tmp_path):
    root, _ = study_with_runs(tmp_path)
    got = groups.features_of(root, "cases/town-40.json", tables=fake_tables({"cases/town-40.json": 40}))
    assert got == {"rows_stops": 40.0, "mean_stops_stop_id": 19.5, "mean_stops_weight": 1.0}, "x and y are private"

def test_declared_features_sql(tmp_path):
    # the pinned harness.yaml gains features: {spread: {sql: "SELECT MAX(stop_id) - MIN(stop_id) FROM stops",
    # describe: "how far the stop numbers spread"}} -> {"spread": 39.0}; a feature reading the solution table `tour`
    # fails load_harness with "features.bad.sql: no such table: tour"

def test_kmeans_recovers_two_clusters():
    points = [[0.0, 0.1], [0.2, 0.0], [0.1, 0.2], [5.0, 5.1], [5.2, 4.9], [4.9, 5.0]]
    labels = groups.kmeans(points, 2, seed=0)
    assert labels == [0, 0, 0, 1, 1, 1] and groups.kmeans(points, 2, seed=0) == labels

def test_silhouette_prefers_right_k():
    points = [[0, 0], [0.1, 0], [0, 0.1], [5, 5], [5.1, 5], [5, 5.1], [10, 0], [10.1, 0], [10, 0.1]]
    s2, s3 = (groups.silhouette(points, groups.kmeans(points, k)) for k in (2, 3))
    assert s3 > s2 > 0

def test_tags_win_over_features(tmp_path):
    root = study_with_cases(tmp_path, sizes=[20, 22, 24, 80, 82, 84, 21, 83], held=2)
    for i in (0, 1, 2, 6):
        groups.write_tag(root, f"cases/c{i}.json", "north")      # c6 and c7 are the held-back ones
    for i in (3, 4, 5, 7):
        groups.write_tag(root, f"cases/c{i}.json", "south")
    doc = groups.compute(root, tables=fake_tables(...))
    assert doc["method"] == "tags" and set(doc["groups"]) == {"north", "south"}

def test_tags_file_with_bom_and_crlf(tmp_path):
    root, _ = study_with_runs(tmp_path)
    (root / "cases" / "town-40.json.tags").write_bytes(b"\xef\xbb\xbfbig \r\n")
    assert groups.read_tag(root, "cases/town-40.json") == "big"

def test_tags_file_is_not_a_case(tmp_path):
    root, home = study_with_runs(tmp_path)
    home.set_tag(root.name, "town-40.json", "big")
    assert "town-40.json.tags" not in home.case_names(root.name)
    assert {c["name"]: c["tag"] for c in home.cases(root.name)}["town-40.json"] == "big"

def test_small_group_collapses_to_one(tmp_path):
    root = study_with_cases(tmp_path, sizes=[20, 22, 80, 82], held=1)   # 3 to learn from
    doc = groups.compute(root, tables=fake_tables(...))
    assert doc["method"] == "one" and list(doc["groups"]) == ["all"]
    assert "a group needs at least two cases to learn from; all cases are one group" in doc["notes"]

def test_groups_json_is_stale_when_cases_change(tmp_path):   # read_groups -> None after a case is added

@pytest.mark.slow
def test_cli_groups_json(tmp_path, capsys):
    # demo-tour with the example solver and 8 cases {"seed": n, "cities": 20} x4 and {"seed": n, "cities": 80} x4, 2 held back;
    # main(["study", "groups", slug, "--json", "--home", h]) == 0 -> method "features", k 2, each group 4 cases;
    # groups.json written; a second call solves nothing (the tables are kept: their files' mtimes unchanged)
```

  In `tests/test_app_page.py`: `test_the_cases_drawer_edits_a_tag` — `casesDrawer`'s body has a *Kind* column, `act: "tag"` and `EK.put(EK.studyPath(st.slug, "/cases/" + EK.q(name) + "/tag")`.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_groups.py tests/test_app_page.py -q -m "not slow"`
Expected: FAIL — `evolvekit.harness.groups` does not exist.

- [ ] **Step 3: Implement** the manifest key, `groups.py`, the store's tags, the route, the drawer (a tag is typed in a small text field per row and saved on Enter or leaving the field; the row shows the group of `groups.json` beside it), and the command.

- [ ] **Step 4: Run them to see them pass**, then the slow one alone

Run: `.venv/Scripts/python.exe -m pytest tests/test_groups.py tests/test_app_page.py tests/test_harness_manifest.py tests/test_app_store.py -q -m "not slow"` → PASS; then `.venv/Scripts/python.exe -m pytest tests/test_groups.py::test_cli_groups_json -q` → PASS.

- [ ] **Step 5: Docs** — `docs/harness-authoring.md` `## features:`: the key, one SQL value per case over the request tables, `describe` as the words `rule.md` uses, and the defaults without it. README row: `study groups SLUG [--k N] [--json]` — "groups the cases by their features (or by the tags given in the app's Cases list) and writes `groups.json`; a group needs at least two cases to learn from".

- [ ] **Step 6: Commit**

```bash
git add evolvekit tests README.md docs/harness-authoring.md
git commit -m "groups: features and tags of cases, k-means with the best silhouette, study groups"
```

---

### Task 9: `per_group`

**Files:**
- Modify: `evolvekit/harness/study.py` (`Study.per_group`; problems), `evolvekit/app/store.py` (`save_step` accepts `per_group`), `tests/test_app_store.py` (Task 3's keep-the-keys test gains `per_group`), `evolvekit/harness/compile.py` (`compile_study(up=)`; `write_run(group=)`), `evolvekit/harness/execute.py` (`run_per_group`, `split_budget`, `group_dirs`, `write_group_exports`; `_final_check`/`_extend_check` take `up`), `evolvekit/harness/groups.py` (`rule_stump`, `rule_lines`), `evolvekit/app/jobs.py` (per-group runs: `status`, `_run_line`, `stop`, `can_resume`, `can_continue`, `results(group=)`), `evolvekit/app/verdict.py` (`groups`), `evolvekit/app/report.py` (*Per kind of case*), `evolvekit/app/api.py` (`?group=` on results, tries and status), `pages/study.js` (each group's verdict; the group chips), `plots.js` (`EK.casesView` badges), `README.md`, `docs/app.md`
- Create: `tests/test_per_group.py`

**Interfaces:**
- Consumes: Task 8's `compute`, `read_groups`, `slug_of`, `features`/`describe` of `groups.json`.
- Produces:
  - `Study.per_group: bool = False` (YAML `per_group: true`, written only when true). Problems: with `vary.code` → `per_group: tunes settings per kind of case; a code study changes one source for every case`; with `schedule` → `per_group: a campaign runs one search; leave out schedule, or per_group, for now`; without tuned settings → `per_group: tune at least one setting`.
  - `compile_study(..., up: str = UP)` — every `../..` it writes is `up`; `write_run(study, harness, plan, root, run_id, *, models=None, bakeoff=False, group: str | None = None) -> Path` — with `group`, the folder is `runs/<id>/groups/<slug_of(group)>/`, `up = "../../../.."`, and its `study.yaml` is `save_study(study)` of the study it is given (that group's cases), not a copy of the study folder's.
  - `execute.split_budget(study: Study, groups: dict[str, dict]) -> dict[str, tuple[float, float]]` — `(hours, usd)` per group in proportion to its cases (training and held back), each floored to four decimals, so the sums never exceed `budget.hours` and `budget.ai.max_usd`.
  - `execute.run_per_group(root, run_id, *, models=None, log=print) -> dict` — `run_study` hands over to it when `study.per_group`: `read_groups` or `compute` (logged), one preview's timing, then each group in name order: `plan_for(study_g, harness, measured, hours=h_g)` (`study_g` with `budget.ai.max_usd = usd_g`), `write_run(group=)`, its own `Job` in its folder, the search, `_final_check(..., up="../../../..")`. The run folder's `job.json`: `{"phase", "pid", "started_at", "per_group": {"order": [names], "current": name, "dirs": {name: "groups/<slug>"}}, "plan": {"hours": budget.hours, "groups": {name: plan}}}`, phase `search`/`check` as the current group's, finished `done` when every group is done, `stopped` when one stopped (the next groups do not run), else `failed`.
  - `execute.group_dirs(run_dir: Path) -> dict[str, Path]`.
  - `jobs`: a per-group run's `status` and card read the current group's folder (the line gains `· group 2 of 3 (group 2)`); `stop` writes the stop request into the run folder and every group folder; `can_resume`, `can_continue` and the page's *Check again* are false for it (decision D37); `results(root, run, document_of, group=None)` reads that group's folder (default the first).
  - `verdict.build_verdict` on a per-group run: each group's document is built from its own folder and pinned study by the code that builds a run's today (factored as `_verdict_at(root, run_dir, study, harness, job)`), without `groups`; the run's document: `schema`, `study`, `run`, `phase`, `decision`, `line`, `advice`, `measure`, `held_back` (all cases), `rule`, `groups: {name: document}`, `by_case` (every group's rows, each with `group`), `files`, `started_at`, `finished_at`, `spent` (the sum when there is AI), no figures of its own. Its decision and words, exactly:
    - any group `pending` or `stopped` → that word, line `Per kind of case: the final check of <name> has not finished.`
    - every group with two or more held-back cases says `use` (at least one such group) → `use`, line `Per kind of case: each group checked on two or more held-back cases is probably better.`, advice `Use each group's settings: settings_by_group.json has them, rule.md says which cases get which.`
    - no group has two or more held-back cases → `single`, line `Per kind of case: no group has two or more held-back cases, so none is proven.`
    - else `keep`, line `Per kind of case: not every group is shown to beat today's settings (<name>: <its decision>).`, advice `Keep today's settings, or use only the groups whose own line says use.`
  - `groups.rule_stump(features: dict[str, dict[str, float]], labels: dict[str, str]) -> dict` — over the training cases: for two groups the feature and threshold (midpoint of neighbouring values) with the fewest misplaced cases (ties: the widest gap, then the feature's name); for three, one feature and two thresholds; `{"feature", "thresholds": [float], "sides": [group names, low to high], "misplaced": int}`.
  - `execute.write_group_exports(root, run_dir, document) -> list[Path]` — `downloads/settings_by_group.json` `{"method", "groups": {name: {"cases": [...], "decision": d, "settings": {...}, "from": "found"|"today"}}}` (the best's values only when that group's own decision is `use`, else today's settings, `base_settings`) and `downloads/rule.md`:
    - features, two groups: `If <describe or "the number of rows in stops"> is above <value>, use the settings of <name>; otherwise those of <name>.`
    - three groups: `If … is at most <v1>, use the settings of <a>; if it is above <v2>, those of <c>; otherwise those of <b>.`
    - tags: one line per tag, `For cases tagged <tag>, use the settings of <tag>.`
    - for a group whose decision is not `use`, its part names `today's settings` instead, with `(<name>: not proven, one held-back case)` for `single`, `(<name>: not shown to beat today's)` otherwise; under the rule, `misplaced` training cases are said: `The rule puts 1 of the 8 cases to learn from in another group than its own.`
  - `report.render_report` gains `## Per kind of case`: each group's line, and the rule.
  - The Study page: a per-group run's verdict shows the run's line and advice, then a list *Per kind of case* (name, cases, its verdict mark and line); the Tries, Cases and Settings views get chips to pick a group (`?group=`); `EK.casesView` shows a badge with each case's group (from `by_case[*].group`, else the case rows' `group`).

- [ ] **Step 1: Write the failing tests** (`tests/test_per_group.py`; `per_group_study(tmp_path, sizes=[20, 22, 24, 26, 80, 82, 84, 86], held=4, tags=None) -> Path` makes a demo-tour study with cases `cases/c<i>.json` = `{"seed": i, "cities": size}`, the last `held` held back, `per_group: true`, and — with `tags={"small": 4, "large": 4}` — the first four tagged `small` and the next four `large`; `per_group_run(tmp_path, {group: (decision, held_back_cases)}) -> tuple[Path, Path]` writes a finished per-group run `r1` by hand: tagged cases, `groups.json` by tags, the run folder's `job.json` with `per_group`, and in each group folder its `study.yaml` and a run from `test_app_results.finished_run` with that decision over that many held-back cases)

```python
def test_per_group_compiles_one_run_per_group(tmp_path):
    root = per_group_study(tmp_path, tags={"small": 4, "large": 4})
    doc = groups.compute(root)
    for name, part in doc["groups"].items():
        study_g = replace(load_study(root), training=part["training"], test=part["test"])
        folder = write_run(study_g, load_harness(root / "harness"), Plan.fixed(), root, "r1", group=name)
        assert folder == root / "runs" / "r1" / "groups" / name
        config = yaml.safe_load((folder / CONFIG).read_text(encoding="utf-8"))
        full = config["evaluate"]["stages"][-1]
        assert full["instances"] == [f"../../../../{c}" for c in part["training"]]
        assert "../../../../harness/runner.py" in full["command"]
        assert load_study(folder).training == part["training"]

def test_per_group_budget_split(tmp_path):
    study = replace(load_study(per_group_study(tmp_path, sizes=[20] * 6, held=0)), budget=Budget(hours=1.0, ai_enabled=True, ai_max_usd=1.0))
    split = execute.split_budget(study, {"a": {"training": ["1", "2", "3", "4"], "test": []}, "b": {"training": ["5", "6"], "test": []}})
    assert split == {"a": (0.6666, 0.6666), "b": (0.3333, 0.3333)}
    assert sum(h for h, _ in split.values()) <= 1.0 and sum(u for _, u in split.values()) <= 1.0

def test_per_group_verdict_requires_every_group(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("keep", 2)})   # (decision, held-back cases)
    doc = build_verdict(root, "r1")
    assert doc["decision"] == "keep"
    assert doc["line"] == "Per kind of case: not every group is shown to beat today's settings (large: keep)."
    assert set(doc["groups"]) == {"small", "large"} and "groups" not in doc["groups"]["small"]
    assert "mean_pct" not in doc and {r["group"] for r in doc["by_case"]} == {"small", "large"}
    root, _ = per_group_run(tmp_path / "b", {"small": ("use", 2), "large": ("use", 2)})
    assert build_verdict(root, "r1")["decision"] == "use"

def test_per_group_single_case_group(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"small": ("use", 2), "large": ("single", 1)})
    doc = build_verdict(root, "r1")
    assert doc["decision"] == "use" and doc["groups"]["large"]["decision"] == "single"
    execute.write_group_exports(root, run_dir, doc)
    settings = json.loads((run_dir / "downloads" / "settings_by_group.json").read_text(encoding="utf-8"))
    assert settings["groups"]["large"]["from"] == "today" and settings["groups"]["small"]["from"] == "found"
    rule = (run_dir / "downloads" / "rule.md").read_text(encoding="utf-8")
    assert "For cases tagged large, use today's settings (large: not proven, one held-back case)." in rule

def test_rule_md_claims_nothing_for_groups_not_use(tmp_path):
    root, run_dir = per_group_run(tmp_path, {"small": ("keep", 2), "large": ("worse", 2)})
    execute.write_group_exports(root, run_dir, build_verdict(root, "r1"))
    rule = (run_dir / "downloads" / "rule.md").read_text(encoding="utf-8")
    assert "better" not in rule.lower() and rule.count("today's settings") == 2

def test_rule_stump_picks_best_split():
    features = {f"c{i}": {"rows_stops": float(n), "mean_stops_weight": 1.0} for i, n in enumerate([30, 40, 80, 90])}
    labels = {"c0": "group 1", "c1": "group 1", "c2": "group 2", "c3": "group 2"}
    assert groups.rule_stump(features, labels) == {"feature": "rows_stops", "thresholds": [60.0],
                                                   "sides": ["group 1", "group 2"], "misplaced": 0}

def test_per_group_refused_with_code_or_schedule(tmp_path):   # the three study_problems sentences

def test_stop_reaches_the_current_group(tmp_path):
    # a live per-group run (job.json with this pid, per_group.current "large"): jobs.stop writes STOP_REQUEST into
    # runs/r1 and runs/r1/groups/small and runs/r1/groups/large; can_resume and can_continue are False afterwards

@pytest.mark.slow
def test_per_group_end_to_end(tmp_path, capsys):
    # demo-tour, the example solver, 8 cases (4 x 20 cities, 4 x 80), tags small/large, 2 held back each,
    # budget.hours 0.05: main(["study", "start", slug, "--wait", "--json", "--home", h]) -> the verdict document has
    # groups small and large, each with its own decision; downloads/settings_by_group.json and rule.md exist;
    # `study report --markdown` has "## Per kind of case"
```

  In `tests/test_app_page.py`:

```python
def test_cases_view_badges():
    out = _node("""var t = {by_case: [{name: "a.json", group: "small"}, {name: "b.json", group: "large"}]};
      process.stdout.write(JSON.stringify(String(EK.casesBadges(t))));""")   # the part of casesView that draws them
    assert "small" in out and "large" in out

def test_study_page_lists_each_groups_verdict():
    view = _page_function(STUDY_PAGE, "groupVerdicts")
    assert "Per kind of case" in view and "EK.markOf" in view and ".line" in view
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_per_group.py tests/test_app_page.py -q -m "not slow"`
Expected: FAIL — `per_group` is not a key of a study.

- [ ] **Step 3: Implement**, in this order: the study key and its problems; `compile_study(up=)` and `write_run(group=)`; `split_budget`, `group_dirs`, `run_per_group` and `_final_check(up=)`; `rule_stump`; the verdict's `groups` and its words; `write_group_exports` (called by `run_per_group` when the last group is done, after the verdict can be built); `jobs`' per-group reading, stop and refusals; the report; the API's `?group=`; the page.

- [ ] **Step 4: Run them to see them pass**, the neighbours, then the slow one alone

Run: `.venv/Scripts/python.exe -m pytest tests/test_per_group.py tests/test_app_page.py tests/test_verdict.py tests/test_study_compile.py tests/test_study_run.py tests/test_report.py tests/test_app_results.py -q -m "not slow"` → PASS; then `.venv/Scripts/python.exe -m pytest tests/test_per_group.py::test_per_group_end_to_end -q` → PASS.

- [ ] **Step 5: Docs** — README, after **Run it in windows** (Task 3): **Settings per kind of case** — `per_group: true`, the groups from `study groups` or the tags, one search per group under the study's time and AI cap split by the groups' cases, a final check per group, the verdict's rule (use only when every group checked on two or more held-back cases says use), `settings_by_group.json` and `rule.md`; that a per-group run cannot yet be continued, resumed or scheduled. `docs/app.md` `## The Study page`: the per-group verdict list, the group chips, the badges.

- [ ] **Step 6: Commit**

```bash
git add evolvekit tests README.md docs/app.md
git commit -m "per_group: one search and one final check per kind of case, the verdict's groups, settings_by_group.json and rule.md"
```

---

### Task 10: Docs, the gate, the PR

**Files:**
- Modify: `README.md` (`## Harnesses and studies`: new `### Studies over time` after `### Code studies`, gathering *Campaigns*, *What evolvekit learned* and *Settings per kind of case*), `docs/app.md` (`## Starting it`: *The app as a service* and *Rehearsing a campaign*; `## A study, from start to answer`: *When it runs*), `evolvekit/skills/shared/evolvekit.md` (the new commands and flags with their exit codes), `docs/superpowers/reviews/2026-11-07-agent-path-build-log.md` (project 4's entries)
- Create: `docs/superpowers/reviews/2026-11-08-over-time-gate-log.md`

- [ ] **Step 1: README — `### Studies over time`**, each part a few sentences and one example, moving the paragraphs Tasks 3, 6 and 9 wrote here and linking the rows:
  - *Campaigns* — the `schedule:` YAML; windows in the computer's own time, a window across midnight belongs to the day it starts, daylight-saving changes keep the clock times; `budget.hours` is the campaign's whole search time; at a window's start the run starts, carries on or gets more time, at its end it stops cleanly unless it is in its final check; one study at a time across the library, the oldest campaign first; `study start` waits for the first window, `--now`, `study stop` pauses; the app must run in the windows (`app --service`, `app install-startup`); a missed window is said on Home.
  - *What evolvekit learned* — the harness page; `study new --warm`; the prior halving per study; what is left out after a harness changes.
  - *Settings per kind of case* — features and tags, `study groups`, `per_group`, the verdict and the two files.
- [ ] **Step 2: `docs/app.md`** — *The app as a service* (what `--service` does, where the log is, the logon task and how to remove it, that a second `evolvekit app` opens the first); *Rehearsing a campaign* (`evolvekit app --service --clock 2026-11-13T17:30 --clock-speed 120`: the windows follow the rehearsal clock, the runs keep real time, Home says so, the clock ends with the app); *When it runs* in Set up's *Limits and budget*.
- [ ] **Step 3: The shared reference** — `study start --now`, `study groups`, `study new --warm`, `app --service`, `app install-startup`, `app uninstall-startup` with their exit codes; then `.venv/Scripts/python.exe -m pytest tests/test_skills_content.py tests/test_skills_install.py -q` → PASS (every `evolvekit …` span parses; the reference stays ≤ 200 lines; no banned word).
- [ ] **Step 4: The persona gate (design §8, project 4)**

  Materials, made by the controller in a throwaway folder `C:\gate\p4\` (never in the evolvekit checkout): a library home `C:\gate\p4\home` with demo-tour built in and the example solver as its application; two finished studies on demo-tour made beforehand from the CLI with real short runs (`study new "Depot spring" --template tune`, then `"Depot summer"`, four sample cases each with one held back, `budget.hours` 0.1), whatever their verdicts say; a draft study "Weekend depot" with eight cases (four `{"seed": n, "cities": 20}`, four `{"seed": n, "cities": 80}`, two held back), the `tune` template, `budget.hours` 0.3, everything set but *When it runs*; `C:\gate\p4\rehearse.cmd` — `"<venv>\Scripts\python.exe" -m evolvekit app --service --home "C:\gate\p4\home" --clock 2026-11-13T17:30 --clock-speed 120`; `C:\gate\p4\open.cmd` — `… -m evolvekit app --home "C:\gate\p4\home"`. The reviewer is a fresh agent in Claude Code with its working folder `C:\gate\p4\`, `evolvekit` on PATH, the built-in browser, and only `README.md` and `docs/app.md` of the evolvekit checkout as reading. It never sees the code, the design, the plans or earlier reviews.

  The note, verbatim:

  > You are a transport planner at a delivery company. You are not a programmer. Your colleagues use evolvekit, in the browser, to find solver settings for the depot's routes, and two such studies were done this autumn. It is Friday 17:30 and you are about to leave for the weekend. Your study "Weekend depot" is set up except for when it runs: you want it to use the computer over the weekend only, so it does not slow anyone down during the week, and to have the answer on Monday morning.
  >
  > This is a rehearsal: double-click `C:\gate\p4\rehearse.cmd` first. It starts evolvekit with a rehearsal clock that runs 120 times faster than real time from Friday 17:30, so Monday 08:00 comes about half an hour later. Then double-click `C:\gate\p4\open.cmd` to open the app. You may read evolvekit's README and docs/app.md.
  >
  > 1. Make "Weekend depot" run over the weekend only, and start it. Then close the browser, as you would on Friday evening.
  > 2. When the rehearsal clock says it is Monday after 08:00 (open.cmd shows the app again), find out what the weekend study found and whether you should use it.
  > 3. Find out what all the studies on this solver have learned so far: which settings matter, and which values won.
  >
  > Score the whole experience out of 10 for a planner who wants to set a study going before the weekend and trust what it says on Monday. Name any blocker: something that stopped you or that you would not trust. Say whether anything ran outside the weekend, whether anything claimed "better" before the final check did, and where the numbers came from.

  Pass: 8 or more with no blocker. Each round is logged in `docs/superpowers/reviews/2026-11-08-over-time-gate-log.md` (the round, the score, the blocker, the fixes made, the commits), the transcripts with any token redacted. Fix and repeat until it passes. The controller checks afterwards: `<home>\app.log` shows every start, resume, continuation and stop of the campaign at a rehearsal time inside Saturday 00:00 – Sunday 23:59 (a final check may run on past the end); the study has one run folder whose sessions are its history; `C:\gate\p4\home\clock.json` is gone once the app stopped; `schtasks /Query /TN "evolvekit app"` finds no task (the gate never installs one); the service is stopped at the end.
- [ ] **Step 5: The gate** — the controller runs `python tasks.py check` (never beside a demo study or the persona gate) → exit 0.
- [ ] **Step 6: Commit and the PR**

```bash
git add README.md docs evolvekit/skills
git commit -m "docs: studies over time -- campaigns, the service, what evolvekit learned, settings per kind of case; the gate's log"
git push -u origin agent/4-over-time
gh pr create --base master --head agent/4-over-time --title "Agent path 4: studies over time" --body-file <the PR body: what it adds; no new dependency (schtasks is part of Windows); the gate's score; stacked on agent/3-code>
```

---

## Decisions

Choices made where the design was silent, each with its reason and what it costs if wrong:

- **D1 — Where the schedule is read.** From the study folder's `study.yaml` (so a planner may change the windows between sessions); the budget from the run's pinned copy. Cost: a window changed during a session applies from the next tick.
- **D2 — The tick is level-triggered.** Every tick decides from what is on disk (inside a span or not, the latest run's phase), never from having seen an edge, so the app down at a window's start, a sleep and a restart all become "starts late", and the end is never carried over. Cost: a phase misread on disk is acted on within five seconds.
- **D3 — A campaign is armed.** `campaign.json` is written by `study start` or the app's Start ("submits the first window"); a schedule alone never starts a draft. Cost: one more file in the study folder.
- **D4 — A person's actions win.** A person's stop pauses the campaign; a person's start, resume, continuation or `--now` is a session by hand that no window edge stops. Cost: a planner who stops on Saturday must start again for Sunday.
- **D5 — How a finished plan goes on.** Design §6.1 says `budget.hours` is the campaign's whole search time and also that a finished plan is continued with the window's length. Both hold if a continuation is given the smaller of the window's time left and the campaign's search time left (`plan.max_hours` less what the run's sessions used); under ten minutes it is not started, and a campaign with less than that left is `complete`. Cost if wrong: the owner meant every window to add time without end; a planner then raises `budget.hours`.
- **D6 — The queue.** Runs queued by a person launch first (the existing ticker, unchanged), then campaigns, the oldest `armed_at` first; nothing is pre-empted. Cost: a long run queued by hand can take a campaign's window.
- **D7 — A final check that did not finish** (stopped, or the computer restarted in it) is run again at the campaign's next window (`resume_final_check`); without this the campaign would wait for ever.
- **D8 — Failure bounds.** At most three launches per window and three failed sessions in a row, then the campaign pauses with the error; a run that fails at once does not make a new run folder every five seconds.
- **D9 — Window edges.** A window belongs to the day it starts; windows whose occurrences meet within a minute are one span (the design's own `23:59` / `00:00` example would otherwise stop at midnight); `24:00` is refused with "use 23:59"; YAML's sexagesimal integers are read as clock times.
- **D10 — Time zone.** The computer's own zone through `datetime.astimezone()`, because `zoneinfo` has no data on Windows without the `tzdata` package; functions take an optional `tz` so tests use a fixed-rule zone (and `ZoneInfo("Europe/Amsterdam")` where it loads). Cost: a schedule cannot name another zone.
- **D11 — Day and month words** are a fixed English table, never `strftime("%a")`, whose words follow the Windows locale.
- **D12 — `until`** is inclusive by the window's start date; a window started on that date runs to its end.
- **D13 — One app per home, one tick at a time.** A second `evolvekit app` opens the first, `--service` exits when one runs, and every tick holds `<home>/scheduler.lock` (an exclusive create, taken over when its pid is dead or it is a minute old), because `study wait` in a terminal ticks too. `RunLock` is not used: it reads, then writes, and two processes can both win.
- **D14 — Sleep.** `active_seconds` counts a session from its first event to its last, so a computer asleep in a session spends search time; this project leaves that rule alone (it is the engine's for every run). On waking outside the window the run is stopped at the next tick; one that reached its final check finishes. See open question 1.
- **D15 — The rehearsal clock.** `app --clock ISO --clock-speed N` writes `<home>/clock.json`, which every process of that home honours only while the app that wrote it runs; it moves the windows only (runs keep real time, so a rehearsal spends real search time), Home and `app.log` say it is on, and it ends with the app. This lets a persona live a weekend in half an hour with no code access, and nothing a planner does by accident can turn it on.
- **D16 — The logon task is an XML definition.** `schtasks /Create /SC ONLOGON /TR …` makes a task that Windows ends after 72 hours, that does not start on battery, whose command is limited to 261 characters and whose quoting breaks on paths with spaces; `/XML` with `ExecutionTimeLimit PT0S` and the battery conditions off has none of that. The task runs `pythonw.exe` (no console window); runs are always launched with `python.exe` (`detach.python_for_runs`).
- **D17 — `study open` starts the service**, with the launcher's output in `app-start.log`, so a library has one app log and rotation never meets a handle another process holds.
- **D18 — The service's log** is the standard `RotatingFileHandler` (5 MB, two kept), with stdout and stderr sent to it, so the ticker's tracebacks are kept.
- **D19 — "When it runs" in Set up.** The design gives no editor for `schedule`, but its gate's persona is a planner who cannot edit YAML; Set up offers the three presets and an *Until* date, and anything else is written in `study.yaml`. Cost: a small new part of the page.
- **D20 — Home between windows.** The card's kind is `campaign` ("Between windows"), not `stopped`, and Home polls every 30 s while a campaign is armed, so the next window's words never go stale.
- **D21 — Time left of a resumed session** counts the search time left across sessions, not from the run's first start (a fault for every resume today that campaigns make the rule), and a campaign session's is at most its window.
- **D22 — `study wait` on a campaign** waits for the campaign's answer, not for the first window's stop; exit 1 when it pauses or ends without a finished run.
- **D23 — A finished study (memory)** is each study's newest `done` run on that harness, archived studies included, the trash not. Code studies, bake-offs and per-group studies are listed with their verdicts, but settings facts come only from settings-tuning studies: a code study's winner is a diff, a bake-off has no search, and a per-group winner is not one value per setting.
- **D24 — Importance** of a study counts when its correlation rests on at least five fully evaluated combinations; the mean is over those.
- **D25 — A changed harness.** Settings are compared with the library's newest harness of that id; a declaration that differs (type, range, scale, choices) leaves that study's figures for that setting out, with a note. A study's own narrower range is not a change.
- **D26 — Direction** compares each winner with its own study's starting value; numbers only.
- **D27 — `warm` is a key of its own.** The design says the study's `step` records the warm start, but `step` is the Set up step number every page reads; `warm: {from, run, at, ranked}` records it instead.
- **D28 — A warm start moves the starting point** (design §6.3: "the starting values are the latest use winner's"), so the new study's final check compares with the winner; `study new --warm` and the README say so. Cost: a planner who never adopted the winner compares with settings nobody uses.
- **D29 — Re-ranking** applies to templates that tune `recommended`: with three or more finished settings studies the tuned set is the same number of settings, those that went most with the measure; recorded in `warm.ranked`.
- **D30 — The prior.** Only warm-started studies get one, computed when the run is written. `k` counts the new study as the first since the winner, so the weight starts at one half: with weight 1 every `param_lhs` draw would be near the value, which excludes the rest of the range, which the design forbids. Weights under 0.05 and values outside the study's range are dropped; the band is ±10 % of the range on the setting's own scale; a bool or a choice is drawn as the value itself; only `param_lhs` reads it.
- **D31 — The prior is not part of the cache key** (`render_block` ignores it), so cached evaluations stay valid.
- **D32 — The harness page is new** (`#/harness/<id>`): the app had none. It is reached from Home's Applications drawer and links row.
- **D33 — Features through `solve --tables-out`.** The SDK has no `tables` command and §10 forbids a new one; a solve at the starting values with the preview's time limit writes the request tables, kept per case file. Cost: one short solve per case the first time (seconds for demo-tour, up to ten seconds a case for PyVRP).
- **D34 — Default features** are each request table's row count and the mean of each numeric request column that is not `private`; columns with no spread are dropped before standardising.
- **D35 — Tags.** `cases/<file>.tags`, one line, stripped of BOM, CR and space, at most 40 characters; as soon as one case has a tag, tags decide and untagged cases form `untagged`; `case_names` skips `.tags` files, which it would otherwise list as cases.
- **D36 — k-means.** k-means++ starts, Lloyd's steps, 20 seeded restarts, the best silhouette among k = 2 and 3 whose groups each have two cases to learn from; `--k` (2–5) forces k; groups named `group 1`… by their mean of the first feature.
- **D37 — `per_group` alone, for now.** Refused with code, a bake-off or a schedule; a per-group run cannot be resumed, continued or checked again; each would need its own rules per group. Cost: a stopped per-group run starts over.
- **D38 — Per-group folders** are complete run folders under `runs/<id>/groups/<slug>/` (paths compiled four levels up), with the run folder's `job.json` naming the order and the current group; a stop is written into every group folder, which is where each search watches for it.
- **D39 — The budget split** is by each group's cases (training and held back), for the hours and for the AI cap, floored so the sums never exceed the study's.
- **D40 — Groups at run start** are `groups.json` when it matches the study's cases, else computed again and logged.
- **D41 — The per-group verdict** has no figures of its own (no group speaks for the run); each group's document carries its own; `by_case` rows say their group.
- **D42 — The rule stump**: one threshold for two groups, one feature with two thresholds for three, one line per tag with tags; a group whose own verdict is not `use` gets today's settings in both files; misplaced training cases are counted under the rule.
- **D43 — A per-group run's views** pick a group (`?group=`, the first by default); the dashboard link stays the run folder's (empty for a per-group run).
- **D44 — Review focus** keeps the task-level plan's five lines and adds six from project 3's gate, each with its test.
- **D45 — The gate never installs the logon task** (a persistent change to the owner's account); Task 4's tests patch `schtasks`.

## Open questions for the controller

1. **Keep the computer awake during a window?** A laptop that sleeps inside a window spends the campaign's search time asleep (D14). The service could ask Windows not to sleep while a campaign's run is in its window (`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` through `ctypes`, released when the run ends; nothing persistent). Add it to Task 4, or leave the README's advice to keep the computer awake?
2. **A real `schtasks` round trip.** Unit tests patch `subprocess`; whether a standard (non-administrator) account may create a logon task from XML is unverified on this machine. May the controller run `evolvekit app install-startup` and `uninstall-startup` once on the owner's account (it adds a task and removes it), or should that wait for the owner?
