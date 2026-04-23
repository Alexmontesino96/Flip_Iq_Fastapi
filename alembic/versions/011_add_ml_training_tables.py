"""Add ML training samples and shadow comparison tables.

Tables for collecting LLM input/output pairs as training data
for local ML models, and for shadow-mode comparison logging.

Revision ID: 011
Revises: 010
"""

from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ml_training_samples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task", sa.String(30), nullable=False, index=True),
        sa.Column("input_keyword", sa.String(500), nullable=True),
        sa.Column("input_title", sa.Text(), nullable=False),
        sa.Column("llm_output", sa.JSON(), nullable=False),
        sa.Column("llm_provider", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "ml_shadow_comparisons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task", sa.String(30), nullable=False, index=True),
        sa.Column("input_keyword", sa.String(500), nullable=True),
        sa.Column("input_title", sa.Text(), nullable=False),
        sa.Column("ml_prediction", sa.JSON(), nullable=False),
        sa.Column("llm_prediction", sa.JSON(), nullable=False),
        sa.Column("agreed", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("ml_shadow_comparisons")
    op.drop_table("ml_training_samples")
