"""Low-res proxy preview: every shot's best take on the narration timeline.

Layout follows the Style Profile (v2.1):
  long-form  1280×720; face-cam composited as a circle in the bottom-right safe area
             (layout.facecam_circle == composite); watermark top-right on shots without the footer.
  shorts     540×960 (proxy of 1080×1920); the animation canvas (3:4 or 9:16) is centred on the brand
             background; face (a_roll) segments fill the frame.
The full-quality single render + FCPXML/SRT exports are phase 7. Frame counts come from absolute
timeline positions so shot boundaries never drift against the audio.
"""

import logging
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.models import AssetKind, Project, Shot
from app.services import style
from app.services.generation import _best_take, _ffmpeg
from app.services.ingest import media
from app.services.ingest.pipeline import latest_asset, replace_derived
from app.services.storage import get_storage

log = logging.getLogger(__name__)
FONT = Path("/usr/share/fonts/brand/Poppins-SemiBold.ttf")
FOOTER_GENERATORS = {"html_motion", "html_animation", "template", "image", "video"}


def _escape_drawtext(s: str) -> str:
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


def build_preview(project_id: str) -> None:
    pid = uuid.UUID(project_id)
    with get_sessionmaker()() as db:
        p = db.get(Project, pid)
        if p is None:
            return
        meta = dict(p.plan_meta or {})
        try:
            profile = style.active(db)
            cv = style.canvas(profile, p)
            W, H = (540, 960) if cv.is_short else (1280, 720)  # noqa: N806
            bg = ((profile.animation_style or {}).get("background") or {}).get("to", "#060b14").lstrip("#")
            shots = list(db.scalars(select(Shot).where(Shot.project_id == pid).order_by(Shot.index)))
            audio = latest_asset(db, pid, AssetKind.audio_final)
            if not shots or audio is None:
                raise RuntimeError("needs a shot list and the final narration")
            storage = get_storage()
            fps = p.fps
            root = storage.root / "tmp"
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=root) as wd:
                work = Path(wd)
                listing, missing, wm_windows = [], 0, []
                for s in shots:
                    a, b = round(s.start_ms * fps / 1000), round(s.end_ms * fps / 1000)
                    n = b - a
                    if n <= 0:
                        continue
                    dur = n / fps
                    seg = work / f"s{s.index:04d}.mp4"
                    take = _best_take(db, s)
                    common = ["-frames:v", str(n), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                              "-pix_fmt", "yuv420p", "-r", str(fps), str(seg)]  # fmt: skip
                    if take and take.storage_key and storage.exists(take.storage_key):
                        _ffmpeg("-i", str(storage.path(take.storage_key)), "-vf",
                                f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x{bg},"
                                f"fps={fps},tpad=stop_mode=clone:stop_duration={dur:.3f}", *common)  # fmt: skip
                        has_footer = cv.footer and take.generator in FOOTER_GENERATORS
                    else:
                        missing += 1
                        _ffmpeg("-f", "lavfi", "-i", f"color=c=0x{bg}:s={W}x{H}:r={fps}:d={dur:.3f}", *common)
                        has_footer = False
                    if not has_footer:
                        wm_windows.append((a / fps, b / fps))
                    listing.append(f"file '{seg.name}'")
                (work / "list.txt").write_text("\n".join(listing))
                video = work / "video.mp4"
                _ffmpeg("-f", "concat", "-safe", "0", "-i", str(work / "list.txt"), "-c", "copy", str(video))
                video = _overlays(p, profile, cv, video, work, W, H, wm_windows)
                out = work / "preview.mp4"
                _ffmpeg("-i", str(video), "-i", str(storage.path(audio.storage_key)), "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out))  # fmt: skip
                info = media.media_meta(out) | {"missing_shots": missing, "shots": len(shots),
                                                "layout": "shorts" if cv.is_short else "long_form"}  # fmt: skip
                replace_derived(db, p, "preview", out, "mp4", "video/mp4", info)
            meta["preview"] = {"status": "done", "missing_shots": missing}
        except Exception as e:  # noqa: BLE001
            log.exception("preview failed")
            db.rollback()
            p = db.get(Project, pid)
            meta["preview"] = {"status": "failed", "error": f"{type(e).__name__}: {e}"[:2000]}
        p.plan_meta = meta
        db.commit()


def _overlays(p, profile, cv: style.Canvas, video: Path, work: Path, W: int, H: int,  # noqa: N803
              wm_windows: list[tuple[float, float]]) -> Path:  # fmt: skip
    """Face-cam circle (long-form) + top-right watermark on shots without the footer."""
    storage = get_storage()
    inputs, chains, last = ["-i", str(video)], [], "0:v"
    cam = None
    if not cv.is_short and cv.facecam_circle == "composite":
        with get_sessionmaker()() as db:
            cam = latest_asset(db, p.id, AssetKind.facecam_final)
    if cam is not None and storage.exists(cam.storage_key):
        zone = cv.safe_zones[0]
        sx = W / cv.stage_w
        d = int(min(zone["w"], zone["h"]) * sx * 0.82) // 2 * 2
        cx = int((zone["x"] + zone["w"] / 2) * sx - d / 2)
        cy = int((zone["y"] + zone["h"] / 2) * sx - d / 2)
        ring = d + 6
        acc = style.accent(profile, p).lstrip("#")
        inputs += [
            "-i",
            str(storage.path(cam.storage_key)),
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x{acc}:s={ring}x{ring}",
        ]
        chains += [
            f"[1:v]crop='min(iw,ih)':'min(iw,ih)',scale={d}:{d},format=yuva420p,"
            f"geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':a='if(lte(hypot(X-{d / 2},Y-{d / 2}),{d / 2 - 1}),255,0)'[cam]",
            f"[2:v]format=yuva420p,geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':"
            f"a='if(lte(hypot(X-{ring / 2},Y-{ring / 2}),{ring / 2 - 1}),255,0)'[ring]",
            f"[{last}][ring]overlay={cx - 3}:{cy - 3}:shortest=1[v1]",
            "[v1][cam]overlay=" + f"{cx}:{cy}:eof_action=pass[v2]",
        ]
        last = "v2"
    text = style.watermark_text(profile)
    if wm_windows and text and FONT.exists():
        enable = "+".join(f"between(t,{a:.3f},{b - 0.001:.3f})" for a, b in wm_windows)
        size = 18 if cv.is_short else 20
        chains.append(
            f"[{last}]drawtext=fontfile={FONT}:text='{_escape_drawtext(text)}':fontsize={size}:"
            f"fontcolor=white@0.72:x=w-tw-28:y=24:shadowcolor=black@0.5:shadowx=1:shadowy=1:enable='{enable}'[v3]"
        )
        last = "v3"
    if not chains:
        return video
    out = work / "video_overlay.mp4"
    _ffmpeg(*inputs, "-filter_complex", ";".join(chains), "-map", f"[{last}]", "-an", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p", str(out))  # fmt: skip
    return out
