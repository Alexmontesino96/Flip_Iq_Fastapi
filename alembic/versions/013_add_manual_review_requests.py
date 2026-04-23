"""Add manual_review_requests table for not-found products

Revision ID: 013
Revises: 012
Create Date: 2026-04-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "manual_review_requests",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True, index=True),
        sa.Column("analysis_id", sa.Integer, sa.ForeignKey("analyses.id"), nullable=True, index=True),
        sa.Column("query", sa.String(500), nullable=False),
        sa.Column("barcode", sa.String(100), nullable=True),
        sa.Column("cost_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("marketplace", sa.String(50), nullable=False, server_default="ebay"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
        sa.Column("resolved_analysis_id", sa.Integer, sa.ForeignKey("analyses.id"), nullable=True),
        sa.Column("admin_notes", sa.Text, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("manual_review_requests")
