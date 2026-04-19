"""backfill_analyses_user_id

Asigna todos los análisis con user_id=NULL al único usuario existente.

Revision ID: c6a3745a7779
Revises: 622d2ddfc489
Create Date: 2026-04-19 18:24:55.007783
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c6a3745a7779'
down_revision: Union[str, None] = '622d2ddfc489'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE analyses
        SET user_id = (SELECT id FROM users ORDER BY id LIMIT 1)
        WHERE user_id IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE analyses
        SET user_id = NULL
        WHERE user_id IS NOT NULL
        """
    )
