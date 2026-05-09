"""Add onesignal_subscription_id to users table

Revision ID: 014
Revises: 013
Create Date: 2026-05-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("onesignal_subscription_id", sa.String(255), nullable=True))
    op.create_index("ix_users_onesignal_sub_id", "users", ["onesignal_subscription_id"])


def downgrade() -> None:
    op.drop_index("ix_users_onesignal_sub_id", table_name="users")
    op.drop_column("users", "onesignal_subscription_id")
