"""Add customerio_synced flag to users

Revision ID: 016
Revises: 015
Create Date: 2026-05-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("customerio_synced", sa.Boolean, nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("users", "customerio_synced")
