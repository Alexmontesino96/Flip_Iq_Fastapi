"""Add share_token to analyses for public sharing

Revision ID: 012
Revises: 011
Create Date: 2026-04-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("share_token", sa.String(32), nullable=True))
    op.create_unique_constraint("uq_analyses_share_token", "analyses", ["share_token"])
    op.create_index("ix_analyses_share_token", "analyses", ["share_token"])


def downgrade() -> None:
    op.drop_index("ix_analyses_share_token", table_name="analyses")
    op.drop_constraint("uq_analyses_share_token", "analyses", type_="unique")
    op.drop_column("analyses", "share_token")
