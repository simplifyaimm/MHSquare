"""short clips (vertical Shorts / Reels cut from a long video)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "short_clips",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("shot_from", sa.Integer(), nullable=False),
        sa.Column("shot_to", sa.Integer(), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=False),
        sa.Column("end_ms", sa.Integer(), nullable=False),
        sa.Column("series", sa.String(80), nullable=True),
        sa.Column("part", sa.Integer(), nullable=True),
        sa.Column("parts", sa.Integer(), nullable=True),
        sa.Column("hook", sa.String(80), nullable=False, server_default=""),
        sa.Column("title", sa.String(120), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("hashtags", sa.JSON(), nullable=False),
        sa.Column("why", sa.Text(), nullable=False, server_default=""),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="planned"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_short_clips_project_id", "short_clips", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_short_clips_project_id", "short_clips")
    op.drop_table("short_clips")
