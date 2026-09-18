"""Phase 1 reliability: generation tokens, quota reservation, job error category.

Additive only. Does not modify 077/078.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "079_phase1_reliability"
down_revision = "078_phase0_1_audit_corrections"
branch_labels = None
depends_on = None


def _has_column(insp, table, col):
    if table not in set(insp.get_table_names()):
        return False
    return col in {c["name"] for c in insp.get_columns(table)}


def _has_table(insp, name):
    return name in set(insp.get_table_names())


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)

    if _has_table(insp, "screening_results") and not _has_column(insp, "screening_results", "analysis_generation"):
        op.add_column(
            "screening_results",
            sa.Column("analysis_generation", sa.Integer(), nullable=False, server_default="1"),
        )
    if _has_table(insp, "transcript_analyses") and not _has_column(insp, "transcript_analyses", "analysis_generation"):
        op.add_column(
            "transcript_analyses",
            sa.Column("analysis_generation", sa.Integer(), nullable=False, server_default="1"),
        )
    if _has_table(insp, "voice_screening_sessions") and not _has_column(insp, "voice_screening_sessions", "result_generation"):
        op.add_column(
            "voice_screening_sessions",
            sa.Column("result_generation", sa.Integer(), nullable=False, server_default="1"),
        )
    if _has_table(insp, "analysis_jobs"):
        if not _has_column(insp, "analysis_jobs", "last_error_category"):
            op.add_column("analysis_jobs", sa.Column("last_error_category", sa.String(length=40), nullable=True))
        if not _has_column(insp, "analysis_jobs", "last_error_at"):
            op.add_column("analysis_jobs", sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True))

    if not _has_table(insp, "quota_reservations"):
        op.create_table(
            "quota_reservations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("operation_id", sa.String(length=64), nullable=False),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("job_id", sa.String(length=36), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_quota_reservations_operation_id", "quota_reservations", ["operation_id"], unique=True)
        op.create_index("ix_quota_reservations_tenant_id", "quota_reservations", ["tenant_id"])
        op.create_index("ix_quota_reservations_status", "quota_reservations", ["status"])
        op.create_index("ix_quota_reservations_expires_at", "quota_reservations", ["expires_at"])


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    if _has_table(insp, "quota_reservations"):
        op.drop_table("quota_reservations")
    if _has_column(insp, "analysis_jobs", "last_error_at"):
        op.drop_column("analysis_jobs", "last_error_at")
    if _has_column(insp, "analysis_jobs", "last_error_category"):
        op.drop_column("analysis_jobs", "last_error_category")
    if _has_column(insp, "voice_screening_sessions", "result_generation"):
        op.drop_column("voice_screening_sessions", "result_generation")
    if _has_column(insp, "transcript_analyses", "analysis_generation"):
        op.drop_column("transcript_analyses", "analysis_generation")
    if _has_column(insp, "screening_results", "analysis_generation"):
        op.drop_column("screening_results", "analysis_generation")
