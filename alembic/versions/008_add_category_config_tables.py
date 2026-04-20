"""Add category configuration tables: categories, category_channels, fee_schedules, shipping_templates

Revision ID: 008
Revises: 007
Create Date: 2026-04-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- categories ---
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(50), unique=True, nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("ebay_category_ids", ARRAY(sa.Integer()), server_default="{}"),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("observation_mode", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("engine_defaults", JSONB(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- category_channels ---
    op.create_table(
        "category_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(50), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("engine_overrides", JSONB(), server_default="{}", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("category_id", "channel", name="uq_category_channel"),
    )

    # --- fee_schedules ---
    op.create_table(
        "fee_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel", sa.String(50), nullable=False),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=True),
        sa.Column("fee_rate", sa.Numeric(6, 4), nullable=False),
        sa.Column("fee_fixed", sa.Numeric(8, 2), server_default="0", nullable=False),
        sa.Column("fee_note", sa.Text(), nullable=True),
        sa.Column("price_min", sa.Numeric(10, 2), nullable=True),
        sa.Column("price_max", sa.Numeric(10, 2), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_fee_schedules_lookup", "fee_schedules", ["channel", "category_id", "valid_from"])

    # --- shipping_templates ---
    op.create_table(
        "shipping_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(50), nullable=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("shipping_cost", sa.Numeric(8, 2), nullable=False),
        sa.Column("packaging_cost", sa.Numeric(8, 2), server_default="0", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("shipping_templates")
    op.drop_index("idx_fee_schedules_lookup", table_name="fee_schedules")
    op.drop_table("fee_schedules")
    op.drop_table("category_channels")
    op.drop_table("categories")
