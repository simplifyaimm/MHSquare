"""Shorts / Reels cut from a project's long video: plan, list, edit, render."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.models import Asset, Project, ProjectStatus, ShortClip
from app.services import shorts as SH
from app.services.storage import get_storage
from app.workers import tasks

router = APIRouter(tags=["shorts"], dependencies=[Depends(get_current_user)])


def _project(db: Session, project_id: uuid.UUID) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return p


def _clip(db: Session, short_id: uuid.UUID) -> ShortClip:
    c = db.get(ShortClip, short_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Short not found")
    return c


@router.get("/projects/{project_id}/shorts")
def list_shorts(project_id: uuid.UUID, db: Session = Depends(get_db)):
    p = _project(db, project_id)
    rows = db.scalars(select(ShortClip).where(ShortClip.project_id == p.id).order_by(ShortClip.index))
    layout = {"width": SH.W, "height": SH.H, "stage": [0, SH.STAGE_Y, SH.STAGE_W, SH.STAGE_H],
              "face_zone": [SH.FACE_X, SH.FACE_Y, SH.FACE_W, SH.FACE_H]}  # fmt: skip
    return {
        "status": (p.plan_meta or {}).get("shorts", {}),
        "layout": layout,
        "shorts": [SH.as_json(c) for c in rows],
    }


@router.post("/projects/{project_id}/shorts/plan", status_code=status.HTTP_202_ACCEPTED)
def plan_shorts(project_id: uuid.UUID, db: Session = Depends(get_db)):
    """AI picks the Shorts from the shot timeline (keeps already-rendered ones)."""
    p = _project(db, project_id)
    if p.status != ProjectStatus.planned:
        raise HTTPException(status.HTTP_409_CONFLICT, "Plan the long video's shots first")
    if ((p.plan_meta or {}).get("shorts") or {}).get("status") == "running":
        raise HTTPException(status.HTTP_409_CONFLICT, "Already finding Shorts")
    SH.begin_plan(db, p)
    return {"status": "running"}


@router.post("/projects/{project_id}/shorts/render", status_code=status.HTTP_202_ACCEPTED)
def render_all(project_id: uuid.UUID, db: Session = Depends(get_db)):
    """Render every Short that isn't rendered yet (or changed since)."""
    p = _project(db, project_id)
    todo = [c for c in db.scalars(select(ShortClip).where(ShortClip.project_id == p.id).order_by(ShortClip.index))
            if c.status in ("planned", "failed") or (c.status == "done" and (c.meta or {}).get("stale"))]  # fmt: skip
    for c in todo:
        c.status = "queued"
    db.commit()
    for c in todo:
        tasks.shorts_render.delay(str(c.id))
    return {"queued": len(todo)}


@router.post("/shorts/{short_id}/render", status_code=status.HTTP_202_ACCEPTED)
def render_one(short_id: uuid.UUID, db: Session = Depends(get_db)):
    c = _clip(db, short_id)
    if c.status in ("queued", "rendering"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Already rendering")
    c.status = "queued"
    db.commit()
    tasks.shorts_render.delay(str(c.id))
    return {"status": "queued"}


class ShortEdit(BaseModel):
    hook: str | None = Field(None, max_length=200)
    title: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=6000)
    hashtags: list[str] | str | None = None


@router.put("/shorts/{short_id}")
def edit_short(short_id: uuid.UUID, body: ShortEdit, db: Session = Depends(get_db)):
    c = _clip(db, short_id)
    try:
        SH.edit(c, body.model_dump(exclude_none=True))
    except SH.PlanError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    db.commit()
    return SH.as_json(c)


@router.delete("/shorts/{short_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_short(short_id: uuid.UUID, db: Session = Depends(get_db)):
    c = _clip(db, short_id)
    if c.asset_id:
        a = db.get(Asset, c.asset_id)
        if a is not None:
            get_storage().delete(a.storage_key)
            db.delete(a)
    db.delete(c)
    db.commit()
