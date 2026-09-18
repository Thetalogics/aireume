"""Phase 0 schema: invoice counters, AI decision audit columns, provider invoice uniqueness.

Historical BillingEvent duplicates: unique (provider, event_id) already shipped in 051.
This revision does not delete billing rows except duplicate (provider, event_id)
pairs. Keeper precedence: success > processing > error/failed > ignored > other;
ties keep the newest id. A successful duplicate is never replaced by an older
failed row.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "077_phase0_core_audit"
down_revision = "076_candidate_merge"
branch_labels = None
depends_on = None


def _has_table(insp, name):
    return name in set(insp.get_table_names())


def _has_column(insp, table, col):
    if not _has_table(insp, table):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def _has_index(insp, table, index_name):
    if not _has_table(insp, table):
        return False
    return index_name in {ix["name"] for ix in insp.get_indexes(table)}


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    is_pg = bind.dialect.name == "postgresql"

    if not _has_table(insp, "invoice_year_counters"):
        op.create_table(
            "invoice_year_counters",
            sa.Column("year", sa.Integer(), primary_key=True),
            sa.Column("last_value", sa.Integer(), nullable=False, server_default="0"),
        )

    if _has_table(insp, "invoices"):
        bind.execute(
            text(
                """
                INSERT INTO invoice_year_counters (year, last_value)
                SELECT CAST(substr(invoice_number, 5, 4) AS INTEGER) AS year,
                       MAX(CAST(substr(invoice_number, 10) AS INTEGER)) AS last_value
                FROM invoices
                WHERE invoice_number LIKE 'INV-____-_____'
                GROUP BY CAST(substr(invoice_number, 5, 4) AS INTEGER)
                ON CONFLICT (year) DO UPDATE SET last_value = GREATEST(invoice_year_counters.last_value, excluded.last_value)
                """
            )
        ) if is_pg else None
        if not is_pg and _has_table(insp, "invoice_year_counters"):
            rows = bind.execute(
                text(
                    """
                    SELECT substr(invoice_number, 5, 4) AS year,
                           MAX(CAST(substr(invoice_number, 10) AS INTEGER)) AS last_value
                    FROM invoices
                    WHERE invoice_number LIKE 'INV-%'
                    GROUP BY substr(invoice_number, 5, 4)
                    """
                )
            ).fetchall()
            for year, last_value in rows:
                try:
                    y = int(year)
                    v = int(last_value or 0)
                except (TypeError, ValueError):
                    continue
                bind.execute(
                    text(
                        "INSERT OR IGNORE INTO invoice_year_counters (year, last_value) VALUES (:y, :v)"
                    ),
                    {"y": y, "v": v},
                )

        if not _has_index(insp, "invoices", "uq_invoice_provider_invoice_id"):
            if is_pg:
                op.execute(
                    sa.text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_provider_invoice_id
                        ON invoices (payment_provider, provider_invoice_id)
                        WHERE provider_invoice_id IS NOT NULL
                        """
                    )
                )
            else:
                op.execute(
                    sa.text(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS uq_invoice_provider_invoice_id
                        ON invoices (payment_provider, provider_invoice_id)
                        WHERE provider_invoice_id IS NOT NULL
                        """
                    )
                )

    if _has_table(insp, "ai_decision_logs"):
        cols = {
            "decision_type": sa.Column("decision_type", sa.String(32), nullable=True),
            "scoring_weights": sa.Column("scoring_weights", sa.JSON(), nullable=True),
            "algorithm_version": sa.Column("algorithm_version", sa.String(32), nullable=True),
            "actor_id": sa.Column("actor_id", sa.Integer(), nullable=True),
            "recommendation": sa.Column("recommendation", sa.String(50), nullable=True),
        }
        if hasattr(insp, "clear_cache"):
            insp.clear_cache()
        existing = {c["name"] for c in insp.get_columns("ai_decision_logs")}
        for name, col in cols.items():
            if name not in existing:
                op.add_column("ai_decision_logs", col)
        if "ix_ai_decision_logs_decision_type" not in {
            ix["name"] for ix in insp.get_indexes("ai_decision_logs")
        }:
            op.create_index(
                "ix_ai_decision_logs_decision_type", "ai_decision_logs", ["decision_type"]
            )

    if is_pg and _has_table(insp, "billing_events"):
        # Duplicate (provider, event_id) rows: keep one keeper per pair.
        # Precedence: success > processing > error/failed > ignored > other.
        # Same status: keep highest id (newest). Never downgrade success to failed.
        bind.execute(
            text(
                """
                DELETE FROM billing_events
                WHERE id IN (
                    SELECT id FROM (
                        SELECT id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY provider, event_id
                                   ORDER BY
                                       CASE lower(coalesce(result, ''))
                                           WHEN 'success' THEN 0
                                           WHEN 'processing' THEN 1
                                           WHEN 'error' THEN 2
                                           WHEN 'failed' THEN 2
                                           WHEN 'ignored' THEN 3
                                           ELSE 9
                                       END,
                                       id DESC
                               ) AS rn
                        FROM billing_events
                        WHERE event_id IS NOT NULL
                    ) ranked
                    WHERE rn > 1
                )
                """
            )
        )


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    if _has_index(insp, "invoices", "uq_invoice_provider_invoice_id"):
        op.drop_index("uq_invoice_provider_invoice_id", table_name="invoices")
    if _has_table(insp, "invoice_year_counters"):
        op.drop_table("invoice_year_counters")
    if _has_table(insp, "ai_decision_logs"):
        for col in ("recommendation", "actor_id", "algorithm_version", "scoring_weights", "decision_type"):
            if _has_column(insp, "ai_decision_logs", col):
                op.drop_column("ai_decision_logs", col)
