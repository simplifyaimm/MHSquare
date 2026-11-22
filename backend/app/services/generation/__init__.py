"""Shot generation: one Take per attempt, routed by visual type (spec §5.4).

Generators
  html_motion  code_animation: LLM writes an HTML/GSAP scene → sandboxed Chromium renders MP4
  template     text_card: local template, no AI cost
  passthrough  a_roll: the cut face-cam segment
  image        still_image: AI illustration + animated push-in/callouts
  video        ai_clip: keyframe + video model (falls back to the image path until a video model is configured)
"""

import base64
import hashlib
import html as htmllib
import json
import logging
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_sessionmaker
from app.llm import client as llm
from app.llm.errors import ErrorKind, LLMError
from app.models import Asset, AssetKind, Project, Shot, ShotState, Take, Transcript
from app.models.style import StyleProfile
from app.services import routing, style
from app.services.generation import prompts as GP
from app.services.generation import renderer
from app.services.generation import scene as S
from app.services.generation.lint import lint as lint_scene
from app.services.ingest.pipeline import latest_asset
from app.services.storage import get_storage

log = logging.getLogger(__name__)
GENERATOR_VERSION = "2"  # bump when prompts/themes change so the cache does not reuse old looks


class GenerationError(RuntimeError):
    def __init__(self, message: str, logs: list[str] | None = None):
        super().__init__(message)
        self.logs = logs or []


# ---------------------------------------------------------------- helpers


def cache_key(*, visual_type: str, model: str | None, prompt: str, style_version: int, refs_hash: str,
              seed: int, duration_ms: int, resolution: str, extra: str = "") -> str:  # fmt: skip
    payload = json.dumps([visual_type, model, prompt, style_version, refs_hash, seed, duration_ms, resolution,
                          extra, GENERATOR_VERSION])  # fmt: skip
    return hashlib.sha256(payload.encode()).hexdigest()


def shot_words(db: Session, shot: Shot) -> list[dict]:
    t = db.scalar(
        select(Transcript).where(Transcript.project_id == shot.project_id, Transcript.is_final.is_(True))
        .order_by(Transcript.version.desc())
    )  # fmt: skip
    if t is None:
        return []
    return [
        {"w": w["w"], "t": round((w["start_ms"] - shot.start_ms) / 1000, 2)}
        for w in t.words
        if shot.start_ms <= w["start_ms"] < shot.end_ms
    ]


def _store(db: Session, p: Project, take: Take, video: Path, thumb: Path | None) -> None:
    storage = get_storage()
    key = storage.new_key(p.id, "mp4")
    size, digest = storage.put_file(key, video)
    a = Asset(project_id=p.id, kind="take_video", storage_key=key, sha256=digest, size=size, mime="video/mp4",
              meta={"take_id": str(take.id)})  # fmt: skip
    db.add(a)
    take.storage_key = key
    ta = None
    if thumb:
        tkey = storage.new_key(p.id, "jpg")
        tsize, tdigest = storage.put_file(tkey, thumb)
        ta = Asset(project_id=p.id, kind="take_thumb", storage_key=tkey, sha256=tdigest, size=tsize,
                   mime="image/jpeg", meta={"take_id": str(take.id)})  # fmt: skip
        db.add(ta)
        take.thumb_key = tkey
    db.flush()
    take.meta = {**(take.meta or {}), "asset_id": str(a.id), "thumb_asset_id": str(ta.id) if ta else None}


def _ffmpeg(*args: str) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", *args], capture_output=True, check=False
    )  # noqa: S603, S607
    if proc.returncode:
        raise GenerationError("ffmpeg: " + proc.stderr.decode(errors="replace")[-800:])


def _thumb_from(video: Path, out: Path, at_s: float) -> Path:
    _ffmpeg("-ss", f"{at_s:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out))
    return out


# ---------------------------------------------------------------- characters


def ensure_characters(db: Session, p: Project, ids: list[str]) -> tuple[dict[str, str], list[dict], float]:
    """Draw each needed character once per project (SVG rig) and keep it in the style guide."""
    sg = dict(p.style_guide or {})
    chars = {c["id"]: c for c in sg.get("characters", [])}
    svgs = dict(sg.get("character_svgs", {}))
    cost = 0.0
    route = routing.resolve("code_animation")
    for cid in ids:
        if cid in svgs or cid not in chars:
            continue
        c = chars[cid]
        text, res = llm.chat(route.model, [{"role": "user", "content": f"Character: {c['name']} — {c['description']}"}],
                             system=GP.CHARACTER_SYSTEM, fallbacks=route.fallbacks, max_tokens=12000,
                             temperature=0.4)  # fmt: skip
        cost += res.cost_usd
        svgs[cid] = S.parse_svg(text)
    if svgs != sg.get("character_svgs"):
        sg["character_svgs"] = svgs
        p.style_guide = sg
        db.commit()
    return {k: svgs[k] for k in ids if k in svgs}, [chars[k] for k in ids if k in chars], cost


def _shot_character_ids(p: Project, shot: Shot) -> list[str]:
    sg = p.style_guide or {}
    ids = (sg.get("shot_characters") or {}).get(str(shot.index), [])
    if not ids and shot.uses_character:
        ids = [c["id"] for c in sg.get("characters", [])]
    return ids


# ---------------------------------------------------------------- html_motion


def _render_scene(p: Project, sc: S.Scene, theme: str, svgs: dict[str, str], duration_ms: int,
                  extra_html: str = "", extra_css: str = "", probe: str | None = "fail",
                  profile: StyleProfile | None = None) -> renderer.RenderResult:  # fmt: skip
    """Render on the content type's canvas with the brand theme + injected footer (Style Profile)."""
    profile = profile or _profile_for(p)
    cv = style.canvas(profile, p)
    scale = 2.0 if (not cv.is_short and p.resolution == "3840x2160") else 1.0
    return renderer.render(
        html=S.character_templates(svgs) + extra_html + sc.html + style.footer_html(profile, cv),
        css=extra_css + sc.css,
        js=GP.RUNTIME_JS + "\n" + sc.js,
        theme_css=style.theme_css(profile, p, theme, cv),
        width=cv.stage_w, height=cv.stage_h, fps=p.fps, duration_ms=duration_ms, scale=scale,
        probe={"safe_zones": cv.safe_zones, "mode": probe} if probe else None,
    )  # fmt: skip


def _profile_for(p: Project) -> StyleProfile:
    with get_sessionmaker()() as db:
        prof = style.active(db)
        db.expunge(prof)
        return prof


def _lint(sc: S.Scene, profile: StyleProfile, theme: str) -> list[str]:
    a = profile.animation_style or {}
    b = profile.brand or {}
    return lint_scene(
        sc,
        palette=list((a.get("palette") or {}).values()) + list(a.get("neutrals") or []),
        allowed_fonts=a.get("allowed_fonts") or ["Oswald", "Poppins", "JetBrains Mono", "Consolas"],
        theme=theme, story_palette=a.get("story_palette") or [],
        handle=(a.get("footer") or {}).get("right") or b.get("handle", ""),
        tagline=(a.get("footer") or {}).get("left", ""),
    )  # fmt: skip


def write_and_render(db: Session, p: Project, shot: Shot, take: Take, model: str, fallbacks: list[str], *,
                     brief: str, extra_html: str = "", extra_css: str = "") -> tuple[Path, Path | None]:  # fmt: skip
    """LLM writes the scene → static checks → render. One repair round for check OR render errors."""
    char_ids = _shot_character_ids(p, shot) if shot.theme == "story" else []
    svgs, chars, cost = ensure_characters(db, p, char_ids)
    previous = None
    if shot.depends_on_previous and shot.index > 0:
        prev = db.scalar(select(Shot).where(Shot.project_id == p.id, Shot.index == shot.index - 1))
        ptake = _best_take(db, prev) if prev and prev.theme == shot.theme else None  # only continue within a mode
        previous = ptake.code if ptake else None
    user = GP.scene_user(
        prompt=brief, duration_ms=shot.duration_ms, words=shot_words(db, shot), style_guide=p.style_guide or {},
        theme=shot.theme, characters=chars, previous=previous, feedback=take.feedback_note,
        transcript=shot.transcript_text,
    )  # fmt: skip
    profile = style.active(db)
    cv = style.canvas(profile, p)
    messages = [{"role": "user", "content": user}]
    system = (
        GP.scene_system(shot.theme, cv.stage_w, cv.stage_h)
        + "\n\n"
        + style.animation_rules(profile, p, shot.theme, cv)
    )
    take.prompt_used = user
    take.meta = {**(take.meta or {}), "style_profile_version": profile.version, "canvas": cv.label, "theme": shot.theme}
    logs: list[str] = []
    for attempt in range(2):
        text, res = llm.chat(
            model, messages, system=system, fallbacks=fallbacks, max_tokens=16000, temperature=0.5
        )
        cost += res.cost_usd
        take.model = res.alias
        take.cost_usd = round(cost, 5)
        try:
            sc = S.parse(text)
            S.check(sc)  # security: always enforced
            issues = _lint(sc, profile, shot.theme)
            if issues and attempt == 0:
                raise S.SceneError("style lint: " + " | ".join(issues))
            take.code = json.dumps(sc.as_dict())
            # first attempt: layout problems abort before rendering (cheap) and go back to the LLM;
            # after the fix round they're recorded as warnings and the shot still renders
            result = _render_scene(p, sc, shot.theme, svgs, shot.duration_ms, extra_html, extra_css,
                                   probe="fail" if attempt == 0 else "warn", profile=profile)  # fmt: skip
            take.meta = {**(take.meta or {}), "render_s": result.info.get("render_s"),
                         "timeline_s": result.info.get("timeline_s"), "fix_rounds": attempt,
                         "lint": "pass" if not issues else "warnings", "lint_warnings": issues,
                         "layout_warnings": result.info.get("layout_warnings", [])}  # fmt: skip
            logs += result.info.get("logs", [])
            take.logs = logs[-60:]
            return result.video, result.thumb
        except (S.SceneError, renderer.RenderError) as e:
            elogs = getattr(e, "logs", [])
            logs += [f"attempt {attempt + 1}: {e}", *elogs]
            take.logs = logs[-60:]
            if attempt == 1:
                raise GenerationError(f"scene failed after a fix attempt: {e}", logs) from e
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": GP.fix_user(str(e), elogs)},
            ]
    raise GenerationError("unreachable")


# ---------------------------------------------------------------- text card (template, free)


_QUOTED = re.compile(r"[\"“”']([^\"“”']{2,80})[\"“”']")


def text_card_scene(shot: Shot) -> S.Scene:
    quoted = _QUOTED.findall(shot.prompt)
    words = shot.transcript_text.split()
    title = quoted[0] if quoted else " ".join(words[:7]).rstrip(",.;:") or "…"
    sub = quoted[1] if len(quoted) > 1 else ""
    eyebrow = quoted[2] if len(quoted) > 2 else ""
    e = htmllib.escape
    html = f"""<div class="mh-bg"></div><div class="mh-glow" style="left:510px;top:90px"></div>
<div class="center tc col gap-24" id="card" style="width:calc(var(--stage-w) - 2*var(--safe-x));align-items:center">
  {f'<div class="mh-eyebrow" id="eb">{e(eyebrow)}</div>' if eyebrow else ""}
  <div class="mh-title xl" id="tt">{e(title)}</div>
  {f'<div class="mh-sub" id="sb" style="font-size:32px">{e(sub)}</div>' if sub else ""}
</div>"""
    beats = ["tl.fromTo('#card', {scale:1}, {scale:1.035, duration:D, ease:'none'}, 0);"]
    if eyebrow:
        beats.append(
            "tl.from('#eb', {opacity:0, y:16, filter:'blur(6px)', duration:.6, ease:'power3.out'}, .1);"
        )
    beats.append(
        "tl.from('#tt', {opacity:0, y:34, filter:'blur(10px)', duration:.9, ease:'power3.out'}, .2);"
    )
    if sub:
        beats.append(
            "tl.from('#sb', {opacity:0, y:20, filter:'blur(6px)', duration:.7, ease:'power3.out'}, .7);"
        )
    js = (f"const tl = gsap.timeline(); window.__scene = tl;\nconst D = {shot.duration_ms / 1000:.3f};\n"
          + "\n".join(beats) + "\ntl.set({}, {}, D);")  # fmt: skip
    return S.Scene(html=html, css="", js=js)


# ---------------------------------------------------------------- a_roll (passthrough)


def a_roll_clip(db: Session, p: Project, shot: Shot, work: Path) -> Path:
    cam = latest_asset(db, p.id, AssetKind.facecam_final)
    if cam is None:
        raise GenerationError("a_roll needs a face-cam video")
    cv = style.canvas(style.active(db), p)
    if cv.is_short:  # shorts: face segments fill the vertical frame
        w, h = cv.export_w, cv.export_h
    else:
        w, h = (3840, 2160) if p.resolution == "3840x2160" else (1920, 1080)
    out = work / "aroll.mp4"
    dur = shot.duration_ms / 1000
    _ffmpeg("-ss", f"{shot.start_ms / 1000:.3f}", "-i", str(get_storage().path(cam.storage_key)), "-t", f"{dur:.3f}",
            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps={p.fps},"
                   f"tpad=stop_mode=clone:stop_duration={dur:.3f},format=yuv420p",
            "-frames:v", str(round(dur * p.fps)), "-an", "-c:v", "libx264", "-crf", "16", "-preset", "medium", str(out))  # fmt: skip
    return out


# ---------------------------------------------------------------- image (+ animated push-in / callouts)


def image_scene(
    db: Session, p: Project, shot: Shot, take: Take, route: routing.Resolved, work: Path
) -> tuple[Path, Path | None]:
    sg = p.style_guide or {}
    chars = {c["id"]: c for c in sg.get("characters", [])}
    who = "; ".join(
        f"{chars[c]['name']}: {chars[c]['description']}" for c in _shot_character_ids(p, shot) if c in chars
    )
    art = ("Flat 2D vector cartoon illustration, clean dark outlines, soft shading, warm friendly mood, soft teal "
           "palette" if shot.theme == "story" else
           "Polished cinematic digital illustration, deep navy and electric blue palette, soft glow")  # fmt: skip
    prompt = (
        f"{art}. {shot.prompt}. {('Characters: ' + who) if who else ''} Widescreen 16:9. No text, no letters."
    )
    if take.feedback_note:
        prompt += f" Revision feedback: {take.feedback_note}"
    img, res = llm.generate_image(
        route.model or "image-gen", prompt, size="1536x1024", fallbacks=route.fallbacks
    )
    take.cost_usd = round(take.cost_usd + res.cost_usd, 5)
    take.model = res.alias
    src = work / "img.bin"
    src.write_bytes(img)
    jpg = work / "img.jpg"
    _ffmpeg(
        "-i",
        str(src),
        "-vf",
        "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080",
        "-q:v",
        "3",
        str(jpg),
    )
    data = base64.b64encode(jpg.read_bytes()).decode()
    extra_css = (
        f"#mh-image{{position:absolute;inset:0;background:url(data:image/jpeg;base64,{data}) center/cover}}"
    )
    brief = (f"{shot.prompt}\n\nAn AI illustration already fills the stage as the element #mh-image (do not recreate "
             "it, do not add a background). Animate a slow camera push-in on #mh-image (scale 1 → 1.12, toward the "
             "subject) and add 1–3 tasteful animated callouts (labels, bubbles, badges, sparkles) synced to the words.")  # fmt: skip
    return write_and_render(db, p, shot, take, route.route.llm_model or "code-llm", [], brief=brief,
                            extra_html='<div id="mh-image"></div>', extra_css=extra_css)  # fmt: skip


# ---------------------------------------------------------------- orchestration


def _best_take(db: Session, shot: Shot) -> Take | None:
    if shot.accepted_take_id:
        t = db.get(Take, shot.accepted_take_id)
        if t and t.status == "done":
            return t
    done = [t for t in shot.takes if t.status == "done" and t.kind == "final"]
    return done[-1] if done else None


def queue_shot(db: Session, shot: Shot, note: str | None = None, keep_seed: bool = False) -> Take:
    """Create the next Take (pending) and mark the shot queued. The worker fills it in."""
    attempt = max((t.attempt for t in shot.takes), default=0) + 1
    last = shot.takes[-1] if shot.takes else None
    seed = last.seed if (keep_seed and last) else uuid.uuid4().int % 2_000_000_000
    route = routing.resolve(shot.visual_type, shot.override_model)
    take = Take(shot_id=shot.id, attempt=attempt, generator=route.generator, seed=seed,
                feedback_note=(note or "").strip() or None, status="pending")  # fmt: skip
    db.add(take)
    shot.state, shot.error = ShotState.queued, None
    db.flush()
    return take


def run_take(take_id: str) -> None:
    """Worker entry point. All state transitions are persisted (crash-safe)."""
    tid = uuid.UUID(take_id)
    with get_sessionmaker()() as db:
        take = db.get(Take, tid)
        if take is None or take.status == "done":
            return
        shot = db.get(Shot, take.shot_id)
        p = db.get(Project, shot.project_id)
        route = routing.resolve(shot.visual_type, shot.override_model)
        take.status, shot.state = "running", ShotState.generating
        db.commit()

        feedback = take.feedback_note or ""
        key = cache_key(visual_type=shot.visual_type, model=route.model, prompt=shot.prompt + "|" + shot.theme,
                        style_version=int((p.style_guide or {}).get("version", 0)), refs_hash="",
                        seed=take.seed, duration_ms=shot.duration_ms, resolution=p.resolution,
                        extra=f"{route.generator}|{feedback}|{shot.start_ms}|{p.content_type}|sp{p.style_profile_version}"
                                f"|{style.active(db).version}")  # fmt: skip
        take.cache_key = key
        hit = db.scalar(select(Take).where(Take.cache_key == key, Take.status == "done", Take.id != take.id))
        if hit and hit.storage_key and get_storage().exists(hit.storage_key):
            take.storage_key, take.thumb_key, take.code = hit.storage_key, hit.thumb_key, hit.code
            take.meta = {**hit.meta, "cache_hit_of": str(hit.id)}
            take.model, take.cost_usd, take.status = hit.model, 0.0, "done"
            shot.state = ShotState.review
            db.commit()
            return

        try:
            with tempfile.TemporaryDirectory(dir=_work_root()) as wd:
                work = Path(wd)
                if route.generator == "html_animation":
                    video, thumb = write_and_render(
                        db, p, shot, take, route.model, route.fallbacks, brief=shot.prompt
                    )
                elif route.generator == "template":
                    take.model = "template"
                    sc = text_card_scene(shot)
                    take.code = json.dumps(sc.as_dict())
                    r = _render_scene(p, sc, "tech", {}, shot.duration_ms, probe="warn")
                    video, thumb = r.video, r.thumb
                elif route.generator == "passthrough":
                    take.model = "facecam"
                    video = a_roll_clip(db, p, shot, work)
                    thumb = _thumb_from(video, work / "t.jpg", shot.duration_ms / 2000)
                elif route.generator in ("image", "video"):
                    if route.generator == "video":
                        img_route = routing.resolve("still_image")
                        take.meta = {
                            **(take.meta or {}),
                            "video_fallback": "keyframe image + motion (no video model yet)",
                        }
                        video, thumb = image_scene(db, p, shot, take, img_route, work)
                    else:
                        video, thumb = image_scene(db, p, shot, take, route, work)
                else:
                    raise GenerationError(f"generator {route.generator} not available")
                local_v = work / "out.mp4"
                shutil.move(str(video), local_v)
                local_t = None
                if thumb:
                    local_t = work / "out.jpg"
                    shutil.move(str(thumb), local_t)
                _store(db, p, take, local_v, local_t)
            take.status, take.error = "done", None
            shot.state, shot.error = ShotState.review, None
            db.commit()
        except LLMError as e:
            db.rollback()
            _fail(tid, e, {ErrorKind.budget: ShotState.paused_budget,
                           ErrorKind.content_policy: ShotState.needs_edit}.get(e.kind, ShotState.failed))  # fmt: skip
        except Exception as e:  # noqa: BLE001
            log.exception("take %s failed", take_id)
            db.rollback()
            _fail(tid, e, ShotState.failed, getattr(e, "logs", None))


def _fail(take_id: uuid.UUID, err: Exception, state: str, logs: list[str] | None = None) -> None:
    with get_sessionmaker()() as db:
        take = db.get(Take, take_id)
        shot = db.get(Shot, take.shot_id)
        msg = f"{type(err).__name__}: {err}"[:4000]
        take.status, take.error = "failed", msg
        if logs:
            take.logs = (take.logs or []) + logs[-40:]
        shot.state, shot.error = state, msg
        db.commit()


def _work_root() -> Path:
    d = get_storage().root / "tmp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def standalone_html(db: Session, take: Take) -> str:
    """The take's scene as a self-contained auto-playing HTML file (same brand theme, footer, characters)."""
    shot = db.get(Shot, take.shot_id)
    p = db.get(Project, shot.project_id)
    if not take.code:
        raise GenerationError("this take has no scene source")
    sc = S.Scene(**json.loads(take.code))
    profile = style.active(db)
    cv = style.canvas(profile, p)
    svgs = (p.style_guide or {}).get("character_svgs", {}) if shot.theme == "story" else {}
    return renderer.standalone(
        html=S.character_templates(svgs) + sc.html + style.footer_html(profile, cv), css=sc.css,
        js=GP.RUNTIME_JS + "\n" + sc.js, theme_css=style.theme_css(profile, p, shot.theme, cv),
        width=cv.stage_w, height=cv.stage_h, title=f"{p.name} — shot {shot.index + 1}",
    )  # fmt: skip
