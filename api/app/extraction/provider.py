import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

ExtractionPath = Literal["text", "vision"]


@dataclass(frozen=True)
class Completion:
    content: str
    model: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    finish_reason: str | None


class ExtractionProvider(Protocol):
    name: str
    max_images: int

    def model_for(self, path: ExtractionPath) -> str:
        """Model id used for an extraction path."""
        ...

    def complete(self, messages: list[dict[str, Any]], path: ExtractionPath) -> Completion:
        """Return the model answer, retrying transient failures within the configured budget."""
        ...


# Raised by a single request for timeouts, 429 and 5xx; retried with backoff.
class RetryableError(Exception):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# The provider could not answer. Fatal means every further request will fail too
# (bad credentials, daily quota), so a batch run should stop instead of continuing.
class ProviderUnavailable(Exception):
    def __init__(self, message: str, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    max_wait: float
    base_delay: float = 1.0


def call_with_retries[T](
    call: Callable[[], T],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> T:
    """Run call; retry RetryableError with exponential backoff and jitter, at most max_attempts."""
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return call()
        except RetryableError as exc:
            if attempt == policy.max_attempts:
                raise ProviderUnavailable(f"gave up after {attempt} attempts: {exc}") from exc
            delay = exc.retry_after
            if delay is None:
                delay = policy.base_delay * 2 ** (attempt - 1) * (1 + jitter())
            if delay > policy.max_wait:
                raise ProviderUnavailable(
                    f"provider asked to wait {delay:.0f}s, limit {policy.max_wait:.0f}s", fatal=True
                ) from exc
            sleep(delay)
    raise AssertionError("unreachable")
