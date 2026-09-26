"""revision-bound render artifacts

Revision ID: f2a6c04e91bd
Revises: d7e4a2b61c90
Create Date: 2026-09-26 23:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "f2a6c04e91bd"
down_revision = "d7e4a2b61c90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "render_artifacts",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("revision_id", sa.String(length=128), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("quality", sa.String(length=16), nullable=False),
        sa.Column("output_start_frame", sa.Integer()),
        sa.Column("output_duration_frames", sa.Integer()),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("renderer", sa.String(length=128), nullable=False),
        sa.Column("renderer_version", sa.String(length=128), nullable=False),
        sa.Column("storage_key", sa.Text()),
        sa.Column("content_type", sa.String(length=128)),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("file_size", sa.Integer()),
        sa.Column("sha256", sa.String(length=64)),
        sa.Column("error", sa.Text()),
        sa.Column("render_time_ms", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["revision_id"], ["project_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_render_artifacts_job_id"),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_render_artifacts_project_id_idempotency_key",
        ),
    )
    op.create_index(
        "ix_render_artifacts_project_id",
        "render_artifacts",
        ["project_id"],
    )
    op.create_index(
        "ix_render_artifacts_revision_id",
        "render_artifacts",
        ["revision_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_render_artifacts_revision_id",
        table_name="render_artifacts",
    )
    op.drop_index(
        "ix_render_artifacts_project_id",
        table_name="render_artifacts",
    )
    op.drop_table("render_artifacts")
