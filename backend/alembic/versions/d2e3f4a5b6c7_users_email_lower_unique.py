"""users: lower-case emails + UNIQUE index on lower(email)

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""
import sqlalchemy as sa
from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    dups = conn.execute(sa.text(
        """
        SELECT lower(btrim(email)) AS norm, array_agg(id ORDER BY id) AS ids,
               array_agg(email ORDER BY id) AS emails
        FROM users GROUP BY 1 HAVING count(*) > 1 ORDER BY 1
        """
    )).fetchall()
    if dups:
        listing = "\n".join(f"  {r.norm}: ids={list(r.ids)} emails={list(r.emails)}" for r in dups)
        raise RuntimeError(
            "Cannot add the case-insensitive unique index on users.email: these "
            "accounts differ only by letter case/whitespace. Merge or rename them "
            "(keep one per group), then re-run the migration.\n" + listing
        )
    # Store every email in its normalized form (strip + lower).
    op.execute("UPDATE users SET email = lower(btrim(email)) WHERE email <> lower(btrim(email))")
    op.execute("CREATE UNIQUE INDEX uq_users_email_lower ON users (lower(email))")


def downgrade():
    # Lower-cased emails are left as they are.
    op.execute("DROP INDEX IF EXISTS uq_users_email_lower")
