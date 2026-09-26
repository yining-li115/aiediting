"""project revision repository

Revision ID: 6d2f0d4e8a91
Revises: 1a5feef2116d
Create Date: 2026-09-26 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "6d2f0d4e8a91"
down_revision = "1a5feef2116d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_revisions",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("parent_revision_id", sa.String(length=128), nullable=True),
        sa.Column("commit_id", sa.String(length=128), nullable=False),
        sa.Column("document_json", sa.JSON(), nullable=False),
        sa.Column("document_hash", sa.String(length=128), nullable=False),
        sa.Column("provenance_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_revision_id"], ["project_revisions.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "commit_id",
            name="uq_project_revisions_commit_id",
        ),
    )
    op.create_index(
        "ix_project_revisions_project_id",
        "project_revisions",
        ["project_id"],
        unique=False,
    )
    op.create_table(
        "project_commits",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("request_kind", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("base_revision_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("candidate_revision_id", sa.String(length=128), nullable=True),
        sa.Column("result_revision_id", sa.String(length=128), nullable=True),
        sa.Column("issue_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["base_revision_id"], ["project_revisions.id"]),
        sa.ForeignKeyConstraint(["candidate_revision_id"], ["project_revisions.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["result_revision_id"], ["project_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "idempotency_key",
            name="uq_project_commits_project_id_idempotency_key",
        ),
    )
    op.create_index(
        "ix_project_commits_project_id",
        "project_commits",
        ["project_id"],
        unique=False,
    )
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("current_revision_id", sa.String(length=128), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_projects_current_revision_id",
            "project_revisions",
            ["current_revision_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_projects_current_revision_id",
            ["current_revision_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_index("ix_projects_current_revision_id")
        batch_op.drop_constraint(
            "fk_projects_current_revision_id",
            type_="foreignkey",
        )
        batch_op.drop_column("current_revision_id")
    op.drop_index("ix_project_commits_project_id", table_name="project_commits")
    op.drop_table("project_commits")
    op.drop_index("ix_project_revisions_project_id", table_name="project_revisions")
    op.drop_table("project_revisions")
