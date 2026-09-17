"""Share-link token hashes, bcrypt passcode width, pending object deletions.

Revision ID: 075_audit_closure
Revises: 074_phase_c_closure
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "075_audit_closure"
down_revision = "074_phase_c_closure"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "handoff_share_links" in tables:
        cols = {c["name"] for c in insp.get_columns("handoff_share_links")}
        if "token_hash" not in cols:
            op.add_column("handoff_share_links", sa.Column("token_hash", sa.String(64), nullable=True))
            op.create_index("ix_handoff_share_links_token_hash", "handoff_share_links", ["token_hash"], unique=True)
        # Widen passcode_hash for bcrypt; SQLite ignores length.
        if bind.dialect.name != "sqlite":
            op.alter_column(
                "handoff_share_links",
                "passcode_hash",
                existing_type=sa.String(64),
                type_=sa.String(128),
                existing_nullable=True,
            )
            op.alter_column(
                "handoff_share_links",
                "token",
                existing_type=sa.String(64),
                nullable=True,
            )

    if "pending_object_deletions" not in tables:
        op.create_table(
            "pending_object_deletions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("storage_key", sa.String(500), nullable=False),
            sa.Column("candidate_id", sa.Integer(), nullable=True),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_pending_object_deletions_tenant_id", "pending_object_deletions", ["tenant_id"])


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "pending_object_deletions" in tables:
        op.drop_table("pending_object_deletions")
    if "handoff_share_links" in tables:
        cols = {c["name"] for c in insp.get_columns("handoff_share_links")}
        if "token_hash" in cols:
            op.drop_index("ix_handoff_share_links_token_hash", table_name="handoff_share_links")
            op.drop_column("handoff_share_links", "token_hash")
