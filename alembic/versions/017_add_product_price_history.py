"""Add product_price_history table for daily price tracking

Revision ID: 017
Revises: 016
Create Date: 2026-05-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_price_history",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("product_id", sa.Integer, sa.ForeignKey("products.id"), nullable=False, index=True),
        sa.Column("recorded_date", sa.Date, nullable=False, index=True),
        sa.Column("ebay_median_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("amazon_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("ebay_sold_count", sa.Integer, nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="cron"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("product_id", "recorded_date", name="uq_product_price_date"),
    )


def downgrade() -> None:
    op.drop_table("product_price_history")
