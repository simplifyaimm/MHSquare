import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class StyleProfile(Base):
    """Brand Kit / Style Profile (v2.1). Every save is a new immutable version; one is active.

    Injected into every prompt that writes words or visuals. Never hard-coded in prompts.
    """

    __tablename__ = "style_profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    language: Mapped[str] = mapped_column(String(8), default="en")
    brand: Mapped[dict] = mapped_column(JSON, default=dict)  # name, tagline, handle, watermark, pillars
    voice_rules: Mapped[str] = mapped_column(Text, default="")
    judge_rubric: Mapped[list] = mapped_column(JSON, default=list)
    audience_default: Mapped[str] = mapped_column(String(16), default="beginner")
    presets: Mapped[dict] = mapped_column(JSON, default=dict)  # per content type
    animation_style: Mapped[dict] = mapped_column(JSON, default=dict)
    layout: Mapped[dict] = mapped_column(JSON, default=dict)
    caption_formula: Mapped[str] = mapped_column(Text, default="")
    reply_framework: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CoveredTopic(Base):
    __tablename__ = "covered_topics"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(300))
    pillar: Mapped[str | None] = mapped_column(String(16), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="covered")  # covered | planned
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    source_project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
