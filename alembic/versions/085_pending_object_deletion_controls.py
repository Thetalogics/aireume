"""Add GDPR object deletion outbox controls.

Revision ID: 085_object_delete_controls
Revises: 084_auth_token_hashes
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "085_object_delete_controls"
down_revision = "084_auth_token_hashes"
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

    if not _has_column(insp, "pending_object_deletions", "status"):
        op.add_column(
            "pending_object_deletions",
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        )
        op.create_index(
            "ix_pending_object_deletions_status",
            "pending_object_deletions",
            ["status"],
        )
    if not _has_column(insp, "pending_object_deletions", "next_retry_at"):
        op.add_column("pending_object_deletions", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
        op.create_index(
            "ix_pending_object_deletions_next_retry_at",
            "pending_object_deletions",
            ["next_retry_at"],
        )
    if not _has_column(insp, "pending_object_deletions", "completed_at"):
        op.add_column("pending_object_deletions", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    if not _has_column(insp, "pending_object_deletions", "dead_lettered_at"):
        op.add_column("pending_object_deletions", sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "pending_object_deletions" not in tables:
        return
    cols = {c["name"] for c in insp.get_columns("pending_object_deletions")}
    if "dead_lettered_at" in cols:
        op.drop_column("pending_object_deletions", "dead_lettered_at")
    if "completed_at" in cols:
        op.drop_column("pending_object_deletions", "completed_at")
    if "next_retry_at" in cols:
        op.drop_index("ix_pending_object_deletions_next_retry_at", table_name="pending_object_deletions")
        op.drop_column("pending_object_deletions", "next_retry_at")
    if "status" in cols:
        op.drop_index("ix_pending_object_deletions_status", table_name="pending_object_deletions")
        op.drop_column("pending_object_deletions", "status")
