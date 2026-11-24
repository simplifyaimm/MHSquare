import json
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.media_urls import signed_media_url
from app.models import Asset, AssetKind, CutProposal, Project, ProjectStatus, Transcript, Upload
from app.schemas.project import (
    AssetOut,
    CutsApprove,
    ProjectCreate,
    ProjectOut,
    ProjectPatch,
    UploadInit,
    UploadOut,
)
from app.services.ingest import cuts as cutlib
from app.services.ingest import pipeline, uploads
from app.services.storage import get_storage

router = APIRouter(tags=["projects"], dependencies=[Depends(get_current_user)])

EDITABLE = {ProjectStatus.draft, ProjectStatus.failed}
VISIBLE_KINDS = {
    AssetKind.script, AssetKind.audio, AssetKind.facecam, AssetKind.character_ref,
    AssetKind.audio_clean, AssetKind.audio_final, AssetKind.facecam_final, "preview",
}  # fmt: skip


def _asset_out(a: Asset) -> AssetOut:
    return AssetOut(
        id=a.id, kind=a.kind, mime=a.mime, size=a.size, original_name=a.original_name,
        meta=a.meta or {}, url=signed_media_url(a.id), created_at=a.created_at,
    )  # fmt: skip


def _project_out(p: Project) -> ProjectOut:
    return ProjectOut(
        id=p.id, name=p.name, status=p.status, ingest_step=p.ingest_step, error=p.error,
        resolution=p.resolution, fps=p.fps, style_notes=p.style_notes,
        facecam_offset_ms=p.facecam_offset_ms, ingest_settings=p.ingest_settings or {},
        content_type=p.content_type or "long_form", pillar=p.pillar, topic_kind=p.topic_kind,
        audience_level=p.audience_level or "beginner", topic=p.topic or "", script_check=p.script_check,
        direction=p.direction,
        publish=p.publish,
        style_profile_version=p.style_profile_version,
        created_at=p.created_at, updated_at=p.updated_at,
        assets=[_asset_out(a) for a in sorted(p.assets, key=lambda a: a.created_at) if a.kind in VISIBLE_KINDS],
    )  # fmt: skip


def _get_project(db: Session, project_id: uuid.UUID) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return p


def _require_editable(p: Project) -> None:
    if p.status not in EDITABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Inputs can't change while the project is {p.status}")


# ---------------------------------------------------------------- projects


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return [_project_out(p) for p in db.scalars(select(Project).order_by(Project.created_at.desc()))]


@router.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    p = Project(name=body.name.strip(), resolution=body.resolution, fps=body.fps, content_type=body.content_type,
                pillar=body.pillar, topic_kind=body.topic_kind, audience_level=body.audience_level,
                topic=body.topic.strip() or body.name.strip())  # fmt: skip
    db.add(p)
    db.commit()
    db.refresh(p)
    return _project_out(p)


@router.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: uuid.UUID, db: Session = Depends(get_db)):
    return _project_out(_get_project(db, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectOut)
def patch_project(project_id: uuid.UUID, body: ProjectPatch, db: Session = Depends(get_db)):
    p = _get_project(db, project_id)
    data = body.model_dump(exclude_unset=True)
    timing_fields = {"facecam_offset_ms", "ingest_settings", "fps"}
    if timing_fields & data.keys():
        _require_editable(p)
    if (
        "content_type" in data
        and data["content_type"] != p.content_type
        and p.status in (ProjectStatus.planning, ProjectStatus.planned)
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Content type is fixed once shots are planned")
    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return _project_out(p)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project_id: uuid.UUID, db: Session = Depends(get_db)):
    p = _get_project(db, project_id)
    if p.status in (ProjectStatus.ingesting, ProjectStatus.finalizing):
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait for the running step to finish")
    db.delete(p)
    db.commit()
    get_storage().delete_prefix(f"projects/{project_id}")


# ---------------------------------------------------------------- uploads


@router.post("/projects/{project_id}/assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    project_id: uuid.UUID,
    kind: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Single-request multipart upload (small files). Large files use /uploads (chunked)."""
    p = _get_project(db, project_id)
    _require_editable(p)
    name = file.filename or "upload"
    try:
        uploads.check_declared(db, p, kind, name, file.size or 1)
    except uploads.UploadRejected as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    tmp = uploads.tmp_path(uuid.uuid4())
    size = 0
    try:
        with tmp.open("wb") as f:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > uploads.LIMITS[AssetKind(kind)]:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large")
                f.write(chunk)
        return _asset_out(uploads.finalize(db, p, kind, tmp, name))
    except uploads.UploadRejected as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/projects/{project_id}/uploads", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
def init_upload(project_id: uuid.UUID, body: UploadInit, db: Session = Depends(get_db)):
    p = _get_project(db, project_id)
    _require_editable(p)
    try:
        uploads.check_declared(db, p, body.kind, body.filename, body.size)
    except uploads.UploadRejected as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    u = Upload(project_id=p.id, kind=body.kind, filename=body.filename, size=body.size)
    db.add(u)
    db.commit()
    uploads.tmp_path(u.id).write_bytes(b"")
    return UploadOut(id=u.id, kind=u.kind, size=u.size, received=0, chunk_size=8 * uploads.MB)


def _get_upload(db: Session, upload_id: uuid.UUID) -> Upload:
    u = db.get(Upload, upload_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found")
    return u


@router.get("/uploads/{upload_id}", response_model=UploadOut)
def get_upload(upload_id: uuid.UUID, db: Session = Depends(get_db)):
    """For resuming: how many bytes the server has."""
    u = _get_upload(db, upload_id)
    return UploadOut(id=u.id, kind=u.kind, size=u.size, received=u.received, chunk_size=8 * uploads.MB)


@router.put("/uploads/{upload_id}", response_model=UploadOut)
async def put_chunk(
    upload_id: uuid.UUID, request: Request, offset: int = Query(ge=0), db: Session = Depends(get_db)
):
    u = _get_upload(db, upload_id)
    if offset != u.received:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Expected offset {u.received}")
    path = uploads.tmp_path(u.id)
    written = 0
    with path.open("r+b") as f:
        f.seek(offset)
        async for chunk in request.stream():
            written += len(chunk)
            if written > uploads.CHUNK_MAX or offset + written > u.size:
                f.truncate(offset)
                raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Chunk too large")
            f.write(chunk)
        f.truncate(offset + written)
    u.received = offset + written
    db.commit()
    return UploadOut(id=u.id, kind=u.kind, size=u.size, received=u.received, chunk_size=8 * uploads.MB)


@router.post("/uploads/{upload_id}/complete", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
def complete_upload(upload_id: uuid.UUID, db: Session = Depends(get_db)):
    u = _get_upload(db, upload_id)
    p = _get_project(db, u.project_id)
    _require_editable(p)
    if u.received != u.size:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Incomplete: {u.received}/{u.size} bytes")
    path = uploads.tmp_path(u.id)
    try:
        asset = uploads.finalize(db, p, u.kind, path, u.filename)
    except uploads.UploadRejected as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    finally:
        path.unlink(missing_ok=True)
        db.delete(u)
        db.commit()
    return _asset_out(asset)


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_asset(asset_id: uuid.UUID, db: Session = Depends(get_db)):
    a = db.get(Asset, asset_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Asset not found")
    _require_editable(_get_project(db, a.project_id))
    if a.kind not in uploads.LIMITS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Derived assets can't be deleted")
    get_storage().delete(a.storage_key)
    db.delete(a)
    db.commit()


# ---------------------------------------------------------------- ingest


@router.post("/projects/{project_id}/ingest", response_model=ProjectOut, status_code=status.HTTP_202_ACCEPTED)
def start_ingest(project_id: uuid.UUID, db: Session = Depends(get_db)):
    p = _get_project(db, project_id)
    try:
        if p.status == ProjectStatus.draft:
            pipeline.begin_ingest(db, p)
        elif p.status == ProjectStatus.failed:
            pipeline.resume_ingest(db, p)
        else:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Project is {p.status}")
    except pipeline.IngestError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    db.refresh(p)
    return _project_out(p)


def _proposal(db: Session, project_id: uuid.UUID) -> CutProposal | None:
    return db.scalar(
        select(CutProposal)
        .where(CutProposal.project_id == project_id)
        .order_by(CutProposal.created_at.desc())
    )


@router.get("/projects/{project_id}/cuts")
def get_cuts(project_id: uuid.UUID, db: Session = Depends(get_db)):
    p = _get_project(db, project_id)
    prop = _proposal(db, project_id)
    if prop is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No cut proposal yet")
    clean = pipeline.latest_asset(db, p.id, AssetKind.audio_clean)
    duration = int((clean.meta or {}).get("duration_ms", 0)) if clean else 0
    return {
        "id": str(prop.id),
        "approved": prop.approved,
        "ranges": prop.ranges,
        "duration_ms": duration,
        "removed_ms": cutlib.removed_ms(prop.ranges, duration, p.fps) if duration else 0,
        "audio": _asset_out(clean) if clean else None,
    }


@router.post("/projects/{project_id}/cuts/approve", response_model=ProjectOut)
def approve_cuts(project_id: uuid.UUID, body: CutsApprove, db: Session = Depends(get_db)):
    p = _get_project(db, project_id)
    try:
        pipeline.approve_cuts(db, p, set(body.enabled_ids))
    except pipeline.IngestError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    db.refresh(p)
    return _project_out(p)


@router.get("/projects/{project_id}/transcript")
def get_transcript(project_id: uuid.UUID, final: bool = True, db: Session = Depends(get_db)):
    _get_project(db, project_id)
    t = db.scalar(
        select(Transcript)
        .where(Transcript.project_id == project_id, Transcript.is_final.is_(final))
        .order_by(Transcript.version.desc())
    )
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transcript not ready")
    return {
        "version": t.version,
        "is_final": t.is_final,
        "language": t.language,
        "text": t.text,
        "words": t.words,
    }


@router.get("/projects/{project_id}/waveform")
def get_waveform(
    project_id: uuid.UUID,
    stage: str = Query("clean", pattern="^(clean|final)$"),
    db: Session = Depends(get_db),
):
    _get_project(db, project_id)
    a = pipeline.latest_asset(db, project_id, AssetKind.waveform, stage=stage)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Waveform not ready")
    return json.loads(Path(get_storage().path(a.storage_key)).read_text())


# ---------------------------------------------------------------- v2.1: script judge, topic registry, stats


@router.post("/projects/{project_id}/script-check")
def script_check(project_id: uuid.UUID, db: Session = Depends(get_db)):
    from app.llm.errors import LLMError
    from app.services import script_check as SC
    from app.services import style as S

    p = _get_project(db, project_id)
    script = pipeline.latest_asset(db, p.id, AssetKind.script)
    if script is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Upload the script first")
    text = get_storage().path(script.storage_key).read_text("utf-8-sig")
    try:
        result = SC.judge(p, S.active(db), text)
    except LLMError as e:  # deterministic checks still help when the judge LLM is unavailable
        result = {"passed": None, "error": f"{e.kind}: {e}",
                  "deterministic": SC.deterministic(SC.spoken_text(text), p.content_type, S.active(db))}  # fmt: skip
    p.script_check = result
    db.commit()
    return result


class DirectionEdit(BaseModel):
    primary_mode: str | None = None
    modes: list[str] | None = None
    templates: list[str] | None = None
    persona: str | None = Field(None, max_length=120)
    hook: str | None = Field(None, max_length=240)
    pacing: Literal["calm", "medium", "fast"] | None = None
    density: Literal["sparse", "balanced", "rich"] | None = None


@router.post("/projects/{project_id}/direction")
def recommend_direction(project_id: uuid.UUID, db: Session = Depends(get_db)):
    """AI creative direction for THIS script: modes, templates, persona, hook, pacing (one small call)."""
    from app.llm.errors import LLMError
    from app.services import direction as DIR
    from app.services import style as S

    p = _get_project(db, project_id)
    script = pipeline.latest_asset(db, p.id, AssetKind.script)
    if script is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Upload the script first")
    text = get_storage().path(script.storage_key).read_text("utf-8-sig")
    try:
        p.direction = DIR.recommend(p, S.active(db), text)
    except DIR.DirectionError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    except LLMError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"AI unavailable: {e}"[:500]) from e
    db.commit()
    return p.direction


@router.put("/projects/{project_id}/direction")
def edit_direction(project_id: uuid.UUID, body: DirectionEdit, db: Session = Depends(get_db)):
    from app.services import direction as DIR
    from app.services import style as S

    p = _get_project(db, project_id)
    try:
        p.direction = DIR.edit(p.direction, body.model_dump(exclude_none=True), S.active(db))
    except DIR.DirectionError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    db.commit()
    return p.direction


class PublishEdit(BaseModel):
    title: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=6000)
    tags: list[str] | str | None = None
    pinned_comment: str | None = Field(None, max_length=1000)


@router.post("/projects/{project_id}/publish")
def generate_publish(project_id: uuid.UUID, db: Session = Depends(get_db)):
    """YouTube details for this video: titles, description with real chapters, tags (one small AI call)."""
    from app.llm.errors import LLMError
    from app.services import publish as PUB
    from app.services import style as S

    p = _get_project(db, project_id)
    script = pipeline.latest_asset(db, p.id, AssetKind.script)
    if script is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Upload the script first")
    text = get_storage().path(script.storage_key).read_text("utf-8-sig")
    try:
        p.publish = PUB.generate(db, p, S.active(db), text)
    except PUB.PublishError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    except LLMError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"AI unavailable: {e}"[:500]) from e
    db.commit()
    return p.publish


@router.put("/projects/{project_id}/publish")
def edit_publish(project_id: uuid.UUID, body: PublishEdit, db: Session = Depends(get_db)):
    from app.services import publish as PUB

    p = _get_project(db, project_id)
    try:
        p.publish = PUB.edit(p.publish, body.model_dump(exclude_none=True))
    except PUB.PublishError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    db.commit()
    return p.publish


@router.post("/projects/{project_id}/mark-final")
def mark_final(project_id: uuid.UUID, db: Session = Depends(get_db)):
    """The script is final: add its topic to the covered-topics registry (spec v2.1 §4)."""
    from app.models.style import CoveredTopic

    p = _get_project(db, project_id)
    title = (p.topic or p.name).strip()
    t = db.scalar(select(CoveredTopic).where(CoveredTopic.source_project_id == p.id))
    if t is None:
        t = CoveredTopic(title=title, pillar=p.pillar, content_type=p.content_type, status="covered",
                         source_project_id=p.id)  # fmt: skip
        db.add(t)
    else:
        t.title, t.status, t.embedding = title, "covered", None
    db.commit()
    return {"topic_id": str(t.id), "title": t.title}


@router.get("/projects-stats")
def stats(db: Session = Depends(get_db)):
    """Reach : Authority ratio (target 2 : 1)."""
    kinds = [k for (k,) in db.execute(select(Project.topic_kind))]
    reach, authority = kinds.count("reach"), kinds.count("authority")
    return {"reach": reach, "authority": authority, "untagged": kinds.count(None), "target": "2:1",
            "on_target": authority == 0 and reach == 0 or (authority > 0 and reach / authority >= 2)}  # fmt: skip


# ---------------------------------------------------------------- script library (reuse a script from another project)


@router.get("/scripts")
def list_scripts(db: Session = Depends(get_db)):
    """Every script already in mhsquare (newest first, de-duplicated by content)."""
    rows = db.execute(
        select(Asset, Project.name).join(Project, Project.id == Asset.project_id)
        .where(Asset.kind == AssetKind.script).order_by(Asset.created_at.desc())
    ).all()  # fmt: skip
    seen, out = set(), []
    storage = get_storage()
    for a, pname in rows:
        if a.sha256 in seen or not storage.exists(a.storage_key):
            continue
        seen.add(a.sha256)
        text = storage.path(a.storage_key).read_text("utf-8-sig", errors="replace")
        title = next(
            (ln.lstrip("# ").strip() for ln in text.splitlines() if ln.strip()), a.original_name or "script"
        )
        out.append({"asset_id": str(a.id), "title": title[:120], "project": pname, "project_id": str(a.project_id),
                    "words": (a.meta or {}).get("words"), "original_name": a.original_name,
                    "created_at": a.created_at})  # fmt: skip
    return out


class FromAsset(BaseModel):
    asset_id: uuid.UUID


@router.post(
    "/projects/{project_id}/script/from-library", response_model=AssetOut, status_code=status.HTTP_201_CREATED
)
def script_from_library(project_id: uuid.UUID, body: FromAsset, db: Session = Depends(get_db)):
    p = _get_project(db, project_id)
    _require_editable(p)
    src = db.get(Asset, body.asset_id)
    if src is None or src.kind != AssetKind.script:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Script not found")
    storage = get_storage()
    tmp = uploads.tmp_path(uuid.uuid4())
    tmp.write_bytes(storage.path(src.storage_key).read_bytes())
    try:
        asset = uploads.finalize(db, p, AssetKind.script, tmp, src.original_name or "script.md")
    finally:
        tmp.unlink(missing_ok=True)
    asset.meta = {**(asset.meta or {}), "copied_from_project": str(src.project_id)}
    db.commit()
    return _asset_out(asset)
