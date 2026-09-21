"""Phase 2 immutable screening decisions and current pointer."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "081_phase2_screening_decisions"
down_revision = "080_phase1_reliability_closure"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())

    if "screening_decisions" not in tables:
        op.create_table(
            "screening_decisions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("candidate_id", sa.Integer(), sa.ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True),
            sa.Column("screening_result_id", sa.Integer(), sa.ForeignKey("screening_results.id", ondelete="CASCADE"), nullable=False),
            sa.Column("role_template_id", sa.Integer(), sa.ForeignKey("role_templates.id", ondelete="SET NULL"), nullable=True),
            sa.Column("requisition_id", sa.Integer(), sa.ForeignKey("requisitions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("decision_version", sa.Integer(), nullable=False),
            sa.Column("analysis_generation", sa.Integer(), nullable=True),
            sa.Column("decision_type", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("algorithm_version", sa.String(length=32), nullable=True),
            sa.Column("deterministic_score", sa.Integer(), nullable=True),
            sa.Column("component_fit_score", sa.Integer(), nullable=True),
            sa.Column("final_fit_score", sa.Integer(), nullable=True),
            sa.Column("eligibility_status", sa.Boolean(), nullable=True),
            sa.Column("eligibility_reason", sa.String(length=200), nullable=True),
            sa.Column("ai_recommendation", sa.String(length=50), nullable=True),
            sa.Column("human_override_recommendation", sa.String(length=50), nullable=True),
            sa.Column("effective_recommendation", sa.String(length=50), nullable=True),
            sa.Column("risk_score", sa.Integer(), nullable=True),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("source_operation_id", sa.String(length=128), nullable=False),
            sa.Column("supersedes_decision_id", sa.Integer(), sa.ForeignKey("screening_decisions.id", ondelete="SET NULL"), nullable=True),
            sa.Column("provenance_complete", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("schema_version", sa.String(length=32), nullable=False, server_default="decision_payload_v1"),
            sa.Column("policy_version", sa.String(length=64), nullable=True),
            sa.Column("original_candidate_id", sa.Integer(), nullable=True),
            sa.Column("component_scores", sa.JSON(), nullable=True),
            sa.Column("risk_signals", sa.JSON(), nullable=True),
            sa.Column("scoring_weights", sa.JSON(), nullable=True),
            sa.Column("effective_weights", sa.JSON(), nullable=True),
            sa.Column("input_snapshot", sa.JSON(), nullable=True),
            sa.Column("model_context", sa.JSON(), nullable=True),
            sa.Column("prompt_context", sa.JSON(), nullable=True),
            sa.Column("policy_context", sa.JSON(), nullable=True),
            sa.Column("eligibility_gate_trace", sa.JSON(), nullable=True),
            sa.Column("formula_trace", sa.JSON(), nullable=True),
            sa.Column("explanation_payload", sa.JSON(), nullable=True),
            sa.Column("override_reason", sa.JSON(), nullable=True),
        )
        op.create_index("ix_screening_decisions_tenant_id", "screening_decisions", ["tenant_id"])
        op.create_index("ix_screening_decisions_candidate_id", "screening_decisions", ["candidate_id"])
        op.create_index("ix_screening_decisions_screening_result_id", "screening_decisions", ["screening_result_id"])
        op.create_index("ix_screening_decisions_decision_type", "screening_decisions", ["decision_type"])
        op.create_index("ix_screening_decisions_created_at", "screening_decisions", ["created_at"])
        op.create_index("ix_screening_decisions_result_created", "screening_decisions", ["screening_result_id", "created_at"])
        op.create_index("ix_screening_decisions_tenant_created", "screening_decisions", ["tenant_id", "created_at"])
        op.create_index("ix_screening_decisions_source_operation", "screening_decisions", ["tenant_id", "source_operation_id"])
        op.create_unique_constraint(
            "uq_screening_decision_result_version",
            "screening_decisions",
            ["screening_result_id", "decision_version"],
        )
        op.create_unique_constraint(
            "uq_screening_decision_tenant_operation_type",
            "screening_decisions",
            ["tenant_id", "source_operation_id", "decision_type"],
        )

    if "decision_narratives" not in set(inspect(bind).get_table_names()):
        op.create_table(
            "decision_narratives",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("screening_decision_id", sa.Integer(), sa.ForeignKey("screening_decisions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("narrative_type", sa.String(length=64), nullable=False, server_default="candidate_narrative"),
            sa.Column("source_operation_id", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("structured_output", sa.JSON(), nullable=True),
            sa.Column("provider_context", sa.JSON(), nullable=True),
            sa.Column("prompt_context", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint(
                "screening_decision_id",
                "narrative_type",
                "source_operation_id",
                name="uq_decision_narrative_operation",
            ),
        )
        op.create_index("ix_decision_narratives_tenant_id", "decision_narratives", ["tenant_id"])
        op.create_index("ix_decision_narratives_screening_decision_id", "decision_narratives", ["screening_decision_id"])

    def _fk_names(table_name: str) -> set[str]:
        return {fk.get("name") for fk in inspect(bind).get_foreign_keys(table_name) if fk.get("name")}

    result_cols = {c["name"] for c in inspect(bind).get_columns("screening_results")}
    if "current_decision_id" not in result_cols:
        op.add_column("screening_results", sa.Column("current_decision_id", sa.Integer(), nullable=True))
        op.create_index("ix_screening_results_current_decision_id", "screening_results", ["current_decision_id"])
    if "fk_screening_results_current_decision" not in _fk_names("screening_results"):
        op.create_foreign_key(
            "fk_screening_results_current_decision",
            "screening_results",
            "screening_decisions",
            ["current_decision_id"],
            ["id"],
            ondelete="SET NULL",
        )

    log_cols = {c["name"] for c in inspect(bind).get_columns("ai_decision_logs")}
    if "screening_decision_id" not in log_cols:
        op.add_column("ai_decision_logs", sa.Column("screening_decision_id", sa.Integer(), nullable=True))
        op.create_index("ix_ai_decision_logs_screening_decision_id", "ai_decision_logs", ["screening_decision_id"])
    if "fk_ai_decision_logs_screening_decision" not in _fk_names("ai_decision_logs"):
        op.create_foreign_key(
            "fk_ai_decision_logs_screening_decision",
            "ai_decision_logs",
            "screening_decisions",
            ["screening_decision_id"],
            ["id"],
            ondelete="SET NULL",
        )

    training_cols = {c["name"] for c in inspect(bind).get_columns("training_examples")}
    if "screening_decision_id" not in training_cols:
        op.add_column("training_examples", sa.Column("screening_decision_id", sa.Integer(), nullable=True))
        op.create_index("ix_training_examples_screening_decision_id", "training_examples", ["screening_decision_id"])
    if "fk_training_examples_screening_decision" not in _fk_names("training_examples"):
        op.create_foreign_key(
            "fk_training_examples_screening_decision",
            "training_examples",
            "screening_decisions",
            ["screening_decision_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade():
    """Destructive: drops Phase 2 governance tables/columns. Historical decisions are not reconstructed."""
    bind = op.get_bind()
    insp = inspect(bind)
    if "screening_decision_id" in {c["name"] for c in insp.get_columns("training_examples")}:
        op.drop_constraint("fk_training_examples_screening_decision", "training_examples", type_="foreignkey")
        op.drop_index("ix_training_examples_screening_decision_id", table_name="training_examples")
        op.drop_column("training_examples", "screening_decision_id")
    if "screening_decision_id" in {c["name"] for c in inspect(bind).get_columns("ai_decision_logs")}:
        op.drop_constraint("fk_ai_decision_logs_screening_decision", "ai_decision_logs", type_="foreignkey")
        op.drop_index("ix_ai_decision_logs_screening_decision_id", table_name="ai_decision_logs")
        op.drop_column("ai_decision_logs", "screening_decision_id")
    if "current_decision_id" in {c["name"] for c in inspect(bind).get_columns("screening_results")}:
        op.drop_constraint("fk_screening_results_current_decision", "screening_results", type_="foreignkey")
        op.drop_index("ix_screening_results_current_decision_id", table_name="screening_results")
        op.drop_column("screening_results", "current_decision_id")
    if "decision_narratives" in inspect(bind).get_table_names():
        op.drop_table("decision_narratives")
    if "screening_decisions" in inspect(bind).get_table_names():
        op.drop_table("screening_decisions")
