"""Add screening result generation mode.

Revision ID: 083_generation_mode
Revises: 082_reliability_closure
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "083_generation_mode"
down_revision = "082_reliability_closure"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    if "screening_results" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("screening_results")}
    if "generation_mode" not in cols:
        op.add_column(
            "screening_results",
            sa.Column("generation_mode", sa.String(32), nullable=False, server_default="pending"),
        )
    op.execute(
        sa.text(
            """
        UPDATE screening_results
        SET generation_mode = CASE
            WHEN narrative_status = 'ready'
                 AND narrative_json IS NOT NULL
                 AND (
                    narrative_json LIKE :ai_true_spaced
                    OR narrative_json LIKE :ai_true_compact
                 )
                THEN 'ai'
            WHEN narrative_status IN ('ready', 'fallback')
                 AND narrative_json IS NOT NULL
                THEN 'deterministic_fallback'
            WHEN narrative_status = 'failed'
                THEN 'failed'
            ELSE 'pending'
        END
        WHERE generation_mode IS NULL OR generation_mode = 'pending'
        """
        ).bindparams(
            ai_true_spaced='%"ai_enhanced": true%',
            ai_true_compact='%"ai_enhanced":true%',
        )
    )


def downgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    if "screening_results" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("screening_results")}
    if "generation_mode" in cols:
        op.drop_column("screening_results", "generation_mode")
