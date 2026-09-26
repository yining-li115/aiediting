"""bounded post-render AutoFix workflow

Revision ID: 0f9e7c2b4a16
Revises: e61f7a9c20bd
Create Date: 2026-09-29 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0f9e7c2b4a16"
down_revision = "e61f7a9c20bd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.add_column(sa.Column("render_autofix_job_id", sa.String(length=64)))
        batch.add_column(
            sa.Column(
                "render_autofix_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("render_autofix_context_json", sa.JSON()))
        batch.add_column(sa.Column("render_autofix_decision_json", sa.JSON()))
        batch.add_column(sa.Column("render_autofix_review_json", sa.JSON()))
        batch.create_foreign_key(
            "fk_agent_runs_render_autofix_job_id_jobs",
            "jobs",
            ["render_autofix_job_id"],
            ["id"],
        )
        batch.create_unique_constraint(
            "uq_agent_runs_render_autofix_job_id",
            ["render_autofix_job_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_runs") as batch:
        batch.drop_constraint(
            "uq_agent_runs_render_autofix_job_id",
            type_="unique",
        )
        batch.drop_constraint(
            "fk_agent_runs_render_autofix_job_id_jobs",
            type_="foreignkey",
        )
        batch.drop_column("render_autofix_review_json")
        batch.drop_column("render_autofix_decision_json")
        batch.drop_column("render_autofix_context_json")
        batch.drop_column("render_autofix_attempts")
        batch.drop_column("render_autofix_job_id")
