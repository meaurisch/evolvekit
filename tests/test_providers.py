"""Request shaping and usage parsing for all four backends. No network, ever."""

from __future__ import annotations

import json
import subprocess
import sys
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


# -- copilot-cli -----------------------------------------------------------


def _copilot(stdout="OK", *, returncode=0, stderr="", seen=None, **kwargs):
    """A CopilotCliProvider whose runner records the call and answers `stdout`."""
    from evolvekit.providers.copilot_cli import CopilotCliProvider

    def runner(argv, **options):
        if seen is not None:
            seen["argv"], seen["options"] = argv, options
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)

    kwargs.setdefault("windows", False)
    provider = CopilotCliProvider(runner=runner, executable="copilot", **kwargs)
    provider._resolve = lambda: (["copilot"], False)  # type: ignore[method-assign]
    return provider


def test_copilot_cli_builds_a_non_interactive_argv_with_the_system_prompt_folded_in():
    seen: dict = {}
    result = _copilot(seen=seen).complete(MESSAGES, model="claude-sonnet-4.5", max_tokens=64, temperature=1.0)
    argv = seen["argv"]
    assert argv[:2] == ["copilot", "-p"]
    assert argv[2] == "you are an optimiser\n\nimprove this", "the CLI has no system-prompt flag"
    for flag in ("-s", "--no-ask-user", "--deny-tool=shell", "--deny-tool=write", "--model=claude-sonnet-4.5"):
        assert flag in argv
    assert not any(a.startswith("--allow") for a in argv), "no tool is ever allowed"
    assert seen["options"]["env"]["COPILOT_AUTO_UPDATE"] == "false"
    assert seen["options"]["stdin"] is subprocess.DEVNULL
    assert result.text == "OK" and result.provider == "copilot-cli"
    assert result.usd == 0.0 and result.requests == 1
    assert result.input_tokens > 0 and result.output_tokens > 0, "estimated when the CLI reports none"


def test_copilot_cli_leaves_the_model_to_the_cli_when_it_is_empty():
    seen: dict = {}
    _copilot(seen=seen).complete(MESSAGES, model="", max_tokens=8, temperature=1.0)
    assert not any(a.startswith("--model") for a in seen["argv"])


def test_copilot_cli_names_the_roles_of_a_longer_conversation():
    from evolvekit.providers.copilot_cli import CopilotCliProvider

    prompt = CopilotCliProvider.build_prompt(
        [{"role": "system", "content": "S"}, {"role": "user", "content": "a"},
         {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}]
    )
    assert prompt == "S\n\n[user]\na\n\n[assistant]\nb\n\n[user]\nc"


def test_copilot_cli_asks_for_json_and_reads_jsonl_tolerantly():
    seen: dict = {}
    lines = [
        {"type": "session.start", "data": {"model": "gpt-5"}},
        {"type": "user.message", "data": {"content": "improve this"}},
        {"type": "assistant.message_delta", "data": {"content": "O"}},
        {"type": "assistant.message", "data": {"content": "thinking aloud"}},
        {"type": "assistant.message", "data": {"content": "OK", "model": "gpt-5"}},
        {"type": "result", "usage": {"premiumRequests": 2, "inputTokens": 120, "outputTokens": 7}},
    ]
    stdout = "\n".join(json.dumps(line) for line in lines) + "\n"
    result = _copilot(stdout, seen=seen, output_format="json").complete(MESSAGES, model="", max_tokens=8, temperature=1.0)
    assert "--output-format=json" in seen["argv"] and "-s" not in seen["argv"]
    assert result.text == "OK", "the last whole assistant message is the answer"
    assert result.requests == 2 and result.usd == 0.0
    assert (result.input_tokens, result.output_tokens) == (120, 7)
    assert result.model == "gpt-5"


def test_copilot_cli_reads_plain_text_and_never_parses_an_answer_that_is_json():
    from evolvekit.providers.copilot_cli import parse_copilot_output

    answer = '{"say": "hi", "cards": []}'
    assert parse_copilot_output(answer, prompt="p", model="m").text == answer, "-s output is the answer itself"
    assert parse_copilot_output(answer, prompt="p", model="m", jsonl=True).text == answer, "no answer in it: text"
    mixed = parse_copilot_output('{"say": "hi"}\nand a second line', prompt="p", model="m", jsonl=True)
    assert mixed.text == '{"say": "hi"}\nand a second line' and mixed.requests == 1 and mixed.model == "m"


def test_copilot_cli_with_no_output_gave_no_answer():
    from evolvekit.providers.copilot_cli import CopilotCliError

    with pytest.raises(CopilotCliError, match="gave no answer") as caught:
        _copilot("  \n").complete(MESSAGES, model="", max_tokens=8, temperature=1.0)
    assert caught.value.kind == "failed"


@pytest.mark.parametrize(
    ("stderr", "kind", "words"),
    [
        ("Error: You are not logged in. Run `copilot` and use /login.", "not_signed_in", "not signed in"),
        ("Failed to authenticate with GitHub", "not_signed_in", "/login"),
        ("Error: Access denied by policy settings", "policy_blocked", "policy blocks the CLI"),
        ("warming up\nError: model gpt-99 is not available\n", "failed", "model gpt-99 is not available"),
        ("", "failed", "no output"),
    ],
)
def test_copilot_cli_failures_say_what_kind_they_are(stderr, kind, words):
    from evolvekit.providers.base import ProviderError
    from evolvekit.providers.copilot_cli import CopilotCliError

    provider = _copilot("", returncode=1, stderr=stderr)
    with pytest.raises(CopilotCliError, match=words) as caught:
        provider.complete(MESSAGES, model="", max_tokens=8, temperature=1.0)
    assert caught.value.kind == kind
    assert isinstance(caught.value, ProviderError), "the search loop's handling still applies"


def test_copilot_cli_sign_in_trouble_on_a_clean_exit_is_still_not_signed_in():
    from evolvekit.providers.copilot_cli import CopilotCliError

    with pytest.raises(CopilotCliError) as caught:
        _copilot("", stderr="Please run /login").complete(MESSAGES, model="", max_tokens=8, temperature=1.0)
    assert caught.value.kind == "not_signed_in"


def test_copilot_cli_that_is_not_installed_is_missing(monkeypatch):
    from evolvekit.providers import copilot_cli

    monkeypatch.setattr(copilot_cli.shutil, "which", lambda name: None)
    provider = copilot_cli.CopilotCliProvider(executable="no-such-copilot-anywhere", windows=False)
    with pytest.raises(copilot_cli.CopilotCliError, match="npm install -g @github/copilot") as caught:
        provider.complete(MESSAGES, model="", max_tokens=8, temperature=1.0)
    assert caught.value.kind == "missing"

    def gone(argv, **options):
        raise FileNotFoundError(argv[0])

    vanished = _copilot()
    vanished._runner = gone
    with pytest.raises(copilot_cli.CopilotCliError) as caught:
        vanished.complete(MESSAGES, model="", max_tokens=8, temperature=1.0)
    assert caught.value.kind == "missing"


def test_copilot_cli_timeout_is_a_failure():
    from evolvekit.providers.copilot_cli import CopilotCliError

    def runner(argv, **options):
        raise subprocess.TimeoutExpired(argv, 1.0)

    provider = _copilot(timeout=1.0)
    provider._runner = runner
    with pytest.raises(CopilotCliError, match="timed out") as caught:
        provider.complete(MESSAGES, model="", max_tokens=8, temperature=1.0)
    assert caught.value.kind == "failed"


def test_copilot_cli_path_overrides_the_binary(monkeypatch):
    from evolvekit.providers.copilot_cli import CopilotCliProvider

    monkeypatch.setenv("COPILOT_CLI_PATH", "C:/tools/copilot.exe")
    assert CopilotCliProvider()._executable == "C:/tools/copilot.exe"


def _npm_tree(tmp_path, bin_field):
    """npm's global layout: `<prefix>/copilot.cmd` and `<prefix>/node_modules/@github/copilot/`."""
    prefix = tmp_path / "npm"
    package = prefix / "node_modules" / "@github" / "copilot"
    package.mkdir(parents=True)
    (package / "npm-loader.js").write_text("// entry", encoding="utf-8")
    (package / "package.json").write_text(json.dumps({"name": "@github/copilot", "bin": bin_field}), encoding="utf-8")
    shim = prefix / "copilot.cmd"
    shim.write_text("@echo off\r\nnode npm-loader.js %*\r\n", encoding="utf-8")
    return shim


@pytest.mark.parametrize("bin_field", ["npm-loader.js", {"copilot": "npm-loader.js"}])
def test_copilot_cli_runs_the_package_script_behind_the_npm_shim(tmp_path, monkeypatch, bin_field):
    from evolvekit.providers import copilot_cli

    shim = _npm_tree(tmp_path, bin_field)
    node = tmp_path / "nodejs" / "node.exe"
    node.parent.mkdir()
    node.write_text("", encoding="utf-8")
    monkeypatch.setattr(copilot_cli.shutil, "which",
                        lambda name: str(shim) if name == "copilot" else str(node) if name == "node" else None)
    prefix, through_cmd = copilot_cli.resolve_command("copilot")
    entry = shim.parent / "node_modules" / "@github" / "copilot" / "npm-loader.js"
    assert prefix == [str(node), str(entry.resolve())] and through_cmd is False

    (shim.parent / "node.exe").write_text("", encoding="utf-8")
    prefix, _ = copilot_cli.resolve_command("copilot")
    assert prefix[0] == str(shim.parent / "node.exe"), "the node next to the shim first, as npm's shim does"

    seen: dict = {}

    def runner(argv, **options):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "OK", "")

    provider = copilot_cli.CopilotCliProvider(runner=runner, executable="copilot", windows=True)
    provider.complete([{"role": "user", "content": 'say "hi" & echo ^%PATH%'}], model="", max_tokens=8, temperature=1.0)
    assert seen["argv"][:4] == [str(shim.parent / "node.exe"), str(entry.resolve()), "-p", 'say "hi" & echo ^%PATH%']


def test_copilot_cli_never_hands_a_prompt_to_a_shim_it_cannot_see_behind(tmp_path, monkeypatch):
    from evolvekit.providers import copilot_cli

    shim = tmp_path / "copilot.cmd"
    shim.write_text("@echo off", encoding="utf-8")
    monkeypatch.setattr(copilot_cli.shutil, "which", lambda name: str(shim) if name == "copilot" else None)
    assert copilot_cli.resolve_command("copilot") == ([str(shim)], True)

    def runner(argv, **options):
        raise AssertionError("cmd.exe must never be given a prompt")

    provider = copilot_cli.CopilotCliProvider(executable="copilot", windows=True, runner=runner)
    with pytest.raises(copilot_cli.CopilotCliError, match="COPILOT_CLI_PATH"):
        provider.complete(MESSAGES, model="", max_tokens=8, temperature=1.0)


def test_copilot_cli_refuses_a_prompt_too_long_for_windows():
    from evolvekit.providers.copilot_cli import MAX_DIRECT_COMMAND, CopilotCliError

    provider = _copilot(windows=True)
    long = [{"role": "user", "content": "x" * (MAX_DIRECT_COMMAND + 1)}]
    with pytest.raises(CopilotCliError, match="too long") as caught:
        provider.complete(long, model="", max_tokens=8, temperature=1.0)
    assert caught.value.kind == "failed"
    fits = provider.complete([{"role": "user", "content": "x" * 12_000}], model="", max_tokens=8, temperature=1.0)
    assert fits.text == "OK", "a 12k big-step prompt fits"
    assert _copilot(windows=False).complete(long, model="", max_tokens=8, temperature=1.0).text == "OK"


def test_copilot_cli_has_no_embeddings_and_is_built_from_config():
    from evolvekit.providers.base import ProviderError
    from evolvekit.providers.copilot_cli import CopilotCliProvider

    provider = build_provider(ModelConfig(role="strong", provider="copilot-cli", model="", price_in_per_mtok=0.0,
                                          price_out_per_mtok=0.0, max_tokens=8, temperature=1.0))
    assert isinstance(provider, CopilotCliProvider) and isinstance(provider, Provider)
    with pytest.raises(ProviderError, match="no embeddings"):
        provider.embed(["x"], model="m")


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


def test_openrouter_asks_for_the_cost_and_reads_it():
    response = chat_response(model="anthropic/claude")
    response.usage.cost = 0.0123
    client, chat = make_client(response)
    result = OpenRouterProvider(client=client).complete(
        MESSAGES, model="anthropic/claude", max_tokens=128, temperature=0.9
    )
    assert chat.calls[0]["extra_body"] == {"usage": {"include": True}}
    assert result.usd == 0.0123
    unpriced, _ = make_client(chat_response())
    result = OpenRouterProvider(client=unpriced).complete(
        MESSAGES, model="m", max_tokens=1, temperature=0.0
    )
    assert result.usd is None


@pytest.mark.parametrize("cls", [OpenRouterProvider, AzureProvider])
def test_json_mode_is_passed_through_where_the_api_has_it(cls):
    client, chat = make_client(chat_response())
    provider = cls(client=client)
    provider.complete(
        MESSAGES, model="m", max_tokens=9, temperature=0.1,
        response_format={"type": "json_object"},
    )
    assert chat.calls[0]["response_format"] == {"type": "json_object"}
    provider.complete(MESSAGES, model="m", max_tokens=9, temperature=0.1)
    assert "response_format" not in chat.calls[1]


def test_json_mode_is_accepted_and_ignored_where_the_backend_has_none():
    fake = FakeProvider(responses=["x"])
    fake.complete(MESSAGES, model="m", max_tokens=9, temperature=0.1, response_format={"type": "json_object"})
    assert fake.calls[0]["response_format"] == {"type": "json_object"}
    # Any executable that exists will do: the argv is built, never run. CI has no `claude`.
    argv = ClaudeCliProvider(runner=lambda *a, **k: None, executable=sys.executable).build_argv(model="")
    assert "--model" not in argv, "an empty model is the CLI's default"
