"""Phase 0.1: align AIDecisionLog.actor_id with users.id ON DELETE SET NULL.

Provider-invoice uniqueness is created in 077_phase0_core_audit after
historical duplicate reconciliation. This revision does not recreate that
index and does not import application services.
"""
from alembic import op
from sqlalchemy import inspect, text

revision = "078_phase0_1_audit_corrections"
down_revision = "077_phase0_core_audit"
branch_labels = None
depends_on = None


def _has_table(insp, name):
    return name in set(insp.get_table_names())


def _has_fk(insp, table, constrained_col, referred_table):
    if not _has_table(insp, table):
        return False
    for fk in insp.get_foreign_keys(table):
        if constrained_col in (fk.get("constrained_columns") or []) and fk.get("referred_table") == referred_table:
            return True
    return False


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)

    if _has_table(insp, "ai_decision_logs") and _has_table(insp, "users"):
        bind.execute(
            text(
                """
                UPDATE ai_decision_logs
                SET actor_id = NULL
                WHERE actor_id IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM users WHERE users.id = ai_decision_logs.actor_id)
                """
            )
        )
        if hasattr(insp, "clear_cache"):
            insp.clear_cache()
        if not _has_fk(insp, "ai_decision_logs", "actor_id", "users"):
            op.create_foreign_key(
                "fk_ai_decision_logs_actor_id_users",
                "ai_decision_logs",
                "users",
                ["actor_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    if _has_fk(insp, "ai_decision_logs", "actor_id", "users"):
        op.drop_constraint(
            "fk_ai_decision_logs_actor_id_users",
            "ai_decision_logs",
            type_="foreignkey",
        )
