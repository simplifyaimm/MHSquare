"""Short-lived signed media URLs (so <audio>/<video> tags work without exposing storage paths)."""

import base64
import hashlib
import hmac
import time
import uuid

from app.core.config import get_settings


def _sig(asset_id: str, exp: int) -> str:
    key = get_settings().secret_key.encode()
    mac = hmac.new(key, f"media:{asset_id}:{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac[:24]).decode().rstrip("=")


def signed_media_url(asset_id: uuid.UUID | str, ttl_s: int | None = None) -> str:
    exp = int(time.time()) + (ttl_s or get_settings().media_url_ttl_s)
    return f"/api/media/{asset_id}?exp={exp}&sig={_sig(str(asset_id), exp)}"


def verify(asset_id: str, exp: int, sig: str) -> bool:
    return exp >= time.time() and hmac.compare_digest(_sig(asset_id, exp), sig)
