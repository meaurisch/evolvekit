"""The assistant (`evolvekit/app/assistant.py`) against the `fake` provider:
cards checked on the preview, corrected at most twice, never sent a row."""

from __future__ import annotations

import json
import sqlite3
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
        status, answer = call(server, "POST", f"/api/studies/{slug}/assistant", {"message": "more"})
        assert status == 200 and answer["cards"]
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
