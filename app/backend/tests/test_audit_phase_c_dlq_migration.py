"""AUD-020: migration 074 must unique original_job_id even when duplicates exist."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect, text


def test_074_consolidates_duplicate_dlq_rows_then_creates_unique():
    import importlib.util
    from pathlib import Path

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    path = Path(__file__).resolve().parents[3] / "alembic" / "versions" / "074_phase_c_closure.py"
    spec = importlib.util.spec_from_file_location("phase_c_closure_074", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    apply_dlq_original_job_id_unique = mod.apply_dlq_original_job_id_unique

    engine = create_engine("sqlite://")
    original = str(uuid.uuid4())
    keep_id = str(uuid.uuid4())
    drop_id = str(uuid.uuid4())
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    later = datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE dead_letter_jobs (
                    id VARCHAR(36) PRIMARY KEY,
                    original_job_id VARCHAR(36) NOT NULL,
                    tenant_id INTEGER NOT NULL,
                    job_type VARCHAR(50) NOT NULL,
                    resume_hash VARCHAR(64) NOT NULL,
                    jd_hash VARCHAR(64) NOT NULL,
                    input_hash VARCHAR(64) NOT NULL,
                    failure_reason TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    original_created_at VARCHAR(40) NOT NULL,
                    failed_at VARCHAR(40) NOT NULL,
                    status VARCHAR(20) NOT NULL
                )
                """
            )
        )
        for row_id, failed_at in ((keep_id, earlier), (drop_id, later)):
            conn.execute(
                text(
                    """
                    INSERT INTO dead_letter_jobs (
                        id, original_job_id, tenant_id, job_type, resume_hash, jd_hash,
                        input_hash, failure_reason, retry_count, original_created_at,
                        failed_at, status
                    ) VALUES (
                        :id, :oid, 1, 'resume_screening', :h, :h, :h, 'timeout', 3,
                        :failed_at, :failed_at, 'pending'
                    )
                    """
                ),
                {"id": row_id, "oid": original, "h": "a" * 64, "failed_at": failed_at},
            )

    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        apply_dlq_original_job_id_unique(conn, Operations(ctx))

    with engine.connect() as conn:
        counts = conn.execute(
            text(
                "SELECT original_job_id, COUNT(*) AS n FROM dead_letter_jobs "
                "GROUP BY original_job_id"
            )
        ).fetchall()
        assert counts == [(original, 1)]
        remaining = conn.execute(text("SELECT id FROM dead_letter_jobs")).scalar()
        assert remaining == keep_id
        insp = inspect(conn)
        uniques = {tuple(u.get("column_names") or []) for u in insp.get_unique_constraints("dead_letter_jobs")}
        unique_indexes = {
            tuple(i.get("column_names") or [])
            for i in insp.get_indexes("dead_letter_jobs")
            if i.get("unique")
        }
        assert ("original_job_id",) in uniques or ("original_job_id",) in unique_indexes
