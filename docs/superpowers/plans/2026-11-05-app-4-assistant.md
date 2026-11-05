# App improvement 4: an assistant that is there everywhere and explains — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The assistant answers on every screen of a study, in plain words as well as with cards; its proposals are kept and their fate recorded; a whole answer applies in one click and in the right order; a key change works at once; every answer carries a real cost or says honestly that none is known; a question can be cancelled.

**Architecture:** `evolvekit/app/assistant.py` keeps the propose–check loop and gains a `screen` context (status, results, plan, failures), a card-event log in the same `assistant.jsonl`, and `apply_cards`. Asking becomes background work in `evolvekit/app/work.py` (like the preview), polled by the page, so a question survives a step change and can be cancelled. Providers of the OpenAI shape gain JSON mode and OpenRouter's reported cost. The page's panel becomes a drawer on every study screen; the redesign restyles it later.

**Tech stack:** Python ≥ 3.11 standard library, the `openai` SDK (already a dependency) for OpenRouter and Azure; pytest with the `fake` provider.

**Spec:** `~/.claude/plans/find-ways-to-significantly-hidden-sutherland.md`, proposal 4; handoff *What the app will do by then* §3.

## Global constraints

- Privacy as today: the model sees schema, catalogues, the study, summaries and now the run's numbers (status, results, per-case values); never a row, a name, an address or a coordinate.
- Every card is still checked on the preview before it is shown; Apply re-checks.
- Streaming is **not** built (decision: three providers, one of them a CLI; the background question with Cancel covers the wait). Say so in the PR.
- Branch `app/assistant` from `app/survives`, stacked; `python tasks.py check` green.

## Review focus

1. A key replaced in Settings while the app runs must be used by the next question, and a removed key must stop the assistant with the "needs a key" answer. Test in Task 1.
2. An answer whose goal names a measure proposed in the same answer must apply as a whole. Test in Task 4.
3. A question cancelled while the model thinks must leave no half entry that the next question replays. Test in Task 3.
4. A question from the Results screen must carry the run's final words and per-case numbers, and the model's plain answer must come back with no card. Test in Task 2.
5. A model with no known price and no reported cost must show "cost not known", and AI search help must refuse to start with a dollar cap it cannot enforce. Test in Task 1.

---

## Task 1: keys that work at once, and a cost that is real or absent

**Files:**
- Modify: `evolvekit/app/assistant.py:48, 398-416` (`PRICES`, `_provider`, `_cost`), `evolvekit/app/jobs.py:48, 139-157` (`PRICES`, `ai_models`), `:195-200` (`start`)
- Modify: `evolvekit/providers/openai_shape.py:214-240` (`completion_from_chat`), `evolvekit/providers/openrouter.py:67-82` (`complete`)
- Modify: `evolvekit/app/store.py:111-146` (`settings`, `save_settings`), `evolvekit/app/static/app.html:560-600` (Settings)
- Test: `tests/test_app_assistant.py`, `tests/test_app_store.py`, `tests/test_providers_openrouter.py` (or the existing provider test file)

**Interfaces:**
- Produces: `assistant._provider(home)` sets `os.environ[key]` from `.env` for every key the app keeps (the app's file wins) and deletes from `os.environ` any such key that the app set earlier and `.env` no longer has (module-level `_SET_BY_APP: set[str]`). One `PRICES` table in `evolvekit/app/prices.py`: `price_of(model: str) -> tuple[float, float] | None`, seeded with `anthropic/claude-sonnet-5: (2.0, 10.0)`, plus `settings.yaml` `prices: {model: [in, out]}` merged by `Home.settings()`. `_cost(completion, config) -> float | None` (`None` when neither the provider nor the table knows). `history()["total_usd"]` sums known costs and gains `"unknown": int` (answers with no known cost). OpenRouter `complete` sends `extra_body={"usage": {"include": True}}` and `completion_from_chat` reads `usage.cost` into `Completion.usd`. `jobs.ai_models` returns prices from `price_of`; `jobs.start` refuses (409) `AI search help is on with a $N cap, but the cost of <model> is not known, so the cap cannot hold: add its price under Settings › Models, or turn AI help off` when `price_of` is `None` and the provider is not `openrouter` or `claude-cli` (both report cost). Settings: an empty key field removes the key; the model field resets to the provider's default when the provider changes (`openrouter`: `anthropic/claude-sonnet-5`; `azure`: empty, labelled *deployment name*; `claude-cli`: empty, *the CLI's default*); under *Models* a price pair per model (expert).

- [ ] **Step 1: Failing tests**

```python
def test_a_key_changed_in_settings_is_used_at_once(home, monkeypatch):
    home.set_keys({"OPENROUTER_API_KEY": "first"}); assistant._provider(home)
    assert os.environ["OPENROUTER_API_KEY"] == "first"
    home.set_keys({"OPENROUTER_API_KEY": "second"}); assistant._provider(home)
    assert os.environ["OPENROUTER_API_KEY"] == "second"
    home.set_keys({"OPENROUTER_API_KEY": ""})
    assert "OPENROUTER_API_KEY" not in os.environ or os.environ["OPENROUTER_API_KEY"] != "second"

def test_a_cost_is_real_or_absent():
    assert _cost(Completion(text="", input_tokens=1000, output_tokens=0, model="m", provider="p", usd=0.0123), None) == 0.0123
    assert _cost(Completion(text="", input_tokens=1_000_000, output_tokens=0, model="anthropic/claude-sonnet-5", provider="openrouter"), config_for("anthropic/claude-sonnet-5")) == 2.0
    assert _cost(Completion(text="", input_tokens=1000, output_tokens=0, model="my-deployment", provider="azure"), config_for("my-deployment")) is None

def test_openrouter_asks_for_the_cost_and_reads_it(fake_openai_client): ...   # extra_body carries usage.include; usage.cost -> Completion.usd

def test_ai_search_help_refuses_a_cap_it_cannot_hold(app): ...   # provider azure, model "dep", budget.ai enabled, start -> 409 with the sentence above
```

- [ ] **Step 2: Run to fail.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Run** `pytest tests/test_app_assistant.py tests/test_app_store.py tests/test_providers*.py tests/test_app_jobs.py -q`. — [ ] **Step 5: Commit** `assistant: a changed key works at once; a cost is real or said to be unknown`.

## Task 2: plain answers, on every screen, with the run in view

**Files:**
- Modify: `evolvekit/app/assistant.py:56-95` (`SYSTEM`), `:170-205` (`context`), `:420-430` (`ask` signature), `evolvekit/app/api.py:421-424`, `evolvekit/app/static/app.html:727-735, 1633-1700` (the panel on every study screen)
- Test: `tests/test_app_assistant.py`

**Interfaces:**
- Produces: `assistant.context(root, study, harness, *, screen: str = "", run: str | None = None, home=None) -> str` adds `## Where the consultant is` with the screen name and, when `run` is given, `jobs.status(...)["sentence"|"best"|"failures"|"busy_line"]`, `jobs.results(...)["headline"|"final"|"by_case"|"changes"]` (numbers and words only), the plan's words, the test run's failures and the preview's error when there is one; the settings catalogue gains `"log": True` for log-scale settings. `SYSTEM` gains: *When the consultant asks a question rather than for a change, answer it in `say` in two to five plain sentences and return no cards. Explain a verdict with its numbers: the chance, the worst case, which cases went which way.* `ask(home, root, message, provider=None, *, screen="", run=None)`; `POST /api/studies/{slug}/assistant` body `{message, screen, run}`. Page: `assistantPanel()` is rendered on steps 1–7, Running and Results as a `<details class="side">` drawer, open by default on steps 4–5, closed elsewhere; it passes `screen` (`step-4`, `running`, `results`) and the run.

- [ ] **Step 1: Failing test** (fake provider, a finished demo run from `tests/test_app_jobs.py`'s fixtures or a run directory copied into `tests/fixtures/`)

```python
def test_a_question_on_the_results_screen_sees_the_verdict_and_gets_a_plain_answer(app_with_finished_run):
    seen = {}
    provider = FakeProvider(reply=lambda messages: seen.update(prompt=messages[1]["content"]) or '{"say": "Only 3 cases were held back, so the range is wide.", "cards": []}')
    answer = assistant.ask(home, root, "Why is this not proven?", provider=provider, screen="results", run=run)
    assert answer["cards"] == [] and answer["say"].startswith("Only 3")
    assert "## Where the consultant is" in seen["prompt"] and "results" in seen["prompt"]
    assert "held-back" in seen["prompt"] and "chance" in seen["prompt"]
```

- [ ] **Step 2: Run to fail.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Run** `pytest tests/test_app_assistant.py -q`; open the app, ask on Results, see the drawer. — [ ] **Step 5: Commit** `assistant: on every screen, answering questions in plain words with the run in view`.

## Task 3: a question in the background, cancellable, with its cards kept

**Files:**
- Modify: `evolvekit/app/work.py` (new `start_ask`, `ask_state`, `cancel_ask`), `evolvekit/app/assistant.py` (`history` folds card events; `_answered` marks applied cards), `evolvekit/app/api.py` (routes), `evolvekit/app/static/app.html` (the panel polls, Cancel, cards from history)
- Test: `tests/test_app_assistant.py`, `tests/test_app_jobs.py`

**Interfaces:**
- Produces: `POST /api/studies/{slug}/assistant` returns `{"turn": "<id>", "state": "thinking"}` at once (409 while a question of this study is still thinking); `GET …/assistant?turn=<id>` → `{"state": "thinking" | "done" | "cancelled", ...the answer when done}`; `POST …/assistant/cancel` → `{"cancelled": True}`: the thread's result is discarded, the history gets `{"role": "assistant", "cancelled": True}` after the user's entry (and `_answered` skips the pair). Every history entry gains `"id"`. `POST …/assistant/cards` `{turn, index, state: "applied" | "dismissed"}` appends `{"role": "card", "turn", "index", "state", "ts"}`; `history()` returns each assistant turn's `cards` with `"state"` (`proposed | applied | dismissed`). `_answered` replays applied cards with `"applied": true` inside the assistant JSON so the model knows. The page draws the last 6 turns with their cards and states, polls a thinking turn every 1.5 s, shows Cancel, and keeps going when the step changes (the drawer re-polls on open).

- [ ] **Step 1: Failing tests**

```python
def test_a_question_runs_in_the_background_and_can_be_cancelled(app, slow_fake_provider): ...   # POST -> thinking; cancel -> cancelled; GET history -> the pair is marked cancelled; a new question is allowed

def test_cards_keep_their_fate(app, fake_provider):
    ...  # ask -> done with 2 cards; POST cards {turn, 0, "applied"}; GET history -> cards[0].state == "applied", cards[1].state == "proposed"
    ...  # the next ask's replayed messages carry "applied": true on that card
```

- [ ] **Step 2: Run to fail.** — [ ] **Step 3: Implement** (`work.start_ask` follows `start_preview`'s shape: a thread, a state dict in `server.work["assistant:" + slug]`, a `threading.Event` for cancel checked after `provider.complete` returns). — [ ] **Step 4: Run** `pytest tests/test_app_assistant.py tests/test_app_jobs.py -q`. — [ ] **Step 5: Commit** `assistant: a question runs in the background, can be cancelled, and its cards are kept`.

## Task 4: apply a whole answer, in order; edit any card

**Files:**
- Modify: `evolvekit/app/assistant.py` (`apply_cards`), `evolvekit/app/api.py:426-438` (`add_card` takes `cards`), `evolvekit/app/static/app.html` (Apply all; Edit on every card)
- Test: `tests/test_app_assistant.py`

**Interfaces:**
- Produces: `assistant.apply_cards(root, cards: list[dict]) -> list[dict]`: orders by kind `kpi, weighted, setting, data_change, constraint, goal, guardrail` (stable within a kind), checks each with `_proposed(cards)` against the study as updated so far, applies the ones that pass, and returns every card with `ok` and `problem`; nothing is applied when the first card fails? No: each card is applied independently and the result says which were. `POST …/cards` accepts `{card}` (as today) or `{cards: [...]}` and returns `{cards: [...], study}`. Page: *Apply all* per answer; *Edit* on every card opens the card's fields inline (`low/high/start/value` for settings, `where/mode/low/high/start` for data changes, `expr/sql` for rules, `sql/rows_sql/direction/unit` for measures, `weighted` pairs, `levels`, `max/min`), then Apply re-checks.

- [ ] **Step 1: Failing test**

```python
def test_an_answer_applies_as_a_whole_in_the_right_order(study_root):
    cards = [{"kind": "goal", "levels": [{"kpi": "late", "direction": "lower"}]},
             {"kind": "guardrail", "kpi": "late", "max": 0},
             {"kind": "kpi", "name": "late", "says": "late stops", "direction": "lower", "sql": "SELECT COUNT(*) FROM stops WHERE 0"}]
    done = assistant.apply_cards(study_root, cards)
    assert [c["ok"] for c in done] == [True, True, True]
    study = load_study(study_root)
    assert "late" in study.kpis and study.goal[0].kpi == "late" and study.guardrails[0].kpi == "late"
```

- [ ] **Step 2: Run to fail.** — [ ] **Step 3: Implement.** — [ ] **Step 4: Run.** — [ ] **Step 5: Commit** `assistant: apply a whole answer in one click, and edit any card first`.

## Task 5: JSON mode where the provider has it

**Files:**
- Modify: `evolvekit/providers/base.py:50-63` (optional `response_format`), `evolvekit/providers/openrouter.py`, `evolvekit/providers/azure.py`, `evolvekit/app/assistant.py` (`ask` passes it)
- Test: the provider tests

**Interfaces:**
- Produces: `complete(..., response_format: dict | None = None)` on every provider (ignored by `claude-cli` and `fake`); `ask` passes `{"type": "json_object"}` for `openrouter` and `azure`; `_parse` stays as the fallback.

- [ ] Steps as above; commit `providers: JSON mode for the assistant where the API offers it`.

## Task 6: the gate, the docs and the PR

- [ ] `docs/app.md` *The assistant*: every screen, questions, kept proposals, Apply all, Cancel, cost known or not; `tasks.py check`; a walk through the app with the `fake` provider is not possible from the page, so test with a real key once if one is set, else rely on the tests; `gh pr create --base app/survives`, decisions in the body (no streaming and why; the cost rule), attribution last.
