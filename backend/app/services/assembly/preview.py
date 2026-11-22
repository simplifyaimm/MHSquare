"""Low-res proxy preview: every shot's best take on the narration timeline (720p, fast).

The full-quality single render + FCPXML/SRT exports are phase 7. Frame counts are computed from
absolute timeline positions so shot boundaries never drift against the audio.
"""

import logging
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.models import AssetKind, Project, Shot
from app.services.generation import _best_take, _ffmpeg
from app.services.ingest import media
from app.services.ingest.pipeline import latest_asset, replace_derived
from app.services.storage import get_storage

log = logging.getLogger(__name__)
W, H = 1280, 720
PLACEHOLDER = "0x0b1222"


def build_preview(project_id: str) -> None:
    pid = uuid.UUID(project_id)
    with get_sessionmaker()() as db:
        p = db.get(Project, pid)
        if p is None:
            return
        meta = dict(p.plan_meta or {})
        try:
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
                listing = []
                missing = 0
                for s in shots:
                    n = round(s.end_ms * fps / 1000) - round(s.start_ms * fps / 1000)
                    if n <= 0:
                        continue
                    dur = n / fps
                    seg = work / f"s{s.index:04d}.mp4"
                    take = _best_take(db, s)
                    common = ["-frames:v", str(n), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                              "-pix_fmt", "yuv420p", "-r", str(fps), str(seg)]  # fmt: skip
                    if take and take.storage_key and storage.exists(take.storage_key):
                        _ffmpeg("-i", str(storage.path(take.storage_key)), "-vf",
                                f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
                                f"fps={fps},tpad=stop_mode=clone:stop_duration={dur:.3f}", *common)  # fmt: skip
                    else:
                        missing += 1
                        _ffmpeg(
                            "-f",
                            "lavfi",
                            "-i",
                            f"color=c={PLACEHOLDER}:s={W}x{H}:r={fps}:d={dur:.3f}",
                            *common,
                        )
                    listing.append(f"file '{seg.name}'")
                (work / "list.txt").write_text("\n".join(listing))
                video = work / "video.mp4"
                _ffmpeg("-f", "concat", "-safe", "0", "-i", str(work / "list.txt"), "-c", "copy", str(video))
                out = work / "preview.mp4"
                _ffmpeg("-i", str(video), "-i", str(storage.path(audio.storage_key)), "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out))  # fmt: skip
                info = media.media_meta(out) | {"missing_shots": missing, "shots": len(shots)}
                replace_derived(db, p, "preview", out, "mp4", "video/mp4", info)
            meta["preview"] = {"status": "done", "missing_shots": missing}
        except Exception as e:  # noqa: BLE001
            log.exception("preview failed")
            db.rollback()
            p = db.get(Project, pid)
            meta["preview"] = {"status": "failed", "error": f"{type(e).__name__}: {e}"[:2000]}
        p.plan_meta = meta
        db.commit()
