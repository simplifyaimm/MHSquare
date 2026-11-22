import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow


class VisualType(enum.StrEnum):
    code_animation = "code_animation"
    ai_clip = "ai_clip"
    still_image = "still_image"
    text_card = "text_card"
    a_roll = "a_roll"


class ShotState(enum.StrEnum):
    planned = "planned"
    keyframe_generating = "keyframe_generating"
    keyframe_review = "keyframe_review"
    queued = "queued"
    generating = "generating"
    review = "review"
    accepted = "accepted"
    paused_budget = "paused_budget"
    needs_edit = "needs_edit"
    failed = "failed"


class Shot(Base):
    __tablename__ = "shots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    transcript_text: Mapped[str] = mapped_column(Text, default="")
    visual_type: Mapped[str] = mapped_column(String(32))
    theme: Mapped[str] = mapped_column(String(32), default="tech")  # tech | story (house styles)
    prompt: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    depends_on_previous: Mapped[bool] = mapped_column(Boolean, default=False)
    uses_character: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(32), default=ShotState.planned, index=True)
    accepted_take_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    override_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    takes: Mapped[list["Take"]] = relationship(
        back_populates="shot", cascade="all, delete-orphan", order_by="Take.attempt"
    )

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class Take(Base):
    """One generation attempt for a shot. All takes are kept; any can be accepted."""

    __tablename__ = "takes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    shot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shots.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), default="final")  # keyframe | final
    generator: Mapped[str] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_used: Mapped[str] = mapped_column(Text, default="")
    seed: Mapped[int] = mapped_column(Integer, default=0)
    feedback_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_operation_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    thumb_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)  # generated scene source (JSON)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|running|done|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    shot: Mapped[Shot] = relationship(back_populates="takes")
