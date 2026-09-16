"""Phase C closure: unique DLQ original_job_id + ATS secret backfill

Revision ID: 074_phase_c_closure
Revises: 073_phase_c_queue
"""
import logging

from alembic import op
import sqlalchemy as sa

revision = "074_phase_c_closure"
down_revision = "073_phase_c_queue"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def apply_dlq_original_job_id_unique(bind, alembic_op):
    """Collapse duplicate DLQ rows, then create UNIQUE(original_job_id). Never skip."""
    insp = sa.inspect(bind)
    if "dead_letter_jobs" not in insp.get_table_names():
        return

    rows = bind.execute(
        sa.text("SELECT id, original_job_id, failed_at FROM dead_letter_jobs")
    ).fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault(str(row[1]), []).append((str(row[0]), row[2]))
    for items in grouped.values():
        items.sort(key=lambda item: (str(item[1] or ""), item[0]))
        for extra_id, _failed in items[1:]:
            bind.execute(
                sa.text("DELETE FROM dead_letter_jobs WHERE id = :id"),
                {"id": extra_id},
            )

    insp = sa.inspect(bind)
    uniques = {tuple(u.get("column_names") or []) for u in insp.get_unique_constraints("dead_letter_jobs")}
    unique_indexes = {
        tuple(i.get("column_names") or [])
        for i in insp.get_indexes("dead_letter_jobs")
        if i.get("unique")
    }
    names = {u.get("name") for u in insp.get_unique_constraints("dead_letter_jobs")}
    if ("original_job_id",) in uniques or ("original_job_id",) in unique_indexes:
        return
    if "uq_dead_letter_original_job_id" in names:
        return
    if bind.dialect.name == "sqlite":
        bind.execute(
            sa.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_dead_letter_original_job_id "
                "ON dead_letter_jobs (original_job_id)"
            )
        )
        return
    alembic_op.create_unique_constraint(
        "uq_dead_letter_original_job_id",
        "dead_letter_jobs",
        ["original_job_id"],
    )


def upgrade():
    bind = op.get_bind()
    apply_dlq_original_job_id_unique(bind, op)

    try:
        from sqlalchemy.orm import Session
        from app.backend.services.integration_secrets import backfill_ats_secrets

        session = Session(bind=bind)
        try:
            result = backfill_ats_secrets(session)
            logger.warning(
                "ATS secret backfill result encrypted=%s skipped=%s reason=%s",
                result.get("encrypted"),
                result.get("skipped"),
                result.get("reason"),
            )
        finally:
            session.close()
    except Exception:
        logger.warning("ATS secret backfill skipped: master key unavailable or import failed")


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "dead_letter_jobs" not in insp.get_table_names():
        return
    uniques = {u.get("name") for u in insp.get_unique_constraints("dead_letter_jobs")}
    if "uq_dead_letter_original_job_id" in uniques:
        op.drop_constraint("uq_dead_letter_original_job_id", "dead_letter_jobs", type_="unique")
        return
    indexes = {i.get("name") for i in insp.get_indexes("dead_letter_jobs")}
    if "uq_dead_letter_original_job_id" in indexes:
        op.drop_index("uq_dead_letter_original_job_id", table_name="dead_letter_jobs")
