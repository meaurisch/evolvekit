"""The app's JSON API: one handler per endpoint (design §7.1).

Every handler takes a `server.Request` and returns something JSON can carry,
or a `server.FileResponse`; a refusal is an `AppError` with a sentence. The
page uses nothing but this, so a server could take the app's place later.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from evolvekit.app import AppError, assistant, jobs, work
from evolvekit.app.detect import detect as detect_applications
from evolvekit.app.server import FileResponse, Request, route
from evolvekit.harness import HarnessError
from evolvekit.harness.execute import plan_for, read_preview
from evolvekit.harness.manifest import Harness
from evolvekit.harness.probe import probe_application
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
            root, study, _ = req.home.load(slug)
        except AppError as exc:
            studies.append({"slug": slug, "name": slug, "harness": "", "kind": "broken", "line": str(exc)})
            continue
        state = jobs.state_line(root, study, req.server.status_document)
        studies.append({"slug": slug, "name": study.name, "harness": f"{study.harness_id} {study.harness_version}",
                        **state, "updated_at": (root / "study.yaml").stat().st_mtime})
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
    return {"home": str(req.home.root), "studies": studies, "harnesses": harnesses, "keys": req.home.keys(),
            "provider": settings["provider"]}


def _templates(harness: Harness) -> list[dict[str, Any]]:
    """The study templates, settings studies first: they are the simpler start."""
    listed = []
    for tid, template in harness.templates.items():
        vary = template.get("vary") or {}
        kind = "data" if vary.get("data") else "settings"
        listed.append({"id": tid, "title": str(template.get("title") or tid),
                       "summary": " ".join(str(template.get("summary") or "").split()), "kind": kind})
    return sorted(listed, key=lambda t: (t["kind"] != "settings", t["title"]))


def get_settings(req: Request) -> dict[str, Any]:
    return {**req.home.settings(), "keys": req.home.keys(), "home": str(req.home.root)}


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
    return describe_harness(entry.harness)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# a study
# ---------------------------------------------------------------------------


def _document(req: Request, slug: str) -> dict[str, Any]:
    root, study, _ = req.home.load(slug)
    document = req.home.document(slug)
    document["preview"] = work.preview_state(root, req.server)
    document["test_run"] = work.test_run_state(root, req.server)
    document["runs"] = jobs.runs(root)
    document["state"] = jobs.state_line(root, study, req.server.status_document)
    return document


def create_study(req: Request) -> dict[str, Any]:
    body = req.json()
    slug = req.home.create_study(str(body.get("harness") or ""), body.get("template") or None, str(body.get("name") or ""))
    return _document(req, slug)


def study(req: Request) -> dict[str, Any]:
    return _document(req, req.params["slug"])


def study_harness(req: Request) -> dict[str, Any]:
    root, _, pinned = req.home.load(req.params["slug"])
    return describe_harness(pinned)


def save_step(req: Request) -> dict[str, Any]:
    slug = req.params["slug"]
    root = req.home.study_root(slug)
    if jobs.running(root):
        raise AppError("the study is running; stop it before changing it", 409)
    req.home.save_step(slug, req.json())
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
    root, study, pinned = req.home.load(slug)
    path = str(req.json().get("path") or "").strip().strip('"')
    if not path:
        raise AppError("path: where the application is")
    if not Path(path).is_absolute():
        raise AppError(f"give the full path to the application; {path!r} is not one")
    probe = probe_application(pinned, path)
    answer = {"path": path, "ok": probe.ok, "message": probe.message, "version": probe.version, "python": probe.python}
    if probe.ok:
        study.application_path = str(Path(path))
        study.application_version = probe.version or ""
        save_study(study, root)
        req.home.remember_application(str(Path(path)))
    return {"probe": answer, "study": _document(req, slug)}


def pick_file(req: Request) -> dict[str, Any]:
    body = req.json()
    kind = "folder" if body.get("kind") == "folder" else "file"
    title = str(body.get("title") or ("Choose a folder" if kind == "folder" else "Choose a file"))[:120]
    try:
        done = subprocess.run([sys.executable, "-c", _PICK, title, kind], capture_output=True, text=True,
                              timeout=PICK_TIMEOUT_S, encoding="utf-8", errors="replace")
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
    try:
        chosen = plan_for(study, pinned, timing)  # type: ignore[arg-type]
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
        hours = _hours_for(study, pinned, timing, GOOD * tunables)
        if hours is not None and hours > study.budget.hours:
            fixes.append({"kind": "time", "hours": hours, "label": f"Allow {_hours_words(hours)} in total",
                          "detail": f"About {GOOD * tunables} combinations for all {tunables}."})
    return {"summary": chosen.summary(), "estimated": estimated, "warning": warning, "fixes": fixes,
            "tunables": tunables, **chosen.to_json()}


THIN, GOOD = 6, 10
"""Combinations per thing tuned: below THIN the plan is thin; GOOD is what a fix aims for."""


def _hours_for(study: Any, harness: Any, timing: dict[str, Any], combinations: int) -> float | None:
    """The least total time, in quarter hours up to two days, whose plan tries `combinations`."""
    import dataclasses

    for quarters in range(1, 193):
        hours = quarters / 4
        trial = dataclasses.replace(study, budget=dataclasses.replace(study.budget, hours=hours))
        try:
            if plan_for(trial, harness, timing).combinations >= combinations:
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
    root, _, _ = req.home.load(req.params["slug"])
    return assistant.ask(req.home, root, str(req.json().get("message") or ""), provider=req.server.assistant_provider)


def add_card(req: Request) -> dict[str, Any]:
    """Add a card to the study -- one of the assistant's, or one the page's
    forms made -- after checking it again on the preview."""
    slug = req.params["slug"]
    root, study, pinned = req.home.load(slug)
    if jobs.running(root):
        raise AppError("the study is running; stop it before changing it", 409)
    card = req.json().get("card")
    checked = assistant.check_card(card, study, pinned, root, assistant._proposed([card]))  # noqa: SLF001
    if not checked["ok"]:
        raise AppError(checked["problem"])
    assistant.apply_card(root, checked)
    return {"card": checked, "study": _document(req, slug)}


def assistant_history(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    return assistant.history(root)


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
    root, _, _ = req.home.load(req.params["slug"])
    return jobs.start(req.home, root)


def stop(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    return jobs.stop(root, _run(req, root))


def final_check(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    seeds = req.json().get("seeds")
    return jobs.resume_final_check(req.home, root, _run(req, root), int(seeds) if seeds is not None else None)


def status(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    return jobs.status(root, _run(req, root), req.server.status_document)


def results(req: Request) -> dict[str, Any]:
    root, _, _ = req.home.load(req.params["slug"])
    return jobs.results(root, _run(req, root), req.server.status_document)


def download(req: Request) -> FileResponse:
    root, _, _ = req.home.load(req.params["slug"])
    export = req.params["export"]
    body, content_type, filename = jobs.download(root, _run(req, root), export, req.server.status_document)
    # The report opens in the browser with ?view=1; everything else is a file to save.
    inline = export == "report" and req.query.get("view") == "1"
    return FileResponse(body, content_type, None if inline else filename)


def next_study(req: Request) -> dict[str, Any]:
    slug = req.params["slug"]
    root, _, _ = req.home.load(slug)
    new = jobs.next_study(req.home, root, _run(req, root), str(req.json().get("name") or ""))
    return _document(req, new)


ROUTES = [
    route("GET", "/api/home")(home),
    route("GET", "/api/settings")(get_settings),
    route("PUT", "/api/settings")(put_settings),
    route("POST", "/api/harnesses")(install),
    route("GET", "/api/harnesses/{harness}")(harness),
    route("GET", "/api/detect")(detect),
    route("POST", "/api/pick-file")(pick_file),
    route("POST", "/api/studies")(create_study),
    route("GET", "/api/studies/{slug}")(study),
    route("PATCH", "/api/studies/{slug}")(save_step),
    route("GET", "/api/studies/{slug}/harness")(study_harness),
    route("POST", "/api/studies/{slug}/application")(application),
    route("PUT", "/api/studies/{slug}/cases/{name}")(put_case),
    route("DELETE", "/api/studies/{slug}/cases/{name}")(delete_case),
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
    route("POST", "/api/studies/{slug}/cards")(add_card),
    route("POST", "/api/studies/{slug}/test-run")(start_test_run),
    route("GET", "/api/studies/{slug}/test-run")(test_run),
    route("POST", "/api/studies/{slug}/start")(start),
    route("POST", "/api/studies/{slug}/stop")(stop),
    route("POST", "/api/studies/{slug}/final-check")(final_check),
    route("GET", "/api/studies/{slug}/status")(status),
    route("GET", "/api/studies/{slug}/results")(results),
    route("GET", "/api/studies/{slug}/download/{export}")(download),
    route("POST", "/api/studies/{slug}/next")(next_study),
]
