"""What the `azure` and `openrouter` backends share: the OpenAI request shape.

Both talk to an OpenAI-shaped `chat.completions.create` / `embeddings.create`,
and both need the same three things around it:

**One place to parse a response.** Empty choices, empty content, a
`finish_reason` of `content_filter` -- each is a `ProviderError` whose message
names what happened, because a run that dies three generations in should say
why in the line the operator reads first.

**A bounded retry.** Three attempts, sleeping 2 s then 4 s, on HTTP 429 and
5xx only. Nothing else is retried: a 401 will still be a 401 in eight seconds
and a content filter will still be a content filter. When the backend supplies
a `Retry-After` that wins over the ladder, capped at the ladder's tail (8 s) so
that a server asking for a five-minute wait cannot silently park the run.

The bound is the point. The post-mortem's single largest multiplier on v1's
token bill was a nested 3x3 retry, and `budget.max_consecutive_provider_errors`
already halts a run against a genuinely dead backend. This layer exists for the
one-second blip, not for the outage.

**Named causes.** A 429, an auth failure and a content filter are three
different problems with three different fixes, and "request failed:
BadRequestError" is none of them.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from evolvekit.providers.base import Completion, ProviderError

__all__ = [
    "RETRY_ATTEMPTS",
    "RETRY_BACKOFF_S",
    "Fault",
    "classify",
    "request_with_retry",
    "completion_from_chat",
    "embed_openai",
]

RETRY_ATTEMPTS = 3
"""Total tries, not extra ones: one call and at most two retries."""

RETRY_BACKOFF_S = (2.0, 4.0, 8.0)
"""Seconds to wait before retry 1, retry 2, and -- as the cap on a
server-supplied `Retry-After` -- the longest this layer will ever sleep."""

_STATUS_PATTERNS = (
    re.compile(r"[Ee]rror code:\s*(\d{3})"),
    re.compile(r"\bHTTP[ /]?(?:1\.[01] )?(\d{3})\b"),
    re.compile(r"\bstatus(?:_code)?[=: ]+(\d{3})\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class Fault:
    """One backend failure, classified into something a human can act on."""

    kind: str
    """`rate_limit` | `server` | `auth` | `content_filter` | `bad_request` | `other`."""
    status: int | None = None
    retry_after: float | None = None
    detail: str = ""

    @property
    def retryable(self) -> bool:
        return self.kind in ("rate_limit", "server")

    def message(self, provider: str, *, attempts: int = 1, what: str = "request") -> str:
        tried = f" after {attempts} attempt(s)" if attempts > 1 else ""
        code = f" (HTTP {self.status})" if self.status else ""
        # "the request" adds nothing; "the embeddings request" says which call
        # of the two this backend makes went wrong.
        where = "" if what == "request" else f" on the {what}"
        if self.kind == "rate_limit":
            wait = (
                f"; the backend asked for {self.retry_after:g}s"
                if self.retry_after is not None
                else ""
            )
            head = f"rate limited{code}{tried}{where}{wait}"
        elif self.kind == "server":
            head = f"the backend returned a server error{code}{tried}{where}"
        elif self.kind == "auth":
            head = (
                f"authentication failed{code} -- check the API key and endpoint "
                "in the environment"
            )
        elif self.kind == "content_filter":
            head = f"the request was blocked by the content filter{code}"
        elif self.kind == "bad_request":
            head = (
                f"the request was rejected as invalid{code} -- check the model "
                "or deployment name"
            )
        else:
            head = f"{what} failed{code}{tried}"
        return f"{provider} provider: {head}: {self.detail}" if self.detail else (
            f"{provider} provider: {head}"
        )


def _status_of(exc: BaseException) -> int | None:
    for attribute in ("status_code", "status", "http_status"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int) and 100 <= value < 600:
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    if isinstance(value, int) and 100 <= value < 600:
        return value
    text = str(exc)
    for pattern in _STATUS_PATTERNS:
        found = pattern.search(text)
        if found:
            return int(found.group(1))
    return None


def _retry_after_of(exc: BaseException) -> float | None:
    value = getattr(exc, "retry_after", None)
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    getter = getattr(headers, "get", None)
    if getter is None:
        return None
    for name in ("retry-after", "Retry-After", "x-ratelimit-reset-requests"):
        raw = getter(name)
        if raw is None:
            continue
        try:
            seconds = float(str(raw).rstrip("sS"))
        except ValueError:
            continue
        if seconds >= 0:
            return seconds
    return None


def classify(exc: BaseException) -> Fault:
    """Turn a backend exception into a `Fault`. Never raises."""
    status = _status_of(exc)
    detail = " ".join(str(exc).split())[:300]
    lowered = detail.lower()
    code = str(getattr(exc, "code", "") or "").lower()

    if "content_filter" in code or "content_filter" in lowered or (
        "content management policy" in lowered
    ):
        return Fault("content_filter", status, None, detail)
    if status == 429 or "rate limit" in lowered or "too many requests" in lowered:
        return Fault("rate_limit", status or 429, _retry_after_of(exc), detail)
    if status in (401, 403) or "invalid api key" in lowered or "unauthorized" in lowered:
        return Fault("auth", status, None, detail)
    if status is not None and 500 <= status < 600:
        return Fault("server", status, _retry_after_of(exc), detail)
    if status == 400 or status == 404:
        return Fault("bad_request", status, None, detail)
    return Fault("other", status, None, detail)


def request_with_retry(
    call: Callable[[], Any],
    *,
    provider: str,
    reraise: tuple[type[BaseException], ...] = (),
    sleep: Callable[[float], None] = time.sleep,
    attempts: int = RETRY_ATTEMPTS,
    what: str = "request",
) -> Any:
    """Call `call()`, retrying a rate limit or a 5xx and nothing else.

    `reraise` names exception types that pass through untouched -- the Azure
    backend's `max_completion_tokens` fallback is driven by a `TypeError` and
    must not be wrapped into a `ProviderError` on the way past.
    """
    last: Fault | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return call()
        except reraise:
            raise
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - classified, then re-raised
            fault = classify(exc)
            last = fault
            if not fault.retryable or attempt >= attempts:
                raise ProviderError(
                    fault.message(provider, attempts=attempt, what=what)
                ) from exc
            sleep(_delay(fault, attempt))
    # Unreachable: the loop either returns or raises.
    raise ProviderError(  # pragma: no cover
        (last or Fault("other")).message(provider, attempts=attempts, what=what)
    )


def _delay(fault: Fault, attempt: int) -> float:
    """Ladder position, or the backend's own request, capped at the ladder's tail."""
    ladder = RETRY_BACKOFF_S[min(attempt, len(RETRY_BACKOFF_S)) - 1]
    if fault.retry_after is None:
        return ladder
    return min(max(fault.retry_after, 0.0), RETRY_BACKOFF_S[-1])


def completion_from_chat(response: Any, *, model: str, provider: str) -> Completion:
    """Parse one OpenAI-shaped chat response, or say precisely what was wrong."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise ProviderError(f"{provider} provider: response contained no choices")
    choice = choices[0]
    reason = getattr(choice, "finish_reason", None)
    text = getattr(getattr(choice, "message", None), "content", None)
    if reason == "content_filter":
        raise ProviderError(
            f"{provider} provider: the response was blocked by the content "
            "filter (finish_reason=content_filter); the prompt reached the "
            "model but the completion was withheld"
        )
    if not text:
        detail = f" (finish_reason={reason})" if reason else ""
        raise ProviderError(
            f"{provider} provider: response contained empty content{detail}"
        )
    usage = getattr(response, "usage", None)
    return Completion(
        text=text,
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        model=str(getattr(response, "model", None) or model),
        provider=provider,
    )


def embed_openai(
    client: Any,
    texts: list[str],
    *,
    model: str,
    owner: Any,
    sleep: Callable[[float], None] = time.sleep,
) -> list[list[float]]:
    """The OpenAI embeddings call both OpenAI-shaped backends share.

    Records the reported prompt tokens on `owner.last_embed_tokens` so the
    ledger can price the call from `models.embed` without the near-duplicate
    gate having to widen `embed()`'s return type.
    """
    provider = getattr(owner, "name", "openai")
    response = request_with_retry(
        lambda: client.embeddings.create(model=model, input=list(texts)),
        provider=provider,
        sleep=sleep,
        what="embeddings request",
    )
    data = getattr(response, "data", None) or []
    if len(data) != len(texts):
        raise ProviderError(
            f"{provider} provider: asked for {len(texts)} embedding(s), "
            f"got {len(data)}"
        )
    usage = getattr(response, "usage", None)
    owner.last_embed_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    vectors = [[float(v) for v in item.embedding] for item in data]
    if any(not vector for vector in vectors):
        raise ProviderError(f"{provider} provider: an embedding came back empty")
    return vectors
