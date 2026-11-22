"""Creator headshots (Brand Kit) and cover photos per project."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.media_urls import signed_brand_url, signed_media_url
from app.models import Asset, AssetKind, Project
from app.models.style import BrandAsset
from app.services.ingest import media
from app.services.ingest.pipeline import latest_asset
from app.services.storage import get_storage
from app.workers import tasks

router = APIRouter(tags=["covers"], dependencies=[Depends(get_current_user)])
MAX_PHOTO = 25 * 1024 * 1024


def _headshot_out(a: BrandAsset) -> dict:
    return {
        "id": str(a.id), "status": a.status, "original_name": a.original_name, "created_at": a.created_at,
        "url": signed_brand_url(a.id, "original"),
        "cutout_url": signed_brand_url(a.id, "cutout") if a.cutout_key else None,
        "error": (a.meta or {}).get("error"),
    }  # fmt: skip


@router.get("/brand/headshots")
def list_headshots(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(BrandAsset).where(BrandAsset.kind == "headshot").order_by(BrandAsset.created_at.desc())
    )
    return [_headshot_out(a) for a in rows]


@router.post("/brand/headshots", status_code=status.HTTP_201_CREATED)
async def upload_headshot(file: UploadFile = File(...), db: Session = Depends(get_db)):
    data = await file.read(MAX_PHOTO + 1)
    if len(data) > MAX_PHOTO:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Photo must be ≤ 25 MB")
    mime = media.sniff(data[:64])
    if mime not in ("image/png", "image/jpeg", "image/webp"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Upload a PNG, JPEG or WebP photo")
    storage = get_storage()
    key = storage.new_key("brand", {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime])
    import io

    size, digest = storage.put_stream(key, io.BytesIO(data))
    a = BrandAsset(kind="headshot", storage_key=key, sha256=digest, size=size, mime=mime,
                   original_name=(file.filename or "photo")[:255], status="pending")  # fmt: skip
    db.add(a)
    db.commit()
    tasks.media_cutout.delay(str(a.id))
    return _headshot_out(a)


@router.delete("/brand/headshots/{headshot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_headshot(headshot_id: uuid.UUID, db: Session = Depends(get_db)):
    a = db.get(BrandAsset, headshot_id)
    if a is not None:
        storage = get_storage()
        for k in (a.storage_key, a.cutout_key):
            if k:
                storage.delete(k)
        db.delete(a)
        db.commit()


@router.post("/brand/headshots/{headshot_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_headshot(headshot_id: uuid.UUID, db: Session = Depends(get_db)):
    a = db.get(BrandAsset, headshot_id)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Photo not found")
    a.status = "pending"
    db.commit()
    tasks.media_cutout.delay(str(a.id))
    return _headshot_out(a)


class CoversIn(BaseModel):
    formats: list[Literal["youtube", "short"]] = Field(
        default_factory=lambda: ["youtube", "short"], min_length=1
    )
    headshot_id: uuid.UUID | None = None
    count: int = Field(4, ge=1, le=6)
    note: str = Field("", max_length=1000)


def _cover_out(a: Asset) -> dict:
    return {
        "id": str(a.id),
        "url": signed_media_url(a.id),
        "size": a.size,
        "created_at": a.created_at,
        **(a.meta or {}),
    }


@router.post("/projects/{project_id}/covers", status_code=status.HTTP_202_ACCEPTED)
def make_covers(project_id: uuid.UUID, body: CoversIn, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    if latest_asset(db, p.id, AssetKind.script) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Upload or choose a script first")
    p.plan_meta = {**(p.plan_meta or {}), "covers": {"status": "running"}}
    db.commit()
    tasks.covers_generate.delay(str(p.id), list(body.formats), str(body.headshot_id) if body.headshot_id else None,
                                body.count, body.note)  # fmt: skip
    return {"status": "running"}


@router.get("/projects/{project_id}/covers")
def list_covers(project_id: uuid.UUID, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    rows = db.scalars(
        select(Asset).where(Asset.project_id == p.id, Asset.kind == "cover").order_by(Asset.created_at.desc())
    )
    return {"status": (p.plan_meta or {}).get("covers", {}), "covers": [_cover_out(a) for a in rows]}


@router.post("/covers/{asset_id}/choose")
def choose_cover(asset_id: uuid.UUID, db: Session = Depends(get_db)):
    a = db.get(Asset, asset_id)
    if a is None or a.kind != "cover":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cover not found")
    fmt = (a.meta or {}).get("format")
    for other in db.scalars(select(Asset).where(Asset.project_id == a.project_id, Asset.kind == "cover")):
        if (other.meta or {}).get("format") == fmt:
            other.meta = {**(other.meta or {}), "chosen": other.id == a.id}
    db.commit()
    return _cover_out(a)


@router.delete("/covers/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cover(asset_id: uuid.UUID, db: Session = Depends(get_db)):
    a = db.get(Asset, asset_id)
    if a is not None and a.kind == "cover":
        get_storage().delete(a.storage_key)
        db.delete(a)
        db.commit()
