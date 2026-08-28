"""The Azure OpenAI backend against recorded responses. Still no network, ever.

Azure is often the only route available in a locked-down corporate
environment, and it has never been exercised against a live endpoint from this
repository -- `test_providers.py` proves the request shape against a stub that
always succeeds. That is the wrong half of the contract to be confident about.
A run that dies four hours in dies on a 429, a content filter or a stale key,
and what matters then is that the message in the terminal names which of the
three it was.

So: the payloads below are shaped like the ones `openai.AzureOpenAI` actually
produces, error strings included, and every test here asserts on behaviour a
real endpoint would produce -- not on the stub.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from evolvekit.providers.azure import AzureOpenAIProvider, AzureProvider
from evolvekit.providers.base import Completion, ProviderError
from evolvekit.providers.openai_shape import (
    RETRY_ATTEMPTS,
    RETRY_BACKOFF_S,
    classify,
)

MESSAGES = [
    {"role": "system", "content": "you are an optimisation engineer"},
    {"role": "user", "content": "improve this heuristic"},
]


# --------------------------------------------------------------------------
# recorded shapes
# --------------------------------------------------------------------------


class RecordedAPIError(Exception):
    """The shape `openai` raises: a status, a code, and a response with headers."""

    def __init__(self, message, *, status_code=None, code=None, headers=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.response = SimpleNamespace(
            status_code=status_code, headers=headers or {}
        )


# Verbatim-shaped Azure error strings, trimmed. These are what shows up in a
# terminal at 3am, and they are the input `classify` has to work from.
RATE_LIMITED = RecordedAPIError(
    "Error code: 429 - {'error': {'code': '429', 'message': 'Requests to the "
    "ChatCompletions_Create Operation under Azure OpenAI API version "
    "2024-10-21 have exceeded token rate limit of your current OpenAI S0 "
    "pricing tier. Please retry after 30 seconds.'}}",
    status_code=429,
    headers={"retry-after": "30"},
)

CONTENT_FILTERED = RecordedAPIError(
    "Error code: 400 - {'error': {'message': \"The response was filtered due "
    "to the prompt triggering Azure OpenAI's content management policy.\", "
    "'code': 'content_filter', 'status': 400}}",
    status_code=400,
    code="content_filter",
)

BAD_KEY = RecordedAPIError(
    "Error code: 401 - {'error': {'code': '401', 'message': 'Access denied due "
    "to invalid subscription key or wrong API endpoint.'}}",
    status_code=401,
)

SERVER_ERROR = RecordedAPIError(
    "Error code: 503 - {'error': {'code': '503', 'message': 'The service is "
    "temporarily unavailable.'}}",
    status_code=503,
)


def chat_response(
    text="def priority(item, bins):\n    return [-(b - item) for b in bins]",
    *,
    prompt=1843,
    completion=57,
    model="gpt-4o-2024-08-06",
    finish_reason="stop",
):
    """The success shape, with Azure's per-choice `finish_reason`."""
    return SimpleNamespace(
        id="chatcmpl-recorded",
        model=model,
        choices=[
            SimpleNamespace(
                index=0,
                finish_reason=finish_reason,
                message=SimpleNamespace(role="assistant", content=text),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
        ),
    )


def embedding_response(vectors=((0.011, -0.023, 0.5),), prompt=7):
    return SimpleNamespace(
        object="list",
        model="text-embedding-3-small",
        data=[
            SimpleNamespace(object="embedding", index=i, embedding=list(v))
            for i, v in enumerate(vectors)
        ],
        usage=SimpleNamespace(prompt_tokens=prompt, total_tokens=prompt),
    )


class Endpoint:
    """Replays a script of responses/exceptions, recording what it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script[min(len(self.calls), len(self.script)) - 1]
        if isinstance(item, BaseException):
            raise item
        return item


def make_provider(chat=(), embeddings=(), sleep=None):
    chat_ep, embed_ep = Endpoint(chat), Endpoint(embeddings)
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=chat_ep), embeddings=embed_ep
    )
    slept: list[float] = []
    provider = AzureOpenAIProvider(client=client, sleep=sleep or slept.append)
    return provider, chat_ep, embed_ep, slept


# --------------------------------------------------------------------------
# the success shapes
# --------------------------------------------------------------------------


def test_the_class_is_reachable_under_both_names():
    assert AzureProvider is AzureOpenAIProvider


def test_a_recorded_chat_response_becomes_a_completion():
    provider, chat, _, _ = make_provider(chat=[chat_response()])
    result = provider.complete(
        MESSAGES, model="gpt-4o-prod", max_tokens=2048, temperature=0.7
    )

    assert isinstance(result, Completion)
    assert result.text.startswith("def priority")
    assert (result.input_tokens, result.output_tokens) == (1843, 57)
    assert result.total_tokens == 1900
    assert result.provider == "azure"
    # The response's own model id, not the deployment name we asked for --
    # which is how a run's ledger records what it was actually served.
    assert result.model == "gpt-4o-2024-08-06"
    assert result.usd is None  # Azure reports no cost; the price table prices it


def test_the_deployment_name_is_what_goes_on_the_wire():
    """`models.<role>.model` is a deployment, and Azure routes on it."""
    provider, chat, _, _ = make_provider(chat=[chat_response()])
    provider.complete(MESSAGES, model="my-gpt4o-deployment", max_tokens=64, temperature=1.0)
    assert chat.calls[0]["model"] == "my-gpt4o-deployment"
    assert chat.calls[0]["messages"] == MESSAGES
    assert chat.calls[0]["max_completion_tokens"] == 64


def test_an_older_api_version_falls_back_to_max_tokens_once():
    """The fallback survives the retry layer: a TypeError is not a fault."""

    class Picky(Endpoint):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            if "max_completion_tokens" in kwargs:
                raise TypeError(
                    "Completions.create() got an unexpected keyword argument "
                    "'max_completion_tokens'"
                )
            return chat_response()

    picky = Picky([])
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=picky), embeddings=Endpoint([])
    )
    provider = AzureOpenAIProvider(client=client, sleep=lambda _s: None)
    provider.complete(MESSAGES, model="dep", max_tokens=128, temperature=1.0)

    assert len(picky.calls) == 2
    assert picky.calls[1]["max_tokens"] == 128
    assert "max_completion_tokens" not in picky.calls[1]


def test_a_recorded_embedding_response_becomes_vectors_and_a_token_count():
    provider, _, embed, _ = make_provider(
        embeddings=[embedding_response(vectors=((0.1, 0.2), (0.3, 0.4)), prompt=11)]
    )
    vectors = provider.embed(["one", "two"], model="text-embedding-3-small")
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert provider.last_embed_tokens == 11
    assert embed.calls[0] == {
        "model": "text-embedding-3-small",
        "input": ["one", "two"],
    }


# --------------------------------------------------------------------------
# the failure shapes
# --------------------------------------------------------------------------


def test_a_429_is_retried_the_bounded_number_of_times_and_then_named():
    provider, chat, _, slept = make_provider(chat=[RATE_LIMITED])
    with pytest.raises(ProviderError) as caught:
        provider.complete(MESSAGES, model="dep", max_tokens=64, temperature=1.0)

    assert len(chat.calls) == RETRY_ATTEMPTS == 3
    message = str(caught.value)
    assert "azure provider:" in message
    assert "rate limited" in message and "HTTP 429" in message
    assert "after 3 attempt(s)" in message
    # The wait the backend asked for is reported even though the ladder caps it.
    assert "asked for 30s" in message


def test_a_retry_after_header_is_honoured_up_to_the_ladders_cap():
    """A backend asking for five minutes must not silently park the run.

    The header wins over the ladder, but only as far as the ladder's tail. Past
    that the retry gives up and `budget.max_consecutive_provider_errors` --
    which counts whole failed children, not HTTP calls -- takes over.
    """
    provider, _chat, _embed, slept = make_provider(chat=[RATE_LIMITED])
    with pytest.raises(ProviderError):
        provider.complete(MESSAGES, model="dep", max_tokens=64, temperature=1.0)
    assert slept == [RETRY_BACKOFF_S[-1], RETRY_BACKOFF_S[-1]]  # 30s asked, 8s taken


def test_a_transient_5xx_that_clears_is_never_seen_by_the_caller():
    provider, chat, _, slept = make_provider(
        chat=[SERVER_ERROR, chat_response(text="recovered")]
    )
    result = provider.complete(MESSAGES, model="dep", max_tokens=64, temperature=1.0)
    assert result.text == "recovered"
    assert len(chat.calls) == 2
    assert slept == [RETRY_BACKOFF_S[0]]  # 2s, the first rung


def test_a_content_filter_is_named_and_is_never_retried():
    """Waiting eight seconds will not make the filter change its mind."""
    provider, chat, _, slept = make_provider(chat=[CONTENT_FILTERED])
    with pytest.raises(ProviderError) as caught:
        provider.complete(MESSAGES, model="dep", max_tokens=64, temperature=1.0)
    assert len(chat.calls) == 1
    assert slept == []
    assert "content filter" in str(caught.value)


def test_a_filtered_response_that_arrives_as_a_success_is_still_an_error():
    """Azure also returns 200 with `finish_reason: content_filter` and no text."""
    provider, _, _, _ = make_provider(
        chat=[chat_response(text=None, finish_reason="content_filter")]
    )
    with pytest.raises(ProviderError) as caught:
        provider.complete(MESSAGES, model="dep", max_tokens=64, temperature=1.0)
    message = str(caught.value)
    assert "content filter" in message
    assert "finish_reason=content_filter" in message


def test_a_bad_key_is_named_as_authentication_and_is_never_retried():
    provider, chat, _, slept = make_provider(chat=[BAD_KEY])
    with pytest.raises(ProviderError) as caught:
        provider.complete(MESSAGES, model="dep", max_tokens=64, temperature=1.0)
    assert len(chat.calls) == 1 and slept == []
    message = str(caught.value)
    assert "authentication failed" in message and "HTTP 401" in message
    assert "API key" in message


def test_a_truncated_completion_says_so_rather_than_returning_nothing():
    provider, _, _, _ = make_provider(
        chat=[chat_response(text="", finish_reason="length")]
    )
    with pytest.raises(ProviderError, match="finish_reason=length"):
        provider.complete(MESSAGES, model="dep", max_tokens=8, temperature=1.0)


def test_an_empty_choices_array_is_named():
    provider, _, _, _ = make_provider(
        chat=[SimpleNamespace(choices=[], usage=None, model="m")]
    )
    with pytest.raises(ProviderError, match="no choices"):
        provider.complete(MESSAGES, model="dep", max_tokens=64, temperature=1.0)


def test_a_short_embedding_batch_is_named():
    provider, _, _, _ = make_provider(embeddings=[embedding_response()])
    with pytest.raises(ProviderError, match="asked for 2 embedding"):
        provider.embed(["a", "b"], model="text-embedding-3-small")


def test_a_rate_limited_embedding_call_retries_the_same_way():
    provider, _, embed, slept = make_provider(
        embeddings=[RATE_LIMITED, embedding_response()]
    )
    vectors = provider.embed(["a"], model="text-embedding-3-small")
    assert len(vectors) == 1 and len(embed.calls) == 2
    assert slept == [RETRY_BACKOFF_S[-1]]


# --------------------------------------------------------------------------
# the classifier itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error,kind,retryable",
    [
        (RATE_LIMITED, "rate_limit", True),
        (SERVER_ERROR, "server", True),
        (BAD_KEY, "auth", False),
        (CONTENT_FILTERED, "content_filter", False),
        (RecordedAPIError("Error code: 404 - deployment not found", status_code=404),
         "bad_request", False),
        (RuntimeError("the socket closed"), "other", False),
    ],
)
def test_every_recorded_failure_lands_in_the_right_class(error, kind, retryable):
    fault = classify(error)
    assert fault.kind == kind
    assert fault.retryable is retryable


def test_the_status_is_read_from_the_message_when_the_attribute_is_missing():
    """Some wrappers hand back a plain exception with the code only in the text."""
    fault = classify(RuntimeError("Error code: 429 - too many requests"))
    assert fault.kind == "rate_limit" and fault.status == 429


def test_the_retry_after_header_is_parsed_off_the_response():
    assert classify(RATE_LIMITED).retry_after == 30.0


def test_the_ladder_is_the_documented_one():
    assert RETRY_ATTEMPTS == 3
    assert RETRY_BACKOFF_S == (2.0, 4.0, 8.0)


# --------------------------------------------------------------------------
# the environment contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    [
        # Constructing the provider with a real-looking endpoint but no key
        # has, under some network conditions, driven the SDK client init down
        # a path that spends several real seconds before the missing-key
        # check fires -- see the 13s outlier this mark is here to keep out of
        # the fast local-iteration run.
        pytest.param("AZURE_OPENAI_ENDPOINT", marks=pytest.mark.slow),
        "AZURE_OPENAI_API_KEY",
    ],
)
def test_a_missing_environment_variable_is_named_before_anything_is_sent(
    monkeypatch, missing
):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://x.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "k")
    monkeypatch.delenv(missing, raising=False)
    with pytest.raises(ProviderError, match=missing):
        AzureOpenAIProvider()
