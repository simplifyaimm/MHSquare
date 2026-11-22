import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
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
    AssetKind.audio_clean, AssetKind.audio_final, AssetKind.facecam_final,
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
    p = Project(name=body.name.strip(), resolution=body.resolution, fps=body.fps)
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
