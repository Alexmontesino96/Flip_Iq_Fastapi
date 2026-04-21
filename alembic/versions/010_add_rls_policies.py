"""Add RLS policies for data isolation between users.

Ensures that even if the frontend queries Supabase directly,
each user can only see their own data.

Revision ID: 010
Revises: 009
Create Date: 2026-04-20
"""

from typing import Sequence, Union

from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---------------------------------------------------------------------------
    # Helper: Supabase exposes auth.uid() which returns the UUID from the JWT.
    # Our users table links supabase_id (text) → user row.
    # Policies use: users.supabase_id = auth.uid()::text
    # ---------------------------------------------------------------------------

    # -- analyses: users can only see/modify their own analyses --
    op.execute("""
        CREATE POLICY analyses_select_own ON analyses
            FOR SELECT TO authenticated
            USING (
                user_id IN (
                    SELECT id FROM users WHERE supabase_id = auth.uid()::text
                )
            )
    """)
    op.execute("""
        CREATE POLICY analyses_insert_own ON analyses
            FOR INSERT TO authenticated
            WITH CHECK (
                user_id IN (
                    SELECT id FROM users WHERE supabase_id = auth.uid()::text
                )
            )
    """)
    op.execute("""
        CREATE POLICY analyses_update_own ON analyses
            FOR UPDATE TO authenticated
            USING (
                user_id IN (
                    SELECT id FROM users WHERE supabase_id = auth.uid()::text
                )
            )
    """)
    op.execute("""
        CREATE POLICY analyses_delete_own ON analyses
            FOR DELETE TO authenticated
            USING (
                user_id IN (
                    SELECT id FROM users WHERE supabase_id = auth.uid()::text
                )
            )
    """)

    # -- analysis_feedbacks: users can only see/modify their own feedback --
    op.execute("""
        CREATE POLICY feedbacks_select_own ON analysis_feedbacks
            FOR SELECT TO authenticated
            USING (
                user_id IN (
                    SELECT id FROM users WHERE supabase_id = auth.uid()::text
                )
            )
    """)
    op.execute("""
        CREATE POLICY feedbacks_insert_own ON analysis_feedbacks
            FOR INSERT TO authenticated
            WITH CHECK (
                user_id IN (
                    SELECT id FROM users WHERE supabase_id = auth.uid()::text
                )
            )
    """)
    op.execute("""
        CREATE POLICY feedbacks_delete_own ON analysis_feedbacks
            FOR DELETE TO authenticated
            USING (
                user_id IN (
                    SELECT id FROM users WHERE supabase_id = auth.uid()::text
                )
            )
    """)

    # -- users: each user can only see/modify their own row --
    op.execute("""
        CREATE POLICY users_select_own ON users
            FOR SELECT TO authenticated
            USING (supabase_id = auth.uid()::text)
    """)
    op.execute("""
        CREATE POLICY users_update_own ON users
            FOR UPDATE TO authenticated
            USING (supabase_id = auth.uid()::text)
    """)

    # -- watchlists: users can only see/modify their own watchlists --
    op.execute("""
        CREATE POLICY watchlists_select_own ON watchlists
            FOR SELECT TO authenticated
            USING (
                user_id IN (
                    SELECT id FROM users WHERE supabase_id = auth.uid()::text
                )
            )
    """)
    op.execute("""
        CREATE POLICY watchlists_insert_own ON watchlists
            FOR INSERT TO authenticated
            WITH CHECK (
                user_id IN (
                    SELECT id FROM users WHERE supabase_id = auth.uid()::text
                )
            )
    """)
    op.execute("""
        CREATE POLICY watchlists_update_own ON watchlists
            FOR UPDATE TO authenticated
            USING (
                user_id IN (
                    SELECT id FROM users WHERE supabase_id = auth.uid()::text
                )
            )
    """)
    op.execute("""
        CREATE POLICY watchlists_delete_own ON watchlists
            FOR DELETE TO authenticated
            USING (
                user_id IN (
                    SELECT id FROM users WHERE supabase_id = auth.uid()::text
                )
            )
    """)

    # -- watchlist_items: access through watchlist ownership --
    op.execute("""
        CREATE POLICY watchlist_items_select_own ON watchlist_items
            FOR SELECT TO authenticated
            USING (
                watchlist_id IN (
                    SELECT w.id FROM watchlists w
                    JOIN users u ON w.user_id = u.id
                    WHERE u.supabase_id = auth.uid()::text
                )
            )
    """)
    op.execute("""
        CREATE POLICY watchlist_items_insert_own ON watchlist_items
            FOR INSERT TO authenticated
            WITH CHECK (
                watchlist_id IN (
                    SELECT w.id FROM watchlists w
                    JOIN users u ON w.user_id = u.id
                    WHERE u.supabase_id = auth.uid()::text
                )
            )
    """)
    op.execute("""
        CREATE POLICY watchlist_items_delete_own ON watchlist_items
            FOR DELETE TO authenticated
            USING (
                watchlist_id IN (
                    SELECT w.id FROM watchlists w
                    JOIN users u ON w.user_id = u.id
                    WHERE u.supabase_id = auth.uid()::text
                )
            )
    """)

    # -- products: read-only for all authenticated users (shared catalog) --
    op.execute("""
        CREATE POLICY products_select_all ON products
            FOR SELECT TO authenticated
            USING (true)
    """)

    # -- subscriptions: users can only see their own --
    op.execute("""
        CREATE POLICY subscriptions_select_own ON subscriptions
            FOR SELECT TO authenticated
            USING (
                user_id IN (
                    SELECT id FROM users WHERE supabase_id = auth.uid()::text
                )
            )
    """)

    # -- waitlist_entries: public read for email verification --
    op.execute("""
        CREATE POLICY waitlist_select_all ON waitlist_entries
            FOR SELECT TO authenticated, anon
            USING (true)
    """)
    op.execute("""
        CREATE POLICY waitlist_insert_all ON waitlist_entries
            FOR INSERT TO authenticated, anon
            WITH CHECK (true)
    """)

    # -- categories/fee_schedules/shipping_templates: read-only shared config --
    for table in ("categories", "category_channels", "fee_schedules", "shipping_templates"):
        op.execute(f"""
            CREATE POLICY {table}_select_all ON {table}
                FOR SELECT TO authenticated, anon
                USING (true)
        """)

    # -- Revoke dangerous permissions from anon/authenticated --
    # Keep SELECT where needed, remove write access on sensitive tables
    for table in ("analyses", "analysis_feedbacks", "users", "watchlists",
                   "watchlist_items", "subscriptions"):
        op.execute(f"REVOKE TRUNCATE ON {table} FROM anon, authenticated")

    # Anon should NOT write to user-owned tables
    for table in ("analyses", "analysis_feedbacks", "users", "watchlists",
                   "watchlist_items", "subscriptions"):
        op.execute(f"REVOKE INSERT, UPDATE, DELETE ON {table} FROM anon")
        op.execute(f"REVOKE SELECT ON {table} FROM anon")


def downgrade() -> None:
    # Drop all policies
    policies = [
        ("analyses", "analyses_select_own"),
        ("analyses", "analyses_insert_own"),
        ("analyses", "analyses_update_own"),
        ("analyses", "analyses_delete_own"),
        ("analysis_feedbacks", "feedbacks_select_own"),
        ("analysis_feedbacks", "feedbacks_insert_own"),
        ("analysis_feedbacks", "feedbacks_delete_own"),
        ("users", "users_select_own"),
        ("users", "users_update_own"),
        ("watchlists", "watchlists_select_own"),
        ("watchlists", "watchlists_insert_own"),
        ("watchlists", "watchlists_update_own"),
        ("watchlists", "watchlists_delete_own"),
        ("watchlist_items", "watchlist_items_select_own"),
        ("watchlist_items", "watchlist_items_insert_own"),
        ("watchlist_items", "watchlist_items_delete_own"),
        ("products", "products_select_all"),
        ("subscriptions", "subscriptions_select_own"),
        ("waitlist_entries", "waitlist_select_all"),
        ("waitlist_entries", "waitlist_insert_all"),
        ("categories", "categories_select_all"),
        ("category_channels", "category_channels_select_all"),
        ("fee_schedules", "fee_schedules_select_all"),
        ("shipping_templates", "shipping_templates_select_all"),
    ]
    for table, policy in policies:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")

    # Re-grant permissions to anon
    for table in ("analyses", "analysis_feedbacks", "users", "watchlists",
                   "watchlist_items", "subscriptions"):
        op.execute(f"GRANT ALL ON {table} TO anon, authenticated")
