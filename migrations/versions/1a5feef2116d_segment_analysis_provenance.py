"""Add provenance fields for material segment analysis.

Revision ID: 1a5feef2116d
Revises: 47fa30e47b05
Create Date: 2026-08-26 16:09:17.279046
"""

from alembic import op
import sqlalchemy as sa


revision = "1a5feef2116d"
down_revision = "47fa30e47b05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("segments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("provider", sa.String(length=64)))
        batch_op.add_column(sa.Column("model", sa.String(length=128)))
        batch_op.add_column(sa.Column("prompt_version", sa.String(length=64)))
        batch_op.add_column(sa.Column("raw_response", sa.Text()))
        batch_op.add_column(
            sa.Column("repairs", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(sa.Column("analyzed_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    with op.batch_alter_table("segments", schema=None) as batch_op:
        batch_op.drop_column("analyzed_at")
        batch_op.drop_column("repairs")
        batch_op.drop_column("raw_response")
        batch_op.drop_column("prompt_version")
        batch_op.drop_column("model")
        batch_op.drop_column("provider")
