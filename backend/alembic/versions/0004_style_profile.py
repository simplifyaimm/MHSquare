"""v2.1 style profile, topic registry, project content settings

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "style_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(300), nullable=False),
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column("brand", sa.JSON(), nullable=False),
        sa.Column("voice_rules", sa.Text(), nullable=False),
        sa.Column("judge_rubric", sa.JSON(), nullable=False),
        sa.Column("audience_default", sa.String(16), nullable=False),
        sa.Column("presets", sa.JSON(), nullable=False),
        sa.Column("animation_style", sa.JSON(), nullable=False),
        sa.Column("layout", sa.JSON(), nullable=False),
        sa.Column("caption_formula", sa.Text(), nullable=False),
        sa.Column("reply_framework", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_style_profiles_version", "style_profiles", ["version"])
    op.create_index("ix_style_profiles_is_active", "style_profiles", ["is_active"])
    op.create_table(
        "covered_topics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("pillar", sa.String(16), nullable=True),
        sa.Column("content_type", sa.String(32), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column(
            "source_project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("projects") as b:
        b.add_column(sa.Column("content_type", sa.String(32), nullable=False, server_default="long_form"))
        b.add_column(sa.Column("pillar", sa.String(16), nullable=True))
        b.add_column(sa.Column("topic_kind", sa.String(16), nullable=True))
        b.add_column(sa.Column("audience_level", sa.String(16), nullable=False, server_default="beginner"))
        b.add_column(sa.Column("topic", sa.String(300), nullable=False, server_default=""))
        b.add_column(sa.Column("script_check", sa.JSON(), nullable=True))
        b.add_column(sa.Column("style_profile_version", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as b:
        for c in (
            "style_profile_version",
            "script_check",
            "topic",
            "audience_level",
            "topic_kind",
            "pillar",
            "content_type",
        ):
            b.drop_column(c)
    op.drop_table("covered_topics")
    op.drop_table("style_profiles")
