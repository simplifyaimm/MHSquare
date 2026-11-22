"""projects, assets, uploads, transcripts, cut proposals

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _fk_project() -> sa.Column:
    return sa.Column(
        "project_id", sa.Uuid(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("ingest_step", sa.String(32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("resolution", sa.String(16), nullable=False),
        sa.Column("fps", sa.Integer(), nullable=False),
        sa.Column("style_notes", sa.Text(), nullable=False),
        sa.Column("style_guide", sa.JSON(), nullable=False),
        sa.Column("facecam_offset_ms", sa.Integer(), nullable=False),
        sa.Column("ingest_settings", sa.JSON(), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk_project(),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.String(100), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False),
        _ts("created_at"),
    )
    op.create_index("ix_assets_project_id", "assets", ["project_id"])
    op.create_index("ix_assets_kind", "assets", ["kind"])
    op.create_table(
        "uploads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk_project(),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("received", sa.BigInteger(), nullable=False),
        _ts("created_at"),
    )
    op.create_index("ix_uploads_project_id", "uploads", ["project_id"])
    op.create_table(
        "transcripts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk_project(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("words", sa.JSON(), nullable=False),
        sa.Column("source_asset_id", sa.Uuid(), nullable=True),
        _ts("created_at"),
    )
    op.create_index("ix_transcripts_project_id", "transcripts", ["project_id"])
    op.create_table(
        "cut_proposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _fk_project(),
        sa.Column("ranges", sa.JSON(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        _ts("created_at"),
        _ts("approved_at", nullable=True),
    )
    op.create_index("ix_cut_proposals_project_id", "cut_proposals", ["project_id"])


def downgrade() -> None:
    for t in ("cut_proposals", "transcripts", "uploads", "assets", "projects"):
        op.drop_table(t)
