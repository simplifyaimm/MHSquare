"""Client for the sandboxed motion renderer (renderers/motion)."""

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.config import get_settings


class RenderError(RuntimeError):
    def __init__(self, message: str, logs: list[str] | None = None):
        super().__init__(message)
        self.logs = logs or []


@dataclass
class RenderResult:
    video: Path
    thumb: Path | None
    info: dict


def render(*, html: str, css: str, js: str, theme_css: str, width: int, height: int, fps: int,
           duration_ms: int, scale: float = 1.0) -> RenderResult:  # fmt: skip
    s = get_settings()
    job = uuid.uuid4().hex[:24]
    body = {
        "job_id": job, "html": html, "css": css, "js": js, "theme_css": theme_css,
        "width": width, "height": height, "fps": fps, "duration_ms": duration_ms, "scale": scale,
    }  # fmt: skip
    try:
        r = httpx.post(f"{s.motion_renderer_url}/render", json=body, timeout=300)
    except httpx.HTTPError as e:
        raise RenderError(f"motion renderer unreachable: {e}") from e
    if r.status_code != 200:
        raise RenderError(f"renderer HTTP {r.status_code}: {r.text[:500]}")
    data = r.json()
    if not data.get("ok"):
        raise RenderError(data.get("error", "render failed"), data.get("logs", []))
    out = Path(s.render_out_dir)
    thumb = out / data["thumb"] if data.get("thumb") else None
    return RenderResult(
        video=out / data["path"], thumb=thumb if thumb and thumb.exists() else None, info=data
    )


def collect(result: RenderResult, dest_dir: Path) -> tuple[Path, Path | None]:
    """Move outputs off the shared render volume into a private work dir."""
    v = dest_dir / "scene.mp4"
    shutil.move(str(result.video), v)
    t = None
    if result.thumb:
        t = dest_dir / "thumb.jpg"
        shutil.move(str(result.thumb), t)
    return v, t
