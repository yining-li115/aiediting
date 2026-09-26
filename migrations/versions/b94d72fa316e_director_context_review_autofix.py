"""director context review and bounded autofix

Revision ID: b94d72fa316e
Revises: a83c19e7d2f4
Create Date: 2026-09-28 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b94d72fa316e"
down_revision = "a83c19e7d2f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("context_json", sa.JSON()))
    op.add_column("agent_runs", sa.Column("review_json", sa.JSON()))
    op.add_column(
        "agent_runs",
        sa.Column(
            "repair_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "repair_attempts")
    op.drop_column("agent_runs", "review_json")
    op.drop_column("agent_runs", "context_json")
