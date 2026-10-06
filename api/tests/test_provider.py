from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from groq import AuthenticationError, BadRequestError, InternalServerError, RateLimitError
from pydantic import SecretStr

from app.config import Settings
from app.extraction.groq_provider import GroqProvider, estimate_tokens, parse_duration
from app.extraction.provider import (
    ProviderUnavailable,
    RetryableError,
    RetryPolicy,
    call_with_retries,
)

REQUEST = httpx.Request("POST", "https://api.groq.example/openai/v1/chat/completions")
MESSAGES = [{"role": "user", "content": "x" * 300}]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def api_error(kind: type[Exception], status: int, **kwargs: Any) -> Exception:
    headers = kwargs.pop("headers", {})
    response = httpx.Response(status, headers=headers, request=REQUEST)
    return kind("error", response=response, body=kwargs.pop("body", None))


def ok_response(content: str = "{}", headers: dict[str, str] | None = None) -> SimpleNamespace:
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=1200, completion_tokens=300),
    )
    return SimpleNamespace(headers=headers or {}, parse=lambda: completion)


class FakeGroqClient:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []
        create = self.create
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
        )

    def create(self, **kwargs: Any) -> object:
        self.requests.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def groq_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"llm_model_text": "text-model", "llm_model_vision": "vision-model"}
    )


def provider(
    settings: Settings, outcomes: list[object], clock: FakeClock
) -> tuple[GroqProvider, FakeGroqClient]:
    client = FakeGroqClient(outcomes)
    instance = GroqProvider(settings, client=client, sleep=clock.sleep, clock=clock)
    return instance, client


def test_retries_use_exponential_backoff_with_jitter() -> None:
    calls = []
    sleeps: list[float] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise RetryableError("timeout")
        return "ok"

    result = call_with_retries(
        flaky, RetryPolicy(max_attempts=3, max_wait=60), sleep=sleeps.append, jitter=lambda: 0.5
    )

    assert result == "ok"
    assert sleeps == [1.5, 3.0]


def test_retries_are_bounded() -> None:
    calls = []

    def always_failing() -> str:
        calls.append(1)
        raise RetryableError("503")

    with pytest.raises(ProviderUnavailable) as caught:
        call_with_retries(always_failing, RetryPolicy(3, 60), sleep=lambda _: None)

    assert len(calls) == 3
    assert caught.value.fatal is False


def test_retry_after_is_honoured_up_to_the_limit() -> None:
    sleeps: list[float] = []
    outcomes: list[object] = [RetryableError("429", retry_after=7), "ok"]

    def call() -> str:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return str(outcome)

    assert call_with_retries(call, RetryPolicy(3, 60), sleep=sleeps.append) == "ok"
    assert sleeps == [7]


def test_long_retry_after_fails_fast_and_fatal() -> None:
    sleeps: list[float] = []

    def call() -> str:
        raise RetryableError("daily limit", retry_after=3600)

    with pytest.raises(ProviderUnavailable) as caught:
        call_with_retries(call, RetryPolicy(3, 60), sleep=sleeps.append)

    assert caught.value.fatal is True
    assert sleeps == []


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("7.66s", 7.66), ("2m59.5s", 179.5), ("120ms", 0.12), ("1h2m", 3720.0)],
)
def test_parse_duration(text: str, seconds: float) -> None:
    assert parse_duration(text) == pytest.approx(seconds)


@pytest.mark.parametrize("text", ["", "soon", "5x", "1.5"])
def test_parse_duration_rejects_garbage(text: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(text)


def test_estimate_counts_images() -> None:
    image_message = [
        {"role": "user", "content": [{"type": "text", "text": "abc"}, {"type": "image_url"}]}
    ]
    assert estimate_tokens(image_message) == 1 + 2048 + 2500


def test_request_uses_strict_schema_and_path_settings(groq_settings: Settings) -> None:
    clock = FakeClock()
    instance, client = provider(groq_settings, [ok_response('{"a": 1}')], clock)

    completion = instance.complete(MESSAGES, "vision")

    request = client.requests[0]
    assert request["model"] == "vision-model"
    assert request["reasoning_effort"] == "none"
    assert request["temperature"] == 0
    assert request["response_format"]["json_schema"]["strict"] is True
    assert completion.content == '{"a": 1}'
    assert (completion.input_tokens, completion.output_tokens) == (1200, 300)


def test_rate_limit_is_retried_after_the_given_delay(groq_settings: Settings) -> None:
    clock = FakeClock()
    limited = api_error(RateLimitError, 429, headers={"retry-after": "2"})
    instance, client = provider(groq_settings, [limited, ok_response()], clock)

    instance.complete(MESSAGES, "text")

    assert len(client.requests) == 2
    assert clock.sleeps == [2.0]


def test_server_errors_give_up_after_max_attempts(groq_settings: Settings) -> None:
    clock = FakeClock()
    errors = [api_error(InternalServerError, 503) for _ in range(3)]
    instance, client = provider(groq_settings, errors, clock)

    with pytest.raises(ProviderUnavailable):
        instance.complete(MESSAGES, "text")

    assert len(client.requests) == 3


def test_bad_credentials_are_fatal_without_retry(groq_settings: Settings) -> None:
    clock = FakeClock()
    instance, client = provider(groq_settings, [api_error(AuthenticationError, 401)], clock)

    with pytest.raises(ProviderUnavailable) as caught:
        instance.complete(MESSAGES, "text")

    assert caught.value.fatal is True
    assert len(client.requests) == 1


def test_schema_rejection_returns_the_failed_generation(groq_settings: Settings) -> None:
    clock = FakeClock()
    body = {"error": {"code": "json_validate_failed", "failed_generation": '{"total_raw": 5}'}}
    instance, _ = provider(groq_settings, [api_error(BadRequestError, 400, body=body)], clock)

    completion = instance.complete(MESSAGES, "text")

    assert completion.content == '{"total_raw": 5}'
    assert completion.finish_reason == "json_validate_failed"


def test_waits_for_the_token_window_when_the_budget_is_low(groq_settings: Settings) -> None:
    clock = FakeClock()
    low = {"x-ratelimit-remaining-tokens": "900", "x-ratelimit-reset-tokens": "30s"}
    instance, _ = provider(groq_settings, [ok_response(headers=low), ok_response()], clock)

    instance.complete(MESSAGES, "text")
    instance.complete(MESSAGES, "text")

    assert clock.sleeps == [30.0]


def test_missing_configuration_is_rejected(settings: Settings) -> None:
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        GroqProvider(settings)
    keyed = settings.model_copy(update={"groq_api_key": SecretStr("secret")})
    with pytest.raises(ValueError, match="LLM_MODEL_TEXT"):
        GroqProvider(keyed)
