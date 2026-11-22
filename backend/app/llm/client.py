"""LiteLLM proxy client — the only code that makes HTTP calls to LiteLLM.

Normal calls use the mhsquare virtual key. The master key is used ONLY for key administration
(creating/updating the budgeted virtual key from the Settings screen).
"""

from datetime import datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.llm import settings_store
from app.llm.settings_store import MODEL_SLOTS


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
                    json={"key": existing, "max_budget": daily_usd, "budget_duration": "1d"},
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
    """Tiny paid call (1 output token) through the proxy to prove an LLM alias + key work."""
    try:
        r = httpx.post(
            _url("/chat/completions"),
            headers=_virtual_headers(),
            json={
                "model": alias,
                "messages": [{"role": "user", "content": "Reply with: ok"}],
                "max_tokens": 1,
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
