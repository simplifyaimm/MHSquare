"""LiteLLM proxy client — the only code that makes HTTP calls to LiteLLM.

Normal calls use the mhsquare virtual key. The master key is used ONLY for key administration
(creating/updating the budgeted virtual key from the Settings screen).
"""

import logging
from datetime import datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.llm import settings_store
from app.llm.settings_store import MODEL_SLOTS

log = logging.getLogger(__name__)


class LiteLLMUnavailable(RuntimeError):
    pass


class LiteLLMAdminError(RuntimeError):
    pass


def _url(path: str) -> str:
    return f"{get_settings().litellm_url}{path}"


def _virtual_headers() -> dict[str, str]:
    key = settings_store.virtual_key()
    return {"Authorization": f"Bearer {key}"} if key else {}


def _master_headers() -> dict[str, str]:
    key = get_settings().litellm_master_key
    if not key:
        raise LiteLLMAdminError("MHSQUARE_LITELLM_MASTER_KEY is not configured")
    return {"Authorization": f"Bearer {key}"}


def litellm_health() -> str:
    resp = httpx.get(_url("/health/liveliness"), timeout=2)
    resp.raise_for_status()
    return "ok"


# ---------------- budget / key admin ----------------


def key_info() -> dict[str, Any] | None:
    """Spend + budget for the mhsquare virtual key (authenticated with the key itself)."""
    key = settings_store.virtual_key()
    if not key:
        return None
    try:
        resp = httpx.get(_url("/key/info"), headers=_virtual_headers(), timeout=5)
    except httpx.HTTPError as e:
        raise LiteLLMUnavailable(str(e)) from e
    if resp.status_code in (401, 403, 404):
        return {"invalid": True}
    resp.raise_for_status()
    info = resp.json().get("info", {})
    return {
        "invalid": False,
        "spend": float(info.get("spend") or 0.0),
        "max_budget": info.get("max_budget"),
        "budget_duration": info.get("budget_duration"),
        "budget_reset_at": info.get("budget_reset_at"),
    }


def ensure_budget_key(daily_usd: float) -> dict[str, Any]:
    """Create the mhsquare virtual key (daily budget) or update its budget. Stores it in llmconfig.env."""
    existing = settings_store.virtual_key()
    try:
        if existing:
            info = key_info()
            if info and not info.get("invalid"):
                r = httpx.post(
                    _url("/key/update"),
                    headers=_master_headers(),
                    json={
                        "key": existing,
                        "max_budget": daily_usd,
                        "budget_duration": "1d",
                        "models": [s.alias for s in MODEL_SLOTS],  # keep in sync with new aliases
                    },
                    timeout=10,
                )
                if r.status_code >= 400:
                    raise LiteLLMAdminError(f"LiteLLM /key/update failed ({r.status_code}): {r.text[:200]}")
                return {"created": False}
        r = httpx.post(
            _url("/key/generate"),
            headers=_master_headers(),
            json={
                "key_alias": f"mhsquare-{datetime.now():%Y%m%d%H%M%S}",
                "max_budget": daily_usd,
                "budget_duration": "1d",
                "models": [s.alias for s in MODEL_SLOTS],
                "metadata": {"app": "mhsquare"},
            },
            timeout=10,
        )
    except httpx.HTTPError as e:
        raise LiteLLMUnavailable(str(e)) from e
    if r.status_code >= 400:
        raise LiteLLMAdminError(f"LiteLLM /key/generate failed ({r.status_code}): {r.text[:200]}")
    settings_store.update({settings_store.VIRTUAL_KEY_VAR: r.json()["key"]})
    return {"created": True}


def test_alias(alias: str) -> dict[str, Any]:
    """Tiny paid call through the proxy to prove an LLM alias + key work. Reasoning models (OpenAI GPT-5,
    DeepSeek V4) spend hidden tokens before answering, so a 1-token cap fails on them: allow a small margin."""
    try:
        r = httpx.post(
            _url("/chat/completions"),
            headers=_virtual_headers(),
            json={
                "model": alias,
                "messages": [{"role": "user", "content": "Reply with: ok"}],
                "max_tokens": 200,
            },
            timeout=60,
        )
    except httpx.HTTPError as e:
        raise LiteLLMUnavailable(str(e)) from e
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message") or r.text
        except ValueError:
            msg = r.text
        return {"ok": False, "status": r.status_code, "message": str(msg)[:400]}
    return {"ok": True, "status": r.status_code, "message": "ok"}


# ---------------------------------------------------------------- model calls (virtual key)
# Retry policy (spec §7): 429 → exponential backoff with jitter (5 tries); 5xx/timeout → 3 tries,
# then the next fallback alias; budget/quota and content-policy errors are raised immediately.

import base64 as _b64  # noqa: E402
import random as _random  # noqa: E402
import time as _time  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402

from app.llm.errors import ErrorKind, LLMError, classify  # noqa: E402

RATE_LIMIT_TRIES = 5
TRANSIENT_TRIES = 3
_sleep = _time.sleep  # patched in tests


@dataclass
class CallResult:
    alias: str
    cost_usd: float
    data: dict = field(default_factory=dict)
    content: bytes = b""


def _error_message(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        return str(err.get("message") if isinstance(err, dict) else err) or resp.text
    except ValueError:
        return resp.text


def _post_once(path: str, body: dict, timeout: float) -> httpx.Response:
    if not settings_store.virtual_key():
        raise LLMError(ErrorKind.invalid, "No mhsquare LiteLLM key: set a daily budget in Settings first")
    try:
        return httpx.post(_url(path), headers=_virtual_headers(), json=body, timeout=timeout)
    except httpx.TimeoutException as e:
        raise LLMError(ErrorKind.transient, f"timeout after {timeout}s") from e
    except httpx.HTTPError as e:
        raise LLMError(ErrorKind.transient, f"LiteLLM unreachable: {e}") from e


_OUR_BUDGET = "budget has been exceeded"  # LiteLLM's message for mhsquare's own daily key budget
_PROVIDER_DOWN = ("credit balance", "insufficient_quota", "exceeded your current quota", "billing", "disabled",
                  "suspended", "invalid api key", "invalid x-api-key", "incorrect api key", "authentication",
                  "api key not valid", "permission", "no credits", "payment required", "quota exceeded",
                  "resource_exhausted", "not found", "does not exist", "llm provider not provided")  # fmt: skip


def provider_down(err: LLMError) -> bool:
    """The PROVIDER behind an alias can't serve us (credit, quota, account, key, unknown model) — another
    alias may still work. mhsquare's own daily budget is NOT this: it must pause everything."""
    m = str(err).lower()
    if _OUR_BUDGET in m:
        return False
    if err.status in (401, 402, 403, 404):
        return True
    return err.kind in (ErrorKind.budget, ErrorKind.invalid) and any(s in m for s in _PROVIDER_DOWN)


def _call(path: str, body: dict, aliases: list[str], timeout: float) -> tuple[str, httpx.Response]:
    """POST with retry/backoff; on persistent transient errors move to the next alias."""
    last: LLMError | None = None
    for alias in aliases:
        rate_tries = transient_tries = 0
        while True:
            try:
                resp = _post_once(path, {**body, "model": alias}, timeout)
                if resp.status_code < 400:
                    return alias, resp
                err = LLMError(classify(resp.status_code, _error_message(resp)), _error_message(resp)[:2000],
                               resp.status_code)  # fmt: skip
            except LLMError as e:
                err = e
            last = err
            if err.kind == ErrorKind.rate_limit and rate_tries < RATE_LIMIT_TRIES - 1:
                rate_tries += 1
                _sleep(min(60, 2**rate_tries) + _random.uniform(0, 1))  # noqa: S311
                continue
            if err.kind == ErrorKind.transient and transient_tries < TRANSIENT_TRIES - 1:
                transient_tries += 1
                _sleep(2 * transient_tries)
                continue
            if err.kind == ErrorKind.transient or provider_down(err):
                if provider_down(err):
                    log.warning("%s unavailable (%s); trying the next model", alias, str(err)[:160])
                break  # try the next fallback alias
            raise err
    assert last is not None
    if provider_down(last):  # every model in the chain is unavailable → pause (resumable), don't fail
        raise LLMError(ErrorKind.budget, f"all models unavailable ({', '.join(aliases)}). Last: {last}"[:2000],
                       last.status)  # fmt: skip
    raise last


def _cost(resp: httpx.Response) -> float:
    try:
        return float(resp.headers.get("x-litellm-response-cost") or 0.0)
    except ValueError:
        return 0.0


def chat(
    alias: str,
    messages: list[dict],
    *,
    system: str | None = None,
    fallbacks: list[str] | None = None,
    max_tokens: int = 8000,
    temperature: float = 0.4,
    timeout: float = 600,
) -> tuple[str, CallResult]:
    """Chat completion → (text, result). A reply cut off at max_tokens (reasoning models spend hidden tokens)
    moves on to the next alias in the chain; the cut-off call's cost is still counted."""
    msgs = ([{"role": "system", "content": system}] if system else []) + messages
    chain, wasted = [alias, *(fallbacks or [])], 0.0
    while True:
        used, resp = _call("/chat/completions",
                           {"messages": msgs, "max_tokens": max_tokens, "temperature": temperature},
                           chain, timeout)  # fmt: skip
        data = resp.json()
        text = data["choices"][0]["message"].get("content") or ""
        if data["choices"][0].get("finish_reason") != "length":
            return text, CallResult(alias=used, cost_usd=_cost(resp) + wasted,
                                    data={"usage": data.get("usage", {})})  # fmt: skip
        wasted += _cost(resp)
        chain = chain[chain.index(used) + 1 :]
        if not chain:
            raise LLMError(ErrorKind.invalid, f"{used}: response cut off at max_tokens={max_tokens}")
        log.warning("%s cut off at max_tokens=%d; trying %s", used, max_tokens, chain[0])


def generate_image(alias: str, prompt: str, *, size: str = "1536x1024", fallbacks: list[str] | None = None,
                   timeout: float = 300) -> tuple[bytes, CallResult]:  # fmt: skip
    used, resp = _call("/images/generations", {"prompt": prompt, "n": 1, "size": size},
                       [alias, *(fallbacks or [])], timeout)  # fmt: skip
    item = (resp.json().get("data") or [{}])[0]
    if item.get("b64_json"):
        img = _b64.b64decode(item["b64_json"])
    elif item.get("url"):  # provider URLs expire: download immediately
        img = httpx.get(item["url"], timeout=120).content
    else:
        raise LLMError(ErrorKind.invalid, "image response had no data")
    return img, CallResult(alias=used, cost_usd=_cost(resp))


def speech(alias: str, text: str, voice: str, timeout: float = 600) -> tuple[bytes, CallResult]:
    used, resp = _call("/audio/speech", {"input": text, "voice": voice}, [alias], timeout)
    return resp.content, CallResult(alias=used, cost_usd=_cost(resp))


def embed(
    texts: list[str], alias: str = "embed", timeout: float = 60
) -> tuple[list[list[float]], CallResult]:
    used, resp = _call("/embeddings", {"input": texts}, [alias], timeout)
    data = sorted(resp.json().get("data", []), key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in data], CallResult(alias=used, cost_usd=_cost(resp))
