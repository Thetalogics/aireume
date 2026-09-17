"""Candidate merge provenance (AUD-040).

Revision ID: 076_candidate_merge
Revises: 075_audit_closure
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "076_candidate_merge"
down_revision = "075_audit_closure"
branch_labels = None
depends_on = None


def _merged_into_fks(insp):
    return [
        fk
        for fk in insp.get_foreign_keys("candidates")
        if "merged_into_id" in (fk.get("constrained_columns") or []) and fk.get("name")
    ]


def _merged_into_indexes(insp):
    out = []
    for idx in insp.get_indexes("candidates"):
        name = idx.get("name")
        cols = list(idx.get("column_names") or [])
        if name and (name == "ix_candidates_merged_into_id" or cols == ["merged_into_id"]):
            out.append(idx)
    return out


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    if hasattr(insp, "clear_cache"):
        insp.clear_cache()
    tables = set(insp.get_table_names())
    if "candidates" in tables:
        cols = {c["name"] for c in insp.get_columns("candidates")}
        if "merged_into_id" not in cols:
            op.add_column("candidates", sa.Column("merged_into_id", sa.Integer(), nullable=True))
            if hasattr(insp, "clear_cache"):
                insp.clear_cache()
        if not any(i["name"] == "ix_candidates_merged_into_id" for i in _merged_into_indexes(insp)):
            op.create_index("ix_candidates_merged_into_id", "candidates", ["merged_into_id"])
        if not _merged_into_fks(insp):
            op.create_foreign_key(
                "fk_candidates_merged_into_id",
                "candidates",
                "candidates",
                ["merged_into_id"],
                ["id"],
            )
    if "candidate_merge_events" not in tables:
        op.create_table(
            "candidate_merge_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
            sa.Column("source_candidate_id", sa.Integer(), nullable=False),
            sa.Column("target_candidate_id", sa.Integer(), nullable=False),
            sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("conflicts_json", sa.Text(), nullable=True),
            sa.Column("already_merged", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_candidate_merge_events_tenant_id", "candidate_merge_events", ["tenant_id"])
        op.create_index("ix_candidate_merge_events_source", "candidate_merge_events", ["source_candidate_id"])
        op.create_index("ix_candidate_merge_events_target", "candidate_merge_events", ["target_candidate_id"])


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    if hasattr(insp, "clear_cache"):
        insp.clear_cache()
    tables = set(insp.get_table_names())
    if "candidate_merge_events" in tables:
        op.drop_table("candidate_merge_events")
        if hasattr(insp, "clear_cache"):
            insp.clear_cache()
    if "candidates" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("candidates")}
        if "merged_into_id" not in cols:
            return
        # Inspect real constraint/index names. Never swallow DDL errors on
        # PostgreSQL: a failed statement aborts the transaction, and a bare
        # except Exception: pass then makes the next DROP fail with
        # InFailedSqlTransaction (CI `alembic downgrade -1`).
        for fk in _merged_into_fks(insp):
            op.drop_constraint(fk["name"], "candidates", type_="foreignkey")
        for idx in _merged_into_indexes(insp):
            op.drop_index(idx["name"], table_name="candidates")
        op.drop_column("candidates", "merged_into_id")
