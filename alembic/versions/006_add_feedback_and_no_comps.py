"""Add analysis_feedbacks table and no_comps_found column

Revision ID: 006
Revises: c6a3745a7779
Create Date: 2026-04-20
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "c6a3745a7779"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add no_comps_found column to analyses
    op.add_column(
        "analyses",
        sa.Column("no_comps_found", sa.Boolean(), server_default="false", nullable=False),
    )

    # 2. Backfill: mark existing analyses with no comps as no_comps_found=True
    # (analyses where estimated_sale_price IS NULL and recommendation = 'pass')
    op.execute(
        "UPDATE analyses SET no_comps_found = true "
        "WHERE estimated_sale_price IS NULL AND recommendation = 'pass'"
    )

    # 3. Create analysis_feedbacks table
    op.create_table(
        "analysis_feedbacks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "analysis_id",
            sa.Integer(),
            sa.ForeignKey("analyses.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("feedback_type", sa.String(30), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("actual_sale_price", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("analysis_feedbacks")
    op.drop_column("analyses", "no_comps_found")
