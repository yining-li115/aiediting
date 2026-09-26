"""continuous edit conversations and bounded Agent turns

Revision ID: 4a8d1f6c2e90
Revises: 0f9e7c2b4a16
Create Date: 2026-09-26 18:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "4a8d1f6c2e90"
down_revision = "0f9e7c2b4a16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "purpose",
            name="uq_conversations_project_id_purpose",
        ),
    )
    op.create_index(
        op.f("ix_conversations_project_id"),
        "conversations",
        ["project_id"],
        unique=False,
    )

    with op.batch_alter_table("agent_turns") as batch:
        batch.alter_column(
            "id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
        )
        batch.alter_column(
            "version_before",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
        )
        batch.alter_column(
            "version_after",
            existing_type=sa.String(length=64),
            type_=sa.String(length=128),
        )
        batch.add_column(sa.Column("conversation_id", sa.String(length=128)))
        batch.add_column(sa.Column("job_id", sa.String(length=64)))
        batch.add_column(sa.Column("request_id", sa.String(length=128)))
        batch.add_column(sa.Column("idempotency_key", sa.String(length=128)))
        batch.add_column(sa.Column("request_hash", sa.String(length=128)))
        batch.add_column(sa.Column("status", sa.String(length=32)))
        batch.add_column(sa.Column("provider_version", sa.String(length=128)))
        batch.add_column(sa.Column("context_json", sa.JSON()))
        batch.add_column(sa.Column("steps_json", sa.JSON()))
        batch.add_column(sa.Column("patch_json", sa.JSON()))
        batch.add_column(sa.Column("render_artifact_id", sa.String(length=128)))
        batch.add_column(sa.Column("render_job_id", sa.String(length=64)))
        batch.add_column(sa.Column("error", sa.Text()))
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            )
        )
        batch.create_foreign_key(
            "fk_agent_turns_project_id_projects",
            "projects",
            ["project_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_agent_turns_conversation_id_conversations",
            "conversations",
            ["conversation_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_agent_turns_job_id_jobs",
            "jobs",
            ["job_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_agent_turns_version_before_project_revisions",
            "project_revisions",
            ["version_before"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_agent_turns_version_after_project_revisions",
            "project_revisions",
            ["version_after"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_agent_turns_render_artifact_id_render_artifacts",
            "render_artifacts",
            ["render_artifact_id"],
            ["id"],
        )
        batch.create_foreign_key(
            "fk_agent_turns_render_job_id_jobs",
            "jobs",
            ["render_job_id"],
            ["id"],
        )
        batch.create_unique_constraint("uq_agent_turns_job_id", ["job_id"])
        batch.create_unique_constraint(
            "uq_agent_turns_project_id_idempotency_key",
            ["project_id", "idempotency_key"],
        )
        batch.create_index(
            op.f("ix_agent_turns_project_id"),
            ["project_id"],
            unique=False,
        )
        batch.create_index(
            op.f("ix_agent_turns_conversation_id"),
            ["conversation_id"],
            unique=False,
        )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("agent_turn_id", sa.String(length=128), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("base_revision_id", sa.String(length=128), nullable=True),
        sa.Column("result_revision_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_turn_id"], ["agent_turns.id"]),
        sa.ForeignKeyConstraint(["base_revision_id"], ["project_revisions.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["result_revision_id"], ["project_revisions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_conversation_messages_agent_turn_id"),
        "conversation_messages",
        ["agent_turn_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_messages_conversation_id"),
        "conversation_messages",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_conversation_messages_project_id"),
        "conversation_messages",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_conversation_messages_project_id"),
        table_name="conversation_messages",
    )
    op.drop_index(
        op.f("ix_conversation_messages_conversation_id"),
        table_name="conversation_messages",
    )
    op.drop_index(
        op.f("ix_conversation_messages_agent_turn_id"),
        table_name="conversation_messages",
    )
    op.drop_table("conversation_messages")

    with op.batch_alter_table("agent_turns") as batch:
        batch.drop_index(op.f("ix_agent_turns_conversation_id"))
        batch.drop_index(op.f("ix_agent_turns_project_id"))
        batch.drop_constraint(
            "uq_agent_turns_project_id_idempotency_key",
            type_="unique",
        )
        batch.drop_constraint("uq_agent_turns_job_id", type_="unique")
        batch.drop_constraint(
            "fk_agent_turns_render_job_id_jobs",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "fk_agent_turns_render_artifact_id_render_artifacts",
            type_="foreignkey",
        )
        batch.drop_constraint("fk_agent_turns_job_id_jobs", type_="foreignkey")
        batch.drop_constraint(
            "fk_agent_turns_version_after_project_revisions",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "fk_agent_turns_version_before_project_revisions",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "fk_agent_turns_conversation_id_conversations",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "fk_agent_turns_project_id_projects",
            type_="foreignkey",
        )
        batch.drop_column("updated_at")
        batch.drop_column("error")
        batch.drop_column("render_job_id")
        batch.drop_column("render_artifact_id")
        batch.drop_column("patch_json")
        batch.drop_column("steps_json")
        batch.drop_column("context_json")
        batch.drop_column("provider_version")
        batch.drop_column("status")
        batch.drop_column("request_hash")
        batch.drop_column("idempotency_key")
        batch.drop_column("request_id")
        batch.drop_column("job_id")
        batch.drop_column("conversation_id")
        batch.alter_column(
            "version_after",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
        )
        batch.alter_column(
            "version_before",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
        )
        batch.alter_column(
            "id",
            existing_type=sa.String(length=128),
            type_=sa.String(length=64),
        )

    op.drop_index(op.f("ix_conversations_project_id"), table_name="conversations")
    op.drop_table("conversations")
