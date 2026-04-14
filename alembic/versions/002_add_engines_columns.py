"""Add engines columns to analyses

Revision ID: 002
Revises: 001
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("confidence_score", sa.Integer(), nullable=True))
    op.add_column("analyses", sa.Column("opportunity_score", sa.Integer(), nullable=True))
    op.add_column("analyses", sa.Column("engines_data", sa.JSON(), nullable=True))
    op.add_column("analyses", sa.Column("ai_explanation", sa.Text(), nullable=True))
    op.add_column("analyses", sa.Column("shipping_cost", sa.Numeric(10, 2), nullable=True))
    op.add_column("analyses", sa.Column("prep_cost", sa.Numeric(10, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("analyses", "prep_cost")
    op.drop_column("analyses", "shipping_cost")
    op.drop_column("analyses", "ai_explanation")
    op.drop_column("analyses", "engines_data")
    op.drop_column("analyses", "opportunity_score")
    op.drop_column("analyses", "confidence_score")
