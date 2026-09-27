"""immutable Style Reference Blueprints

Revision ID: 9b7d3c1e5f20
Revises: 4a8d1f6c2e90
Create Date: 2026-09-27 01:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "9b7d3c1e5f20"
down_revision = "4a8d1f6c2e90"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "style_reference_blueprints",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("asset_id", sa.String(length=64), nullable=False),
        sa.Column("blueprint_version", sa.String(length=16), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("blueprint_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["asset_id"], ["assets.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "asset_id",
            "evidence_hash",
            name="uq_style_blueprints_project_asset_evidence",
        ),
    )
    op.create_index(
        op.f("ix_style_reference_blueprints_project_id"),
        "style_reference_blueprints",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_style_reference_blueprints_asset_id"),
        "style_reference_blueprints",
        ["asset_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_style_reference_blueprints_asset_id"),
        table_name="style_reference_blueprints",
    )
    op.drop_index(
        op.f("ix_style_reference_blueprints_project_id"),
        table_name="style_reference_blueprints",
    )
    op.drop_table("style_reference_blueprints")
