"""enrich candidates profile, add jd_cache and skills tables

Revision ID: 001
Revises:
Create Date: 2026-04-04

Changes:
  - candidates: add 11 profile columns (resume_file_hash, raw_resume_text,
    parsed_skills, parsed_education, parsed_work_exp, gap_analysis_json,
    current_role, current_company, total_years_exp, profile_quality,
    profile_updated_at)
  - new table: jd_cache
  - new table: skills

Idempotent: safe when tables/columns already exist (e.g. SQLAlchemy create_all before Alembic).
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(insp, name: str) -> bool:
    return name in insp.get_table_names()


def _column_names(insp, table: str) -> set:
    return {c["name"] for c in insp.get_columns(table)}


def _index_names(insp, table: str) -> set:
    return {i["name"] for i in insp.get_indexes(table)}


def upgrade() -> None:
    # ── baseline: create the core tables ────────────────────────────────────
    # This is the first revision (down_revision=None). Historically the app
    # bootstrapped its schema with Base.metadata.create_all() and Alembic was
    # introduced only to *layer* changes on top — so every later revision that
    # touches a "core" table (candidates, tenants, users, screening_results, …)
    # assumes it already exists. In production the schema is now applied purely
    # via `alembic upgrade heads` (docker-entrypoint.sh, no create_all), so on a
    # bare database those core tables are never created and revision 002 fails
    # reflecting `candidates`.
    #
    # These tables are the ones defined in the models but created by *no*
    # migration (the legacy create_all set), minus dead_letter_jobs (see below).
    # Requisition tables (except requisition_candidates, which FKs screening_results)
    # must be created before screening_results because the model now references
    # requisitions.id. Migration 056 remains idempotent for these tables.
    # We create them here from the model
    # metadata. All later ALTERs against them are idempotent (guarded by column/
    # index existence checks), so materialising them at their final shape is
    # safe. create_all() is also a no-op for tables that already exist, so this
    # remains safe on databases that were originally seeded via create_all.
    #
    # `dead_letter_jobs` is intentionally excluded here because it has a foreign
    # key to `analysis_jobs`, which is created later by revision 008; it is
    # created by its own revision once that dependency exists.
    from app.backend.db.database import Base
    import app.backend.models.db_models  # noqa: F401 — registers all models

    _core_tables = [
        "subscription_plans",
        "tenants",
        "users",
        "candidates",
        "role_templates",
        "requisitions",
        "requisition_criteria_versions",
        "requisition_hiring_managers",
        "tenant_requisition_settings",
        "screening_results",
        "screening_projects",
        "screening_project_candidates",
        "interview_templates",
        "team_members",
        "comments",
        "training_examples",
        "transcript_analyses",
        "ats_connections",
        "ats_sync_logs",
    ]
    _tables = [
        Base.metadata.tables[name]
        for name in _core_tables
        if name in Base.metadata.tables
    ]
    Base.metadata.create_all(bind=op.get_bind(), tables=_tables)

    insp = _inspector()

    # create_all uses live ORM metadata, so later-owned columns (tenants.desired_plan_id
    # from 072) can appear here on a fresh database. Strip them so later revisions
    # remain the source of truth for upgrade/downgrade.
    if _table_exists(insp, "tenants") and "desired_plan_id" in _column_names(insp, "tenants"):
        for fk in insp.get_foreign_keys("tenants"):
            if fk.get("constrained_columns") == ["desired_plan_id"] and fk.get("name"):
                op.drop_constraint(fk["name"], "tenants", type_="foreignkey")
        op.drop_column("tenants", "desired_plan_id")
        insp = _inspector()

    # ── candidates: add profile columns (skip if already present) ───────────
    if _table_exists(insp, "candidates"):
        cand_cols = _column_names(insp, "candidates")
        add = []
        if "resume_file_hash" not in cand_cols:
            add.append(sa.Column("resume_file_hash", sa.String(64), nullable=True))
        if "raw_resume_text" not in cand_cols:
            add.append(sa.Column("raw_resume_text", sa.Text(), nullable=True))
        if "parsed_skills" not in cand_cols:
            add.append(sa.Column("parsed_skills", sa.Text(), nullable=True))
        if "parsed_education" not in cand_cols:
            add.append(sa.Column("parsed_education", sa.Text(), nullable=True))
        if "parsed_work_exp" not in cand_cols:
            add.append(sa.Column("parsed_work_exp", sa.Text(), nullable=True))
        if "gap_analysis_json" not in cand_cols:
            add.append(sa.Column("gap_analysis_json", sa.Text(), nullable=True))
        if "current_role" not in cand_cols:
            add.append(sa.Column("current_role", sa.String(255), nullable=True))
        if "current_company" not in cand_cols:
            add.append(sa.Column("current_company", sa.String(255), nullable=True))
        if "total_years_exp" not in cand_cols:
            add.append(sa.Column("total_years_exp", sa.Float(), nullable=True))
        if "profile_quality" not in cand_cols:
            add.append(sa.Column("profile_quality", sa.String(20), nullable=True))
        if "profile_updated_at" not in cand_cols:
            add.append(sa.Column("profile_updated_at", sa.DateTime(timezone=True), nullable=True))

        for col in add:
            op.add_column("candidates", col)

        insp = _inspector()
        if "ix_candidates_resume_file_hash" not in _index_names(insp, "candidates"):
            op.create_index("ix_candidates_resume_file_hash", "candidates", ["resume_file_hash"])

    # ── jd_cache table ─────────────────────────────────────────────────────────
    insp = _inspector()
    if not _table_exists(insp, "jd_cache"):
        op.create_table(
            "jd_cache",
            sa.Column("hash", sa.String(64), primary_key=True),
            sa.Column("result_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )

    # ── skills table ───────────────────────────────────────────────────────────
    insp = _inspector()
    if not _table_exists(insp, "skills"):
        op.create_table(
            "skills",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(200), nullable=False, unique=True),
            sa.Column("aliases", sa.Text(), nullable=True),
            sa.Column("domain", sa.String(50), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="active"),
            sa.Column("source", sa.String(20), nullable=False, server_default="seed"),
            sa.Column("frequency", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_skills_id", "skills", ["id"])
        op.create_index("ix_skills_name", "skills", ["name"], unique=True)
    else:
        insp = _inspector()
        idx = _index_names(insp, "skills")
        if "ix_skills_id" not in idx:
            op.create_index("ix_skills_id", "skills", ["id"])
        if "ix_skills_name" not in idx:
            op.create_index("ix_skills_name", "skills", ["name"], unique=True)


def downgrade() -> None:
    op.drop_table("skills")
    op.drop_table("jd_cache")
    with op.batch_alter_table("candidates") as batch_op:
        batch_op.drop_index("ix_candidates_resume_file_hash")
        batch_op.drop_column("profile_updated_at")
        batch_op.drop_column("profile_quality")
        batch_op.drop_column("total_years_exp")
        batch_op.drop_column("current_company")
        batch_op.drop_column("current_role")
        batch_op.drop_column("gap_analysis_json")
        batch_op.drop_column("parsed_work_exp")
        batch_op.drop_column("parsed_education")
        batch_op.drop_column("parsed_skills")
        batch_op.drop_column("raw_resume_text")
        batch_op.drop_column("resume_file_hash")
