"""The assistant (`evolvekit/app/assistant.py`) against the `fake` provider:
cards checked on the preview, corrected at most twice, never sent a row."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from evolvekit.app import assistant
from evolvekit.app.server import AppServer
from evolvekit.app.store import Home
from evolvekit.harness.execute import preview
from evolvekit.harness.study import load_study
from evolvekit.providers.base import Completion
from evolvekit.providers.fake import FakeProvider
from test_app_server import call

ROOT = Path(__file__).resolve().parents[1]
SOLVER = ROOT / "examples" / "cli-solver" / "solver.py"
NORTH_LEGS = ("SELECT TOTAL(t.leg) FROM tour t JOIN stops s ON s.stop_id = t.stop_id WHERE s.tag = 'north'")
GOOD = {
    "say": "Weights on the northern stops, judged by the plain tour length.",
    "cards": [
        {"kind": "kpi", "name": "north_legs", "says": "Kilometres of legs into northern stops", "direction": "lower",
         "unit": "km", "sql": NORTH_LEGS,
         "rows_sql": "SELECT t.stop_id, t.leg FROM tour t JOIN stops s ON s.stop_id = t.stop_id WHERE s.tag = 'north'"},
        {"kind": "data_change", "name": "north_weight", "lever": "stop_weights", "column": "weight",
         "where": "tag = 'north'", "mode": "scale", "low": 0.5, "high": 2.0, "start": 1.0,
         "says": "Scale the weight of the northern stops by 0.5 to 2"},
        {"kind": "constraint", "says": "The northern weight stays at least 0.6", "expr": "north_weight >= 0.6"},
        {"kind": "guardrail", "kpi": "longest_leg", "max": 100},
        {"kind": "goal", "levels": [{"kpi": "tour_length", "direction": "lower"}]},
    ],
}


@pytest.fixture
def study(tmp_path) -> tuple[Home, Path]:
    home = Home(tmp_path / "home")
    slug = home.create_study("demo-tour", "tune", "Weights")
    home.save_step(slug, {"application": {"path": str(SOLVER), "version": ""},
                          "limits": {"time_per_case_s": 0.5, "retries": 1, "runs_per_case": 1}})
    home.use_samples(slug)
    root = home.study_root(slug)
    assert preview(root)["ok"]
    return home, root


def test_good_cards_are_shown_with_what_they_measure_on_the_preview(study):
    home, root = study
    provider = FakeProvider(responses=[json.dumps(GOOD)])
    answer = assistant.ask(home, root, "Try weights on the northern stops", provider=provider)
    assert answer["say"] == GOOD["say"]
    kinds = {card["kind"]: card for card in answer["cards"]}
    assert all(card["ok"] for card in answer["cards"]), answer["cards"]
    assert kinds["kpi"]["value"] > 0 and kinds["kpi"]["rows"] > 0
    assert kinds["data_change"]["applies_to"] > 0 and kinds["data_change"]["table"] == "stops"
    assert kinds["constraint"]["holds_today"] is True
    assert len(provider.calls) == 1


def test_a_card_that_fails_goes_back_to_the_model_and_is_fixed(study):
    home, root = study
    broken = json.loads(json.dumps(GOOD))
    broken["cards"][0]["sql"] = "SELECT TOTAL(distance) FROM tour"
    provider = FakeProvider(responses=[json.dumps(broken), json.dumps(GOOD)])
    answer = assistant.ask(home, root, "Try weights on the northern stops", provider=provider)
    assert all(card["ok"] for card in answer["cards"])
    assert len(provider.calls) == 2
    correction = provider.calls[1]["messages"][-1]["content"]
    assert "failed the check" in correction and "no such column: distance" in correction


def test_after_two_corrections_the_card_says_it_could_not_be_expressed(study):
    home, root = study
    broken = {"say": "Here it is.", "cards": [dict(GOOD["cards"][0], sql="SELECT TOTAL(distance) FROM tour")]}
    provider = FakeProvider(responses=[json.dumps(broken)] * 3, cycle=False)
    answer = assistant.ask(home, root, "Measure the northern legs", provider=provider)
    (card,) = answer["cards"]
    assert card["ok"] is False and card["problem"].startswith("It could not be expressed:")
    assert "Try saying it differently" in card["problem"]
    assert len(provider.calls) == 3


def test_an_answer_that_is_not_json_is_asked_again(study):
    home, root = study
    provider = FakeProvider(responses=["Sure! Here are some ideas...", "```json\n" + json.dumps(GOOD) + "\n```"])
    answer = assistant.ask(home, root, "Anything", provider=provider)
    assert all(card["ok"] for card in answer["cards"]) and len(answer["cards"]) == 5


def test_the_model_is_sent_summaries_and_never_a_row(study):
    home, root = study
    provider = FakeProvider(responses=[json.dumps(GOOD)])
    assistant.ask(home, root, "Try weights", provider=provider)
    sent = "\n".join(m["content"] for m in provider.calls[0]["messages"])
    db = sqlite3.connect(root / "preview" / "tables.sqlite")
    coordinates = [row for row in db.execute("SELECT x, y FROM stops").fetchall()]
    db.close()
    leaked = [value for pair in coordinates for value in pair if repr(value) in sent or f"{value:.4f}" in sent]
    assert not leaked, f"row values reached the model: {leaked[:3]}"
    assert '"north"' in sent and '"south"' in sent, "categorical values are summarised"
    assert '"rows": 30' in sent and "application" not in json.loads(sent.split("## The study so far\n")[1].split("\n\n## ")[0])


class _Priced:
    name = "priced"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, *, model, max_tokens, temperature):
        self.calls += 1
        return Completion(text=json.dumps(GOOD), input_tokens=1000, output_tokens=200, model=model, provider="priced", usd=0.0125)


def test_every_answer_costs_what_the_provider_says_and_the_total_is_kept(study):
    home, root = study
    assistant.ask(home, root, "One", provider=_Priced())
    answer = assistant.ask(home, root, "Two", provider=_Priced())
    assert answer["usd"] == 0.0125 and answer["total_usd"] == 0.025
    history = assistant.history(root)
    assert [t["role"] for t in history["turns"]] == ["user", "assistant", "user", "assistant"]


def test_without_a_key_the_assistant_says_how_to_add_one(study, monkeypatch):
    home, root = study
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    answer = assistant.ask(home, root, "Anything")
    assert answer["needs_key"] is True and "Settings" in answer["say"] and answer["cards"] == []


def _settled(server: AppServer, slug: str, turn: str, timeout: float = 30.0) -> dict:
    """The answer to `turn`, polled as the page polls it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, body = call(server, "GET", f"/api/studies/{slug}/assistant?turn={turn}")
        assert status == 200, body
        if body["state"] != "thinking":
            return body
        time.sleep(0.05)
    raise AssertionError("the assistant never finished")


def _asked(server: AppServer, slug: str, message: str, **where) -> dict:
    """A question through the API, waited for: what the page gets in the end."""
    status, started = call(server, "POST", f"/api/studies/{slug}/assistant", {"message": message, **where})
    assert status == 200 and started["state"] == "thinking" and started["turn"], started
    answer = _settled(server, slug, started["turn"])
    assert answer["turn"] == started["turn"]
    return answer


def test_cards_are_added_to_the_study_through_the_api(study):
    home, root = study
    server = AppServer(home.root, port=0)
    server.start()
    try:
        slug = root.name
        for card in GOOD["cards"][:2]:
            status, body = call(server, "POST", f"/api/studies/{slug}/cards", {"card": card})
            assert status == 200, body
        status, body = call(server, "POST", f"/api/studies/{slug}/cards",
                            {"card": dict(GOOD["cards"][1], name="nobody", where="tag = 'east'")})
        assert status == 400 and "selects no row" in body["error"]
        studied = load_study(root)
        assert studied.data["north_weight"].where == "tag = 'north'" and studied.kpis["north_legs"].sql == NORTH_LEGS
        server.assistant_provider = FakeProvider(responses=[json.dumps(GOOD)])
        answer = _asked(server, slug, "more")
        assert answer["state"] == "done" and answer["cards"]
    finally:
        server.stop()


class _Slow(FakeProvider):
    """A model that answers only when the test lets it: `complete` waits for `go`."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.asked = threading.Event()
        self.go = threading.Event()

    def complete(self, messages, **kwargs):
        self.asked.set()
        assert self.go.wait(30), "the test never let the model answer"
        return super().complete(messages, **kwargs)


def test_a_question_runs_in_the_background_and_can_be_cancelled(study):
    home, root = study
    server = AppServer(home.root, port=0)
    server.start()
    try:
        slug = root.name
        slow = _Slow(responses=[json.dumps(GOOD)])
        server.assistant_provider = slow
        status, started = call(server, "POST", f"/api/studies/{slug}/assistant", {"message": "Try weights", "screen": "step-4"})
        assert status == 200 and started["state"] == "thinking" and started["turn"], started
        turn = started["turn"]
        assert slow.asked.wait(10), "the model is asked in the background"
        status, body = call(server, "GET", f"/api/studies/{slug}/assistant?turn={turn}")
        assert status == 200 and body["state"] == "thinking"
        status, body = call(server, "GET", f"/api/studies/{slug}/assistant")
        assert status == 200 and body["thinking"] == turn, "a page opened meanwhile learns a question is still being thought about"
        status, body = call(server, "POST", f"/api/studies/{slug}/assistant", {"message": "Another"})
        assert status == 409 and "thinking" in body["error"]
        status, body = call(server, "POST", f"/api/studies/{slug}/assistant/cancel")
        assert status == 200 and body == {"cancelled": True}
        assert call(server, "GET", f"/api/studies/{slug}/assistant?turn={turn}")[1]["state"] == "cancelled"
        slow.go.set()
        server.work[f"assistant:{slug}"]["thread"].join(10)
        status, history = call(server, "GET", f"/api/studies/{slug}/assistant")
        assert [t["role"] for t in history["turns"]] == ["user", "assistant"]
        answer = history["turns"][1]
        assert answer["cancelled"] is True and "cards" not in answer, "the model's answer is thrown away"
        assert history["thinking"] is None and all(t["id"] for t in history["turns"])
        assert history["turns"][0]["id"] == turn and answer["turn"] == turn
        assert call(server, "POST", f"/api/studies/{slug}/assistant/cancel")[1] == {"cancelled": False}, "nothing to cancel now"
        # A new question is allowed, and the cancelled one is not put to the model again.
        fast = FakeProvider(responses=[json.dumps(GOOD)])
        server.assistant_provider = fast
        answer = _asked(server, slug, "Scale the northern weights")
        assert answer["state"] == "done" and len(answer["cards"]) == 5 and answer["usd"] is None, "the fake has no price"
        asked = [m["content"] for m in fast.calls[0]["messages"] if m["role"] == "user"]
        assert len(asked) == 2 and asked[-1] == "Scale the northern weights", "no half entry is replayed"
    finally:
        server.stop()


def test_a_cancelled_question_direct_from_python(study):
    home, root = study
    cancelled = threading.Event()
    cancelled.set()
    answer = assistant.ask(home, root, "Try weights", provider=_Priced(), cancelled=cancelled)
    assert answer["cancelled"] is True and "cards" not in answer
    turns = assistant.history(root)["turns"]
    assert turns[-1]["cancelled"] is True and turns[-1]["usd"] == 0.0125, "what the discarded answer cost is still counted"
    assert assistant._answered(assistant._entries(root)) == []


def test_cards_keep_their_fate(study):
    home, root = study
    server = AppServer(home.root, port=0)
    server.start()
    try:
        slug = root.name
        server.assistant_provider = FakeProvider(responses=[json.dumps(GOOD)])
        answer = _asked(server, slug, "Try weights")
        turn = answer["turn"]
        assert len(answer["cards"]) == 5
        events = f"/api/studies/{slug}/assistant/cards"
        for index, state in [(0, "applied"), (1, "dismissed"), (2, "dismissed"), (2, "applied")]:
            status, body = call(server, "POST", events, {"turn": turn, "index": index, "state": state})
            assert status == 200 and body == {"turn": turn, "index": index, "state": state}, body
        assert call(server, "POST", events, {"turn": turn, "index": 9, "state": "applied"})[0] == 404
        assert call(server, "POST", events, {"turn": "nope", "index": 0, "state": "applied"})[0] == 404
        assert call(server, "POST", events, {"turn": turn, "index": 0, "state": "lost"})[0] == 400
        status, history = call(server, "GET", f"/api/studies/{slug}/assistant")
        assert [t["role"] for t in history["turns"]] == ["user", "assistant"], "card events are folded in, not listed"
        reply = history["turns"][1]
        assert reply["turn"] == turn
        assert [c["state"] for c in reply["cards"]] == ["applied", "dismissed", "applied", "proposed", "proposed"], "the last event wins"
        # The next question's replay tells the model which of its proposals were taken.
        fast = FakeProvider(responses=['{"say": "Noted.", "cards": []}'])
        server.assistant_provider = fast
        _asked(server, slug, "And now?")
        replayed = json.loads([m for m in fast.calls[0]["messages"] if m["role"] == "assistant"][1]["content"])
        assert [c.get("applied") for c in replayed["cards"]] == [True, None, True, None, None]
        assert replayed["cards"][0]["name"] == "north_legs"
    finally:
        server.stop()


def test_the_model_knows_which_goal_wants_a_guardrail(tmp_path):
    home = Home(tmp_path / "home")
    root = home.study_root(home.create_study("pyvrp", "tune", "Costs"))
    from evolvekit.harness.manifest import load_harness

    sent = assistant.context(root, load_study(root), load_harness(root / "harness"))
    catalogues = json.loads(sent.split("## Catalogues\n")[1].split("\n\n## ")[0])
    assert catalogues["kpis"]["real_cost"]["guard"] == {"kpi": "feasible", "min": 1.0}
    assert "guard" not in catalogues["kpis"]["solver_cost"], "PyVRP's own cost prices broken rules already"
    assert 'A KPI with a "guard"' in assistant.SYSTEM


class _Silent:
    """A model that spends its whole budget thinking: empty, finish_reason=length."""

    name = "silent"

    def complete(self, messages, *, model, max_tokens, temperature):
        raise RuntimeError("openrouter provider: response contained empty content (finish_reason=length)")


def test_a_model_that_gives_no_answer_says_so_in_the_chat_and_is_not_asked_again(study):
    home, root = study
    answer = assistant.ask(home, root, "Trucks must stay dearer than vans", provider=_Silent())
    assert answer["failed"] == "The model thought for too long and gave no answer. Ask again, perhaps in fewer words."
    assert answer["cards"] == [] and assistant.history(root)["turns"][-1]["failed"] == answer["failed"], "kept for a reload"
    provider = FakeProvider(responses=[json.dumps(GOOD)])
    assistant.ask(home, root, "Try weights", provider=provider)
    asked = [m["content"] for m in provider.calls[0]["messages"] if m["role"] == "user"]
    assert "Trucks must stay dearer than vans" not in asked, "an unanswered question is not put to the model again"
    assert asked[-1] == "Try weights"
    assert assistant.MAX_TOKENS >= 16000, "room to think and still answer"


def test_a_key_changed_in_settings_is_used_at_once(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-the-shell")
    home = Home(tmp_path / "home")
    home.set_keys({"OPENROUTER_API_KEY": "first"})
    assistant._provider(home)
    assert os.environ["OPENROUTER_API_KEY"] == "first", "the app's file wins over a shell variable"
    home.set_keys({"OPENROUTER_API_KEY": "second"})
    assistant._provider(home)
    assert os.environ["OPENROUTER_API_KEY"] == "second"
    home.set_keys({"OPENROUTER_API_KEY": ""})
    assert "OPENROUTER_API_KEY" not in os.environ
    assert home.keys()["OPENROUTER_API_KEY"] is False


def test_a_removed_key_stops_the_assistant_with_the_needs_a_key_answer(study, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    home, root = study
    home.set_keys({"OPENROUTER_API_KEY": "first"})
    assistant._provider(home)
    home.set_keys({"OPENROUTER_API_KEY": ""})
    answer = assistant.ask(home, root, "Anything")
    assert answer["needs_key"] is True and answer["cards"] == []


def test_a_cost_is_real_or_absent():
    def config_for(model: str):
        return assistant._config("openrouter", model, {})

    reported = Completion(text="", input_tokens=1000, output_tokens=0, model="m", provider="p", usd=0.0123)
    assert assistant._cost(reported, None) == 0.0123
    priced = Completion(text="", input_tokens=1_000_000, output_tokens=0, model="anthropic/claude-sonnet-5", provider="openrouter")
    assert assistant._cost(priced, config_for("anthropic/claude-sonnet-5")) == 2.0
    unpriced = Completion(text="", input_tokens=1000, output_tokens=0, model="my-deployment", provider="azure")
    assert assistant._cost(unpriced, config_for("my-deployment")) is None
    assert assistant._cost(unpriced, assistant._config("azure", "my-deployment", {"my-deployment": [1.0, 3.0]})) == 0.001


class _Unpriced:
    """A provider that reports no cost, for a model nobody knows the price of."""

    name = "azure"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, messages, *, model, max_tokens, temperature, response_format=None):
        self.calls.append({"model": model, "response_format": response_format})
        return Completion(text=json.dumps(GOOD), input_tokens=1000, output_tokens=200, model=model, provider=self.name)


def test_an_unknown_cost_is_said_to_be_unknown_and_kept_out_of_the_total(study):
    home, root = study
    assistant.ask(home, root, "One", provider=_Priced())
    answer = assistant.ask(home, root, "Two", provider=_Unpriced())
    assert answer["usd"] is None and answer["total_usd"] == 0.0125
    history = assistant.history(root)
    assert history["total_usd"] == 0.0125 and history["unknown"] == 1
    assert history["turns"][-1]["usd"] is None


def test_the_assistant_asks_for_json_where_the_api_has_it(study):
    home, root = study
    provider = _Unpriced()
    assistant.ask(home, root, "One", provider=provider)
    assert provider.calls[0]["response_format"] == {"type": "json_object"}
    fake = FakeProvider(responses=[json.dumps(GOOD)])
    assistant.ask(home, root, "Two", provider=fake)
    assert fake.calls[0]["response_format"] is None, "the fake provider is not asked for JSON mode"


def _finished_run(root: Path, run: str = "20260101-000000") -> str:
    """A finished run written by hand, as `jobs.results` reads one: a search
    whose best was c1, and a final check of it against the starting point on
    three held-back cases. Nothing is run."""
    from evolvekit.confirm import paired_summary

    run_dir = root / "runs" / run
    final = run_dir / "confirm" / "final"
    final.mkdir(parents=True)
    (run_dir / "job.json").write_text(json.dumps({
        "phase": "done", "pid": 2 ** 22 + 7, "started_at": "2026-01-01T00:00:00+00:00", "finished_at": "2026-01-01T00:10:00+00:00",
        "plan": {"rounds": 2, "hours": 0.05, "workers": 1, "run_s": 1.0, "summary": "2 rounds of 4 combinations, then a final check"},
        "search": {"generations": 2},
        "final": {"candidate": "c1", "comparison": "confirm/final/comparison.json", "seeds": 1}}), encoding="utf-8")
    (run_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (run_dir / "runs.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"id": "seed", "operator": "human-seed", "score": 100.0, "params": {"neighbours": 8}, "kpis": {"tour_length": 100.0}},
        {"id": "c1", "operator": "mutate", "score": 110.0, "params": {"neighbours": 12}, "kpis": {"tour_length": 90.0}},
    ]) + "\n", encoding="utf-8")
    held = {"a.json": (100.0, 98.7), "b.json": (80.0, 81.6), "c.json": (120.0, 115.2)}
    checked = []
    for case, (start, best) in held.items():
        checked.append({"configuration": "baseline", "instance": case, "seed": 1001, "ok": True, "kpis": {"tour_length": start}})
        checked.append({"configuration": "c1", "instance": case, "seed": 1001, "ok": True, "kpis": {"tour_length": best}})
    (final / "results.json").write_text(json.dumps(checked), encoding="utf-8")
    gains = [(start - best) / start * 100 for start, best in held.values()]  # lower is better: a positive gain
    (final / "comparison.json").write_text(json.dumps({
        "seeds": [1001], "instances": list(held), "baseline_id": "baseline", "candidates": ["c1"],
        "per_candidate": {"c1": {"summary": paired_summary(gains)}}}), encoding="utf-8")
    return run


def test_a_question_on_the_results_screen_sees_the_verdict_and_gets_a_plain_answer(study):
    home, root = study
    run = _finished_run(root)
    provider = FakeProvider(responses=['{"say": "Only 3 cases were held back, so the range is wide.", "cards": []}'])
    answer = assistant.ask(home, root, "Why is this not proven?", provider=provider, screen="results", run=run)
    assert answer["cards"] == [] and answer["say"].startswith("Only 3")
    seen = {"prompt": provider.calls[0]["messages"][1]["content"]}
    where = seen["prompt"].split("## Where the consultant is\n")[1]
    assert where.startswith("Screen: results")
    assert "held-back" in where and "chance" in where and "Not proven on 3 held-back cases" in where
    assert "a.json" in where and "98.7" in where, "the per-case numbers, as the results page shows them"
    assert "2 rounds of 4 combinations" in where, "the plan's words"
    assert "Finished." in where, "the status in words"
    assert "neighbours" in where, "what changed"
    db = sqlite3.connect(root / "preview" / "tables.sqlite")
    coordinates = [row for row in db.execute("SELECT x, y FROM stops").fetchall()]
    db.close()
    assert not [v for pair in coordinates for v in pair if f"{v:.4f}" in seen["prompt"]], "still never a row"
    assert "When the consultant asks a question" in assistant.SYSTEM and "return no cards" in assistant.SYSTEM


def test_the_context_names_the_screen_and_marks_log_scale_settings(study):
    home, root = study
    from evolvekit.harness.manifest import load_harness

    sent = assistant.context(root, load_study(root), load_harness(root / "harness"), screen="step-4")
    where = sent.split("## Where the consultant is\n")[1]
    assert where.startswith("Screen: step-4") and "held-back" not in where, "no run: no run section"
    catalogues = json.loads(sent.split("## Catalogues\n")[1].split("\n\n## ")[0])
    assert catalogues["settings"]["restart_after"]["log"] is True
    assert "log" not in catalogues["settings"]["neighbours"]


def test_the_api_passes_the_screen_and_the_run_to_the_assistant(study):
    home, root = study
    run = _finished_run(root)
    server = AppServer(home.root, port=0)
    server.start()
    try:
        provider = FakeProvider(responses=[json.dumps({"say": "Three cases.", "cards": []})])
        server.assistant_provider = provider
        answer = _asked(server, root.name, "Why?", screen="results", run=run)
        assert answer["state"] == "done" and answer["say"] == "Three cases." and answer["cards"] == []
        prompt = provider.calls[0]["messages"][1]["content"]
        assert "Screen: results" in prompt and "held-back" in prompt
    finally:
        server.stop()


WHOLE = [{"kind": "goal", "levels": [{"kpi": "late", "direction": "lower"}]},
         {"kind": "guardrail", "kpi": "late", "max": 0},
         {"kind": "kpi", "name": "late", "says": "late stops", "direction": "lower", "sql": "SELECT COUNT(*) FROM stops WHERE 0"}]
"""An answer whose goal and guardrail name the measure it proposes: it only
applies as a whole when the measure goes in first, whatever the model's order."""


def test_an_answer_applies_as_a_whole_in_the_right_order(study):
    _, root = study
    done = assistant.apply_cards(root, WHOLE)
    assert [c["ok"] for c in done] == [True, True, True], [c["problem"] for c in done]
    assert [c["kind"] for c in done] == ["goal", "guardrail", "kpi"], "the answer comes back in its own order"
    studied = load_study(root)
    assert "late" in studied.kpis and studied.goal[0].kpi == "late" and studied.guardrails[0].kpi == "late"
    assert done[2]["value"] == 0, "each card is checked on the preview as it goes in"


def test_a_card_that_fails_in_the_list_leaves_the_others_applied(study):
    _, root = study
    cards = [{"kind": "guardrail", "kpi": "nobody", "max": 1}, *WHOLE]
    done = assistant.apply_cards(root, cards)
    assert [c["ok"] for c in done] == [False, True, True, True]
    assert "'nobody' is not a KPI" in done[0]["problem"]
    studied = load_study(root)
    assert "late" in studied.kpis and studied.goal[0].kpi == "late" and [g.kpi for g in studied.guardrails] == ["late"]
    done = assistant.apply_cards(root, [{"kind": "wrong"}, WHOLE[2]])
    assert done[0]["ok"] is False and done[1]["ok"] is False and "not taken" in done[1]["problem"], "the second time, the name is taken"


def test_the_api_applies_a_list_of_cards_and_records_their_fate(study):
    home, root = study
    server = AppServer(home.root, port=0)
    server.start()
    try:
        slug = root.name
        server.assistant_provider = FakeProvider(responses=[json.dumps({"say": "Late stops.", "cards": WHOLE})])
        turn = _asked(server, slug, "Count late stops")["turn"]
        bad = {"kind": "guardrail", "kpi": "nobody", "max": 1, "index": 7}
        posted = [bad, *({**card, "index": i} for i, card in enumerate(WHOLE))]
        status, body = call(server, "POST", f"/api/studies/{slug}/cards", {"cards": posted, "turn": turn})
        assert status == 200, body
        assert [c["ok"] for c in body["cards"]] == [False, True, True, True] and body["cards"][0]["problem"]
        assert body["study"]["study"]["goal"]["levels"][0]["kpi"] == "late" and "late" in body["study"]["study"]["kpis"]
        status, history = call(server, "GET", f"/api/studies/{slug}/assistant")
        assert [c["state"] for c in history["turns"][1]["cards"]] == ["applied", "applied", "applied"], "the fate of each applied card is kept"
        # The single form is as it was.
        status, body = call(server, "POST", f"/api/studies/{slug}/cards", {"card": GOOD["cards"][3]})
        assert status == 200 and body["card"]["ok"] and "study" in body
        assert call(server, "POST", f"/api/studies/{slug}/cards", {"cards": "no"})[0] == 400
    finally:
        server.stop()
