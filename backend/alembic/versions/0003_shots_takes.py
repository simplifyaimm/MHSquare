"""shots and takes

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("transcript_text", sa.Text(), nullable=False),
        sa.Column("visual_type", sa.String(32), nullable=False),
        sa.Column("theme", sa.String(32), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("depends_on_previous", sa.Boolean(), nullable=False),
        sa.Column("uses_character", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("accepted_take_id", sa.Uuid(), nullable=True),
        sa.Column("override_model", sa.String(100), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_shots_project_id", "shots", ["project_id"])
    op.create_index("ix_shots_state", "shots", ["state"])
    op.create_table(
        "takes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("shot_id", sa.Uuid(), sa.ForeignKey("shots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("generator", sa.String(32), nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("prompt_used", sa.Text(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("feedback_note", sa.Text(), nullable=True),
        sa.Column("provider_operation_id", sa.String(255), nullable=True),
        sa.Column("cache_key", sa.String(64), nullable=True),
        sa.Column("storage_key", sa.String(512), nullable=True),
        sa.Column("thumb_key", sa.String(512), nullable=True),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("logs", sa.JSON(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_takes_shot_id", "takes", ["shot_id"])
    op.create_index("ix_takes_cache_key", "takes", ["cache_key"])
    op.add_column("projects", sa.Column("plan_meta", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "plan_meta")
    op.drop_table("takes")
    op.drop_table("shots")
