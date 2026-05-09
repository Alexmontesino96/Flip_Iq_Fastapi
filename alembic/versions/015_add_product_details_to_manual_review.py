"""Add product_name, product_category, image_url to manual_review_requests

Revision ID: 015
Revises: 014
Create Date: 2026-05-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("manual_review_requests", sa.Column("product_name", sa.String(300), nullable=True))
    op.add_column("manual_review_requests", sa.Column("product_category", sa.String(100), nullable=True))
    op.add_column("manual_review_requests", sa.Column("image_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("manual_review_requests", "image_url")
    op.drop_column("manual_review_requests", "product_category")
    op.drop_column("manual_review_requests", "product_name")
