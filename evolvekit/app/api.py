"""The app's JSON API: one handler per endpoint (design §7.1).

Every handler takes a `server.Request` and returns something JSON can carry,
or a `server.FileResponse`; a refusal is an `AppError` with a sentence. The
page uses nothing but this, so a server could take the app's place later.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from evolvekit import resources
from evolvekit.app import AppError, assistant, compare, jobs, scheduler, tries, work
from evolvekit.app.detach import no_window_flags
from evolvekit.app.detect import detect as detect_applications
from evolvekit.app.server import FileResponse, Request, route
from evolvekit.harness import HarnessError, memory
from evolvekit.harness.execute import plan_for, read_preview
from evolvekit.harness.manifest import Harness
from evolvekit.harness.plan import plan_notes
from evolvekit.harness.schedule import PRESETS, when_words
from evolvekit.harness.study import SettingChoice, save_study

__all__ = ["ROUTES", "describe_harness"]

PICK_TIMEOUT_S = 600
_PICK = r"""
import json, sys
try:
    import tkinter
    from tkinter import filedialog
except Exception as exc:
    print(json.dumps({"error": "no file dialog here (%s)" % exc})); sys.exit(0)
root = tkinter.Tk(); root.withdraw()
try:
    root.attributes("-topmost", True)
except Exception:
    pass
title, kind = sys.argv[1], sys.argv[2]
path = filedialog.askdirectory(title=title) if kind == "folder" else filedialog.askopenfilename(title=title)
print(json.dumps({"path": path or ""}))
"""


# ---------------------------------------------------------------------------
# describing a harness
# ---------------------------------------------------------------------------


def describe_harness(harness: Harness) -> dict[str, Any]:
    """Everything the page shows about a harness, in plain JSON."""
    readme = harness.root / "README.md"
    return {
        "id": harness.id, "version": harness.version, "key": harness.key, "title": harness.title,
        "summary": harness.summary,
        "application": {"kind": harness.application.kind, "label": harness.application.label,
                        "help": harness.application.help, "module": harness.application.module,
                        "version": harness.application.version},
        "cases": {"label": harness.cases.label, "formats": list(harness.cases.formats), "describe": harness.cases.describe},
        "inputs": {name: {"label": spec.label, "formats": list(spec.formats), "describe": spec.describe,
                          "required": spec.required, "provides": spec.provides}
                   for name, spec in harness.inputs.items()},
        "time_limit": {"accepts": harness.time_limit.accepts, "default_s": harness.time_limit.default_s,
                       "min_s": harness.time_limit.min_s},
        "seeds": harness.seeds,
        "settings": [
            {"name": s.name, "type": s.parameter.type, "low": s.parameter.low, "high": s.parameter.high,
             "log": s.parameter.log, "default": s.parameter.default, "choices": list(s.parameter.choices),
             "label": s.label, "help": s.help, "explain": s.explain, "group": s.group, "recommended": s.recommended}
            for s in harness.settings.values()
        ],
        "tables": {name: {"source": t.source, "describe": t.describe,
                          "columns": {c: {"type": col.type, "unit": col.unit, "describe": col.describe,
                                          "categorical": col.categorical} for c, col in t.columns.items()}}
                   for name, t in harness.tables.items()},
        "levers": {name: {"label": lv.label, "table": lv.table, "columns": dict(lv.columns), "modes": list(lv.modes),
                          "help": lv.help, "explain": lv.explain, "code": lv.code}
                   for name, lv in harness.levers.items()},
        "kpis": {name: {"label": k.label, "direction": k.direction, "unit": k.unit, "help": k.help, "sql": k.sql,
                        "measure": k.measure, "positive": k.positive, "changes_with_levers": k.changes_with_levers,
                        "guard": k.guard}
                 for name, k in harness.kpis.items()},
        "kpi_templates": {name: {"label": t.label, "direction": t.direction, "unit": t.unit, "help": t.help, "sql": t.sql,
                                 "params": {p.name: {"type": p.type, "label": p.label, "choices": list(p.choices),
                                                     "from": p.source} for p in t.params.values()}}
                          for name, t in harness.kpi_templates.items()},
        "templates": {tid: {"title": str(t.get("title") or tid), "summary": str(t.get("summary") or "").strip()}
                      for tid, t in harness.templates.items()},
        "exports": {name: {"label": e.label, "for": e.applies_to} for name, e in harness.exports.items()},
        # What a code study may change; `repo` as written (Set up proposes it resolved: `code_proposal`).
        "code": None if harness.code is None else {
            "label": harness.code.label, "repo": harness.code.repo, "open": list(harness.code.open),
            "build": harness.code.build, "app": harness.code.app},
        "defaults": {"time_per_case_s": harness.defaults.time_per_case_s, "retries": harness.defaults.retries,
                     "budget_hours": harness.defaults.budget_hours, "test_share": harness.defaults.test_share,
                     "runs_per_case": harness.defaults.runs_per_case},
        "readme": readme.read_text(encoding="utf-8") if readme.is_file() else "",
    }


# ---------------------------------------------------------------------------
# the home, settings, harnesses
# ---------------------------------------------------------------------------


def home(req: Request) -> dict[str, Any]:
    studies = []
    for slug in req.home.slugs():
        try:
            root, study, pinned = req.home.load(slug)
        except AppError as exc:
            studies.append({"slug": slug, "name": slug, "harness": "", "kind": "broken", "line": str(exc)})
            continue
        state = jobs.state_line(root, study, req.server.status_document)
        if state.get("kind") == "draft":
            # What a draft still lacks, so its card names it (persona p4, H6): the first problem's
            # step, key and text (`Home.problems`), None when Set up has nothing missing.
            problems = req.home.problems(slug)
            state["missing"] = ({"step": problems[0]["step"], "key": problems[0]["key"], "text": problems[0]["text"]}
                                if problems else None)
        template = (pinned.templates.get(study.template) or {}).get("title") if study.template else None
        studies.append({"slug": slug, "name": study.name, "harness_id": pinned.id, "harness": pinned.title + (f" · {template}" if template else ""),
                        **state, "updated_at": (root / "study.yaml").stat().st_mtime,
                        "archived": req.home.is_archived(slug)})
    studies.sort(key=lambda s: -float(s.get("updated_at") or 0))
    harnesses = []
    for entry in req.home.harness_entries():
        if entry.harness is None:
            harnesses.append({"id": entry.path.name, "problem": entry.problem, "built_in": entry.built_in})
            continue
        h = entry.harness
        harnesses.append({"id": h.id, "version": h.version, "title": h.title, "summary": h.summary,
                          "built_in": entry.built_in, "templates": _templates(h),
                          "application": {"kind": h.application.kind, "label": h.application.label}})
    settings = req.home.settings()
    out = {"home": str(req.home.root), "studies": studies, "harnesses": harnesses, "keys": req.home.keys(),
           "provider": settings["provider"], "trash": req.home.trash()}
    clock = scheduler.clock_of(req.home)
    if clock.simulated:  # a rehearsal (`app --clock`): windows follow it, runs keep real time
        out["clock"] = {"line": clock.words()}
    return out


def shutdown(req: Request) -> dict[str, bool]:
    """`evolvekit app stop`: the app ends cleanly (its clock and `app.json` removed), after this answer."""
    req.server.request_shutdown()
    return {"stopping": True}


def _templates(harness: Harness) -> list[dict[str, Any]]:
    """The study templates, settings studies first: they are the simpler start."""
    listed = []
    for tid, template in harness.templates.items():
        vary = template.get("vary") or {}
        kind = "code" if vary.get("code") else "data" if vary.get("data") else "settings"
        listed.append({"id": tid, "title": str(template.get("title") or tid),
                       "summary": " ".join(str(template.get("summary") or "").split()), "kind": kind})
    return sorted(listed, key=lambda t: (t["kind"] != "settings", t["title"]))


def get_settings(req: Request) -> dict[str, Any]:
    """The settings, which keys are set (never their values), the home, and `frozen`: the app is the
    installed one (`evolvekit-app.exe`), not a pip install (`evolvekit app`)."""
    return {**req.home.settings(), "keys": req.home.keys(), "home": str(req.home.root), "frozen": resources.is_frozen()}


def check_assistant(req: Request) -> dict[str, Any]:
    return assistant.check_model(req.home)


def put_settings(req: Request) -> dict[str, Any]:
    req.home.save_settings(req.json())
    return get_settings(req)


def install(req: Request) -> dict[str, Any]:
    """A harness from the .zip in the body -- or, as JSON `{path}`, from a
    .zip on this computer."""
    had = {(e.harness.id, e.harness.version): e.built_in for e in req.home.harness_entries() if e.harness is not None}
    if (req.headers.get("Content-Type") or "").startswith("application/json"):
        raw = str(req.json().get("path") or "").strip().strip('"')
        path = Path(raw).expanduser()
        if not raw or not path.is_absolute() or not path.is_file():
            raise AppError("give the full path of the harness's .zip file, such as C:\\Users\\you\\Downloads\\pyvrp-1.0.0.zip")
        harness = req.home.install(path.read_bytes(), path.name)
    else:
        harness = req.home.install(req.body, req.query.get("name") or "harness.zip")
    before = had.get((harness.id, harness.version))
    return {**describe_harness(harness), "already": None if before is None else "built in" if before else "installed"}


def harness(req: Request) -> dict[str, Any]:
    entry = req.home.harness_entry(req.params["harness"], req.query.get("version") or None)
    described = describe_harness(entry.harness)  # type: ignore[arg-type]
    described["learned"] = memory.learned(req.home, described["id"], document_of=req.server.status_document)
    return described


# ---------------------------------------------------------------------------
# a study
# ---------------------------------------------------------------------------


def _document(req: Request, slug: str) -> dict[str, Any]:
    root, study, pinned = req.home.load(slug)
    document = req.home.document(slug)
    # What each data change starts from, on the preview case: rules and
    # people think in rates, the search in factors.
    document["data_today"] = {name: jobs._span_text(values) for name, change in study.data.items()  # noqa: SLF001
                              if (values := jobs._today(root, pinned, change))}  # noqa: SLF001
    document["preview"] = work.preview_state(root, req.server)
    document["test_run"] = work.test_run_state(root, req.server)
    document["runs"] = jobs.runs(root)
    document["state"] = jobs.state_line(root, study, req.server.status_document)
    # The campaign's words (None without a schedule) and the windows Set up offers (D19).
    document["campaign"] = document["state"].get("campaign")
    document["schedule_presets"] = PRESETS
    if study.schedule is not None:
        # The hours one week of its windows gives (real hours, so a rehearsal's are fewer), and the
        # campaign's search time left on its run (None before its first run): persona p4, H1.
        clock = scheduler.clock_of(req.home)
        document["schedule_hours"] = scheduler.schedule_hours(study.schedule, now=clock.now(), speed=clock.speed)
        run = scheduler.campaign_run(root)
        document["campaign_left_s"] = (scheduler.campaign_left_s(root / "runs" / run) if run is not None else None)
        # What the scheduled Continue says on its button ("… in the next window (Sat 00:00)"), even
        # when the campaign is finished and says no next window; and whether a renewed campaign waits
        # to start a fresh run after the latest one, when Continue is not offered (persona p4, H5).
        now = clock.now()
        found = study.schedule.next_edge(now)
        document["next_window"] = ({"at": found[0].isoformat(), "edge": found[1], "words": when_words(found[0], now)}
                                   if found is not None else None)
        stored = scheduler.read_campaign(root) or {}
        latest = document["runs"][-1]["id"] if document["runs"] else None
        document["new_run_waits"] = bool(stored.get("state") == "armed" and latest is not None
                                         and stored.get("after") == latest)
    clock = scheduler.clock_of(req.home)
    if clock.simulated:  # a rehearsal: Set up says a window's hours on its clock and in real time
        document["clock"] = {"line": clock.words(), "speed": clock.speed}
    document["notes"] = plan_notes(len(study.test))  # too few held-back cases for a firm answer, before any run
    return document


def create_study(req: Request) -> dict[str, Any]:
    """A new study from JSON `{harness, template, name}` -- or, with a zip in
    the body (`Content-Type: application/zip`, or any body with `?name=`),
    the study that zip holds (`Home.export_study` wrote it)."""
    kind = req.headers.get("Content-Type") or ""
    zipped = req.body and not kind.startswith("application/json") and (kind.startswith("application/zip") or bool(req.query.get("name")))
    if zipped:
        slug = req.home.import_study(req.body, req.query.get("name") or "study.zip")
    else:
        body = req.json()
        slug = req.home.create_study(str(body.get("harness") or ""), body.get("template") or None, str(body.get("name") or ""))
    return _document(req, slug)


def delete_study(req: Request) -> dict[str, Any]:
    """To the trash, where it waits for a restore: `{"trashed": entry}`."""
    slug = req.params["slug"]
    if jobs.running(req.home.study_root(slug)):
        raise AppError("the study is running; stop it before deleting it", 409)
    return {"trashed": req.home.delete_study(slug)}


def restore_study(req: Request) -> dict[str, Any]:
    req.home.restore_study(req.params["entry"])
    return home(req)


def duplicate_study(req: Request) -> dict[str, Any]:
    slug = req.home.duplicate_study(req.params["slug"], str(req.json().get("name") or ""))
    return _document(req, slug)


def study(req: Request) -> dict[str, Any]:
    return _document(req, req.params["slug"])


def study_harness(req: Request) -> dict[str, Any]:
    root, _, pinned = req.home.load(req.params["slug"])
    return describe_harness(pinned)


def save_step(req: Request) -> dict[str, Any]:
    """Parts of the study to merge in -- and `archived: true|false`, which is
    a marker on the folder, not a part of the study."""
    slug = req.params["slug"]
    root = req.home.study_root(slug)
    if jobs.running(root):
        raise AppError("the study is running; stop it before changing it", 409)
    changes = dict(req.json())
    archived = changes.pop("archived", None)
    if archived is not None and not isinstance(archived, bool):
        raise AppError("archived: true or false")
    if changes:
        req.home.save_step(slug, changes)
    if archived is not None:
        req.home.set_archived(slug, archived)
    return _document(req, slug)


def detect(req: Request) -> list[dict[str, Any]]:
    if req.query.get("study"):
        _, _, pinned = req.home.load(req.query["study"])
    else:
        entry = req.home.harness_entry(req.query.get("harness") or "")
        pinned = entry.harness  # type: ignore[assignment]
    recent = req.home.settings()["recent_applications"]
    return detect_applications(pinned, recent=recent)


def application(req: Request) -> dict[str, Any]:
    slug = req.params["slug"]
    answer = req.home.set_application(slug, str(req.json().get("path") or ""))
    return {"probe": answer, "study": _document(req, slug)}


def code(req: Request) -> dict[str, Any]:
    """Where a code study's source is: JSON `{repo, from, brief}`, each optional
    (`repo` empty: the harness's own). The answer is `Home.set_code`'s, with the study."""
    slug = req.params["slug"]
    if jobs.running(req.home.study_root(slug)):
        raise AppError("the study is running; stop it before changing it", 409)
    body = req.json()
    brief = body.get("brief")
    if brief is not None and not isinstance(brief, str):
        raise AppError("brief: a sentence")
    answer = req.home.set_code(slug, str(body.get("repo") or ""), str(body.get("from") or "") or None, brief)
    return {"code": answer, "study": _document(req, slug)}


def pick_file(req: Request) -> dict[str, Any]:
    body = req.json()
    kind = "folder" if body.get("kind") == "folder" else "file"
    title = str(body.get("title") or ("Choose a folder" if kind == "folder" else "Choose a file"))[:120]
    try:
        done = subprocess.run([*resources.python_code_argv(_PICK), title, kind], capture_output=True, text=True,
                              timeout=PICK_TIMEOUT_S, encoding="utf-8", errors="replace",
                              creationflags=no_window_flags())
    except subprocess.TimeoutExpired:
        return {"cancelled": True}
    try:
        answer = json.loads(done.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        raise AppError("the file dialog could not be opened; paste the path instead", 501) from None
    if answer.get("error"):
        raise AppError(f"{answer['error']}; paste the path instead", 501)
    return {"path": answer["path"]} if answer.get("path") else {"cancelled": True}


def put_case(req: Request) -> dict[str, Any]:
    slug = req.params["slug"]
    if jobs.running(req.home.study_root(slug)):
        raise AppError("the study is running; its cases cannot change now", 409)
    return req.home.put_case(slug, req.params["name"], req.body)


def delete_case(req: Request) -> dict[str, Any]:
    slug = req.params["slug"]
    if jobs.running(req.home.study_root(slug)):
        raise AppError("the study is running; its cases cannot change now", 409)
    req.home.delete_case(slug, req.params["name"])
    return _document(req, slug)


def restore_case(req: Request) -> dict[str, Any]:
    slug = req.params["slug"]
    if jobs.running(req.home.study_root(slug)):
        raise AppError("the study is running; its cases cannot change now", 409)
    req.home.restore_case(slug, req.params["name"])
    return _document(req, slug)


def put_tag(req: Request) -> dict[str, Any]:
    """JSON `{tag}`: what kind of case it is (`""` takes the tag away); tags
    decide the groups of `study groups` as soon as one case has one."""
    slug = req.params["slug"]
    req.home.set_tag(slug, req.params["name"], req.json().get("tag", ""))
    return _document(req, slug)


def import_cases(req: Request) -> dict[str, Any]:
    slug = req.params["slug"]
    if jobs.running(req.home.study_root(slug)):
        raise AppError("the study is running; its cases cannot change now", 409)
    imported = req.home.import_folder(slug, str(req.json().get("folder") or ""))
    return {"imported": imported, "study": _document(req, slug)}


def use_samples(req: Request) -> dict[str, Any]:
    slug = req.params["slug"]
    req.home.use_samples(slug)
    return _document(req, slug)


def inspect_cases(req: Request) -> dict[str, Any]:
    slug = req.params["slug"]
    for name in req.home.case_names(slug):
        req.home.inspect_case(slug, name)
    return _document(req, slug)


def split(req: Request) -> dict[str, Any]:
    slug = req.params["slug"]
    test = req.json().get("test")
    if not isinstance(test, int) or isinstance(test, bool):
        raise AppError("test: how many cases to hold back, a whole number")
    req.home.split(slug, test)
    return _document(req, slug)


def put_input(req: Request) -> dict[str, Any]:
    slug = req.params["slug"]
    req.home.put_input(slug, req.params["input"], req.params["name"], req.body)
    return _document(req, slug)


def remove_input(req: Request) -> dict[str, Any]:
    slug = req.params["slug"]
    req.home.remove_input(slug, req.params["input"])
    return _document(req, slug)


def plan(req: Request) -> dict[str, Any]:
    root, study, pinned = req.home.load(req.params["slug"])
    measured = read_preview(root)
    estimated = not (measured and measured.get("ok"))
    timing = measured if not estimated else {"overhead_s": 0.0, "wall_s": study.limits.time_per_case_s}
    models = jobs.ai_models(req.home) if study.budget.ai_enabled else None  # what `study start` compiles with
    try:
        chosen = plan_for(study, pinned, timing, models=models)  # type: ignore[arg-type]
    except (HarnessError, ValueError, ZeroDivisionError) as exc:
        raise AppError(f"no plan yet: {exc}") from None
    tunables = len(study.tunables())
    warning, fixes = "", []
    if tunables and chosen.combinations < THIN * tunables and not chosen.blocked:
        warning = (f"About {chosen.combinations} combinations for {tunables} things to tune is thin: the search may find "
                   "a better combination, but it will likely miss the best one.")
        tuned = study.tuned_settings()
        if not study.data and len(tuned) > 2:
            ranked = [n for n in pinned.recommended if n in tuned] + [n for n in tuned if n not in pinned.recommended]
            keep = max(2, min(len(tuned) - 1, chosen.combinations // GOOD))
            fixes.append({
                "kind": "fewer", "keep": ranked[:keep], "label": f"Tune only the {keep} that matter most",
                "detail": (", ".join(pinned.settings[n].label for n in ranked[:keep] if n in pinned.settings)
                           + f": about {chosen.combinations // keep} tries each. The others keep their starting values."),
            })
        hours = _hours_for(study, pinned, timing, GOOD * tunables, models)
        if hours is not None and hours > study.budget.hours:
            fixes.append({"kind": "time", "hours": hours, "label": f"Allow {_hours_words(hours)} in total",
                          "detail": f"About {GOOD * tunables} combinations for all {tunables}."})
    window = whole = None
    if study.schedule is not None and not chosen.blocked:
        # A scheduled study ends on its windows, not "done around 17:34" from now (persona p4, H1).
        clock = scheduler.clock_of(req.home)
        now = clock.now()
        # A campaign with a finished run goes on with what is left of its search time (m9).
        hours = scheduler.campaign_plan_hours(root, study, chosen.hours, chosen.final_check_s, now=now)
        window = scheduler.plan_window(study.schedule, hours, now=now, speed=clock.speed)
        if hours == chosen.hours:  # the line spreads the study's own hours: the offer's done time is the line's
            whole = _whole_window(study, pinned, timing, models, now=now, speed=clock.speed)
    return {"summary": chosen.summary(when=window["line"] if window else None), "estimated": estimated,
            "warning": warning, "fixes": fixes, "tunables": tunables, **chosen.to_json(),
            **({"window": window} if window else {}), **({"whole_window": whole} if whole else {})}


def _whole_window(study: Any, harness: Any, timing: dict[str, Any], models: dict[str, Any] | None, *,
                  now: Any, speed: float) -> dict[str, Any] | None:
    """Set up's "Search through the whole window" (persona p4 round 2, I1): the hours one week of the
    windows gives less the final check the plan makes for them, so both end inside the windows."""
    import dataclasses

    def check_s(hours: float) -> float:
        trial = dataclasses.replace(study, budget=dataclasses.replace(study.budget, hours=hours))
        return plan_for(trial, harness, timing, models=models).final_check_s

    try:
        return scheduler.whole_window(study.schedule, study.budget.hours, check_s, now=now, speed=speed)
    except (HarnessError, ValueError, ZeroDivisionError):
        return None


THIN, GOOD = 6, 10
"""Combinations per thing tuned: below THIN the plan is thin; GOOD is what a fix aims for."""


def _hours_for(study: Any, harness: Any, timing: dict[str, Any], combinations: int,
               models: dict[str, Any] | None = None) -> float | None:
    """The least total time, in quarter hours up to two days, whose plan tries `combinations`."""
    import dataclasses

    for quarters in range(1, 193):
        hours = quarters / 4
        trial = dataclasses.replace(study, budget=dataclasses.replace(study.budget, hours=hours))
        try:
            if plan_for(trial, harness, timing, models=models).combinations >= combinations:
                return hours
        except (HarnessError, ValueError, ZeroDivisionError):
            return None
    return None


def _hours_words(hours: float) -> str:
    whole, minutes = divmod(round(hours * 60), 60)
    if not whole:
        return f"{minutes} minutes"
    return f"{whole} h {minutes} min" if minutes else f"{whole} hour{'s' if whole != 1 else ''}"


def tune_only(req: Request) -> dict[str, Any]:
    """Tune only `keep` of the settings the study tunes; the others keep their starting values."""
    slug = req.params["slug"]
    root, study, _ = req.home.load(slug)
    if jobs.running(root):
        raise AppError("the study is running; stop it before changing it", 409)
    keep = [str(name) for name in (req.json().get("keep") or [])]
    tuned = study.tuned_settings()
    if not keep or any(name not in tuned for name in keep):
        raise AppError("keep: name some of the settings the study tunes")
    for name in tuned:
        if name not in keep:
            study.settings[name] = SettingChoice("default")
    save_study(study, root)
    return _document(req, slug)


# ---------------------------------------------------------------------------
# preview, KPIs, the assistant, the test run
# ---------------------------------------------------------------------------


def start_preview(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    return work.start_preview(req.server, root)


def preview(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    return work.preview_state(root, req.server)


def try_kpi(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    body = req.json()
    return work.try_sql(root, str(body.get("sql") or ""), body.get("params") or {}, str(body.get("rows_sql") or ""))


def choices(req: Request) -> dict[str, Any]:
    root, _, pinned = req.home.load(req.params["slug"])
    return work.template_choices(root, pinned)


def ask_assistant(req: Request) -> dict[str, Any]:
    """A question from `screen` (`step-1`…`step-7`, `running`, `results`),
    with `run` in view where the screen shows one. Answers at once with
    `{turn, state: "thinking"}`; the answer comes from `GET …/assistant?turn=`."""
    root, _, _ = req.home.load(req.params["slug"])
    body = req.json()
    run = body.get("run")
    return work.start_ask(req.server, req.home, root, str(body.get("message") or ""), provider=req.server.assistant_provider,
                          screen=str(body.get("screen") or ""), run=str(run) if run else None,
                          document_of=req.server.status_document)


def cancel_assistant(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    return work.cancel_ask(req.server, root)


def card_event(req: Request) -> dict[str, Any]:
    """What became of one of the assistant's cards: `{turn, index, state}`,
    state `applied` or `dismissed`."""
    root, _, _ = req.home.load(req.params["slug"])
    body = req.json()
    return assistant.record_card(root, str(body.get("turn") or ""), body.get("index"), body.get("state"))


def add_card(req: Request) -> dict[str, Any]:
    """Add cards to the study -- the assistant's, or ones the page's forms
    made -- after checking them again on the preview. `{card}` adds one and
    refuses (400) when it fails; `{cards: [...]}` adds a whole answer in the
    right order, answers `{cards, study}` with each card's `ok` and `problem`,
    and with `turn` given records each applied card's fate by its `index` in
    that answer."""
    slug = req.params["slug"]
    root, study, pinned = req.home.load(slug)
    if jobs.running(root):
        raise AppError("the study is running; stop it before changing it", 409)
    body = req.json()
    if "cards" in body:
        cards = body.get("cards")
        if not isinstance(cards, list):
            raise AppError("cards: a list of cards")
        done = assistant.apply_cards(root, cards)
        turn = str(body.get("turn") or "")
        for card in done:
            if turn and card["ok"] and isinstance(card.get("index"), int):
                try:
                    assistant.record_card(root, turn, card["index"], "applied")
                except AppError:
                    pass  # the card went in; an index that names no card of the turn only loses the note
        return {"cards": done, "study": _document(req, slug)}
    card = body.get("card")
    checked = assistant.check_card(card, study, pinned, root, assistant._proposed([card]))  # noqa: SLF001
    if not checked["ok"]:
        raise AppError(checked["problem"])
    assistant.apply_card(root, checked)
    return {"card": checked, "study": _document(req, slug)}


def assistant_history(req: Request) -> dict[str, Any]:
    """With `?turn=`, where that question stands (and its answer when done);
    without, the whole conversation, plus `thinking`: the id of the question
    being thought about now, or null."""
    root, _, _ = req.home.load(req.params["slug"])
    if req.query.get("turn"):
        return work.ask_state(req.server, root, req.query["turn"])
    return {**assistant.history(root), "thinking": work.ask_state(req.server, root).get("turn")}


def start_test_run(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    return work.start_test_run(req.server, root)


def test_run(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    return work.test_run_state(root, req.server)


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


def _run(req: Request, root: Path) -> str:
    run = req.query.get("run") or jobs.latest_run(root)
    if not run:
        raise AppError("the study has not been started yet", 404)
    return run


def start(req: Request) -> dict[str, Any]:
    """A new run; with a body `{"resume": "<run>"}`, that run again from where it stopped. A study
    with a schedule is armed instead (its windows start the sessions, D3), or with `{"now": true}`
    started at once as a session by hand (`scheduler.start_campaign`)."""
    root, study, _ = req.home.load(req.params["slug"])
    body = req.json()
    resume, now = body.get("resume"), body.get("now", False)
    if resume is not None and not isinstance(resume, str):
        raise AppError("resume: the name of a run")
    if not isinstance(now, bool):
        raise AppError("now: true or false")
    if resume is not None:
        return scheduler.by_hand(req.home, root, lambda: jobs.start(req.home, root, resume=resume))
    if study.schedule is not None:
        return scheduler.start_campaign(req.home, root, now=scheduler.clock_of(req.home).now(), at_once=now)
    return jobs.start(req.home, root)


def bakeoff(req: Request) -> dict[str, Any]:
    """`{"refs": ["branch", ...]}`: a run with no search that checks each branch
    against the starting point on the held-back cases (`jobs.start(bakeoff=)`)."""
    root, _, _ = req.home.load(req.params["slug"])
    refs = req.json().get("refs")
    if not isinstance(refs, list) or not refs or not all(isinstance(r, str) and r.strip() for r in refs):
        raise AppError("refs: name at least one branch")
    return jobs.start(req.home, root, bakeoff=[r.strip() for r in refs])


def verdict(req: Request) -> dict[str, Any]:
    """The verdict document of a run (`?run=`, else the latest): design 3.4."""
    from evolvekit.app.verdict import build_verdict

    root, _, _ = req.home.load(req.params["slug"])
    return build_verdict(root, _run(req, root))


def stop(req: Request) -> dict[str, Any]:
    """Stop the run; on an armed campaign, a person's stop also pauses it (D4)."""
    root, _, _ = req.home.load(req.params["slug"])
    if (scheduler.read_campaign(root) or {}).get("state") == "armed":
        run = req.query.get("run") or jobs.latest_run(root)
        if run is not None:
            jobs._run_dir(root, run)  # noqa: SLF001 - a name that is no run is refused before anything is paused
        return scheduler.stop_by_person(req.home, root, run)
    return jobs.stop(root, _run(req, root))


def final_check(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    seeds = req.json().get("seeds")
    run = _run(req, root)
    return scheduler.by_hand(req.home, root, lambda: jobs.resume_final_check(
        req.home, root, run, int(seeds) if seeds is not None else None))


def continue_run(req: Request) -> dict[str, Any]:
    """`{"run": RUN, "hours": H}`: a finished run searched on for H hours
    more (the study's hours when not said), then checked again. On a study with a
    schedule the time goes to its next window instead (`scheduler.continue_campaign`:
    `{"scheduled": true, "next", "words", ...}`), or with `{"now": true}` at once, as a
    session by hand."""
    root, study, _ = req.home.load(req.params["slug"])
    body = req.json()
    run = body.get("run") or _run(req, root)
    if not isinstance(run, str):
        raise AppError("run: the name of a run")
    now = body.get("now", False)
    if not isinstance(now, bool):
        raise AppError("now: true or false")
    if study.schedule is not None and not now:
        return scheduler.continue_campaign(req.home, root, run, body.get("hours"),
                                           now=scheduler.clock_of(req.home).now())
    return scheduler.by_hand(req.home, root, lambda: jobs.continue_run(req.home, root, run, body.get("hours")))


def _group(req: Request) -> str | None:
    """`?group=`: the group of a per-group run to read (its name); None for the run's own choice."""
    return req.query.get("group") or None


def _run_folder(req: Request, root: Path) -> tuple[str, Path]:
    """`(run, folder)` of `?run=`: the run's folder, or for a per-group run the
    folder of `?group=` (else the current group while it runs, else the first)."""
    run = _run(req, root)
    run_dir = jobs._run_dir(root, run)  # noqa: SLF001 - the run's folder, judged by name
    picked = jobs._group_folder(run_dir, jobs.phase(run_dir), _group(req))  # noqa: SLF001
    return run, picked[1] if picked is not None else run_dir


def status(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    return jobs.status(root, _run(req, root), req.server.status_document, group=_group(req))


RUNS_POLL_LIVE_S, RUNS_POLL_IDLE_S = 5, 10
"""How often the open Study page asks for the runs: while a run is live, and while none is."""


def runs_of(req: Request) -> dict[str, Any]:
    """Every run of the study as it is now (`jobs.runs`, no status document read) and the newest:
    what the open Study page asks every few seconds, so it notices a run that started or ended.
    `poll_s` is when to ask again: RUNS_POLL_LIVE_S while a run is queued or live, else RUNS_POLL_IDLE_S.
    `campaign` is a scheduled study's `scheduler.campaign_state` (None without a schedule). A finished run's line is kept between calls (`jobs.run_line`), so asking costs little."""
    root, study, _ = req.home.load(req.params["slug"])
    listed = jobs.runs(root)
    live = any(r.get("phase") in (*jobs.ACTIVE, "queued") for r in listed)
    earlier = jobs.earlier_use(root, listed[-1]) if listed else None  # the header's "An earlier run … says use"
    # A scheduled study's campaign as it is now (its line, when its runs ran): the open page follows it.
    campaign = (scheduler.campaign_state(root, study, now=scheduler.clock_of(req.home).now())
                if study.schedule is not None else None)
    return {"runs": listed, "latest": jobs.latest_run(root), "poll_s": RUNS_POLL_LIVE_S if live else RUNS_POLL_IDLE_S,
            "earlier": earlier, "campaign": campaign}


def tries_of(req: Request) -> dict[str, Any]:
    """Every try of a run, for the Study page's Tries, Cases and Settings views (`tries.digest`)."""
    root, _, _ = req.home.load(req.params["slug"])
    run, run_dir = _run_folder(req, root)  # a per-group run: one group's tries (`?group=`)
    return {"run": run, **tries.digest(run_dir)}


def try_of(req: Request) -> dict[str, Any]:
    """One combination of a run in full: what it set, every run of it, and what a
    failed run printed (the last lines of its output) and was started with; in a
    code study its `diff` against the starting point (`None` in any other run)."""
    from evolvekit.status import candidate_detail

    root, _, _ = req.home.load(req.params["slug"])
    _, run_dir = _run_folder(req, root)  # a per-group run: one group's combination (`?group=`)
    detail = candidate_detail(run_dir, req.params["combination"])
    if not detail:
        raise AppError(f"there is no combination {req.params['combination']!r} in this run", 404)
    keep = ("id", "generation", "operator", "parent_id", "rejected", "reject_reason", "last_failure", "gated",
            "stages_reached", "kpis", "parameters", "evaluations")
    diff = str(detail.get("block") or "") if tries.tree_run(run_dir) else None
    return {**{key: detail.get(key) for key in keep}, "diff": diff}


def results(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    return jobs.results(root, _run(req, root), req.server.status_document, group=_group(req))


def download(req: Request) -> FileResponse:
    slug = req.params["slug"]
    root, _, _ = req.home.load(slug)
    export = req.params["export"]
    if export == "study":  # the study itself, as one zip: no run needed
        return FileResponse(req.home.export_study(slug), "application/zip", f"{slug}.zip")
    body, content_type, filename = jobs.download(root, _run(req, root), export, req.server.status_document,
                                                 group=_group(req))
    # The report opens in the browser with ?view=1; everything else is a file to save.
    inline = export == "report" and req.query.get("view") == "1"
    return FileResponse(body, content_type, None if inline else filename)


def next_study(req: Request) -> dict[str, Any]:
    slug = req.params["slug"]
    root, _, _ = req.home.load(slug)
    new = jobs.next_study(req.home, root, _run(req, root), str(req.json().get("name") or ""))
    return _document(req, new)


def compare_runs(req: Request) -> dict[str, Any]:
    """Two studies or runs side by side: `?a=<slug>[/<run>]&b=<slug>[/<run>]`; a bare slug is its latest run."""
    return compare.compare(req.home, req.query.get("a") or "", req.query.get("b") or "", req.server.status_document)


ROUTES = [
    route("GET", "/api/home")(home),
    route("POST", "/api/shutdown")(shutdown),
    route("GET", "/api/compare")(compare_runs),
    route("GET", "/api/settings")(get_settings),
    route("PUT", "/api/settings")(put_settings),
    route("POST", "/api/settings/check")(check_assistant),
    route("POST", "/api/harnesses")(install),
    route("GET", "/api/harnesses/{harness}")(harness),
    route("POST", "/api/detect")(detect),  # it looks around the machine (up to seconds): not a GET
    route("POST", "/api/pick-file")(pick_file),
    route("POST", "/api/studies")(create_study),
    route("POST", "/api/trash/{entry}/restore")(restore_study),
    route("GET", "/api/studies/{slug}")(study),
    route("PATCH", "/api/studies/{slug}")(save_step),
    route("DELETE", "/api/studies/{slug}")(delete_study),
    route("POST", "/api/studies/{slug}/duplicate")(duplicate_study),
    route("GET", "/api/studies/{slug}/harness")(study_harness),
    route("POST", "/api/studies/{slug}/application")(application),
    route("POST", "/api/studies/{slug}/code")(code),
    route("PUT", "/api/studies/{slug}/cases/{name}")(put_case),
    route("DELETE", "/api/studies/{slug}/cases/{name}")(delete_case),
    route("POST", "/api/studies/{slug}/cases/{name}/restore")(restore_case),
    route("PUT", "/api/studies/{slug}/cases/{name}/tag")(put_tag),
    route("POST", "/api/studies/{slug}/samples")(use_samples),
    route("POST", "/api/studies/{slug}/import")(import_cases),
    route("POST", "/api/studies/{slug}/inspect")(inspect_cases),
    route("POST", "/api/studies/{slug}/split")(split),
    route("PUT", "/api/studies/{slug}/inputs/{input}/{name}")(put_input),
    route("DELETE", "/api/studies/{slug}/inputs/{input}")(remove_input),
    route("GET", "/api/studies/{slug}/plan")(plan),
    route("POST", "/api/studies/{slug}/tune-only")(tune_only),
    route("POST", "/api/studies/{slug}/preview")(start_preview),
    route("GET", "/api/studies/{slug}/preview")(preview),
    route("POST", "/api/studies/{slug}/kpis/try")(try_kpi),
    route("GET", "/api/studies/{slug}/choices")(choices),
    route("POST", "/api/studies/{slug}/assistant")(ask_assistant),
    route("GET", "/api/studies/{slug}/assistant")(assistant_history),
    route("POST", "/api/studies/{slug}/assistant/cancel")(cancel_assistant),
    route("POST", "/api/studies/{slug}/assistant/cards")(card_event),
    route("POST", "/api/studies/{slug}/cards")(add_card),
    route("POST", "/api/studies/{slug}/test-run")(start_test_run),
    route("GET", "/api/studies/{slug}/test-run")(test_run),
    route("POST", "/api/studies/{slug}/start")(start),
    route("POST", "/api/studies/{slug}/bakeoff")(bakeoff),
    route("POST", "/api/studies/{slug}/stop")(stop),
    route("POST", "/api/studies/{slug}/final-check")(final_check),
    route("POST", "/api/studies/{slug}/continue")(continue_run),
    route("GET", "/api/studies/{slug}/status")(status),
    route("GET", "/api/studies/{slug}/runs")(runs_of),
    route("GET", "/api/studies/{slug}/results")(results),
    route("GET", "/api/studies/{slug}/verdict")(verdict),
    route("GET", "/api/studies/{slug}/tries")(tries_of),
    route("GET", "/api/studies/{slug}/tries/{combination}")(try_of),
    route("GET", "/api/studies/{slug}/download/{export}")(download),
    route("POST", "/api/studies/{slug}/next")(next_study),
]
