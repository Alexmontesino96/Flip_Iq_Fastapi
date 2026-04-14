"""Make user_id nullable in analyses and watchlists

Revision ID: 003
Revises: 002
Create Date: 2026-04-12
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("analyses", "user_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("watchlists", "user_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    op.alter_column("watchlists", "user_id", existing_type=sa.Integer(), nullable=False)
    op.alter_column("analyses", "user_id", existing_type=sa.Integer(), nullable=False)
