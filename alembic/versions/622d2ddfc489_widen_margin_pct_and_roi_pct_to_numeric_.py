"""Widen margin_pct and roi_pct to Numeric(10,2)

Revision ID: 622d2ddfc489
Revises: 005
Create Date: 2026-04-16 23:05:01.945744
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '622d2ddfc489'
down_revision: Union[str, None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('analyses', 'margin_pct',
               existing_type=sa.NUMERIC(precision=5, scale=2),
               type_=sa.Numeric(precision=10, scale=2),
               existing_nullable=True)
    op.alter_column('analyses', 'roi_pct',
               existing_type=sa.NUMERIC(precision=5, scale=2),
               type_=sa.Numeric(precision=10, scale=2),
               existing_nullable=True)


def downgrade() -> None:
    op.alter_column('analyses', 'roi_pct',
               existing_type=sa.Numeric(precision=10, scale=2),
               type_=sa.NUMERIC(precision=5, scale=2),
               existing_nullable=True)
    op.alter_column('analyses', 'margin_pct',
               existing_type=sa.Numeric(precision=10, scale=2),
               type_=sa.NUMERIC(precision=5, scale=2),
               existing_nullable=True)
