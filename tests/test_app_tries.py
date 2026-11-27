"""`evolvekit/app/tries.py`: every try of a run, digested for the Study page."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evolvekit.app.tries import digest

T0 = "2026-01-01T00:00:00+00:00"


def at(minutes: float) -> str:
    h, m = divmod(int(minutes), 60)
    return f"2026-01-01T{h:02d}:{m:02d}:00+00:00"


def write(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(i) + "\n" for i in items), encoding="utf-8")


def evaluation(cid, stage, case, value, minute, seed=0, ok=True):
    return [{"type": "eval_started", "ts": at(minute - 1), "candidate_id": cid, "stage": stage, "instance": case, "seed": seed},
            {"type": "eval_finished", "ts": at(minute), "candidate_id": cid, "stage": stage, "instance": case, "seed": seed,
             "ok": ok, "kpis": {"cost": value} if ok else {}}]


def a_run(tmp_path: Path, *, finished=True, minimize=True) -> Path:
    run = tmp_path / "runs" / "20260101-000000"
    events = [{"type": "run_started", "ts": T0, "objective": "cost", "direction": "minimize" if minimize else "maximize",
               "parameters": [{"name": "depth", "type": "int", "low": 1, "high": 9, "default": 3, "log": False, "choices": []}]},
              {"type": "generation_started", "ts": T0, "generation": 0},
              {"type": "stage_started", "ts": T0, "stage": "screen"}]
    # the starting point: 100 on a, 200 on b (quick look); and 300 on c in the full look
    events += evaluation("g000-c0001", "screen", "a", 100, 2) + evaluation("g000-c0001", "screen", "b", 200, 3)
    events += [{"type": "stage_started", "ts": at(3), "stage": "full"}]
    events += (evaluation("g000-c0001", "full", "a", 100, 5) + evaluation("g000-c0001", "full", "b", 200, 6)
               + evaluation("g000-c0001", "full", "c", 300, 7))
    events += [{"type": "generation_started", "ts": at(10), "generation": 1}]
    # combination 2: a big quick look (20 % on both), never a full look
    events += evaluation("g001-c0002", "screen", "a", 80, 12) + evaluation("g001-c0002", "screen", "b", 160, 13)
    # combination 3: a small but full look: 1 %, 2 %, 3 %; two runs on c average to 3 %
    events += evaluation("g001-c0003", "screen", "a", 99, 14) + evaluation("g001-c0003", "screen", "b", 196, 15)
    events += (evaluation("g001-c0003", "full", "a", 99, 20) + evaluation("g001-c0003", "full", "b", 196, 21)
               + evaluation("g001-c0003", "full", "c", 288, 22, seed=0) + evaluation("g001-c0003", "full", "c", 294, 23, seed=1))
    # combination 4: failed everywhere
    events += evaluation("g001-c0004", "screen", "a", None, 24, ok=False)
    if finished:
        events += [{"type": "run_finished", "ts": at(30)}]
    else:
        # combination 5 still running: one of two quick-look cases done
        events += evaluation("g001-c0005", "screen", "a", 90, 26)
        events += [{"type": "eval_started", "ts": at(27), "candidate_id": "g001-c0005", "stage": "screen", "instance": "b", "seed": 0}]
    write(run / "events.jsonl", events)
    write(run / "runs.jsonl", [
        {"id": "g000-c0001", "generation": 0, "operator": "human-seed", "parent_id": None, "params": {"depth": 3}},
        {"id": "g001-c0002", "generation": 1, "operator": "param_local", "parent_id": "g000-c0001", "params": {"depth": 7}, "competes": False},
        {"id": "g001-c0003", "generation": 1, "operator": "param_local", "parent_id": "g000-c0001", "params": {"depth": 4}},
        {"id": "g001-c0004", "generation": 1, "operator": "param_local", "parent_id": "g000-c0001", "params": {"depth": 9}, "rejected": True},
    ])
    return run


def test_a_try_is_measured_against_the_starting_point_case_by_case(tmp_path):
    d = digest(a_run(tmp_path))
    assert d["available"] and d["full_stage"] == "full" and d["cases"] == ["a", "b", "c"] and d["quick_cases"] == 2
    by = {c["id"]: c for c in d["combinations"]}
    quick, full, failed = by["g001-c0002"], by["g001-c0003"], by["g001-c0004"]
    assert quick["full"] is False and quick["pct"] == pytest.approx(20.0)
    assert quick["excluded"] is None, "a healthy quick look keeps its ordinary unchecked dot"
    assert full["full"] is True and full["pct"] == pytest.approx(2.0), "1 %, 2 % and 3 % (the mean of two runs on c)"
    assert full["cases"]["full"] == {"a": pytest.approx(1.0), "b": pytest.approx(2.0), "c": pytest.approx(3.0)}
    assert failed["failed"] is True and failed["pct"] is None
    assert by["g000-c0001"]["base"] is True and d["base"] == "g000-c0001"
    assert full["params"] == {"depth": 4} and d["parameters"][0]["name"] == "depth"
    assert d["done"] is True and d["running"] == []
    assert [r["round"] for r in d["rounds"]] == [0, 1]


def test_the_best_so_far_line_counts_full_looks_only(tmp_path):
    d = digest(a_run(tmp_path))
    assert [b["id"] for b in d["best"]] == ["g000-c0001", "g001-c0003"], "the 20 % quick look never sets the line"
    assert d["best"][-1]["pct"] == pytest.approx(2.0)
    assert {c["id"] for c in d["combinations"] if c["best"]} == {"g001-c0003"}


def test_a_full_look_half_done_never_sets_the_best_so_far_line(tmp_path):
    """Persona round 1, F3: the app's best so far fell (8.99 %, then 7.97 %) while the best stayed: a
    full look half done counted as the mean of the cases it had. It counts once it finished (its row)."""
    run = a_run(tmp_path, finished=False)
    with (run / "events.jsonl").open("a", encoding="utf-8") as handle:
        for item in evaluation("g001-c0006", "full", "a", 50, 28):  # 50 % on its first case, two to go
            handle.write(json.dumps(item) + "\n")
    d = digest(run)
    assert next(c for c in d["combinations"] if c["id"] == "g001-c0006")["pct"] == pytest.approx(50.0)
    assert [b["id"] for b in d["best"]] == ["g000-c0001", "g001-c0003"], "not before its row is written"


def test_better_means_higher_when_the_goal_is_to_maximise(tmp_path):
    d = digest(a_run(tmp_path, minimize=False))
    by = {c["id"]: c for c in d["combinations"]}
    assert by["g001-c0002"]["pct"] == pytest.approx(-20.0)
    assert [b["id"] for b in d["best"]] == ["g000-c0001"], "nothing beat the starting point"


@pytest.mark.parametrize("minimize", [True, False])
@pytest.mark.parametrize("excluded", [
    {"gated": "Safety limit exceeded", "competes": False},
    {"gated": "Safety limit exceeded"},
    {"competes": False, "veto": "holdout below the seed"},
])
def test_excluded_full_looks_never_displace_the_eligible_best(tmp_path, minimize, excluded):
    from evolvekit.app.verdict import best_so_far

    run = tmp_path / "run"
    direction = "minimize" if minimize else "maximize"
    # The warm start uses depth 7, not the default 3. Its measured cost is
    # 0.4; changes are relative percent, not percentage points.
    values = [0.4, 0.36 if minimize else 0.44, 0.2 if minimize else 0.6]
    ids = ["g000-c0001", "g001-c0002", "g001-c0003"]
    events = [{"type": "run_started", "ts": T0, "objective": "cost", "direction": direction,
               "parameters": [{"name": "depth", "default": 3}]},
              {"type": "stage_started", "ts": T0, "stage": "full"}]
    rows = []
    for i, (cid, value) in enumerate(zip(ids, values)):
        events += evaluation(cid, "full", "sample", value, 2 + i * 2)
        rows.append({"id": cid, "operator": "human-seed" if i == 0 else "param_local",
                     "params": {"depth": 7}, "rejected": False, "competes": True,
                     "kpis": {"cost": value}, "score": -value if minimize else value})
    rows[-1].pop("competes")
    rows[-1].update(excluded)
    write(run / "events.jsonl", events)
    write(run / "runs.jsonl", rows)
    (run / "study.yaml").write_text("warm: {from: earlier, run: previous}\n", encoding="utf-8")

    d = digest(run)
    assert [b["id"] for b in d["best"]] == ids[:2]
    assert d["best"][-1]["pct"] == pytest.approx(10.0)
    blocked = next(c for c in d["combinations"] if c["id"] == ids[-1])
    assert blocked["excluded"] and not blocked["failed"] and not blocked["best"]
    assert blocked["pct"] == pytest.approx(50.0), "the excluded raw result can still be inspected"
    status_best = best_so_far(run, {"kpi": "cost", "direction": "lower" if minimize else "higher"},
                              rows=rows, job={"phase": "search"})
    assert status_best["id"] == d["best"][-1]["id"]
    assert status_best["pct"] == pytest.approx(d["best"][-1]["pct"])


def test_a_running_try_says_how_far_it_got(tmp_path):
    d = digest(a_run(tmp_path, finished=False))
    assert d["done"] is False
    assert d["running"] == [{"id": "g001-c0005", "stage": "screen", "since_h": pytest.approx(25 / 60, abs=1e-3), "done": 1, "need": 2, "full": False}]


def test_the_final_check_as_far_as_it_got(tmp_path):
    run = a_run(tmp_path)
    check = []
    for case, base, new in (("x", 100, 98), ("y", 200, 194), ("z", 300, 297)):
        check += [{"type": "eval_finished", "candidate_id": "baseline", "instance": case, "seed": 0, "ok": True, "kpis": {"cost": base}},
                  {"type": "eval_finished", "candidate_id": "g001-c0003", "instance": case, "seed": 0, "ok": True, "kpis": {"cost": new}}]
    write(run / "confirm" / "final" / "events.jsonl", [{"type": "confirm_started", "runs_per_candidate": 2}] + check)
    c = digest(run)["check"]
    assert c["pairs"] == 3 and c["cases"] == 3 and c["runs_per_case"] == 2
    assert c["per_case"] == {"x": pytest.approx(2.0), "y": pytest.approx(3.0), "z": pytest.approx(1.0)}
    assert c["mean"] == pytest.approx(2.0, abs=0.01) and c["ci95"][0] < 2.0 < c["ci95"][1]
    from evolvekit.confirm import verdict_summary
    from evolvekit.stats import log_gain

    own = verdict_summary([log_gain(b, n, "minimize") for b, n in ((100, 98), (200, 194), (300, 297))],
                          direction="minimize")
    mine = (c["mean"], c["ci95"], c["chance"], c["worst"])
    assert mine == (own["mean"], own["ci95"], own["chance"], own["worst"]), \
        "the verdict's own quantity: log gains through verdict_summary, not a mean of percentages"


def test_a_run_with_nothing_yet_is_empty_not_an_error(tmp_path):
    run = tmp_path / "runs" / "x"
    run.mkdir(parents=True)
    assert digest(run)["available"] is False
    write(run / "events.jsonl", [{"type": "run_started", "ts": T0, "objective": "cost", "direction": "minimize", "parameters": []}])
    d = digest(run)
    assert d["available"] is True and d["combinations"] == []


def test_the_digest_is_rebuilt_when_the_run_changes(tmp_path):
    run = a_run(tmp_path, finished=False)
    assert digest(run)["done"] is False
    with (run / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "eval_finished", "ts": at(28), "candidate_id": "g001-c0005", "stage": "screen", "instance": "b",
                                 "seed": 0, "ok": True, "kpis": {"cost": 180}}) + "\n")
        handle.write(json.dumps({"type": "run_finished", "ts": at(30)}) + "\n")
    assert digest(run)["done"] is True


def test_one_combination_in_full_through_the_api(tmp_path):
    """GET …/tries/{combination}: what it set and every run of it, for the Study page's details drawer
    (it replaces the old dashboard's candidate view)."""
    import http.client

    from evolvekit.app.server import AppServer

    server = AppServer(tmp_path / "home", port=0)
    server.start()
    try:
        def get(path):
            connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=60)
            connection.request("GET", path, headers={"Host": f"127.0.0.1:{server.port}", "X-Evolvekit-Token": server.token})
            response = connection.getresponse()
            body = json.loads(response.read())
            connection.close()
            return response.status, body

        slug = server.home.create_study("demo-tour", "tune", "Tries")
        a_run(server.home.study_root(slug))
        status, detail = get(f"/api/studies/{slug}/tries/g001-c0003?run=20260101-000000")
        assert status == 200 and detail["id"] == "g001-c0003" and detail["parent_id"] == "g000-c0001"
        assert len(detail["evaluations"]) == 6 and all(e["ok"] for e in detail["evaluations"])
        assert "block" not in detail, "the page gets what it shows, not the run's internals"
        status, _ = get(f"/api/studies/{slug}/tries/g999-c9999?run=20260101-000000")
        assert status == 404
        status, digest = get(f"/api/studies/{slug}/tries?run=20260101-000000")
        assert {c["id"]: c["failed_runs"] for c in digest["combinations"]}["g001-c0004"] == 1

        run = server.home.study_root(slug) / "runs" / "20260101-000000"
        events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        for event in events:
            if event.get("type") == "run_started":
                event["objective"] = "tour_length"
            if "kpis" in event and "cost" in event["kpis"]:
                event["kpis"] = {"tour_length": event["kpis"]["cost"]}
        for case in ("a", "b", "c"):
            events += evaluation("g002-c0006", "full", case, 1, 29)
            events[-1]["kpis"] = {"tour_length": 1}
        write(run / "events.jsonl", events)
        rows = [json.loads(line) for line in (run / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
        for row, value in zip(rows, (200, 120, 195.3333, 1000)):
            row.update(kpis={"tour_length": value}, score=-value, competes=row["id"] in ("g000-c0001", "g001-c0003"))
        rows.append({"id": "g002-c0006", "score": -1, "kpis": {"tour_length": 1}, "rejected": False,
                     "competes": False, "gated": "Safety limit exceeded"})
        write(run / "runs.jsonl", rows)
        status, chart = get(f"/api/studies/{slug}/tries?run=20260101-000000")
        status_code, summary = get(f"/api/studies/{slug}/status?run=20260101-000000")
        assert status == status_code == 200
        assert chart["best"][-1]["id"] == summary["best"]["id"] == "g001-c0003"
        assert next(c for c in chart["combinations"] if c["id"] == "g002-c0006")["excluded"]
    finally:
        server.stop()
