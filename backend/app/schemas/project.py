import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ContentType = Literal["long_form", "short_series", "short_growth", "short_tools", "short_advice", "reel"]
Pillar = Literal["series", "growth", "tools", "advice"]


class IngestSettings(BaseModel):
    fillers: list[str] = Field(default_factory=list, max_length=50)
    silence_ms: int = Field(600, ge=200, le=5000)
    keep_pause_ms: int = Field(150, ge=0, le=1000)
    language: str | None = Field(None, max_length=8, pattern=r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    content_type: ContentType = "long_form"
    pillar: Pillar | None = None
    topic_kind: Literal["reach", "authority"] | None = None
    audience_level: Literal["beginner", "advanced"] = "beginner"
    topic: str = Field("", max_length=300)
    resolution: Literal["1920x1080", "3840x2160"] = "1920x1080"
    fps: Literal[24, 25, 30, 50, 60] = 30


class ProjectPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    content_type: ContentType | None = None
    pillar: Pillar | None = None
    topic_kind: Literal["reach", "authority"] | None = None
    audience_level: Literal["beginner", "advanced"] | None = None
    topic: str | None = Field(None, max_length=300)
    resolution: Literal["1920x1080", "3840x2160"] | None = None
    fps: Literal[24, 25, 30, 50, 60] | None = None
    style_notes: str | None = Field(None, max_length=20_000)
    facecam_offset_ms: int | None = Field(None, ge=-600_000, le=600_000)
    ingest_settings: IngestSettings | None = None


class AssetOut(BaseModel):
    id: uuid.UUID
    kind: str
    mime: str
    size: int
    original_name: str | None
    meta: dict
    url: str
    created_at: datetime


class ProjectOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    ingest_step: str | None
    error: str | None
    resolution: str
    fps: int
    style_notes: str
    facecam_offset_ms: int
    ingest_settings: dict
    content_type: str
    pillar: str | None
    topic_kind: str | None
    audience_level: str
    topic: str
    script_check: dict | None
    style_profile_version: int | None
    created_at: datetime
    updated_at: datetime
    assets: list[AssetOut] = []


class UploadInit(BaseModel):
    kind: Literal["script", "audio", "facecam", "character_ref"]
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)


class UploadOut(BaseModel):
    id: uuid.UUID
    kind: str
    size: int
    received: int
    chunk_size: int


class CutsApprove(BaseModel):
    enabled_ids: list[str] = Field(max_length=20_000)
