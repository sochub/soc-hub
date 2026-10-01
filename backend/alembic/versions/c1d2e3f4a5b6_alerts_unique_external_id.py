"""alerts: unique (tenant_id, source, external_id) for idempotent ingest

Revision ID: c1d2e3f4a5b6
Revises: b1s2l3a4c5k6
"""
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "b1s2l3a4c5k6"
branch_labels = None
depends_on = None


def upgrade():
    # De-duplicate WITHOUT deleting data: in each duplicate group keep the
    # lowest id unchanged and suffix later rows' external_id with '#dup-<id>'.
    op.execute(
        """
        UPDATE alerts a
        SET external_id = a.external_id || '#dup-' || a.id
        FROM (
            SELECT id, row_number() OVER (
                PARTITION BY tenant_id, source, external_id ORDER BY id
            ) AS rn
            FROM alerts
            WHERE external_id IS NOT NULL
        ) d
        WHERE a.id = d.id AND d.rn > 1
        """
    )
    op.create_unique_constraint(
        "uq_alerts_tenant_source_external", "alerts", ["tenant_id", "source", "external_id"]
    )


def downgrade():
    # Renamed duplicates keep their '#dup-<id>' suffix.
    op.drop_constraint("uq_alerts_tenant_source_external", "alerts", type_="unique")
