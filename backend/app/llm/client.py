"""LiteLLM proxy client. Phase 1: health only. Completions/images/videos land in later phases."""

import httpx

from app.core.config import get_settings


def _headers() -> dict[str, str]:
    key = get_settings().litellm_api_key
    return {"Authorization": f"Bearer {key}"} if key else {}


def litellm_health() -> str:
    resp = httpx.get(f"{get_settings().litellm_url}/health/liveliness", timeout=2)
    resp.raise_for_status()
    return "ok"
