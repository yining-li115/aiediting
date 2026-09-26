"""media processing readiness

Revision ID: 84c9a6e75d21
Revises: 6d2f0d4e8a91
Create Date: 2026-09-26 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "84c9a6e75d21"
down_revision = "6d2f0d4e8a91"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("thumbnail_key", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "readiness",
                sa.String(length=32),
                nullable=False,
                server_default="uploaded",
            )
        )
        batch_op.add_column(
            sa.Column("processing_job_id", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("processing_error", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_assets_processing_job_id",
            "jobs",
            ["processing_job_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_assets_processing_job_id",
            ["processing_job_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_index("ix_assets_processing_job_id")
        batch_op.drop_constraint("fk_assets_processing_job_id", type_="foreignkey")
        batch_op.drop_column("processed_at")
        batch_op.drop_column("processing_error")
        batch_op.drop_column("processing_job_id")
        batch_op.drop_column("readiness")
        batch_op.drop_column("thumbnail_key")
