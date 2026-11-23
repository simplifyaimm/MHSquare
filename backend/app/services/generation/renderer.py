"""Client for the sandboxed motion renderer (renderers/motion)."""

import random
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.core.config import get_settings


class RenderError(RuntimeError):
    def __init__(self, message: str, logs: list[str] | None = None):
        super().__init__(message)
        self.logs = logs or []


BUSY_WAIT_S = 1800  # how long a take may wait for a free renderer slot (whole-video batches queue up)
RENDER_TIMEOUT_S = 900  # one render once it has a slot (renderer caps itself at max(180 s, 0.8 s/frame))


def _post_render(body: dict, timeout: float) -> httpx.Response:
    """POST /render, waiting (outside the render timeout) while the renderer reports every slot busy."""
    url = f"{get_settings().motion_renderer_url}/render"
    deadline, delay = time.monotonic() + BUSY_WAIT_S, 2.0
    while True:
        try:
            r = httpx.post(url, json=body, timeout=timeout)
        except httpx.HTTPError as e:
            raise RenderError(f"motion renderer unreachable: {e}") from e
        if r.status_code != 503 or not _is_busy(r):
            return r
        if time.monotonic() > deadline:
            raise RenderError(f"motion renderer busy for over {BUSY_WAIT_S // 60} min")
        time.sleep(delay + random.uniform(0, 1))  # noqa: S311 - jitter, not crypto
        delay = min(delay * 1.5, 15.0)


def _is_busy(r: httpx.Response) -> bool:
    try:
        return bool(r.json().get("busy"))
    except ValueError:
        return False


@dataclass
class RenderResult:
    video: Path
    thumb: Path | None
    info: dict
    qa_frames: list[Path] = field(default_factory=list)


def render(*, html: str, css: str, js: str, theme_css: str, width: int, height: int, fps: int,
           duration_ms: int, scale: float = 1.0, probe: dict | None = None,
           qa_frames: list[float] | None = None) -> RenderResult:  # fmt: skip
    s = get_settings()
    job = uuid.uuid4().hex[:24]
    body = {
        "job_id": job, "html": html, "css": css, "js": js, "theme_css": theme_css,
        "width": width, "height": height, "fps": fps, "duration_ms": duration_ms, "scale": scale, "probe": probe,
        "qa_frames": qa_frames or [],
    }  # fmt: skip
    r = _post_render(body, RENDER_TIMEOUT_S)
    if r.status_code != 200:
        raise RenderError(f"renderer HTTP {r.status_code}: {r.text[:500]}")
    data = r.json()
    if not data.get("ok"):
        raise RenderError(data.get("error", "render failed"), data.get("logs", []))
    out = Path(s.render_out_dir)
    thumb = out / data["thumb"] if data.get("thumb") else None
    qa = [out / n for n in data.get("qa_frames", []) if (out / n).exists()]
    return RenderResult(video=out / data["path"], thumb=thumb if thumb and thumb.exists() else None, info=data,
                        qa_frames=qa)  # fmt: skip


def collect(result: RenderResult, dest_dir: Path) -> tuple[Path, Path | None]:
    """Move outputs off the shared render volume into a private work dir."""
    v = dest_dir / "scene.mp4"
    shutil.move(str(result.video), v)
    t = None
    if result.thumb:
        t = dest_dir / "thumb.jpg"
        shutil.move(str(result.thumb), t)
    return v, t


def standalone(*, html: str, css: str, js: str, theme_css: str, width: int, height: int, title: str) -> str:
    """Self-contained auto-playing HTML of a scene (for sharing or opening in a browser)."""
    s = get_settings()
    body = {"job_id": uuid.uuid4().hex[:24], "html": html, "css": css, "js": js, "theme_css": theme_css,
            "width": width, "height": height, "fps": 30, "duration_ms": 1000, "title": title}  # fmt: skip
    try:
        r = httpx.post(f"{s.motion_renderer_url}/standalone", json=body, timeout=30)
    except httpx.HTTPError as e:
        raise RenderError(f"motion renderer unreachable: {e}") from e
    if r.status_code != 200:
        raise RenderError(f"renderer HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["html"]


def render_still(*, html: str, css: str, js: str, theme_css: str, width: int, height: int,
                 scale: float = 1.0) -> Path:  # fmt: skip
    """Final-state PNG (covers). Same sandbox as video renders."""
    s = get_settings()
    body = {"job_id": uuid.uuid4().hex[:24], "html": html, "css": css, "js": js, "theme_css": theme_css,
            "width": width, "height": height, "fps": 30, "duration_ms": 2000, "scale": scale, "still": True}  # fmt: skip
    r = _post_render(body, 120)
    data = (
        r.json() if r.status_code == 200 else {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
    )
    if not data.get("ok"):
        raise RenderError(data.get("error", "still render failed"), data.get("logs", []))
    return Path(s.render_out_dir) / data["png"]
