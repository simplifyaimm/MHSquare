import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class ShortClip(Base):
    """A vertical Short / Reel cut from a long video: a range of its shots, re-laid out for 9:16 with an empty
    zone for the creator's talking head, captions, and its own title/description. Optionally part of a series."""

    __tablename__ = "short_clips"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)  # posting order (day 1, 2, …)
    shot_from: Mapped[int] = mapped_column(Integer)
    shot_to: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    series: Mapped[str | None] = mapped_column(String(80), nullable=True)
    part: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hook: Mapped[str] = mapped_column(String(80), default="")  # on-screen title at the top
    title: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    why: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="planned")  # planned | rendering | done | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)  # the rendered MP4
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms
