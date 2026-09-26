"""Track GDPR erasure workflow state on object deletion rows.

Revision ID: 086_gdpr_erasure_workflow
Revises: 085_object_delete_controls
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "086_gdpr_erasure_workflow"
down_revision = "085_object_delete_controls"
branch_labels = None
depends_on = None


def _has_column(insp, table: str, column: str) -> bool:
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "pending_object_deletions" not in tables:
        return

    if not _has_column(insp, "pending_object_deletions", "workflow_type"):
        op.add_column(
            "pending_object_deletions",
            sa.Column("workflow_type", sa.String(32), nullable=False, server_default="object_delete"),
        )
        op.create_index(
            "ix_pending_object_deletions_workflow_type",
            "pending_object_deletions",
            ["workflow_type"],
        )
    if not _has_column(insp, "pending_object_deletions", "workflow_status"):
        op.add_column(
            "pending_object_deletions",
            sa.Column("workflow_status", sa.String(32), nullable=False, server_default="object_pending"),
        )
        op.create_index(
            "ix_pending_object_deletions_workflow_status",
            "pending_object_deletions",
            ["workflow_status"],
        )
    if not _has_column(insp, "pending_object_deletions", "erasure_reason"):
        op.add_column("pending_object_deletions", sa.Column("erasure_reason", sa.Text(), nullable=True))
    if not _has_column(insp, "pending_object_deletions", "finalized_at"):
        op.add_column("pending_object_deletions", sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True))
    if not _has_column(insp, "pending_object_deletions", "lease_owner"):
        op.add_column("pending_object_deletions", sa.Column("lease_owner", sa.String(64), nullable=True))
        op.create_index(
            "ix_pending_object_deletions_lease_owner",
            "pending_object_deletions",
            ["lease_owner"],
        )
    if not _has_column(insp, "pending_object_deletions", "lease_expires_at"):
        op.add_column("pending_object_deletions", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        op.create_index(
            "ix_pending_object_deletions_lease_expires_at",
            "pending_object_deletions",
            ["lease_expires_at"],
        )

    insp = inspect(bind)
    indexes = {idx["name"] for idx in insp.get_indexes("pending_object_deletions")}
    if "uq_pending_object_deletions_tenant_key" not in indexes:
        # Keep the most actionable legacy row: pending first, then the newest row.
        bind.execute(sa.text(
            "DELETE FROM pending_object_deletions "
            "WHERE id NOT IN ("
            "SELECT id FROM ("
            "SELECT id, ROW_NUMBER() OVER ("
            "PARTITION BY tenant_id, storage_key "
            "ORDER BY CASE WHEN status = 'pending' THEN 0 ELSE 1 END, id DESC"
            ") AS row_rank FROM pending_object_deletions"
            ") ranked WHERE row_rank = 1"
            ")"
        ))
        op.create_index(
            "uq_pending_object_deletions_tenant_key",
            "pending_object_deletions",
            ["tenant_id", "storage_key"],
            unique=True,
        )


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "pending_object_deletions" not in tables:
        return
    cols = {c["name"] for c in insp.get_columns("pending_object_deletions")}
    indexes = {idx["name"] for idx in insp.get_indexes("pending_object_deletions")}
    if "uq_pending_object_deletions_tenant_key" in indexes:
        op.drop_index("uq_pending_object_deletions_tenant_key", table_name="pending_object_deletions")
    if "lease_expires_at" in cols:
        op.drop_index("ix_pending_object_deletions_lease_expires_at", table_name="pending_object_deletions")
        op.drop_column("pending_object_deletions", "lease_expires_at")
    if "lease_owner" in cols:
        op.drop_index("ix_pending_object_deletions_lease_owner", table_name="pending_object_deletions")
        op.drop_column("pending_object_deletions", "lease_owner")
    if "finalized_at" in cols:
        op.drop_column("pending_object_deletions", "finalized_at")
    if "erasure_reason" in cols:
        op.drop_column("pending_object_deletions", "erasure_reason")
    if "workflow_status" in cols:
        op.drop_index("ix_pending_object_deletions_workflow_status", table_name="pending_object_deletions")
        op.drop_column("pending_object_deletions", "workflow_status")
    if "workflow_type" in cols:
        op.drop_index("ix_pending_object_deletions_workflow_type", table_name="pending_object_deletions")
        op.drop_column("pending_object_deletions", "workflow_type")
