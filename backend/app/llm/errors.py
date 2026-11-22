"""Classify LiteLLM/provider failures into the actions the spec prescribes (§7)."""

from enum import StrEnum


class ErrorKind(StrEnum):
    budget = "budget"  # our daily budget / provider quota or credits → paused_budget
    rate_limit = "rate_limit"  # 429 → backoff with jitter, then failed
    content_policy = "content_policy"  # safety block → needs_edit, no fallback
    transient = "transient"  # 5xx / timeout → retry, then fallback model
    invalid = "invalid"  # 4xx request/config problem → failed, no retry


class LLMError(RuntimeError):
    def __init__(self, kind: ErrorKind, message: str, status: int | None = None):
        super().__init__(message)
        self.kind, self.status = kind, status


_BUDGET = ("budget has been exceeded", "budget_exceeded", "exceeded budget", "max_budget", "insufficient_quota",
           "no credits remaining", "exceeded your current quota", "billing", "credit balance is too low")  # fmt: skip
_POLICY = ("content_policy", "content policy", "safety", "responsible ai", "moderation", "blocked due to",
           "prohibited", "violat")  # fmt: skip


def classify(status: int | None, message: str) -> ErrorKind:
    m = message.lower()
    if any(s in m for s in _BUDGET):
        return ErrorKind.budget
    if any(s in m for s in _POLICY) and status not in (500, 502, 503, 504):
        return ErrorKind.content_policy
    if status == 429:
        return ErrorKind.rate_limit
    if status is None or status >= 500 or status in (408, 409):
        return ErrorKind.transient
    return ErrorKind.invalid
