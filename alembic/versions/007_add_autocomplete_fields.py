"""Add autocomplete fields to products: normalized_title, indexes, popularity signals

Revision ID: 007
Revises: 006
Create Date: 2026-04-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New columns on products
    op.add_column("products", sa.Column("ebay_epid", sa.String(20), nullable=True))
    op.add_column("products", sa.Column("normalized_title", sa.Text(), nullable=True))
    op.add_column("products", sa.Column("search_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("products", sa.Column("scan_count", sa.Integer(), server_default="0", nullable=False))
    op.add_column("products", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("products", sa.Column("ebay_avg_sold_price", sa.Numeric(10, 2), nullable=True))
    op.add_column("products", sa.Column("ebay_listing_count", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("price_updated_at", sa.DateTime(timezone=True), nullable=True))

    # Backfill normalized_title from existing titles (lowercase + strip)
    op.execute("""
        UPDATE products
        SET normalized_title = LOWER(REGEXP_REPLACE(
            REGEXP_REPLACE(title, '[^a-zA-Z0-9\\s]', ' ', 'g'),
            '\\s+', ' ', 'g'
        ))
        WHERE normalized_title IS NULL
    """)

    # B-tree prefix index for "starts with" queries
    op.execute("""
        CREATE INDEX idx_products_normalized_prefix
        ON products (normalized_title text_pattern_ops)
    """)

    # ILIKE index for "contains" queries
    op.create_index("idx_products_normalized_title", "products", ["normalized_title"])

    # Popularity index for ranking
    op.create_index("idx_products_search_count", "products", ["search_count"])

    # Try to enable pg_trgm (may fail on managed Postgres like Supabase)
    try:
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute("""
            CREATE INDEX idx_products_normalized_trgm
            ON products USING GIN (normalized_title gin_trgm_ops)
        """)
    except Exception:
        pass  # trigram not available, ILIKE fallback works fine


def downgrade() -> None:
    op.drop_index("idx_products_search_count", table_name="products")
    op.drop_index("idx_products_normalized_title", table_name="products")
    op.execute("DROP INDEX IF EXISTS idx_products_normalized_prefix")
    op.execute("DROP INDEX IF EXISTS idx_products_normalized_trgm")

    op.drop_column("products", "price_updated_at")
    op.drop_column("products", "ebay_listing_count")
    op.drop_column("products", "ebay_avg_sold_price")
    op.drop_column("products", "last_seen_at")
    op.drop_column("products", "scan_count")
    op.drop_column("products", "search_count")
    op.drop_column("products", "normalized_title")
    op.drop_column("products", "ebay_epid")
