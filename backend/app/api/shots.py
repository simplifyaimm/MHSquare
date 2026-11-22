import json
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.media_urls import signed_media_url
from app.models import AssetKind, Project, ProjectStatus, Shot, ShotState, Take
from app.services import generation, planner, routing
from app.services.budget.estimate import estimate_shot
from app.services.ingest import tts
from app.services.ingest.pipeline import latest_asset
from app.workers import tasks

router = APIRouter(tags=["shots"], dependencies=[Depends(get_current_user)])

BUSY = {ShotState.queued, ShotState.generating, ShotState.keyframe_generating}
MIN_SHOT_MS = 500

VisualTypeT = Literal["code_animation", "ai_clip", "still_image", "text_card", "a_roll"]


class TakeOut(BaseModel):
    id: uuid.UUID
    attempt: int
    status: str
    generator: str
    model: str | None
    cost_usd: float
    seed: int
    feedback_note: str | None
    error: str | None
    logs: list[str]
    video_url: str | None
    thumb_url: str | None
    meta: dict
    created_at: datetime


class ShotOut(BaseModel):
    id: uuid.UUID
    index: int
    start_ms: int
    end_ms: int
    transcript_text: str
    visual_type: str
    theme: str
    prompt: str
    notes: str
    depends_on_previous: bool
    uses_character: bool
    state: str
    accepted_take_id: uuid.UUID | None
    override_model: str | None
    error: str | None
    generator: str
    estimate_usd: float
    takes: list[TakeOut]


def _take_out(t: Take) -> TakeOut:
    m = t.meta or {}
    return TakeOut(
        id=t.id, attempt=t.attempt, status=t.status, generator=t.generator, model=t.model, cost_usd=t.cost_usd or 0.0,
        seed=t.seed, feedback_note=t.feedback_note, error=t.error,
        logs=[str(x) for x in (t.logs or [])][-20:] if t.status == "failed" else [],
        video_url=signed_media_url(m["asset_id"]) if m.get("asset_id") else None,
        thumb_url=signed_media_url(m["thumb_asset_id"]) if m.get("thumb_asset_id") else None,
        meta={k: v for k, v in m.items() if k not in ("asset_id", "thumb_asset_id")}, created_at=t.created_at,
    )  # fmt: skip


def _shot_out(s: Shot, cfg: routing.RoutingConfig) -> ShotOut:
    r = routing.resolve(s.visual_type, s.override_model, cfg)
    return ShotOut(
        id=s.id, index=s.index, start_ms=s.start_ms, end_ms=s.end_ms, transcript_text=s.transcript_text,
        visual_type=s.visual_type, theme=s.theme, prompt=s.prompt, notes=s.notes,
        depends_on_previous=s.depends_on_previous, uses_character=s.uses_character, state=s.state,
        accepted_take_id=s.accepted_take_id, override_model=s.override_model, error=s.error,
        generator=r.generator, estimate_usd=estimate_shot(r.generator, s.duration_ms),
        takes=[_take_out(t) for t in s.takes],
    )  # fmt: skip


def _project(db: Session, pid: uuid.UUID) -> Project:
    p = db.get(Project, pid)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return p


def _shot(db: Session, sid: uuid.UUID) -> Shot:
    s = db.get(Shot, sid)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Shot not found")
    return s


def _shots(db: Session, pid: uuid.UUID) -> list[Shot]:
    return list(db.scalars(select(Shot).where(Shot.project_id == pid).order_by(Shot.index)))


def _reindex(shots: list[Shot]) -> None:
    for i, s in enumerate(sorted(shots, key=lambda x: x.start_ms)):
        s.index = i


# ---------------------------------------------------------------- planning


@router.post("/projects/{project_id}/plan", status_code=status.HTTP_202_ACCEPTED)
def plan(project_id: uuid.UUID, db: Session = Depends(get_db)):
    p = _project(db, project_id)
    if any(s.state in BUSY for s in _shots(db, p.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait for running generations to finish")
    try:
        planner.begin_planning(db, p)
    except planner.PlanError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    db.refresh(p)
    return {"status": p.status}


@router.get("/projects/{project_id}/shots")
def list_shots(project_id: uuid.UUID, db: Session = Depends(get_db)):
    p = _project(db, project_id)
    cfg = routing.load()
    shots = [_shot_out(s, cfg) for s in _shots(db, p.id)]
    preview = latest_asset(db, p.id, "preview")
    return {
        "status": p.status,
        "error": p.error,
        "style_guide": {k: v for k, v in (p.style_guide or {}).items() if k != "character_svgs"},
        "characters_drawn": sorted((p.style_guide or {}).get("character_svgs", {}).keys()),
        "plan_meta": p.plan_meta or {},
        "shots": shots,
        "estimate_usd": round(sum(s.estimate_usd for s in shots if s.state not in (ShotState.accepted,)), 2),
        "spent_usd": round(sum(t.cost_usd for s in shots for t in s.takes), 4),
        "preview": {"url": signed_media_url(preview.id), "meta": preview.meta} if preview else None,
        "allowed_visual_types": cfg.planner.allowed_visual_types,
    }


class ShotPatch(BaseModel):
    visual_type: VisualTypeT | None = None
    theme: Literal["tech", "story"] | None = None
    prompt: str | None = Field(None, min_length=1, max_length=8000)
    notes: str | None = Field(None, max_length=4000)
    start_ms: int | None = Field(None, ge=0)
    end_ms: int | None = Field(None, ge=0)
    depends_on_previous: bool | None = None
    override_model: str | None = Field(None, max_length=100)


@router.patch("/shots/{shot_id}")
def patch_shot(shot_id: uuid.UUID, body: ShotPatch, db: Session = Depends(get_db)):
    s = _shot(db, shot_id)
    if s.state in BUSY:
        raise HTTPException(status.HTTP_409_CONFLICT, "Shot is generating")
    data = body.model_dump(exclude_unset=True)
    shots = _shots(db, s.project_id)
    i = next(k for k, x in enumerate(shots) if x.id == s.id)
    if "start_ms" in data and data["start_ms"] != s.start_ms:
        if i == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The first shot starts at 0")
        prev = shots[i - 1]
        if (
            not (prev.start_ms + MIN_SHOT_MS <= data["start_ms"] <= s.end_ms - MIN_SHOT_MS)
            or prev.state in BUSY
        ):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Boundary out of range")
        prev.end_ms = s.start_ms = data["start_ms"]
    if "end_ms" in data and data["end_ms"] != s.end_ms:
        if i == len(shots) - 1:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The last shot ends with the narration")
        nxt = shots[i + 1]
        if not (s.start_ms + MIN_SHOT_MS <= data["end_ms"] <= nxt.end_ms - MIN_SHOT_MS) or nxt.state in BUSY:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Boundary out of range")
        nxt.start_ms = s.end_ms = data["end_ms"]
    for k in ("visual_type", "theme", "prompt", "notes", "depends_on_previous", "override_model"):
        if k in data:
            setattr(s, k, data[k] if k != "override_model" else (data[k] or None))
    if (
        s.state in (ShotState.needs_edit, ShotState.failed)
        and {"prompt", "visual_type", "theme"} & data.keys()
    ):
        s.state, s.error = ShotState.planned, None
    _refresh_transcripts(db, shots)
    db.commit()
    return _shot_out(s, routing.load())


def _refresh_transcripts(db: Session, shots: list[Shot]) -> None:
    from app.services.generation import shot_words  # local: avoids a cycle at import time

    for sh in shots:
        sh.transcript_text = " ".join(w["w"] for w in shot_words(db, sh))


class SplitBody(BaseModel):
    at_ms: int


@router.post("/shots/{shot_id}/split")
def split_shot(shot_id: uuid.UUID, body: SplitBody, db: Session = Depends(get_db)):
    s = _shot(db, shot_id)
    if s.state in BUSY:
        raise HTTPException(status.HTTP_409_CONFLICT, "Shot is generating")
    if not (s.start_ms + MIN_SHOT_MS <= body.at_ms <= s.end_ms - MIN_SHOT_MS):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Split point must leave ≥ 0.5 s on each side"
        )
    new = Shot(project_id=s.project_id, index=s.index + 1, start_ms=body.at_ms, end_ms=s.end_ms,
               visual_type=s.visual_type, theme=s.theme, prompt=s.prompt, notes=s.notes,
               depends_on_previous=True, uses_character=s.uses_character, state=ShotState.planned)  # fmt: skip
    s.end_ms = body.at_ms
    s.state = ShotState.planned if s.state != ShotState.accepted else s.state
    db.add(new)
    db.flush()
    shots = _shots(db, s.project_id)
    _reindex(shots)
    _refresh_transcripts(db, shots)
    db.commit()
    cfg = routing.load()
    return [_shot_out(x, cfg) for x in _shots(db, s.project_id)]


class MergeBody(BaseModel):
    shot_ids: list[uuid.UUID] = Field(min_length=2, max_length=2)


@router.post("/shots/merge")
def merge_shots(body: MergeBody, db: Session = Depends(get_db)):
    a, b = sorted((_shot(db, i) for i in body.shot_ids), key=lambda x: x.start_ms)
    if a.project_id != b.project_id or a.end_ms != b.start_ms:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Only adjacent shots can be merged")
    if a.state in BUSY or b.state in BUSY:
        raise HTTPException(status.HTTP_409_CONFLICT, "Shot is generating")
    a.end_ms = b.end_ms
    a.prompt = f"{a.prompt}\n\nThen: {b.prompt}"
    a.state, a.accepted_take_id = ShotState.planned, None
    db.delete(b)
    db.flush()
    shots = _shots(db, a.project_id)
    _reindex(shots)
    _refresh_transcripts(db, shots)
    db.commit()
    cfg = routing.load()
    return [_shot_out(x, cfg) for x in _shots(db, a.project_id)]


@router.get("/projects/{project_id}/estimate")
def estimate(project_id: uuid.UUID, db: Session = Depends(get_db)):
    cfg = routing.load()
    rows = [_shot_out(s, cfg) for s in _shots(db, _project(db, project_id).id)]
    return {
        "shots": [{"index": r.index, "generator": r.generator, "estimate_usd": r.estimate_usd} for r in rows],
        "total_usd": round(sum(r.estimate_usd for r in rows), 2),
    }


# ---------------------------------------------------------------- generation + review


class GenerateBody(BaseModel):
    shot_ids: list[uuid.UUID] | None = None  # default: every shot without a usable take


@router.post("/projects/{project_id}/generate", status_code=status.HTTP_202_ACCEPTED)
def generate(project_id: uuid.UUID, body: GenerateBody, db: Session = Depends(get_db)):
    p = _project(db, project_id)
    if p.status != ProjectStatus.planned:
        raise HTTPException(status.HTTP_409_CONFLICT, "Plan the shot list first")
    shots = _shots(db, p.id)
    wanted = set(body.shot_ids or [])
    todo = [s for s in shots if (s.id in wanted) or (not wanted and s.state in (
        ShotState.planned, ShotState.failed, ShotState.paused_budget))]  # fmt: skip
    todo = [s for s in todo if s.state not in BUSY]
    takes = {s.id: generation.queue_shot(db, s) for s in todo}
    db.commit()
    # dependent shots run in sequence after their predecessor; everything else in parallel
    chains: list[list[str]] = []
    for s in shots:
        if s.id not in takes:
            continue
        if s.depends_on_previous and chains and s.index > 0 and shots[s.index - 1].id in takes:
            chains[-1].append(str(takes[s.id].id))
        else:
            chains.append([str(takes[s.id].id)])
    tasks.dispatch_takes(chains)
    return {"queued": len(takes)}


class RegenerateBody(BaseModel):
    note: str | None = Field(None, max_length=4000)
    keep_seed: bool = False


@router.post("/shots/{shot_id}/regenerate", status_code=status.HTTP_202_ACCEPTED)
def regenerate(shot_id: uuid.UUID, body: RegenerateBody, db: Session = Depends(get_db)):
    s = _shot(db, shot_id)
    if s.state in BUSY:
        raise HTTPException(status.HTTP_409_CONFLICT, "Shot is already generating")
    take = generation.queue_shot(db, s, note=body.note, keep_seed=body.keep_seed)
    db.commit()
    tasks.dispatch_takes([[str(take.id)]])
    return {"take_id": str(take.id)}


@router.post("/shots/{shot_id}/takes/{take_id}/accept")
def accept(shot_id: uuid.UUID, take_id: uuid.UUID, db: Session = Depends(get_db)):
    s = _shot(db, shot_id)
    t = db.get(Take, take_id)
    if t is None or t.shot_id != s.id or t.status != "done":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "Only a finished take of this shot can be accepted"
        )
    s.accepted_take_id, s.state = t.id, ShotState.accepted
    db.commit()
    return _shot_out(s, routing.load())


@router.get("/takes/{take_id}/code")
def take_code(take_id: uuid.UUID, db: Session = Depends(get_db)):
    t = db.get(Take, take_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Take not found")
    return json.loads(t.code) if t.code else {}


@router.post("/projects/{project_id}/preview", status_code=status.HTTP_202_ACCEPTED)
def make_preview(project_id: uuid.UUID, db: Session = Depends(get_db)):
    p = _project(db, project_id)
    p.plan_meta = {**(p.plan_meta or {}), "preview": {"status": "running"}}
    db.commit()
    tasks.preview_build.delay(str(p.id))
    return {"status": "running"}


# ---------------------------------------------------------------- draft narration (TTS)


class TTSBody(BaseModel):
    voice: str = "Kore"


@router.get("/tts/voices")
def voices():
    return {"voices": tts.VOICES}


@router.post("/projects/{project_id}/narration/tts", status_code=status.HTTP_202_ACCEPTED)
def narration_tts(project_id: uuid.UUID, body: TTSBody, db: Session = Depends(get_db)):
    p = _project(db, project_id)
    if p.status not in (ProjectStatus.draft, ProjectStatus.failed):
        raise HTTPException(status.HTTP_409_CONFLICT, "Narration can only change before ingest")
    if body.voice not in tts.VOICES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown voice")
    if latest_asset(db, p.id, AssetKind.script) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Upload the script first")
    p.ingest_step, p.error = "tts", None
    db.commit()
    tasks.ingest_tts.delay(str(p.id), body.voice)
    return {"status": "running"}


@router.get("/takes/{take_id}/html")
def take_html(take_id: uuid.UUID, db: Session = Depends(get_db)):
    from fastapi.responses import HTMLResponse

    t = db.get(Take, take_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Take not found")
    try:
        html = generation.standalone_html(db, t)
    except (generation.GenerationError, generation.renderer.RenderError) as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    return HTMLResponse(html, headers={"Content-Disposition": f'attachment; filename="shot-take-{t.attempt}.html"',
                                       "Content-Security-Policy": "default-src \'none\'; style-src \'unsafe-inline\'; "
                                       "script-src \'unsafe-inline\'; img-src data:; font-src data:"})  # fmt: skip
