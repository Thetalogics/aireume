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


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "dead_letter_jobs" in insp.get_table_names():
        uniques = {u.get("name") for u in insp.get_unique_constraints("dead_letter_jobs")}
        indexes = {i.get("name") for i in insp.get_indexes("dead_letter_jobs")}
        if "uq_dead_letter_original_job_id" not in uniques:
            dup = bind.execute(
                sa.text(
                    "SELECT original_job_id FROM dead_letter_jobs "
                    "GROUP BY original_job_id HAVING COUNT(*) > 1 LIMIT 1"
                )
            ).fetchone()
            if dup is None:
                op.create_unique_constraint(
                    "uq_dead_letter_original_job_id",
                    "dead_letter_jobs",
                    ["original_job_id"],
                )
            else:
                logger.warning("Skipping unique DLQ original_job_id: duplicates exist")
        _ = indexes

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
    if "dead_letter_jobs" in insp.get_table_names():
        uniques = {u.get("name") for u in insp.get_unique_constraints("dead_letter_jobs")}
        if "uq_dead_letter_original_job_id" in uniques:
            op.drop_constraint("uq_dead_letter_original_job_id", "dead_letter_jobs", type_="unique")
