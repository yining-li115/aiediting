"""persist derived audio Asset analysis

Revision ID: 3e8a1b9c5d72
Revises: 2c4e6a8f0b31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "3e8a1b9c5d72"
down_revision = "2c4e6a8f0b31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("audio_analysis_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("assets", "audio_analysis_json")
