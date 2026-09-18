"""Phase 1 reliability closure: callback identity and quota operation identity."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "080_phase1_reliability_closure"
down_revision = "079_phase1_reliability"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)

    voice_columns = {c["name"] for c in insp.get_columns("voice_screening_sessions")}
    if "completion_event_id" not in voice_columns:
        op.add_column(
            "voice_screening_sessions",
            sa.Column("completion_event_id", sa.String(length=100), nullable=True),
        )

    indexes = {i["name"]: i for i in insp.get_indexes("quota_reservations")}
    operation_index = indexes.get("ix_quota_reservations_operation_id")
    if operation_index:
        op.drop_index("ix_quota_reservations_operation_id", table_name="quota_reservations")

    indexes = {i["name"]: i for i in inspect(bind).get_indexes("quota_reservations")}
    if "uq_quota_reservation_tenant_operation" not in indexes:
        op.create_index(
            "uq_quota_reservation_tenant_operation",
            "quota_reservations",
            ["tenant_id", "operation_id"],
            unique=True,
        )


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    indexes = {i["name"] for i in insp.get_indexes("quota_reservations")}
    if "uq_quota_reservation_tenant_operation" in indexes:
        op.drop_index(
            "uq_quota_reservation_tenant_operation",
            table_name="quota_reservations",
        )
    indexes = {i["name"] for i in inspect(bind).get_indexes("quota_reservations")}
    if "ix_quota_reservations_operation_id" not in indexes:
        op.create_index(
            "ix_quota_reservations_operation_id",
            "quota_reservations",
            ["operation_id"],
            unique=True,
        )
    voice_columns = {c["name"] for c in insp.get_columns("voice_screening_sessions")}
    if "completion_event_id" in voice_columns:
        op.drop_column("voice_screening_sessions", "completion_event_id")
