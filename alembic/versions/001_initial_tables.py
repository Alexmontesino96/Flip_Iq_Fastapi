"""initial_tables

Revision ID: 001
Revises:
Create Date: 2026-04-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), unique=True, index=True, nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("tier", sa.String(20), server_default="free", nullable=False),
        sa.Column("credits_remaining", sa.Integer(), server_default="20", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("barcode", sa.String(50), index=True, nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("brand", sa.String(255), nullable=True),
        sa.Column("category", sa.String(255), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("avg_sell_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "analyses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), index=True, nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), index=True, nullable=False),
        sa.Column("cost_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("marketplace", sa.String(50), nullable=False),
        sa.Column("estimated_sale_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("net_profit", sa.Numeric(10, 2), nullable=True),
        sa.Column("margin_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("roi_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("flip_score", sa.Integer(), nullable=True),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("velocity_score", sa.Integer(), nullable=True),
        sa.Column("recommendation", sa.String(20), nullable=True),
        sa.Column("channels", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "watchlists",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), index=True, nullable=False),
        sa.Column("name", sa.String(255), server_default="Mi Watchlist", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("watchlist_id", sa.Integer(), sa.ForeignKey("watchlists.id"), index=True, nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), index=True, nullable=False),
        sa.Column("target_buy_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("notes", sa.String(500), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("watchlist_items")
    op.drop_table("watchlists")
    op.drop_table("analyses")
    op.drop_table("products")
    op.drop_table("users")
