"""Add tenants.desired_plan_id for unpaid plan intent.

Revision ID: 072_tenant_desired_plan
Revises: 071_user_refresh_epoch
"""
from alembic import op
import sqlalchemy as sa

revision = "072_tenant_desired_plan"
down_revision = "071_user_refresh_epoch"
branch_labels = None
depends_on = None


def _desired_plan_fks(insp):
    return [
        fk
        for fk in insp.get_foreign_keys("tenants")
        if fk.get("constrained_columns") == ["desired_plan_id"] and fk.get("name")
    ]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "tenants" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("tenants")}
    if "desired_plan_id" not in cols:
        op.add_column(
            "tenants",
            sa.Column("desired_plan_id", sa.Integer(), nullable=True),
        )
        insp = sa.inspect(bind)
    if not _desired_plan_fks(insp):
        op.create_foreign_key(
            "fk_tenants_desired_plan_id",
            "tenants",
            "subscription_plans",
            ["desired_plan_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "tenants" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("tenants")}
    if "desired_plan_id" not in cols:
        return
    for fk in _desired_plan_fks(insp):
        op.drop_constraint(fk["name"], "tenants", type_="foreignkey")
    op.drop_column("tenants", "desired_plan_id")
