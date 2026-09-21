"""Reliability closure: idempotency state machine, training runs, worker heartbeat."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "082_reliability_closure"
down_revision = "081_phase2_screening_decisions"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("idempotency_keys")}
    if "state" not in cols:
        op.add_column(
            "idempotency_keys",
            sa.Column("state", sa.String(20), nullable=False, server_default="completed"),
        )
    if "owner_token" not in cols:
        op.add_column("idempotency_keys", sa.Column("owner_token", sa.String(64), nullable=True))
    if "lease_expires_at" not in cols:
        op.add_column(
            "idempotency_keys",
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    tables = set(insp.get_table_names())
    if "training_runs" not in tables:
        op.create_table(
            "training_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("state", sa.String(32), nullable=False, server_default="idle"),
            sa.Column("model_name", sa.String(200), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_training_runs_tenant", "training_runs", ["tenant_id"], unique=True)
    if "worker_heartbeats" not in tables:
        op.create_table(
            "worker_heartbeats",
            sa.Column("role", sa.String(64), primary_key=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("detail", sa.String(200), nullable=True),
        )


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    if "worker_heartbeats" in insp.get_table_names():
        op.drop_table("worker_heartbeats")
    if "training_runs" in insp.get_table_names():
        op.drop_index("ix_training_runs_tenant", table_name="training_runs")
        op.drop_table("training_runs")
    cols = {c["name"] for c in inspect(bind).get_columns("idempotency_keys")}
    if "lease_expires_at" in cols:
        op.drop_column("idempotency_keys", "lease_expires_at")
    if "owner_token" in cols:
        op.drop_column("idempotency_keys", "owner_token")
    if "state" in cols:
        op.drop_column("idempotency_keys", "state")
