import httpx
import redis
from fastapi import APIRouter, Response
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import get_engine
from app.llm.client import litellm_health

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def liveness():
    return {"status": "ok"}


def _check_db() -> str:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))
    return "ok"


def _check_redis() -> str:
    r = redis.Redis.from_url(get_settings().redis_url, socket_timeout=2)
    r.ping()
    return "ok"


def _check_renderer() -> str:
    resp = httpx.get(f"{get_settings().renderer_url}/health", timeout=2)
    resp.raise_for_status()
    return "ok"


@router.get("/ready")
def readiness(response: Response):
    """db + redis are required; litellm and the renderer are reported but don't fail readiness."""
    checks = {"db": _check_db, "redis": _check_redis, "litellm": litellm_health, "renderer": _check_renderer}
    required = {"db", "redis"}
    result: dict[str, str] = {}
    for name, fn in checks.items():
        try:
            result[name] = fn()
        except Exception as e:  # noqa: BLE001 - health check reports any failure
            result[name] = f"error: {type(e).__name__}"
    ok = all(result[n] == "ok" for n in required)
    degraded = any(v != "ok" for v in result.values())
    response.status_code = 200 if ok else 503
    return {"status": "ok" if not degraded else ("degraded" if ok else "down"), "checks": result}
