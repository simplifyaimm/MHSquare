"""Brand Kit / Style Profile (versioned), covered-topics registry, publishing helpers."""

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import get_current_user
from app.llm.errors import LLMError
from app.models.style import CoveredTopic, StyleProfile
from app.services import style as S
from app.services import topics as T
from app.services.script_check import suggest_reply
from app.services.style import defaults as D

router = APIRouter(tags=["style"], dependencies=[Depends(get_current_user)])

HEX = r"^#[0-9a-fA-F]{6}$"
Pillar = Literal["series", "growth", "tools", "advice"]
ContentType = Literal["long_form", "short_series", "short_growth", "short_tools", "short_advice", "reel"]


class PillarIn(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    color: str = Field(pattern=HEX)


class BrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    tagline: str = Field(max_length=120)
    handle: str = Field(max_length=60)
    watermark: str = Field(max_length=60)
    pillars: dict[Pillar, PillarIn]


class LayoutIn(BaseModel):
    facecam_circle: Literal["composite", "leave_space"] = "composite"
    facecam_safe_area: dict[Literal["w_pct", "h_pct"], int] = {"w_pct": 22, "h_pct": 22}
    watermark_position: Literal["top-right", "top-left"] = "top-right"
    footer_on_animations: bool = True
    shorts_canvas_aspect: Literal["3:4", "9:16"] = "3:4"
    shorts_export: list[int] = [1080, 1920]
    facecam_circle_shorts: Literal["composite", "off"] = "composite"
    facecam_ring: dict[str, Any] = {"glow": True, "width_px": 4}

    @field_validator("facecam_safe_area")
    @classmethod
    def _pct(cls, v):
        if not all(5 <= x <= 45 for x in v.values()):
            raise ValueError("safe area must be 5–45 %")
        return v


class ProfileIn(BaseModel):
    note: str = Field("", max_length=300)
    language: Literal["en"] = "en"
    brand: BrandIn | None = None
    voice_rules: str | None = Field(None, max_length=20_000)
    judge_rubric: list[dict[str, Any]] | None = None
    audience_default: Literal["beginner", "advanced"] | None = None
    presets: dict[ContentType, dict[str, Any]] | None = None
    animation_style: dict[str, Any] | None = None
    layout: LayoutIn | None = None
    caption_formula: str | None = Field(None, max_length=20_000)
    reply_framework: str | None = Field(None, max_length=20_000)

    @field_validator("animation_style")
    @classmethod
    def _anim(cls, v):
        if v is None:
            return v
        import re

        for k, c in (v.get("palette") or {}).items():
            if not re.match(HEX, str(c)):
                raise ValueError(f"palette.{k} must be #rrggbb")
        if not v.get("allowed_fonts"):
            raise ValueError("animation_style.allowed_fonts is required")
        return v


# ---------------------------------------------------------------- style profile


@router.get("/style/profile")
def get_profile(db: Session = Depends(get_db)):
    p = S.active(db)
    return {**S.as_dict(p), "content_types": D.CONTENT_TYPES, "pillars": D.PILLARS}


@router.get("/style/versions")
def versions(db: Session = Depends(get_db)):
    rows = db.scalars(select(StyleProfile).order_by(StyleProfile.version.desc())).all()
    return [
        {"version": r.version, "is_active": r.is_active, "note": r.note, "created_at": r.created_at}
        for r in rows
    ]


@router.put("/style/profile")
def save_profile(body: ProfileIn, db: Session = Depends(get_db)):
    data = body.model_dump(exclude_none=True, exclude={"note"})
    try:
        p = S.save_version(db, data, body.note or "Edited in the UI")
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    return S.as_dict(p)


@router.post("/style/versions/{version}/activate")
def activate(version: int, db: Session = Depends(get_db)):
    try:
        return S.as_dict(S.activate(db, version))
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e


@router.post("/style/reset")
def reset(db: Session = Depends(get_db)):
    return S.as_dict(S.save_version(db, D.default_profile(), "Reset to seeded Simplify AI defaults"))


# ---------------------------------------------------------------- topics


class TopicIn(BaseModel):
    title: str = Field(min_length=2, max_length=300)
    pillar: Pillar | None = None
    content_type: ContentType | None = None
    status: Literal["covered", "planned"] = "covered"


class TopicPatch(BaseModel):
    title: str | None = Field(None, min_length=2, max_length=300)
    pillar: Pillar | None = None
    content_type: ContentType | None = None
    status: Literal["covered", "planned"] | None = None


def _topic_out(t: CoveredTopic) -> dict:
    return {"id": str(t.id), "title": t.title, "pillar": t.pillar, "content_type": t.content_type, "status": t.status,
            "source_project_id": str(t.source_project_id) if t.source_project_id else None, "created_at": t.created_at}  # fmt: skip


@router.get("/topics")
def list_topics(db: Session = Depends(get_db)):
    S.ensure_seeded(db)
    return [_topic_out(t) for t in db.scalars(select(CoveredTopic).order_by(CoveredTopic.created_at.desc()))]


@router.post("/topics", status_code=status.HTTP_201_CREATED)
def add_topic(body: TopicIn, db: Session = Depends(get_db)):
    t = CoveredTopic(**body.model_dump())
    db.add(t)
    db.commit()
    return _topic_out(t)


@router.patch("/topics/{topic_id}")
def patch_topic(topic_id: uuid.UUID, body: TopicPatch, db: Session = Depends(get_db)):
    t = db.get(CoveredTopic, topic_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Topic not found")
    data = body.model_dump(exclude_unset=True)
    if "title" in data and data["title"] != t.title:
        t.embedding = None
    for k, v in data.items():
        setattr(t, k, v)
    db.commit()
    return _topic_out(t)


@router.delete("/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_topic(topic_id: uuid.UUID, db: Session = Depends(get_db)):
    t = db.get(CoveredTopic, topic_id)
    if t is not None:
        db.delete(t)
        db.commit()


class CheckIn(BaseModel):
    title: str = Field(min_length=2, max_length=300)


@router.post("/topics/check")
def check_topic(body: CheckIn, db: Session = Depends(get_db)):
    S.ensure_seeded(db)
    return T.check(db, body.title)


# ---------------------------------------------------------------- publishing helpers


class ReplyIn(BaseModel):
    comment: str = Field(min_length=2, max_length=4000)
    context: str = Field("", max_length=4000)


@router.post("/tools/suggest-reply")
def reply(body: ReplyIn, db: Session = Depends(get_db)):
    try:
        text, cost = suggest_reply(S.active(db), body.comment, body.context)
    except LLMError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"{e.kind}: {e}") from e
    return {"reply": text, "cost_usd": cost}
