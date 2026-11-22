"""Single-pass shot planning over the FINAL transcript (spec §5.2)."""

import json
import logging
import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import get_sessionmaker
from app.llm import client as llm
from app.models import AssetKind, Project, ProjectStatus, Shot, ShotState, Transcript
from app.services import routing, style
from app.services.ingest.pipeline import latest_asset
from app.services.planner import prompt as P
from app.services.storage import get_storage

log = logging.getLogger(__name__)

MAX_AI_CLIP_MS = 8000
_dispatch: dict = {}


def register_dispatch(name: str, fn) -> None:
    _dispatch[name] = fn


class PlanError(RuntimeError):
    pass


# ---------------------------------------------------------------- schema


class Character(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    name: str
    description: str


class StyleGuide(BaseModel):
    summary: str = ""
    mood: str = ""
    camera_language: str = ""
    typography: str = ""
    palette: dict[str, list[str]] = Field(default_factory=dict)
    characters: list[Character] = Field(default_factory=list)


class PlannedShot(BaseModel):
    from_segment: int = Field(ge=1)
    to_segment: int = Field(ge=1)
    visual_type: Literal["code_animation", "ai_clip", "still_image", "text_card", "a_roll"]
    theme: Literal["tech", "story"] = "tech"
    prompt: str = Field(min_length=10)
    depends_on_previous: bool = False
    uses_character: bool = False
    characters: list[str] = Field(default_factory=list)
    notes: str = ""


class Plan(BaseModel):
    style_guide: StyleGuide
    shots: list[PlannedShot] = Field(min_length=1)


# ---------------------------------------------------------------- segments


def segment_words(words: list[dict], max_words: int = 16, pause_ms: int = 350) -> list[dict]:
    """Phrase-level segments: break at sentence ends, long pauses, or commas in long phrases."""
    segs: list[dict] = []
    cur: list[dict] = []

    def flush():
        if cur:
            segs.append({
                "n": len(segs) + 1, "start_ms": cur[0]["start_ms"], "end_ms": cur[-1]["end_ms"],
                "text": " ".join(w["w"] for w in cur),
            })  # fmt: skip
            cur.clear()

    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start_ms"] - w["end_ms"]) if nxt else 0
        token = w["w"].rstrip()
        if (
            token.endswith((".", "?", "!", ":"))
            or gap >= pause_ms
            or (token.endswith((",", ";")) and len(cur) >= 8)
            or len(cur) >= max_words
        ):
            flush()
    flush()
    return segs


def extract_json(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = m.group(1) if m else text[text.find("{") : text.rfind("}") + 1]
    return json.loads(raw)


def validate_plan(data: dict, n_segments: int, allowed: list[str], has_facecam: bool) -> Plan:
    plan = Plan.model_validate(data)
    errors: list[str] = []
    expected = 1
    for i, s in enumerate(plan.shots, 1):
        if s.from_segment != expected:
            errors.append(f"shot {i}: from_segment={s.from_segment}, expected {expected}")
        if s.to_segment < s.from_segment:
            errors.append(f"shot {i}: to_segment < from_segment")
        if s.visual_type not in allowed:
            errors.append(f"shot {i}: visual_type {s.visual_type} is not available ({', '.join(allowed)})")
        if s.visual_type == "a_roll" and not has_facecam:
            errors.append(f"shot {i}: a_roll used but there is no face-cam")
        expected = s.to_segment + 1
    if expected != n_segments + 1:
        errors.append(f"shots end at segment {expected - 1}, but there are {n_segments} segments")
    char_ids = {c.id for c in plan.style_guide.characters}
    for i, s in enumerate(plan.shots, 1):
        unknown = set(s.characters) - char_ids
        if unknown:
            errors.append(f"shot {i}: characters {sorted(unknown)} not defined in style_guide.characters")
    if errors:
        raise PlanError("\n".join(errors))
    return plan


def build_shots(plan: Plan, segments: list[dict], words: list[dict], duration_ms: int) -> list[dict]:
    """Planned shots → contiguous [0, duration] time ranges snapped to segment/word boundaries."""
    out: list[dict] = []
    for i, s in enumerate(plan.shots):
        start = 0 if i == 0 else segments[s.from_segment - 1]["start_ms"]
        end = (
            duration_ms
            if i == len(plan.shots) - 1
            else segments[plan.shots[i + 1].from_segment - 1]["start_ms"]
        )
        base = s.model_dump(exclude={"from_segment", "to_segment"})
        if s.visual_type == "ai_clip" and end - start > MAX_AI_CLIP_MS:
            parts = -(-(end - start) // MAX_AI_CLIP_MS)
            step = (end - start) // parts
            for k in range(parts):
                a, b = start + k * step, end if k == parts - 1 else start + (k + 1) * step
                out.append({**base, "start_ms": a, "end_ms": b,
                            "depends_on_previous": base["depends_on_previous"] or k > 0})  # fmt: skip
        else:
            out.append({**base, "start_ms": start, "end_ms": end})
    for sh in out:
        sh["transcript_text"] = " ".join(
            w["w"] for w in words if w["start_ms"] >= sh["start_ms"] and w["start_ms"] < sh["end_ms"]
        )
    return out


# ---------------------------------------------------------------- entry points


def begin_planning(db: Session, p: Project) -> None:
    if p.status not in (ProjectStatus.ready, ProjectStatus.planned):
        raise PlanError(f"Project is {p.status}; finish ingest first")
    p.status, p.error = ProjectStatus.planning, None
    db.commit()
    _dispatch["plan"](str(p.id))


def run_planning(project_id: str) -> None:
    pid = uuid.UUID(project_id)
    with get_sessionmaker()() as db:
        p = db.get(Project, pid)
        if p is None:
            return
        try:
            _plan(db, p)
        except Exception as e:  # noqa: BLE001
            log.exception("planning failed")
            db.rollback()
            p = db.get(Project, pid)
            has_shots = db.scalar(select(Shot.id).where(Shot.project_id == pid).limit(1)) is not None
            p.status = ProjectStatus.planned if has_shots else ProjectStatus.ready
            p.error = f"Planning failed: {type(e).__name__}: {e}"[:4000]
            db.commit()


def _plan(db: Session, p: Project) -> None:
    transcript = db.scalar(
        select(Transcript).where(Transcript.project_id == p.id, Transcript.is_final.is_(True))
        .order_by(Transcript.version.desc())
    )  # fmt: skip
    audio = latest_asset(db, p.id, AssetKind.audio_final)
    script_asset = latest_asset(db, p.id, AssetKind.script)
    if transcript is None or audio is None or not transcript.words:
        raise PlanError("final transcript missing")
    script = get_storage().path(script_asset.storage_key).read_text("utf-8-sig") if script_asset else ""
    words = transcript.words
    duration = int(audio.meta.get("duration_ms") or words[-1]["end_ms"])
    segments = segment_words(words)
    has_facecam = latest_asset(db, p.id, AssetKind.facecam_final) is not None
    cfg = routing.load()
    profile = style.active(db)
    long_form = not style.is_short(p.content_type)
    # long-form: the face cam is a persistent PiP circle, never an a_roll shot (spec v2.1 §3.1)
    allowed = [
        t for t in cfg.planner.allowed_visual_types if t != "a_roll" or (has_facecam and not long_form)
    ]
    brief = style.planner_brief(profile, p, has_facecam)
    p.style_profile_version = profile.version

    messages = [{"role": "user", "content": P.user_message(script, segments, duration, has_facecam, allowed,
                                                           p.style_notes, brief)}]  # fmt: skip
    total_cost, used = 0.0, cfg.planner.model
    plan: Plan | None = None
    last_err = ""
    for attempt in range(2):  # one retry with the validation errors (spec)
        text, res = llm.chat(cfg.planner.model, messages, system=P.SYSTEM, fallbacks=cfg.planner.fallbacks,
                             max_tokens=16000, temperature=0.5)  # fmt: skip
        total_cost += res.cost_usd
        used = res.alias
        try:
            plan = validate_plan(extract_json(text), len(segments), allowed, has_facecam)
            break
        except (ValueError, ValidationError, PlanError) as e:
            last_err = str(e)[:4000]
            log.warning("plan attempt %d invalid: %s", attempt + 1, last_err)
            messages += [
                {"role": "assistant", "content": text},
                {"role": "user", "content": P.repair_message(last_err)},
            ]
    if plan is None:
        raise PlanError(f"planner output invalid after retry: {last_err}")

    shots = build_shots(plan, segments, words, duration)
    db.execute(delete(Shot).where(Shot.project_id == p.id))
    for i, s in enumerate(shots):
        db.add(Shot(
            project_id=p.id, index=i, start_ms=s["start_ms"], end_ms=s["end_ms"],
            transcript_text=s["transcript_text"], visual_type=s["visual_type"], theme=s["theme"],
            prompt=s["prompt"], notes=s["notes"], depends_on_previous=s["depends_on_previous"],
            uses_character=s["uses_character"] or bool(s["characters"]), state=ShotState.planned,
        ))  # fmt: skip
    version = int((p.style_guide or {}).get("version", 0)) + 1
    p.style_guide = {"version": version, **plan.style_guide.model_dump(),
                     "shot_characters": {str(i): s["characters"] for i, s in enumerate(shots)}}  # fmt: skip
    p.plan_meta = {"model": used, "cost_usd": round(total_cost, 4), "segments": len(segments),
                   "attempts": attempt + 1}  # fmt: skip
    p.status, p.error = ProjectStatus.planned, None
    db.commit()
