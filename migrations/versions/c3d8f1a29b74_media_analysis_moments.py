"""media analysis moments

Revision ID: c3d8f1a29b74
Revises: 84c9a6e75d21
Create Date: 2026-09-26 20:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "c3d8f1a29b74"
down_revision = "84c9a6e75d21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "analysis_readiness",
                sa.String(length=32),
                nullable=False,
                server_default="pending",
            )
        )
        batch_op.add_column(sa.Column("analysis_job_id", sa.String(length=64)))
        batch_op.add_column(sa.Column("analysis_error", sa.Text()))
        batch_op.add_column(sa.Column("analyzed_at", sa.DateTime(timezone=True)))
        batch_op.create_foreign_key(
            "fk_assets_analysis_job_id",
            "jobs",
            ["analysis_job_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_assets_analysis_job_id",
            ["analysis_job_id"],
            unique=False,
        )

    op.create_table(
        "media_shots",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("start_tick", sa.Integer(), nullable=False),
        sa.Column("end_tick", sa.Integer(), nullable=False),
        sa.Column("timescale", sa.Integer(), nullable=False),
        sa.Column("detector", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "asset_id",
            "ordinal",
            name="uq_media_shots_asset_ordinal",
        ),
    )
    op.create_index("ix_media_shots_asset_id", "media_shots", ["asset_id"])
    op.create_index("ix_media_shots_project_id", "media_shots", ["project_id"])

    op.create_table(
        "moments",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("shot_ids_json", sa.JSON(), nullable=False),
        sa.Column("start_tick", sa.Integer(), nullable=False),
        sa.Column("end_tick", sa.Integer(), nullable=False),
        sa.Column("timescale", sa.Integer(), nullable=False),
        sa.Column("analysis_window_count", sa.Integer(), nullable=False),
        sa.Column("keyframe_keys_json", sa.JSON(), nullable=False),
        sa.Column("technical_quality", sa.Float()),
        sa.Column("sharpness", sa.Float()),
        sa.Column("exposure_score", sa.Float()),
        sa.Column("stability_score", sa.Float()),
        sa.Column("motion_quality", sa.Float()),
        sa.Column("camera_motion", sa.String(length=64), nullable=False),
        sa.Column("is_usable", sa.Boolean(), nullable=False),
        sa.Column("reject_reason", sa.String(length=256)),
        sa.Column("semantic_status", sa.String(length=32), nullable=False),
        sa.Column("semantic", sa.Text()),
        sa.Column("semantic_role", sa.String(length=64), nullable=False),
        sa.Column("shot_type", sa.String(length=64), nullable=False),
        sa.Column("people", sa.Integer()),
        sa.Column("emotion_json", sa.JSON()),
        sa.Column("interestingness", sa.Float()),
        sa.Column("composition_score", sa.Float()),
        sa.Column("semantic_error", sa.Text()),
        sa.Column("provider", sa.String(length=64)),
        sa.Column("model", sa.String(length=128)),
        sa.Column("prompt_version", sa.String(length=64)),
        sa.Column("raw_response", sa.Text()),
        sa.Column("embedding", sa.LargeBinary()),
        sa.Column("embedding_dim", sa.Integer()),
        sa.Column("analyzed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "ordinal", name="uq_moments_asset_ordinal"),
    )
    op.create_index("ix_moments_asset_id", "moments", ["asset_id"])
    op.create_index("ix_moments_project_id", "moments", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_moments_project_id", table_name="moments")
    op.drop_index("ix_moments_asset_id", table_name="moments")
    op.drop_table("moments")
    op.drop_index("ix_media_shots_project_id", table_name="media_shots")
    op.drop_index("ix_media_shots_asset_id", table_name="media_shots")
    op.drop_table("media_shots")
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_index("ix_assets_analysis_job_id")
        batch_op.drop_constraint("fk_assets_analysis_job_id", type_="foreignkey")
        batch_op.drop_column("analyzed_at")
        batch_op.drop_column("analysis_error")
        batch_op.drop_column("analysis_job_id")
        batch_op.drop_column("analysis_readiness")
