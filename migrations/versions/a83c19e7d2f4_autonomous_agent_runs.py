"""autonomous agent runs

Revision ID: a83c19e7d2f4
Revises: f2a6c04e91bd
Create Date: 2026-09-26 23:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "a83c19e7d2f4"
down_revision = "f2a6c04e91bd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("base_revision_id", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=128), nullable=False),
        sa.Column("provider_version", sa.String(length=128), nullable=False),
        sa.Column("decision_json", sa.JSON()),
        sa.Column("patch_json", sa.JSON()),
        sa.Column("result_revision_id", sa.String(length=128)),
        sa.Column("render_artifact_id", sa.String(length=128)),
        sa.Column("render_job_id", sa.String(length=64)),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["base_revision_id"], ["project_revisions.id"]),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["render_artifact_id"], ["render_artifacts.id"]),
        sa.ForeignKeyConstraint(["render_job_id"], ["jobs.id"]),
        sa.ForeignKeyConstraint(["result_revision_id"], ["project_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_agent_runs_job_id"),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_agent_runs_project_id_idempotency_key",
        ),
    )
    op.create_index("ix_agent_runs_project_id", "agent_runs", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_project_id", table_name="agent_runs")
    op.drop_table("agent_runs")
