"""Request shaping and usage parsing for all four backends. No network, ever."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from evolvekit.config import ModelConfig
from evolvekit.providers import build_provider
from evolvekit.providers.azure import AzureProvider
from evolvekit.providers.base import Completion, Provider, split_system
from evolvekit.providers.claude_cli import ClaudeCliProvider, parse_cli_json
from evolvekit.providers.fake import FakeProvider
from evolvekit.providers.openrouter import DEFAULT_BASE_URL, OpenRouterProvider

MESSAGES = [
    {"role": "system", "content": "you are an optimiser"},
    {"role": "user", "content": "improve this"},
]


class RecordingChat:
    """Stands in for `client.chat.completions`."""

    def __init__(self, response, *, fail_on=None):
        self.response = response
        self.calls = []
        self.fail_on = fail_on

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_on and self.fail_on in kwargs:
            raise TypeError(f"unexpected keyword argument {self.fail_on!r}")
        return self.response


def make_client(response, **kwargs):
    chat = RecordingChat(response, **kwargs)
    return SimpleNamespace(chat=SimpleNamespace(completions=chat)), chat


def chat_response(text="new code", prompt=11, completion=7, model="gpt-x"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion),
        model=model,
    )


# -- azure -----------------------------------------------------------------


def test_azure_shapes_the_request_and_parses_usage():
    client, chat = make_client(chat_response())
    provider = AzureProvider(client=client)
    result = provider.complete(MESSAGES, model="dep-1", max_tokens=999, temperature=0.4)

    assert len(chat.calls) == 1
    sent = chat.calls[0]
    assert sent["model"] == "dep-1"
    assert sent["messages"] == MESSAGES  # system message stays inline
    assert sent["temperature"] == 0.4
    assert sent["max_completion_tokens"] == 999

    assert isinstance(result, Completion)
    assert (result.text, result.input_tokens, result.output_tokens) == ("new code", 11, 7)
    assert result.provider == "azure" and result.model == "gpt-x"
    assert result.usd is None  # priced from the config table, not the response


def test_azure_falls_back_to_max_tokens_exactly_once():
    client, chat = make_client(chat_response(), fail_on="max_completion_tokens")
    # The retry re-issues without the new kwarg; a second TypeError would escape.
    chat.fail_on = "max_completion_tokens"
    provider = AzureProvider(client=client)
    provider.complete(MESSAGES, model="dep-1", max_tokens=64, temperature=1.0)
    assert len(chat.calls) == 2
    assert "max_completion_tokens" in chat.calls[0]
    assert chat.calls[1]["max_tokens"] == 64


def test_azure_empty_choices_is_a_provider_error():
    from evolvekit.providers.base import ProviderError

    client, _ = make_client(SimpleNamespace(choices=[], usage=None, model="m"))
    with pytest.raises(ProviderError, match="no choices"):
        AzureProvider(client=client).complete(
            MESSAGES, model="m", max_tokens=8, temperature=1.0
        )


def test_azure_missing_env_is_reported_clearly(monkeypatch):
    from evolvekit.providers.base import ProviderError

    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="AZURE_OPENAI_ENDPOINT"):
        AzureProvider()


# -- openrouter ------------------------------------------------------------


def test_openrouter_uses_max_tokens_and_parses_usage():
    client, chat = make_client(chat_response(model="anthropic/claude"))
    provider = OpenRouterProvider(client=client)
    result = provider.complete(
        MESSAGES, model="anthropic/claude", max_tokens=128, temperature=0.9
    )
    sent = chat.calls[0]
    assert sent["max_tokens"] == 128
    assert "max_completion_tokens" not in sent
    assert result.provider == "openrouter"
    assert result.input_tokens == 11 and result.output_tokens == 7


def test_openrouter_default_base_url_is_the_public_endpoint():
    assert DEFAULT_BASE_URL == "https://openrouter.ai/api/v1"


def test_openrouter_missing_key(monkeypatch):
    from evolvekit.providers.base import ProviderError

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="OPENROUTER_API_KEY"):
        OpenRouterProvider()


# -- claude-cli ------------------------------------------------------------

CLI_PAYLOAD = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "OK",
    "session_id": "abc",
    "num_turns": 1,
    "total_cost_usd": 0.0514,
    "usage": {
        "input_tokens": 10,
        "cache_creation_input_tokens": 25569,
        "cache_read_input_tokens": 0,
        "output_tokens": 50,
    },
    "modelUsage": {"claude-haiku-4-5-20251001": {"costUSD": 0.0514}},
}


def test_claude_cli_parses_the_real_payload_shape():
    result = parse_cli_json(json.dumps(CLI_PAYLOAD), fallback_model="haiku")
    assert result.text == "OK"
    # Cached tokens are billed, so they count towards the token cap.
    assert result.input_tokens == 10 + 25569
    assert result.output_tokens == 50
    assert result.usd == pytest.approx(0.0514)
    assert result.model == "claude-haiku-4-5-20251001"
    assert result.provider == "claude-cli"


def test_claude_cli_reports_backend_errors():
    from evolvekit.providers.base import ProviderError

    payload = dict(CLI_PAYLOAD, is_error=True, subtype="error_max_turns")
    with pytest.raises(ProviderError, match="error_max_turns"):
        parse_cli_json(json.dumps(payload), fallback_model="haiku")


def test_claude_cli_rejects_non_json_stdout():
    from evolvekit.providers.base import ProviderError

    with pytest.raises(ProviderError, match="not JSON"):
        parse_cli_json("Usage: claude [options]", fallback_model="haiku")


def test_claude_cli_rejects_empty_result():
    from evolvekit.providers.base import ProviderError

    with pytest.raises(ProviderError, match="no 'result' text"):
        parse_cli_json(json.dumps(dict(CLI_PAYLOAD, result="")), fallback_model="h")


def test_claude_cli_builds_a_non_interactive_argv():
    provider = ClaudeCliProvider(runner=lambda *a, **k: None, executable="claude")
    provider._resolve = lambda: "claude"  # type: ignore[method-assign]
    argv = provider.build_argv(model="sonnet", system="be terse")
    assert argv[0] == "claude"
    for flag in ("-p", "--output-format", "json", "--max-turns", "1"):
        assert flag in argv
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert argv[argv.index("--system-prompt") + 1] == "be terse"
    assert "--no-session-persistence" in argv


def test_claude_cli_sends_the_prompt_on_stdin_not_argv():
    seen = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        seen["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(argv, 0, json.dumps(CLI_PAYLOAD), "")

    provider = ClaudeCliProvider(runner=runner, executable="claude")
    provider._resolve = lambda: "claude"  # type: ignore[method-assign]
    result = provider.complete(MESSAGES, model="sonnet", max_tokens=64, temperature=1.0)

    assert result.text == "OK"
    assert seen["input"] == "improve this"  # system went to --system-prompt
    assert "improve this" not in " ".join(seen["argv"])


def test_claude_cli_non_zero_exit_is_a_provider_error():
    from evolvekit.providers.base import ProviderError

    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "not logged in")

    provider = ClaudeCliProvider(runner=runner, executable="claude")
    provider._resolve = lambda: "claude"  # type: ignore[method-assign]
    with pytest.raises(ProviderError, match="not logged in"):
        provider.complete(MESSAGES, model="sonnet", max_tokens=8, temperature=1.0)


def test_claude_cli_timeout_is_a_provider_error():
    from evolvekit.providers.base import ProviderError

    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1.0)

    provider = ClaudeCliProvider(runner=runner, executable="claude", timeout=1.0)
    provider._resolve = lambda: "claude"  # type: ignore[method-assign]
    with pytest.raises(ProviderError, match="timed out"):
        provider.complete(MESSAGES, model="sonnet", max_tokens=8, temperature=1.0)


# -- fake ------------------------------------------------------------------


def test_fake_replays_in_order_then_cycles():
    provider = FakeProvider(["one", "two"])
    texts = [
        provider.complete(MESSAGES, model="m", max_tokens=8, temperature=1.0).text
        for _ in range(3)
    ]
    assert texts == ["one", "two", "one"]
    assert len(provider.calls) == 3
    assert provider.calls[0]["model"] == "m"


def test_fake_without_cycling_raises_when_exhausted():
    from evolvekit.providers.base import ProviderError

    provider = FakeProvider(["only"], cycle=False)
    provider.complete(MESSAGES, model="m", max_tokens=8, temperature=1.0)
    with pytest.raises(ProviderError, match="script exhausted"):
        provider.complete(MESSAGES, model="m", max_tokens=8, temperature=1.0)


def test_fake_loads_responses_from_a_yaml_file(tmp_path):
    path = tmp_path / "replies.yaml"
    path.write_text("- alpha\n- beta\n", encoding="utf-8")
    provider = FakeProvider(base_dir=tmp_path, responses_path="replies.yaml")
    assert provider.complete(MESSAGES, model="m", max_tokens=8, temperature=1.0).text == "alpha"


def test_fake_requires_a_script():
    from evolvekit.providers.base import ProviderError

    with pytest.raises(ProviderError, match="no scripted responses"):
        FakeProvider()


# -- keyed responses -------------------------------------------------------


def test_a_keyed_response_answers_the_call_it_names():
    provider = FakeProvider(
        [
            {"when": "big step", "response": "the big answer"},
            "queued one",
            "queued two",
        ]
    )
    assert _reply(provider, "make a small edit") == "queued one"
    assert _reply(provider, "this is a scheduled big step") == "the big answer"
    assert _reply(provider, "make another edit") == "queued two"


def test_keyed_responses_are_consumed_in_order_then_the_last_one_repeats():
    provider = FakeProvider(
        [
            {"when": "retry", "response": "first"},
            {"when": "retry", "response": "second"},
            "queued",
        ]
    )
    assert [_reply(provider, "retry please") for _ in range(3)] == [
        "first",
        "second",
        "second",
    ]


def test_the_first_matching_key_wins_so_order_disambiguates():
    provider = FakeProvider(
        [
            {"when": "identical", "response": "retry answer"},
            {"when": "Combine", "response": "crossover answer"},
            "queued",
        ]
    )
    prompt = "Combine the two blocks. Your last answer was identical to x."
    assert _reply(provider, prompt) == "retry answer"


def test_a_script_of_nothing_but_unmatched_keys_is_an_error():
    from evolvekit.providers.base import ProviderError

    provider = FakeProvider([{"when": "never", "response": "x"}])
    with pytest.raises(ProviderError, match="none of them matched"):
        _reply(provider, "something else")


def test_a_keyed_entry_needs_a_when_and_a_response():
    from evolvekit.providers.base import ProviderError

    with pytest.raises(ProviderError, match="`when` string"):
        FakeProvider([{"response": "x"}])
    with pytest.raises(ProviderError, match="no `response`"):
        FakeProvider([{"when": "x"}])
    with pytest.raises(ProviderError, match="unknown key"):
        FakeProvider([{"when": "x", "response": "y", "temperature": 1}])


def _reply(provider: FakeProvider, prompt: str) -> str:
    return provider.complete(
        [{"role": "user", "content": prompt}],
        model="m",
        max_tokens=16,
        temperature=1.0,
    ).text


# -- registry --------------------------------------------------------------


def test_build_provider_returns_the_named_backend(tmp_path):
    cfg = ModelConfig.parse(
        {"provider": "fake", "model": "m", "options": {"responses": ["x"]}}, "small"
    )
    provider = build_provider(cfg, base_dir=tmp_path)
    assert isinstance(provider, FakeProvider)
    assert isinstance(provider, Provider)  # satisfies the structural protocol


def test_split_system_separates_the_stable_prefix():
    system, rest = split_system(MESSAGES)
    assert system == "you are an optimiser"
    assert [m["role"] for m in rest] == ["user"]
