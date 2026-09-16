"""DLQ artifact_id + idempotency fingerprint (AUD-021/022)

Revision ID: 073_phase_c_queue
Revises: 072_tenant_desired_plan
"""
from alembic import op
import sqlalchemy as sa

revision = "073_phase_c_queue"
down_revision = "072_tenant_desired_plan"
branch_labels = None
depends_on = None


def _pk_name(table: str, pk: dict) -> str:
    name = pk.get("name")
    if name:
        return name
    return f"{table}_pkey"


def _idempotency_duplicate_keys(bind) -> bool:
    ik = sa.table("idempotency_keys", sa.column("key"))
    stmt = sa.select(ik.c.key).group_by(ik.c.key).having(sa.func.count() > 1).limit(1)
    return bind.execute(stmt).fetchone() is not None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "dead_letter_jobs" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("dead_letter_jobs")}
        if "artifact_id" not in cols:
            op.add_column(
                "dead_letter_jobs",
                sa.Column("artifact_id", sa.Uuid(as_uuid=True), nullable=True),
            )
            insp = sa.inspect(bind)
        fks = {
            tuple(fk.get("constrained_columns") or [])
            for fk in insp.get_foreign_keys("dead_letter_jobs")
        }
        if ("artifact_id",) not in fks and "analysis_artifacts" in insp.get_table_names():
            op.create_foreign_key(
                "fk_dead_letter_jobs_artifact_id",
                "dead_letter_jobs",
                "analysis_artifacts",
                ["artifact_id"],
                ["id"],
                ondelete="SET NULL",
            )
    # Task 7: idempotency_keys request fingerprint (AUD-022)
    if "idempotency_keys" in insp.get_table_names():
        op.execute(sa.text("DELETE FROM idempotency_keys"))
        cols = {c["name"] for c in insp.get_columns("idempotency_keys")}
        pk = insp.get_pk_constraint("idempotency_keys") or {}
        pk_cols = list(pk.get("constrained_columns") or [])
        uniques = {tuple(u.get("column_names") or []) for u in insp.get_unique_constraints("idempotency_keys")}
        with op.batch_alter_table("idempotency_keys") as batch_op:
            if "request_fingerprint" not in cols:
                batch_op.add_column(
                    sa.Column("request_fingerprint", sa.String(64), nullable=False)
                )
            if pk_cols == ["key"]:
                batch_op.drop_constraint(_pk_name("idempotency_keys", pk), type_="primary")
                batch_op.create_primary_key(
                    "pk_idempotency_keys", ["key", "tenant_id", "endpoint"]
                )
            if ("key", "tenant_id", "endpoint") not in uniques:
                batch_op.create_unique_constraint(
                    "uq_idempotency_key_tenant_endpoint",
                    ["key", "tenant_id", "endpoint"],
                )
    # Task 8: enc:v1 ciphertext exceeds VARCHAR(255)
    insp = sa.inspect(bind)
    if "ats_connections" in insp.get_table_names():
        for col in insp.get_columns("ats_connections"):
            if col["name"] != "webhook_secret":
                continue
            col_type = col["type"]
            if isinstance(col_type, sa.Text):
                break
            if isinstance(col_type, sa.String) and getattr(col_type, "length", None) == 255:
                with op.batch_alter_table("ats_connections") as batch_op:
                    batch_op.alter_column(
                        "webhook_secret",
                        existing_type=sa.String(255),
                        type_=sa.Text(),
                        existing_nullable=True,
                    )
            break


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "idempotency_keys" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("idempotency_keys")}
        uniques = {u.get("name") for u in insp.get_unique_constraints("idempotency_keys")}
        pk = insp.get_pk_constraint("idempotency_keys") or {}
        pk_cols = list(pk.get("constrained_columns") or [])
        if pk_cols == ["key", "tenant_id", "endpoint"]:
            if _idempotency_duplicate_keys(bind):
                raise RuntimeError(
                    "Cannot restore primary key on idempotency_keys.key: "
                    "duplicate key values exist"
                )
        with op.batch_alter_table("idempotency_keys") as batch_op:
            if "uq_idempotency_key_tenant_endpoint" in uniques:
                batch_op.drop_constraint("uq_idempotency_key_tenant_endpoint", type_="unique")
            if pk_cols == ["key", "tenant_id", "endpoint"]:
                batch_op.drop_constraint(_pk_name("idempotency_keys", pk), type_="primary")
                batch_op.create_primary_key("idempotency_keys_pkey", ["key"])
            if "request_fingerprint" in cols:
                batch_op.drop_column("request_fingerprint")
    insp = sa.inspect(bind)
    if "dead_letter_jobs" not in insp.get_table_names():
        return
    for fk in insp.get_foreign_keys("dead_letter_jobs"):
        if fk.get("constrained_columns") == ["artifact_id"] and fk.get("name"):
            op.drop_constraint(fk["name"], "dead_letter_jobs", type_="foreignkey")
    cols = {c["name"] for c in insp.get_columns("dead_letter_jobs")}
    if "artifact_id" in cols:
        op.drop_column("dead_letter_jobs", "artifact_id")
