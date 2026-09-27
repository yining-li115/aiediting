"""persist model-authored source understanding v2

Revision ID: 2c4e6a8f0b31
Revises: 9b7d3c1e5f20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "2c4e6a8f0b31"
down_revision = "9b7d3c1e5f20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("moments", sa.Column("understanding_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("moments", "understanding_json")
