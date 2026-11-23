import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow


class ProjectStatus(enum.StrEnum):
    draft = "draft"  # uploading inputs
    ingesting = "ingesting"  # cleanup + first transcription + cut detection
    cuts_review = "cuts_review"  # waiting for me to approve cuts
    finalizing = "finalizing"  # applying cuts + final transcription
    ready = "ready"  # final transcript exists → plan shots
    planning = "planning"  # LLM is writing the shot list
    planned = "planned"  # shot list exists: edit, generate, review, preview
    failed = "failed"


class IngestStep(enum.StrEnum):
    cleanup = "cleanup"
    transcribe_raw = "transcribe_raw"
    detect_cuts = "detect_cuts"
    apply_cuts = "apply_cuts"
    transcribe_final = "transcribe_final"


class AssetKind(enum.StrEnum):
    # uploaded
    script = "script"
    audio = "audio"
    facecam = "facecam"
    character_ref = "character_ref"
    # derived
    audio_clean = "audio_clean"
    audio_final = "audio_final"
    facecam_final = "facecam_final"
    waveform = "waveform"
    output = "output"


UPLOAD_KINDS = {AssetKind.script, AssetKind.audio, AssetKind.facecam, AssetKind.character_ref}

DEFAULT_INGEST_SETTINGS = {
    "fillers": ["um", "uh", "umm", "uhh", "erm", "er", "ah", "hmm", "mm", "like"],
    "silence_ms": 600,
    "keep_pause_ms": 150,
    "language": None,  # auto-detect
}


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default=ProjectStatus.draft)
    ingest_step: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution: Mapped[str] = mapped_column(String(16), default="1920x1080")
    fps: Mapped[int] = mapped_column(Integer, default=30)
    style_notes: Mapped[str] = mapped_column(Text, default="")
    style_guide: Mapped[dict] = mapped_column(JSON, default=dict)  # {"version": n, …}
    plan_meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # planner cost/model/error
    # v2.1 content settings
    content_type: Mapped[str] = mapped_column(String(32), default="long_form")
    pillar: Mapped[str | None] = mapped_column(String(16), nullable=True)
    topic_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)  # reach | authority
    audience_level: Mapped[str] = mapped_column(String(16), default="beginner")
    topic: Mapped[str] = mapped_column(String(300), default="")
    script_check: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    direction: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # creative direction (AI + creator)
    style_profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    facecam_offset_ms: Mapped[int] = mapped_column(Integer, default=0)
    ingest_settings: Mapped[dict] = mapped_column(JSON, default=lambda: dict(DEFAULT_INGEST_SETTINGS))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    assets: Mapped[list["Asset"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    storage_key: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str] = mapped_column(String(100))
    original_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # duration_ms, width, height, fps, …
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="assets")


class Upload(Base):
    """Resumable chunked upload in progress. Becomes an Asset on completion."""

    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(255))
    size: Mapped[int] = mapped_column(BigInteger)
    received: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Transcript(Base):
    __tablename__ = "transcripts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    # [{"w": "hello", "start_ms": 120, "end_ms": 480, "score": 0.93}, …]
    words: Mapped[list] = mapped_column(JSON, default=list)
    source_asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CutProposal(Base):
    __tablename__ = "cut_proposals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    # [{"id": "c1", "start_ms": …, "end_ms": …, "kind": "filler|silence", "text": "um", "enabled": true}]
    ranges: Mapped[list] = mapped_column(JSON, default=list)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
