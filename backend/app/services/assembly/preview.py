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
import os
import tempfile
import uuid
import zlib
from concurrent.futures import ThreadPoolExecutor
from functools import partial
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
CPUS = os.cpu_count() or 2
# concurrent shot encodes and x264 threads each. Proxy clips are small (720p); a final 1080p/4K encode needs far
# more memory per job (4 parallel 4K "slow" encodes exhausted the 8 GB Docker VM and hung Docker).
JOBS = {"proxy": (max(1, min(4, CPUS // 2)), 2), "final": (2, max(2, CPUS // 2))}
PROXY_VERSION = "p2"  # bump when the proxy encode settings change: old cached clips are then rebuilt


def _escape_drawtext(s: str) -> str:
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’").replace("%", "\\%")


QUALITY = {
    # key: (meta key, asset kind, crf, preset, audio bitrate)
    "proxy": ("preview", "preview", "24", "veryfast", "160k"),
    # motion graphics at crf 18: "veryfast" measured 24 fps on 4K vs 11 fps for "slow", PSNR 54.7 vs 57.5 dB
    # (both far above the ~45 dB visibility threshold) and a smaller file; YouTube re-encodes the upload anyway
    "final": ("final", "final_render", "18", "veryfast", "320k"),
}


def _watermark(profile, cv: style.Canvas, W: int) -> str:  # noqa: N803
    """drawtext filter for the top-right watermark (shots without the brand footer), or ''."""
    text = style.watermark_text(profile)
    if not text or not FONT.exists():
        return ""
    unit = W / (540 if cv.is_short else 1280)
    size = round((18 if cv.is_short else 20) * unit)
    return (f"drawtext=fontfile={FONT}:text='{_escape_drawtext(text)}':fontsize={size}:fontcolor=white@0.72:"
            f"x=w-tw-{round(28 * unit)}:y={round(24 * unit)}:shadowcolor=black@0.5:shadowx=1:shadowy=1")  # fmt: skip


def _size(p: Project, cv: style.Canvas, quality: str) -> tuple[int, int]:
    if quality == "proxy":
        return (540, 960) if cv.is_short else (1280, 720)
    if cv.is_short:
        return cv.export_w, cv.export_h
    return (3840, 2160) if p.resolution == "3840x2160" else (1920, 1080)


def _bg(profile) -> str:
    return ((profile.animation_style or {}).get("background") or {}).get("to", "#060b14").lstrip("#")


def _proxy_cache(project_id: uuid.UUID) -> Path:
    d = get_storage().root / "cache" / "proxy" / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _take_segment(src: Path | None, out: Path, W: int, H: int, bg: str, fps: int, n: int, quality: str,  # noqa: N803
                  wm: str = "") -> None:  # fmt: skip
    """One shot, fitted to the frame and held to exactly n frames (no take → brand background), with the
    watermark burned in here so the whole video never needs a second full encode for it. The proxy downscales
    with the fast bilinear filter: shot renders are 4K, and the default scaler was most of the preview time."""
    _, _, crf, preset, _ = QUALITY[quality]
    threads = JOBS[quality][1]
    flags = ":flags=fast_bilinear" if quality == "proxy" else ""
    part = out.with_name(f"{out.stem}.{uuid.uuid4().hex[:8]}.part")  # unique: warm-up and a build may race
    if src is None:
        inp, vf = ["-f", "lavfi", "-i", f"color=c=0x{bg}:s={W}x{H}:r={fps}:d={n / fps:.3f}"], "null"
    else:
        inp = ["-i", str(src)]
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=decrease{flags},pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:"
              f"color=0x{bg},fps={fps},tpad=stop_mode=clone:stop_duration={n / fps:.3f}")  # fmt: skip
    if wm:
        vf += "," + wm
    _ffmpeg(*inp, "-vf", vf, "-frames:v", str(n), "-an", "-c:v", "libx264", "-preset", preset, "-crf", crf,
            "-pix_fmt", "yuv420p", "-r", str(fps), "-threads", str(threads), "-f", "mp4", str(part))  # fmt: skip
    part.replace(out)  # atomic: a half-written clip is never picked up from the cache


def _proxy_path(p: Project, shot: Shot, take, W: int, H: int, bg: str, wm: str = "") -> tuple[Path, int]:  # noqa: N803
    """Cached proxy clip of a take: keyed by the take and everything that changes its pixels."""
    a, b = round(shot.start_ms * p.fps / 1000), round(shot.end_ms * p.fps / 1000)
    tag = f"_wm{zlib.crc32(wm.encode()):08x}" if wm else ""
    return _proxy_cache(p.id) / f"{PROXY_VERSION}_{take.id}_{W}x{H}_{p.fps}_{b - a}_{bg}{tag}.mp4", b - a


def _encoded_audio(project_id: uuid.UUID, audio, abr: str) -> Path:
    """The final narration as AAC, cached per audio asset and bitrate (an 18-minute WAV took ~50 s per build)."""
    d = get_storage().root / "cache" / "audio" / str(project_id)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{audio.id}_{abr}.m4a"
    if not out.exists():
        for old in d.glob(f"*_{abr}.m4a"):  # a new narration replaces the old one
            old.unlink(missing_ok=True)
        part = d / f"{audio.id}_{abr}.{uuid.uuid4().hex[:8]}.part"
        _ffmpeg("-i", str(get_storage().path(audio.storage_key)), "-vn", "-c:a", "aac", "-b:a", abr, "-f", "mp4",
                str(part))  # fmt: skip
        part.replace(out)
    return out


def warm_proxy(take_id: str) -> bool:
    """After a take renders: prepare its preview clip now, so building the preview mostly just joins clips."""
    from app.models import Take

    storage = get_storage()
    with get_sessionmaker()() as db:
        take = db.get(Take, uuid.UUID(take_id))
        if (
            take is None
            or take.status != "done"
            or not take.storage_key
            or not storage.exists(take.storage_key)
        ):
            return False
        shot = db.get(Shot, take.shot_id)
        p = db.get(Project, shot.project_id)
        best = _best_take(db, shot)
        if best is None or best.id != take.id:
            return False
        profile = style.active(db)
        cv = style.canvas(profile, p)
        W, H = _size(p, cv, "proxy")  # noqa: N806
        bg = _bg(profile)
        wm = "" if cv.footer and take.generator in FOOTER_GENERATORS else _watermark(profile, cv, W)
        out, n = _proxy_path(p, shot, take, W, H, bg, wm)
        if n <= 0 or out.exists():
            return False
        _take_segment(storage.path(take.storage_key), out, W, H, bg, p.fps, n, "proxy", wm)
        return True


def build_preview(project_id: str, quality: str = "proxy") -> None:
    meta_key, kind, crf, preset, abr = QUALITY[quality]
    pid = uuid.UUID(project_id)
    with get_sessionmaker()() as db:
        p = db.get(Project, pid)
        if p is None:
            return
        meta = dict(p.plan_meta or {})
        try:
            profile = style.active(db)
            cv = style.canvas(profile, p)
            W, H = _size(p, cv, quality)  # noqa: N806
            bg = _bg(profile)
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
                listing, missing, jobs, used = [], 0, [], set()
                watermark = _watermark(profile, cv, W)
                for s in shots:
                    a, b = round(s.start_ms * fps / 1000), round(s.end_ms * fps / 1000)
                    n = b - a
                    if n <= 0:
                        continue
                    take = _best_take(db, s)
                    if take and take.storage_key and storage.exists(take.storage_key):
                        src = storage.path(take.storage_key)
                        wm = "" if cv.footer and take.generator in FOOTER_GENERATORS else watermark
                        if quality == "proxy":  # reuse the clip from the last build or from warm_proxy
                            seg, _ = _proxy_path(p, s, take, W, H, bg, wm)
                            used.add(seg.name)
                        else:
                            seg = work / f"s{s.index:04d}.mp4"
                    else:
                        missing += 1
                        src, wm, seg = None, watermark, work / f"s{s.index:04d}.mp4"
                    if not seg.exists():
                        jobs.append(partial(_take_segment, src, seg, W, H, bg, fps, n, quality, wm))
                    listing.append(f"file '{seg}'")
                # shots are independent: encode them in parallel (bounded by memory for the final render)
                with ThreadPoolExecutor(max_workers=JOBS[quality][0]) as pool:
                    list(pool.map(lambda job: job(), jobs))
                (work / "list.txt").write_text("\n".join(listing))
                video = work / "video.mp4"
                _ffmpeg("-f", "concat", "-safe", "0", "-i", str(work / "list.txt"), "-c", "copy", str(video))
                video = _facecam(p, profile, cv, video, work, W, H, crf, preset)
                out = work / f"{kind}.mp4"
                aac = _encoded_audio(p.id, audio, abr)  # the narration rarely changes: encode it once
                _ffmpeg("-i", str(video), "-i", str(aac), "-map", "0:v", "-map", "1:a", "-c", "copy",
                        "-shortest", "-movflags", "+faststart", str(out))  # fmt: skip
                info = media.media_meta(out) | {"missing_shots": missing, "shots": len(shots),
                                                "layout": "shorts" if cv.is_short else "long_form"}  # fmt: skip
                replace_derived(db, p, kind, out, "mp4", "video/mp4", info)
            if quality == "proxy":  # drop cached clips of takes/timings this build no longer uses
                for f in _proxy_cache(pid).glob("*.mp4"):
                    if f.name not in used:
                        f.unlink(missing_ok=True)
            meta[meta_key] = {"status": "done", "missing_shots": missing}
        except Exception as e:  # noqa: BLE001
            log.exception("preview failed")
            db.rollback()
            p = db.get(Project, pid)
            meta[meta_key] = {"status": "failed", "error": f"{type(e).__name__}: {e}"[:2000]}
        p.plan_meta = meta
        db.commit()


def recover_interrupted() -> int:
    """Worker boot: a preview/final build still 'running' died with its worker. Mark it failed so the page stops
    spinning and offers to build again (a redelivered build task that does run overwrites this)."""
    n = 0
    with get_sessionmaker()() as db:
        for p in db.scalars(select(Project)):
            meta = dict(p.plan_meta or {})
            for key in ("preview", "final"):
                if (meta.get(key) or {}).get("status") == "running":
                    meta[key] = {
                        "status": "failed",
                        "error": "Interrupted (worker or Docker restarted). Build again.",
                    }
                    n += 1
            if meta != (p.plan_meta or {}):
                p.plan_meta = meta
        db.commit()
    return n


def _facecam(p, profile, cv: style.Canvas, video: Path, work: Path, W: int, H: int,  # noqa: N803
             crf: str = "24", preset: str = "veryfast") -> Path:  # fmt: skip
    """Face-cam circle (long-form): the only overlay that needs a pass over the whole video (the watermark is
    burned into each shot clip)."""
    storage = get_storage()
    inputs, chains, last = ["-i", str(video)], [], "0:v"
    cam = None
    if cv.facecam_circle == "composite":
        with get_sessionmaker()() as db:
            cam = latest_asset(db, p.id, AssetKind.facecam_final)
    if cam is not None and storage.exists(cam.storage_key):
        cx, cy, d = facecam_geometry(cv, W, H)
        # a quiet frame: a thin light rim and a soft dark shadow (an accent-coloured glow pulled the eye away
        # from the content)
        ring_w = max(2, round(d * 0.012))
        glow = max(4, round(d * 0.06))
        size = d + 2 * (ring_w + 3 * glow)
        c, r_in, r_out = size / 2, d / 2 - 1, d / 2 + ring_w
        # the circle mask and the ring never change: draw each ONCE as an image (a per-pixel geq on every frame
        # of a 15-minute video was most of the preview time); the overlays repeat the single frame
        mask, ring = work / "mask.png", work / "ring.png"
        _ffmpeg("-f", "lavfi", "-i", f"color=c=black:s={d}x{d}", "-vf",
                f"format=gray,geq=lum='if(lte(hypot(X-{d / 2},Y-{d / 2}),{d / 2 - 1}),255,0)'",
                "-frames:v", "1", str(mask))  # fmt: skip
        # rim (white, 40 %) + gaussian-ish dark shadow outside it, transparent inside
        rr = f"hypot(X-{c},Y-{c})"
        rim = f"lte({rr},{r_out})"
        _ffmpeg("-f", "lavfi", "-i", f"color=c=black:s={size}x{size}", "-vf",
                f"format=rgba,geq=r='if({rim},255,0)':g='if({rim},255,0)':b='if({rim},255,0)':"
                f"a='if(lt({rr},{r_in}),0,if({rim},100,150*exp(-pow({rr}-{r_out},2)/{2 * glow * glow})))'",
                "-frames:v", "1", str(ring))  # fmt: skip
        # the still mask and ring are looped for the whole video (a single frame is only applied to frame 1)
        inputs += [
            "-i",
            str(storage.path(cam.storage_key)),
            "-loop",
            "1",
            "-i",
            str(mask),
            "-loop",
            "1",
            "-i",
            str(ring),
        ]
        chains += [
            f"[1:v]crop='min(iw,ih)':'min(iw,ih)',scale={d}:{d},format=yuva420p[c0]",
            "[c0][2:v]alphamerge[cam]",
            f"[{last}][3:v]overlay={int(cx - size / 2)}:{int(cy - size / 2)}:shortest=1[v1]",
            f"[v1][cam]overlay={int(cx - d / 2)}:{int(cy - d / 2)}:eof_action=pass[v2]",
        ]
        last = "v2"
    if not chains:
        return video
    out = work / "video_overlay.mp4"
    _ffmpeg(*inputs, "-filter_complex", ";".join(chains), "-map", f"[{last}]", "-an", "-c:v", "libx264",
            "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p", str(out))  # fmt: skip
    return out


def facecam_geometry(cv: style.Canvas, W: int, H: int) -> tuple[float, float, int]:  # noqa: N803
    """Centre (x, y) and diameter of the face-cam circle in output pixels."""
    if not cv.is_short:  # long-form: 25 % of the frame height, tucked into the bottom-right corner
        d = int(H * 0.25) // 2 * 2
        m = round(H * 0.022)
        return W - m - d / 2, H - m - d / 2, d
    if cv.safe_zones:  # shorts with a full 9:16 canvas: centred in the keep-clear zone
        z = cv.safe_zones[0]
        sx = W / cv.export_w
        d = int(min(z["w"], z["h"]) * sx * 0.9) // 2 * 2  # a margin from the frame edge
        return (z["x"] + z["w"] / 2) * sx, (z["y"] + cv.offset_y + z["h"] * 0.46) * sx, d
    # shorts with a 3:4 canvas: in the bottom band under the animation, right-aligned
    sx = W / cv.export_w
    band = cv.offset_y * sx
    d = int(band * 0.86) // 2 * 2
    return W - d / 2 - 36 * sx, H - band / 2, d
