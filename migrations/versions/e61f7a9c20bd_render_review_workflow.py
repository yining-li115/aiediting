"""revision-bound render review workflow

Revision ID: e61f7a9c20bd
Revises: b94d72fa316e
Create Date: 2026-09-28 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "e61f7a9c20bd"
down_revision = "b94d72fa316e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("render_artifacts") as batch:
        batch.add_column(sa.Column("review_job_id", sa.String(length=64)))
        batch.add_column(
            sa.Column(
                "review_status",
                sa.String(length=32),
                nullable=False,
                server_default="not_requested",
            )
        )
        batch.add_column(sa.Column("review_provider", sa.String(length=128)))
        batch.add_column(
            sa.Column("review_provider_version", sa.String(length=128))
        )
        batch.add_column(sa.Column("review_context_json", sa.JSON()))
        batch.add_column(sa.Column("review_json", sa.JSON()))
        batch.add_column(sa.Column("review_error", sa.Text()))
        batch.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True)))
        batch.create_foreign_key(
            "fk_render_artifacts_review_job_id_jobs",
            "jobs",
            ["review_job_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_render_artifacts_review_job_id",
            ["review_job_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("render_artifacts") as batch:
        batch.drop_constraint(
            "uq_render_artifacts_review_job_id",
            type_="unique",
        )
        batch.drop_constraint(
            "fk_render_artifacts_review_job_id_jobs",
            type_="foreignkey",
        )
        batch.drop_column("reviewed_at")
        batch.drop_column("review_error")
        batch.drop_column("review_json")
        batch.drop_column("review_context_json")
        batch.drop_column("review_provider_version")
        batch.drop_column("review_provider")
        batch.drop_column("review_status")
        batch.drop_column("review_job_id")
