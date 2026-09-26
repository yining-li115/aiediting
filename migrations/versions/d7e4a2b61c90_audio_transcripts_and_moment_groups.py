"""audio transcripts and semantic moment groups

Revision ID: d7e4a2b61c90
Revises: c3d8f1a29b74
Create Date: 2026-09-26 22:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "d7e4a2b61c90"
down_revision = "c3d8f1a29b74"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transcript_segments",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start_tick", sa.Integer(), nullable=False),
        sa.Column("end_tick", sa.Integer(), nullable=False),
        sa.Column("timescale", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=32)),
        sa.Column("speaker", sa.String(length=128)),
        sa.Column("emotion", sa.String(length=64)),
        sa.Column("confidence", sa.Float()),
        sa.Column("provider", sa.String(length=64)),
        sa.Column("model", sa.String(length=128)),
        sa.Column("raw_response", sa.Text()),
        sa.Column("analyzed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "asset_id",
            "ordinal",
            name="uq_transcript_segments_asset_ordinal",
        ),
    )
    op.create_index(
        "ix_transcript_segments_asset_id",
        "transcript_segments",
        ["asset_id"],
    )
    op.create_index(
        "ix_transcript_segments_project_id",
        "transcript_segments",
        ["project_id"],
    )
    with op.batch_alter_table("moments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "transcript_segment_ids_json",
                sa.JSON(),
                nullable=False,
                server_default="[]",
            )
        )
        batch_op.add_column(
            sa.Column(
                "has_speech",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("asr_text", sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table("moments", schema=None) as batch_op:
        batch_op.drop_column("asr_text")
        batch_op.drop_column("has_speech")
        batch_op.drop_column("transcript_segment_ids_json")
    op.drop_index(
        "ix_transcript_segments_project_id",
        table_name="transcript_segments",
    )
    op.drop_index(
        "ix_transcript_segments_asset_id",
        table_name="transcript_segments",
    )
    op.drop_table("transcript_segments")
