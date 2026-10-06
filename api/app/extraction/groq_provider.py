import json
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from groq import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    Groq,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)

from app.config import Settings
from app.extraction.contract import strict_json_schema
from app.extraction.provider import (
    Completion,
    ExtractionPath,
    ProviderUnavailable,
    RetryableError,
    RetryPolicy,
    call_with_retries,
)

MAX_OUTPUT_TOKENS = 4096
# Rough per-request allowance for the system prompt, schema and answer, in tokens.
REQUEST_OVERHEAD_TOKENS = 2500
IMAGE_TOKENS = 2048
CHARS_PER_TOKEN = 3
DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
DURATION_UNITS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}


def parse_duration(text: str) -> float:
    """Seconds from Groq durations such as '7.66s', '2m59.56s' or '120ms'."""
    parts = DURATION_PART.findall(text.strip())
    if not parts or "".join(number + unit for number, unit in parts) != text.strip():
        raise ValueError(f"unrecognised duration {text!r}")
    return sum(float(number) * DURATION_UNITS[unit] for number, unit in parts)


def retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    """Value of the retry-after header in seconds, if present and numeric."""
    value = headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Upper-bound guess of the tokens a request will use, for rate-limit pacing."""
    images = 0
    chars = 0
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            chars += len(content)
            continue
        for part in content:
            if part["type"] == "image_url":
                images += 1
            else:
                chars += len(part["text"])
    return chars // CHARS_PER_TOKEN + images * IMAGE_TOKENS + REQUEST_OVERHEAD_TOKENS


class GroqProvider:
    name = "groq"

    def __init__(
        self,
        settings: Settings,
        client: Groq | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        api_key = settings.groq_api_key.get_secret_value()
        if client is None and not api_key:
            raise ValueError("GROQ_API_KEY is not set")
        if not settings.llm_model_text or not settings.llm_model_vision:
            raise ValueError("LLM_MODEL_TEXT and LLM_MODEL_VISION must be set")
        self.client = client or Groq(
            api_key=api_key, max_retries=0, timeout=settings.llm_timeout_seconds
        )
        self.models: dict[ExtractionPath, str] = {
            "text": settings.llm_model_text,
            "vision": settings.llm_model_vision,
        }
        self.reasoning: dict[ExtractionPath, str] = {
            "text": settings.llm_reasoning_effort_text,
            "vision": settings.llm_reasoning_effort_vision,
        }
        self.max_images = settings.llm_max_vision_pages
        self.policy = RetryPolicy(
            max_attempts=settings.llm_max_attempts, max_wait=settings.llm_max_wait_seconds
        )
        self.sleep = sleep
        self.clock = clock
        self.response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "invoice_extraction",
                "strict": True,
                "schema": strict_json_schema(),
            },
        }
        # Per model: tokens left in the current minute and when that window resets.
        self.token_budget: dict[str, tuple[int, float]] = {}

    def model_for(self, path: ExtractionPath) -> str:
        """Model id used for an extraction path."""
        return self.models[path]

    def complete(self, messages: list[dict[str, Any]], path: ExtractionPath) -> Completion:
        """Call the model for a path with bounded retries."""
        return call_with_retries(
            lambda: self._request(messages, path), self.policy, sleep=self.sleep
        )

    def _wait_for_budget(self, model: str, needed: int) -> None:
        remaining, reset_at = self.token_budget.get(model, (needed, 0.0))
        wait = reset_at - self.clock()
        if remaining < needed and wait > 0:
            self.sleep(min(wait, self.policy.max_wait))

    def _record_budget(self, model: str, headers: Mapping[str, str]) -> None:
        remaining = headers.get("x-ratelimit-remaining-tokens")
        reset = headers.get("x-ratelimit-reset-tokens")
        if remaining is None or reset is None:
            return
        try:
            self.token_budget[model] = (int(float(remaining)), self.clock() + parse_duration(reset))
        except ValueError:
            self.token_budget.pop(model, None)

    def _request(self, messages: list[dict[str, Any]], path: ExtractionPath) -> Completion:
        model = self.models[path]
        self._wait_for_budget(model, estimate_tokens(messages))
        started = self.clock()
        try:
            raw = self.client.chat.completions.with_raw_response.create(
                model=model,
                messages=messages,
                response_format=self.response_format,
                temperature=0,
                max_completion_tokens=MAX_OUTPUT_TOKENS,
                reasoning_effort=self.reasoning[path],
            )
        except RateLimitError as exc:
            raise RetryableError(
                "rate limited", retry_after=retry_after_seconds(exc.response.headers)
            ) from exc
        except (APITimeoutError, APIConnectionError, InternalServerError) as exc:
            raise RetryableError(type(exc).__name__) from exc
        except (AuthenticationError, PermissionDeniedError) as exc:
            raise ProviderUnavailable(
                f"{type(exc).__name__}: check GROQ_API_KEY", fatal=True
            ) from exc
        except BadRequestError as exc:
            failed = failed_generation(exc)
            if failed is None:
                raise ProviderUnavailable(f"bad request: {exc.message}") from exc
            return Completion(
                content=failed,
                model=model,
                latency_ms=int((self.clock() - started) * 1000),
                input_tokens=None,
                output_tokens=None,
                finish_reason="json_validate_failed",
            )
        except APIStatusError as exc:
            raise ProviderUnavailable(f"HTTP {exc.status_code}: {exc.message}") from exc

        latency_ms = int((self.clock() - started) * 1000)
        self._record_budget(model, raw.headers)
        completion = raw.parse()
        choice = completion.choices[0]
        usage = completion.usage
        return Completion(
            content=choice.message.content or "",
            model=model,
            latency_ms=latency_ms,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            finish_reason=choice.finish_reason,
        )


def failed_generation(exc: BadRequestError) -> str | None:
    """Model output Groq rejected for not matching the schema, so it can be repaired once."""
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body)
    if not isinstance(error, dict) or error.get("code") != "json_validate_failed":
        return None
    generation = error.get("failed_generation")
    return generation if isinstance(generation, str) else json.dumps(generation)
