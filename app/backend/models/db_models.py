from sqlalchemy import (
    Column, Integer, String, DateTime, Date, Text, Boolean, LargeBinary,
    ForeignKey, Float, func, BigInteger, UniqueConstraint, Index, JSON, Uuid, text
)
from datetime import datetime, timezone
import json
import uuid
from typing import Any
from sqlalchemy.orm import relationship
from app.backend.db.database import Base


# ─── Multi-tenancy ────────────────────────────────────────────────────────────

class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id           = Column(Integer, primary_key=True, index=True)
    name         = Column(String(50), unique=True, nullable=False)   # free / pro / enterprise
    display_name = Column(String(100), nullable=True)                # Human-readable name
    description  = Column(Text, nullable=True)                        # Plan description
    limits       = Column(Text, nullable=False, default="{}")      # JSON: analyses_per_month, batch_size, etc.
    price_monthly = Column(Integer, nullable=False, default=0)       # Monthly price in cents
    price_yearly = Column(Integer, nullable=False, default=0)        # Yearly price in cents
    currency     = Column(String(3), nullable=False, default="USD")  # ISO currency code
    features     = Column(Text, nullable=False, default="[]")      # JSON array of feature strings
    is_active    = Column(Boolean, nullable=False, default=True)     # Whether plan is available
    sort_order   = Column(Integer, nullable=False, default=0)        # Display order
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), onupdate=func.now())

    tenants = relationship("Tenant", back_populates="plan", foreign_keys="Tenant.plan_id")
    plan_features = relationship("PlanFeature", back_populates="plan", cascade="all, delete-orphan")


class Tenant(Base):
    __tablename__ = "tenants"

    id         = Column(Integer, primary_key=True, index=True)
    name          = Column(String(200), nullable=False)
    slug          = Column(String(100), unique=True, nullable=False)
    contact_email = Column(String(255), nullable=True)
    plan_id       = Column(Integer, ForeignKey("subscription_plans.id"), nullable=True)
    desired_plan_id = Column(Integer, ForeignKey("subscription_plans.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # ── Subscription & Usage Tracking ─────────────────────────────────────────
    subscription_status     = Column(String(20), nullable=False, default="active")  # active/trialing/cancelled/past_due/suspended
    current_period_start    = Column(DateTime(timezone=True), nullable=True)
    current_period_end      = Column(DateTime(timezone=True), nullable=True)
    analyses_count_this_month = Column(Integer, nullable=False, default=0)
    storage_used_bytes      = Column(BigInteger, nullable=False, default=0)
    usage_reset_at          = Column(DateTime(timezone=True), nullable=True)  # Last monthly reset

    # Stripe integration (for future payment integration)
    stripe_customer_id      = Column(String(255), nullable=True)
    stripe_subscription_id  = Column(String(255), nullable=True)
    subscription_updated_at = Column(DateTime(timezone=True), nullable=True)
    suspended_at    = Column(DateTime(timezone=True), nullable=True)
    suspended_reason = Column(Text, nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    metadata_json   = Column(Text, nullable=False, default="{}")
    
    # Tenant-level default scoring weights (JSON string)
    scoring_weights = Column(Text, nullable=True)  # JSON: custom weights for this tenant

    # Onboarding tracking
    onboarding_completed    = Column(Boolean, nullable=False, default=False)
    onboarding_completed_at = Column(DateTime(timezone=True), nullable=True)

    # Self-serve trial
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)

    # CRM health signals (updated by crm_service)
    health_score = Column(Integer, nullable=True)   # 0-100
    churn_risk   = Column(String(20), nullable=True)  # low | medium | high

    # White-label branding
    custom_domain       = Column(String(255), nullable=True, unique=True, index=True)
    brand_name          = Column(String(200), nullable=True)
    brand_logo_url      = Column(String(500), nullable=True)
    brand_primary_color = Column(String(20), nullable=True)
    brand_favicon_url   = Column(String(500), nullable=True)

    plan         = relationship("SubscriptionPlan", back_populates="tenants", foreign_keys=[plan_id])
    desired_plan = relationship("SubscriptionPlan", foreign_keys=[desired_plan_id])
    users        = relationship("User", back_populates="tenant")
    candidates   = relationship("Candidate", back_populates="tenant")
    templates    = relationship("RoleTemplate", back_populates="tenant")
    results      = relationship("ScreeningResult", back_populates="tenant")
    team_members = relationship("TeamMember", back_populates="tenant")
    usage_logs   = relationship("UsageLog", back_populates="tenant")
    email_config = relationship("TenantEmailConfig", backref="tenant", uselist=False)
    sso_config = relationship("SSOConfig", backref="tenant", uselist=False)


class User(Base):
    __tablename__ = "users"

    id              = Column(Integer, primary_key=True, index=True)
    tenant_id       = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    email           = Column(String(255), nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role            = Column(String(50), nullable=False, default="recruiter")  # admin / recruiter / viewer
    is_active       = Column(Boolean, default=True)
    is_platform_admin = Column(Boolean, nullable=False, default=False)
    platform_role   = Column(String(50), nullable=True)  # super_admin | billing_admin | support | security_admin | readonly
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    mfa_secret      = Column(String(64), nullable=True)
    mfa_enabled     = Column(Boolean, nullable=False, default=False, server_default="false")
    refresh_epoch   = Column(Integer, nullable=False, default=0, server_default="0")

    # Email verification
    email_verified             = Column(Boolean, nullable=False, server_default='false', default=False)
    email_verification_token   = Column(String(255), nullable=True)
    email_verification_token_hash = Column(String(64), nullable=True, index=True)
    email_verification_sent_at = Column(DateTime(timezone=True), nullable=True)

    # Per-user getting-started checklist (JSON object of step -> bool)
    getting_started_progress = Column(Text, nullable=False, default="{}")
    preferences_json         = Column(Text, nullable=False, default="{}")

    tenant       = relationship("Tenant", back_populates="users")
    team_member  = relationship("TeamMember", back_populates="user", uselist=False)
    comments     = relationship("Comment", back_populates="author")
    usage_logs   = relationship("UsageLog", back_populates="user")

    __table_args__ = (
        UniqueConstraint('tenant_id', 'email', name='uq_users_tenant_email'),
    )

    oauth_identities = relationship("UserOAuthIdentity", back_populates="user", cascade="all, delete-orphan")

    @property
    def is_platform_admin_compat(self) -> bool:
        """Backward compatibility: any non-null platform_role counts as platform admin."""
        return self.is_platform_admin or (self.platform_role is not None)


class OnboardingFunnelEvent(Base):
    """Product onboarding funnel events for analytics."""
    __tablename__ = "onboarding_funnel_events"

    id              = Column(Integer, primary_key=True, index=True)
    tenant_id       = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id         = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    event_name      = Column(String(100), nullable=False, index=True)
    properties_json = Column(Text, nullable=False, default="{}")
    created_at      = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    tenant = relationship("Tenant")
    user   = relationship("User")


class UserOAuthIdentity(Base):
    """Links a user to a Google/Microsoft OAuth account."""
    __tablename__ = "user_oauth_identities"

    id               = Column(Integer, primary_key=True, index=True)
    user_id          = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider         = Column(String(30), nullable=False)  # google | microsoft
    provider_user_id = Column(String(255), nullable=False)
    email_at_link    = Column(String(255), nullable=True)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="oauth_identities")

    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id", name="uq_oauth_provider_user"),
    )


class TenantAccountNote(Base):
    """Internal CRM notes for a tenant (platform admin only)."""
    __tablename__ = "tenant_account_notes"

    id         = Column(Integer, primary_key=True, index=True)
    tenant_id  = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id  = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note_type  = Column(String(30), nullable=False, default="general")  # general | support | sales | churn
    body       = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    tenant = relationship("Tenant", backref="account_notes")
    author = relationship("User")


class TenantNpsResponse(Base):
    """Net Promoter Score survey response per tenant user."""
    __tablename__ = "tenant_nps_responses"

    id         = Column(Integer, primary_key=True, index=True)
    tenant_id  = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    score      = Column(Integer, nullable=False)  # 0-10
    comment    = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    tenant = relationship("Tenant", backref="nps_responses")
    user   = relationship("User")


class UsageLog(Base):
    """Detailed usage tracking for subscription billing and analytics."""
    __tablename__ = "usage_logs"

    id         = Column(Integer, primary_key=True, index=True)
    tenant_id  = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action     = Column(String(50), nullable=False)  # resume_analysis, batch_analysis, etc.
    quantity   = Column(Integer, nullable=False, default=1)
    details    = Column(Text, nullable=True)  # JSON with context
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    tenant = relationship("Tenant", back_populates="usage_logs")
    user   = relationship("User", back_populates="usage_logs")


class QuotaReservation(Base):
    """Durable quota hold so abandoned work can be released."""
    __tablename__ = "quota_reservations"

    id = Column(Integer, primary_key=True, index=True)
    operation_id = Column(String(64), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default="pending", index=True)
    job_id = Column(String(36), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "operation_id", name="uq_quota_reservation_tenant_operation"),
    )


# ─── Candidate & results ──────────────────────────────────────────────────────

class Candidate(Base):
    __tablename__ = "candidates"

    id         = Column(Integer, primary_key=True, index=True)
    tenant_id  = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    merged_into_id = Column(Integer, ForeignKey("candidates.id"), nullable=True, index=True)
    name       = Column(String(255), nullable=True)
    email      = Column(String(255), nullable=True, index=True)
    phone      = Column(String(50), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # ── Deduplication constraints (enforced via partial unique indexes in migration 033) ──
    # - uq_candidate_tenant_email:     UNIQUE (tenant_id, email)          WHERE email IS NOT NULL
    # - uq_candidate_tenant_file_hash: UNIQUE (tenant_id, resume_file_hash) WHERE resume_file_hash IS NOT NULL
    # SQLAlchemy UniqueConstraint is not used here because partial indexes are not natively
    # supported in __table_args__; constraints are managed exclusively via Alembic migration.

    # ── Enriched profile (stored once, re-used for every JD re-analysis) ──────
    resume_file_hash   = Column(String(64),  nullable=True, index=True)  # MD5(file bytes)
    resume_filename    = Column(String(255), nullable=True)              # Original filename
    resume_file_data   = Column(LargeBinary, nullable=True)              # Original file bytes (BYTEA) — legacy, migrated to object storage
    resume_file_key    = Column(String(500), nullable=True)              # S3/MinIO object key (preferred over BYTEA)
    resume_converted_pdf_data = Column(LargeBinary, nullable=True)       # PDF conversion of .doc for browser viewing — legacy
    resume_pdf_key     = Column(String(500), nullable=True)              # S3/MinIO object key for converted PDF
    raw_resume_text    = Column(Text,        nullable=True)
    parsed_skills      = Column(Text,        nullable=True)   # JSON array
    parsed_education   = Column(Text,        nullable=True)   # JSON array
    parsed_work_exp    = Column(Text,        nullable=True)   # JSON array
    gap_analysis_json  = Column(Text,        nullable=True)   # JSON object
    current_role       = Column(String(255), nullable=True)
    current_company    = Column(String(255), nullable=True)
    total_years_exp    = Column(Float,       nullable=True)
    profile_quality    = Column(String(20),  nullable=True)   # high | medium | low
    profile_updated_at = Column(DateTime(timezone=True), nullable=True)

    # Full parse_resume() output as JSON (contact_info, raw_text, skills, …) — audit / re-analyze
    parser_snapshot_json = Column(Text, nullable=True)

    # AI-generated professional summary for the candidate profile
    ai_professional_summary = Column(Text, nullable=True)

    tenant               = relationship("Tenant", back_populates="candidates")
    results              = relationship("ScreeningResult", back_populates="candidate")
    transcript_analyses  = relationship("TranscriptAnalysis", back_populates="candidate")
    merged_into          = relationship("Candidate", remote_side="Candidate.id")


class CandidateMergeEvent(Base):
    """Provenance for an explicit, tenant-scoped candidate merge (AUD-040)."""
    __tablename__ = "candidate_merge_events"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    source_candidate_id = Column(Integer, nullable=False, index=True)
    target_candidate_id = Column(Integer, nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reason = Column(Text, nullable=True)
    conflicts_json = Column(Text, nullable=True)
    already_merged = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CandidateNote(Base):
    __tablename__ = "candidate_notes"

    id = Column(Integer, primary_key=True, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    candidate = relationship("Candidate", backref="notes")
    user = relationship("User")
    tenant = relationship("Tenant")


class ScreeningResult(Base):
    __tablename__ = "screening_results"

    id                 = Column(Integer, primary_key=True, index=True)
    tenant_id          = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    candidate_id       = Column(Integer, ForeignKey("candidates.id"), nullable=True, index=True)
    role_template_id   = Column(Integer, ForeignKey("role_templates.id"), nullable=True)
    requisition_id     = Column(Integer, ForeignKey("requisitions.id"), nullable=True, index=True)
    resume_text        = Column(Text, nullable=False)
    jd_text            = Column(Text, nullable=False)
    parsed_data        = Column(Text, nullable=False)   # JSON string
    analysis_result    = Column(Text, nullable=False)   # JSON string
    narrative_json     = Column(Text, nullable=True)    # LLM narrative (generated asynchronously)
    narrative_status   = Column(String(20), default="pending")  # pending | processing | ready | failed
    narrative_error    = Column(Text, nullable=True)            # error details when failed (null when successful)
    generation_mode    = Column(String(32), nullable=False, default="pending", server_default="pending")  # pending | ai | deterministic_fallback | failed
    interview_kit_status = Column(String(20), default="pending", server_default="pending")  # pending | processing | ready | fallback | skipped
    interview_kit_error = Column(Text, nullable=True)  # failure reason when interview_kit_status=fallback
    candidate_intelligence_json = Column(Text, nullable=True)
    candidate_intelligence_status = Column(String(32), nullable=True)  # pending | ready | failed
    voice_strategy_json = Column(Text, nullable=True)           # Pre-built AI voice interview plan (JSON)
    voice_strategy_status = Column(String(20), default="pending", server_default="pending")  # pending | processing | ready | fallback | skipped
    voice_strategy_config_hash = Column(String(64), nullable=True)  # Hash of duration/question config used
    status             = Column(String(50), default="pending")  # pending/shortlisted/rejected/in-review/hired
    is_active          = Column(Boolean, default=True)          # active version for candidate analysis
    version_number     = Column(Integer, default=1)             # version tracking for re-analysis
    analysis_generation = Column(Integer, nullable=False, default=1, server_default="1")
    role_category      = Column(String(50), nullable=True)      # technical, sales, hr, marketing, operations, leadership
    weight_reasoning   = Column(Text, nullable=True)            # JSON: reasoning for suggested weights
    suggested_weights_json = Column(Text, nullable=True)        # JSON: suggested scoring weights
    timestamp          = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    # ── Deterministic scoring fields ─────────────────────────────────────────
    deterministic_score = Column(Integer, nullable=True)        # Hard-capped deterministic score
    domain_match_score  = Column(Float, nullable=True)           # Domain match confidence
    core_skill_score    = Column(Float, nullable=True)           # Core skill match ratio
    eligibility_status  = Column(Boolean, nullable=True)         # Whether candidate passed eligibility gates
    eligibility_reason  = Column(String(100), nullable=True)     # Rejection reason if ineligible
    status_updated_at   = Column(DateTime(timezone=True), nullable=True)  # When status was last changed

    # ── Post-interview outcome (human or AI call) ────────────────────────────
    call_fit_score              = Column(Integer, nullable=True)
    call_source                 = Column(String(20), nullable=True)   # human | ai
    consolidated_recommendation = Column(String(50), nullable=True)
    consolidated_reasoning      = Column(Text, nullable=True)
    call_completed_at           = Column(DateTime(timezone=True), nullable=True)

    tenant        = relationship("Tenant", back_populates="results")
    candidate     = relationship("Candidate", back_populates="results")
    role_template = relationship("RoleTemplate", back_populates="results")
    requisition   = relationship("Requisition", back_populates="screening_results")
    comments      = relationship("Comment", back_populates="result")
    evaluations = relationship("InterviewEvaluation", back_populates="result", cascade="all, delete-orphan")
    overall_assessment = relationship("OverallAssessment", back_populates="result", cascade="all, delete-orphan", uselist=True)
    training_examples = relationship("TrainingExample", back_populates="result")
    current_decision_id = Column(Integer, nullable=True, index=True)
    current_decision = relationship(
        "ScreeningDecision",
        foreign_keys=lambda: [ScreeningResult.current_decision_id],
        primaryjoin="ScreeningResult.current_decision_id==ScreeningDecision.id",
        post_update=True,
    )
    decisions = relationship(
        "ScreeningDecision",
        back_populates="screening_result",
        foreign_keys="ScreeningDecision.screening_result_id",
        passive_deletes=True,
    )


class ScreeningDecision(Base):
    """Immutable historical hiring decision while retained.

    Ordinary application flows never update a row. GDPR/candidate deletion may
    CASCADE-delete ledger rows with the parent ScreeningResult. That is an
    explicit retention/privacy action, not ordinary reanalysis cleanup.
    """
    __tablename__ = "screening_decisions"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True, index=True)
    screening_result_id = Column(Integer, ForeignKey("screening_results.id", ondelete="CASCADE"), nullable=False, index=True)
    role_template_id = Column(Integer, ForeignKey("role_templates.id", ondelete="SET NULL"), nullable=True)
    requisition_id = Column(Integer, ForeignKey("requisitions.id", ondelete="SET NULL"), nullable=True)
    decision_version = Column(Integer, nullable=False)
    analysis_generation = Column(Integer, nullable=True)
    decision_type = Column(String(32), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    algorithm_version = Column(String(32), nullable=True)
    deterministic_score = Column(Integer, nullable=True)
    component_fit_score = Column(Integer, nullable=True)
    final_fit_score = Column(Integer, nullable=True)
    eligibility_status = Column(Boolean, nullable=True)
    eligibility_reason = Column(String(200), nullable=True)
    ai_recommendation = Column(String(50), nullable=True)
    human_override_recommendation = Column(String(50), nullable=True)
    effective_recommendation = Column(String(50), nullable=True)
    risk_score = Column(Integer, nullable=True)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source_operation_id = Column(String(128), nullable=False)
    supersedes_decision_id = Column(Integer, ForeignKey("screening_decisions.id", ondelete="SET NULL"), nullable=True)
    provenance_complete = Column(Boolean, nullable=False, default=True, server_default="true")
    schema_version = Column(String(32), nullable=False, default="decision_payload_v1")
    policy_version = Column(String(64), nullable=True)
    original_candidate_id = Column(Integer, nullable=True)
    component_scores = Column(JSON, nullable=True)
    risk_signals = Column(JSON, nullable=True)
    scoring_weights = Column(JSON, nullable=True)
    effective_weights = Column(JSON, nullable=True)
    input_snapshot = Column(JSON, nullable=True)
    model_context = Column(JSON, nullable=True)
    prompt_context = Column(JSON, nullable=True)
    policy_context = Column(JSON, nullable=True)
    eligibility_gate_trace = Column(JSON, nullable=True)
    formula_trace = Column(JSON, nullable=True)
    explanation_payload = Column(JSON, nullable=True)
    override_reason = Column(JSON, nullable=True)

    __table_args__ = (
        UniqueConstraint("screening_result_id", "decision_version", name="uq_screening_decision_result_version"),
        UniqueConstraint("tenant_id", "source_operation_id", "decision_type", name="uq_screening_decision_tenant_operation_type"),
        Index("ix_screening_decisions_result_created", "screening_result_id", "created_at"),
        Index("ix_screening_decisions_tenant_created", "tenant_id", "created_at"),
        Index("ix_screening_decisions_source_operation", "tenant_id", "source_operation_id"),
    )

    screening_result = relationship(
        "ScreeningResult",
        back_populates="decisions",
        foreign_keys=[screening_result_id],
        passive_deletes=True,
    )
    narratives = relationship(
        "DecisionNarrative",
        back_populates="decision",
        passive_deletes=True,
    )


class DecisionNarrative(Base):
    __tablename__ = "decision_narratives"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    screening_decision_id = Column(Integer, ForeignKey("screening_decisions.id", ondelete="CASCADE"), nullable=False, index=True)
    narrative_type = Column(String(64), nullable=False, default="candidate_narrative")
    source_operation_id = Column(String(128), nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    structured_output = Column(JSON, nullable=True)
    provider_context = Column(JSON, nullable=True)
    prompt_context = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    generated_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "screening_decision_id",
            "narrative_type",
            "source_operation_id",
            name="uq_decision_narrative_operation",
        ),
    )

    decision = relationship(
        "ScreeningDecision",
        back_populates="narratives",
        passive_deletes=True,
    )


# ─── Role templates ───────────────────────────────────────────────────────────

class RoleTemplate(Base):
    __tablename__ = "role_templates"

    id              = Column(Integer, primary_key=True, index=True)
    tenant_id       = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name            = Column(String(200), nullable=False)
    jd_text         = Column(Text, nullable=False)
    scoring_weights = Column(Text, nullable=True)   # JSON: {skills,experience,stability,education}
    tags            = Column(String(500), nullable=True)
    required_skills_override = Column(Text, nullable=True)  # JSON: ["skill1", "skill2"] or [{"skill": "...", "proficiency": "..."}]
    nice_to_have_skills_override = Column(Text, nullable=True)  # Same format
    created_by      = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    tenant               = relationship("Tenant", back_populates="templates")
    results              = relationship("ScreeningResult", back_populates="role_template")
    transcript_analyses  = relationship("TranscriptAnalysis", back_populates="role_template")
    created_by_user      = relationship("User", foreign_keys=[created_by])


# ─── Requisitions (replaces role templates + screening projects) ─────────────

class Requisition(Base):
    """Calibrated hiring opening — intake, criteria, pipeline, HM sign-off."""
    __tablename__ = "requisitions"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    jd_text = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    client_name = Column(String(200), nullable=True)
    headcount = Column(Integer, nullable=True)
    location = Column(String(200), nullable=True)
    status = Column(String(30), nullable=False, default="draft", server_default="draft")
    intake_status = Column(String(30), nullable=False, default="draft", server_default="draft")
    intake_json = Column(Text, nullable=True)
    search_brief_json = Column(Text, nullable=True)
    calibrated_criteria_json = Column(Text, nullable=True)
    current_criteria_version = Column(Integer, nullable=False, default=0, server_default="0")
    scoring_weights = Column(Text, nullable=True)
    tags = Column(String(500), nullable=True)
    required_skills_override = Column(Text, nullable=True)
    nice_to_have_skills_override = Column(Text, nullable=True)
    must_ask_questions_json = Column(Text, nullable=True)
    primary_hiring_manager_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_recruiter_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    opened_on_behalf_of_hm_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    routing_policy_json = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    legacy_role_template_id = Column(Integer, ForeignKey("role_templates.id", ondelete="SET NULL"), nullable=True)
    legacy_project_id = Column(Integer, nullable=True)
    external_ats_id = Column(String(100), nullable=True)
    ats_provider = Column(String(30), nullable=True)
    hm_approved_at = Column(DateTime(timezone=True), nullable=True)
    hm_approved_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    hm_request_email = Column(String(255), nullable=True)
    hm_request_status = Column(String(30), nullable=True)
    hm_requested_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    hm_requested_at = Column(DateTime(timezone=True), nullable=True)
    hm_request_notes = Column(Text, nullable=True)
    calibrated_at = Column(DateTime(timezone=True), nullable=True)
    calibrated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    closed_at = Column(DateTime(timezone=True), nullable=True)

    tenant = relationship("Tenant", backref="requisitions")
    primary_hiring_manager = relationship("User", foreign_keys=[primary_hiring_manager_id])
    assigned_recruiter = relationship("User", foreign_keys=[assigned_recruiter_id])
    opened_on_behalf_of_hm = relationship("User", foreign_keys=[opened_on_behalf_of_hm_id])
    creator = relationship("User", foreign_keys=[created_by])
    legacy_role_template = relationship("RoleTemplate", foreign_keys=[legacy_role_template_id])
    criteria_versions = relationship("RequisitionCriteriaVersion", back_populates="requisition", cascade="all, delete-orphan")
    hiring_managers = relationship("RequisitionHiringManager", back_populates="requisition", cascade="all, delete-orphan")
    req_candidates = relationship("RequisitionCandidate", back_populates="requisition", cascade="all, delete-orphan")
    screening_results = relationship("ScreeningResult", back_populates="requisition")

    __table_args__ = (
        Index("ix_requisitions_tenant_status", "tenant_id", "status"),
    )


class RequisitionCriteriaVersion(Base):
    __tablename__ = "requisition_criteria_versions"

    id = Column(Integer, primary_key=True)
    requisition_id = Column(Integer, ForeignKey("requisitions.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    criteria_json = Column(Text, nullable=False)
    source = Column(String(30), nullable=False, default="calibration", server_default="calibration")
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    requisition = relationship("Requisition", back_populates="criteria_versions")
    author = relationship("User", foreign_keys=[created_by])

    __table_args__ = (
        UniqueConstraint("requisition_id", "version", name="uq_req_criteria_version"),
    )


class RequisitionHiringManager(Base):
    __tablename__ = "requisition_hiring_managers"

    id = Column(Integer, primary_key=True)
    requisition_id = Column(Integer, ForeignKey("requisitions.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    is_primary = Column(Boolean, nullable=False, default=False, server_default="false")
    assigned_at = Column(DateTime(timezone=True), server_default=func.now())

    requisition = relationship("Requisition", back_populates="hiring_managers")
    user = relationship("User")

    __table_args__ = (
        UniqueConstraint("requisition_id", "user_id", name="uq_req_hm_user"),
    )


class RequisitionOpenRequest(Base):
    """HM-initiated request for TA/recruiter to open a requisition."""
    __tablename__ = "requisition_open_requests"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    requested_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(200), nullable=False)
    jd_text = Column(Text, nullable=False)
    notes = Column(Text, nullable=True)
    location = Column(String(200), nullable=True)
    headcount = Column(Integer, nullable=True)
    status = Column(String(30), nullable=False, default="pending", server_default="pending")
    assigned_recruiter_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    requisition_id = Column(Integer, ForeignKey("requisitions.id", ondelete="SET NULL"), nullable=True)
    assigned_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    requester = relationship("User", foreign_keys=[requested_by])
    assigned_recruiter = relationship("User", foreign_keys=[assigned_recruiter_id])
    requisition = relationship("Requisition", foreign_keys=[requisition_id])


class RequisitionCandidate(Base):
    __tablename__ = "requisition_candidates"

    id = Column(Integer, primary_key=True)
    requisition_id = Column(Integer, ForeignKey("requisitions.id", ondelete="CASCADE"), nullable=False)
    candidate_id = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False)
    screening_result_id = Column(Integer, ForeignKey("screening_results.id", ondelete="SET NULL"), nullable=True)
    pipeline_status = Column(String(50), nullable=False, default="pending", server_default="pending")
    submission_status = Column(String(30), nullable=False, default="none", server_default="none")
    hm_outcome = Column(String(30), nullable=True)
    outcome_reason_code = Column(String(50), nullable=True)
    outcome_notes = Column(Text, nullable=True)
    submission_json = Column(Text, nullable=True)
    parse_confidence_json = Column(Text, nullable=True)
    added_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    outcome_at = Column(DateTime(timezone=True), nullable=True)
    added_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    requisition = relationship("Requisition", back_populates="req_candidates")
    candidate = relationship("Candidate", backref="requisition_memberships")
    screening_result = relationship("ScreeningResult", backref="requisition_memberships")
    adder = relationship("User", foreign_keys=[added_by])

    __table_args__ = (
        UniqueConstraint("requisition_id", "candidate_id", name="uq_requisition_candidate"),
        Index("ix_req_candidates_requisition_status", "requisition_id", "pipeline_status"),
    )


class TenantRequisitionSettings(Base):
    __tablename__ = "tenant_requisition_settings"

    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    intake_gate_mode = Column(String(20), nullable=False, default="warn", server_default="warn")
    screening_mode = Column(String(30), nullable=False, default="requisition_required", server_default="requisition_required")
    hm_pipeline_permission = Column(String(30), nullable=False, default="view_only", server_default="view_only")
    # One-time RoleTemplate/project → requisition migration. Prevents resurrecting
    # deleted requisitions from leftover shadow RoleTemplates on every list/dashboard.
    legacy_jd_migrated = Column(Boolean, nullable=False, default=False, server_default="false")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant = relationship("Tenant", backref="requisition_settings", uselist=False)


class SkillClassificationTemplate(Base):
    """Saved skill classification templates scoped per tenant for reuse across JDs."""
    __tablename__ = "skill_classification_templates"

    id                  = Column(Integer, primary_key=True, index=True)
    tenant_id           = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    name                = Column(String(255), nullable=False)
    role_template_id    = Column(Integer, ForeignKey("role_templates.id"), nullable=True)
    required_skills     = Column(Text, nullable=False, default="[]")       # JSON array
    nice_to_have_skills = Column(Text, nullable=False, default="[]")       # JSON array
    created_by          = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant          = relationship("Tenant")
    role_template   = relationship("RoleTemplate")
    created_by_user = relationship("User")


# ─── Team collaboration ───────────────────────────────────────────────────────

class TeamMember(Base):
    __tablename__ = "team_members"

    id        = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    user_id   = Column(Integer, ForeignKey("users.id"), nullable=False)
    role      = Column(String(50), nullable=False, default="recruiter")

    tenant = relationship("Tenant", back_populates="team_members")
    user   = relationship("User", back_populates="team_member")


class Comment(Base):
    __tablename__ = "comments"

    id        = Column(Integer, primary_key=True, index=True)
    result_id = Column(Integer, ForeignKey("screening_results.id"), nullable=False)
    user_id   = Column(Integer, ForeignKey("users.id"), nullable=False)
    text      = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    result = relationship("ScreeningResult", back_populates="comments")
    author = relationship("User", back_populates="comments")


class InterviewEvaluation(Base):
    """Per-question recruiter evaluation (note + rating)."""
    __tablename__ = "interview_evaluations"

    id                = Column(Integer, primary_key=True, index=True)
    result_id         = Column(Integer, ForeignKey("screening_results.id"), nullable=False, index=True)
    user_id           = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_category = Column(String(30), nullable=False)
    question_index    = Column(Integer, nullable=False)
    rating            = Column(String(10), nullable=True)
    notes             = Column(Text, nullable=True)
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at        = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    result = relationship("ScreeningResult", back_populates="evaluations")
    evaluator = relationship("User")

    __table_args__ = (
        UniqueConstraint('result_id', 'user_id', 'question_category', 'question_index',
                         name='uq_eval_per_question'),
    )

class OverallAssessment(Base):
    """Recruiter's overall assessment for HM scorecard."""
    __tablename__ = "overall_assessments"

    id                       = Column(Integer, primary_key=True, index=True)
    result_id                = Column(Integer, ForeignKey("screening_results.id"), nullable=False, index=True)
    user_id                  = Column(Integer, ForeignKey("users.id"), nullable=False)
    overall_assessment       = Column(Text, nullable=True)
    recruiter_recommendation = Column(String(10), nullable=True)
    debrief_json             = Column(Text, nullable=True)        # JSON string of LLM-generated debrief
    recruiter_score          = Column(Integer, nullable=True)       # 0-100 computed score
    created_at               = Column(DateTime(timezone=True), server_default=func.now())
    updated_at               = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    result = relationship("ScreeningResult", back_populates="overall_assessment")
    evaluator = relationship("User")

    __table_args__ = (
        UniqueConstraint('result_id', 'user_id', name='uq_overall_per_user'),
    )


# ─── Transcript analysis ──────────────────────────────────────────────────────

class TranscriptAnalysis(Base):
    __tablename__ = "transcript_analyses"

    id               = Column(Integer, primary_key=True, index=True)
    tenant_id        = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    candidate_id     = Column(Integer, ForeignKey("candidates.id"), nullable=True)
    role_template_id = Column(Integer, ForeignKey("role_templates.id"), nullable=True)
    transcript_text  = Column(Text, nullable=False)
    source_platform  = Column(String(50), nullable=True)   # zoom / teams / manual
    analysis_result  = Column(Text, nullable=False)         # JSON
    analysis_generation = Column(Integer, nullable=False, default=1, server_default="1")
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    candidate     = relationship("Candidate", back_populates="transcript_analyses")
    role_template = relationship("RoleTemplate", back_populates="transcript_analyses")


# ─── Custom AI training ───────────────────────────────────────────────────────

class TrainingExample(Base):
    __tablename__ = "training_examples"

    id                  = Column(Integer, primary_key=True, index=True)
    tenant_id           = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    screening_result_id = Column(Integer, ForeignKey("screening_results.id"), nullable=False)
    screening_decision_id = Column(Integer, nullable=True, index=True)
    outcome             = Column(String(50), nullable=False)   # hired / rejected
    feedback            = Column(Text, nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    result = relationship("ScreeningResult", back_populates="training_examples")
    screening_decision = relationship(
        "ScreeningDecision",
        foreign_keys=lambda: [TrainingExample.screening_decision_id],
        primaryjoin="TrainingExample.screening_decision_id==ScreeningDecision.id",
    )


# ─── Hybrid pipeline caches & skills registry ─────────────────────────────────

class JdCache(Base):
    """Shared JD parse cache across all workers. Keyed by MD5 of first 2000 chars."""
    __tablename__ = "jd_cache"

    hash        = Column(String(64), primary_key=True)
    result_json = Column(Text,       nullable=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


class Skill(Base):
    """Dynamic skills registry — seed from hardcoded list, grow via discovery."""
    __tablename__ = "skills"

    id         = Column(Integer,     primary_key=True, index=True)
    name       = Column(String(200), unique=True, nullable=False)
    aliases    = Column(Text,        nullable=True)   # comma-separated alias list
    domain     = Column(String(50),  nullable=True)   # backend|frontend|data_science|...
    status     = Column(String(20),  nullable=False, default="active")  # active|pending|rejected
    source     = Column(String(20),  nullable=False, default="seed")    # seed|discovered|manual
    frequency  = Column(Integer,     nullable=False, default=0)         # times seen in JDs/resumes
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ─── Token revocation ─────────────────────────────────────────────────────────

class RevokedToken(Base):
    """Tracks revoked JWT tokens to prevent reuse after logout."""
    __tablename__ = "revoked_tokens"

    id          = Column(Integer, primary_key=True, index=True)
    jti         = Column(String(64), unique=True, index=True, nullable=False)  # JWT ID (UUID)
    revoked_at  = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at  = Column(DateTime(timezone=True), nullable=False)  # When token would have expired


class PasswordResetToken(Base):
    """Tracks password reset tokens for secure password reset flow."""
    __tablename__ = "password_reset_tokens"

    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token      = Column(String(255), unique=True, nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)


class AuditLog(Base):
    """Platform admin audit trail."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_email = Column(String(255), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(Integer, nullable=True)
    details = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    entry_hash = Column(String(64), nullable=True)
    prev_hash = Column(String(64), nullable=True)
    impersonated_by = Column(Integer, nullable=True)


class FieldAuditLog(Base):
    """Field-level audit trail for candidate/report edits — dynamic reports."""
    __tablename__ = "field_audit_logs"

    id            = Column(Integer, primary_key=True, index=True)
    tenant_id     = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    entity_type   = Column(String(50), nullable=False)    # 'candidate', 'screening_result'
    entity_id     = Column(Integer, nullable=False, index=True)
    field_name    = Column(String(100), nullable=False)
    old_value     = Column(Text, nullable=True)
    new_value     = Column(Text, nullable=True)
    changed_by    = Column(Integer, ForeignKey("users.id"), nullable=False)
    changed_at    = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    change_reason = Column(String(500), nullable=True)

    changed_by_user = relationship("User")

class FeatureFlag(Base):
    """Global feature flags for the platform."""
    __tablename__ = "feature_flags"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    display_name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    enabled_globally = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    overrides = relationship("TenantFeatureOverride", back_populates="feature_flag")
    plan_features = relationship("PlanFeature", back_populates="feature_flag", cascade="all, delete-orphan")

class TenantFeatureOverride(Base):
    """Per-tenant feature flag overrides."""
    __tablename__ = "tenant_feature_overrides"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    feature_flag_id = Column(Integer, ForeignKey("feature_flags.id", ondelete="CASCADE"), nullable=False)
    enabled = Column(Boolean, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    feature_flag = relationship("FeatureFlag", back_populates="overrides")

    __table_args__ = (UniqueConstraint('tenant_id', 'feature_flag_id', name='uq_tenant_feature'),)

class RateLimitConfig(Base):
    """Per-tenant rate limiting configuration."""
    __tablename__ = "rate_limit_configs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), unique=True, nullable=False)
    requests_per_minute = Column(Integer, nullable=False, default=60)
    llm_concurrent_max = Column(Integer, nullable=False, default=2)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class Webhook(Base):
    """Tenant webhook configuration for event notifications."""
    __tablename__ = "webhooks"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    url = Column(String(500), nullable=False)
    secret = Column(String(255), nullable=False)
    events = Column(Text, nullable=False, default="[]")  # JSON array of event names
    is_active = Column(Boolean, nullable=False, default=True)
    failure_count = Column(Integer, nullable=False, default=0)
    last_triggered_at = Column(DateTime(timezone=True), nullable=True)
    last_failure_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    deliveries = relationship("WebhookDelivery", back_populates="webhook", cascade="all, delete-orphan")


class WebhookDelivery(Base):
    """Record of a webhook delivery attempt."""
    __tablename__ = "webhook_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    webhook_id = Column(Integer, ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
    event = Column(String(100), nullable=False)
    payload = Column(Text, nullable=True)  # JSON
    response_status = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    success = Column(Boolean, nullable=False, default=False)
    attempt = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    webhook = relationship("Webhook", back_populates="deliveries")


class PlatformSetting(Base):
    """Platform-level key-value settings store."""
    __tablename__ = "platform_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)  # JSON string
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PlatformConfig(Base):
    """Platform-level key-value configuration for billing provider settings."""
    __tablename__ = "platform_configs"

    id = Column(Integer, primary_key=True, index=True)
    config_key = Column(String(255), unique=True, nullable=False, index=True)
    config_value = Column(Text, nullable=False)
    description = Column(String(500), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class TenantEmailConfig(Base):
    """Per-tenant SMTP email configuration for outbound notifications."""
    __tablename__ = "tenant_email_configs"

    id              = Column(Integer, primary_key=True, index=True)
    tenant_id       = Column(Integer, ForeignKey("tenants.id"), unique=True, nullable=False)
    smtp_host       = Column(String(255), nullable=False)
    smtp_port       = Column(Integer, default=587)
    smtp_user       = Column(String(255), nullable=True)
    smtp_password   = Column(String(500), nullable=True)          # Fernet-encrypted
    smtp_from       = Column(String(255), nullable=False)
    from_name       = Column(String(255), nullable=True)
    reply_to        = Column(String(255), nullable=True)
    encryption_type = Column(String(10), default="tls")           # tls, ssl, none
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    configured_by   = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_test_at    = Column(DateTime(timezone=True), nullable=True)
    last_test_success = Column(Boolean, nullable=True)


class ImpersonationSession(Base):
    """Tracks active admin impersonation sessions for support/debugging."""
    __tablename__ = "impersonation_sessions"

    id              = Column(Integer, primary_key=True, index=True)
    admin_user_id   = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    target_user_id  = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash      = Column(String(64), unique=True, nullable=False, index=True)
    expires_at      = Column(DateTime(timezone=True), nullable=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    revoked_at      = Column(DateTime(timezone=True), nullable=True)
    ip_address      = Column(String(45), nullable=True)

    admin_user  = relationship("User", foreign_keys=[admin_user_id])
    target_user = relationship("User", foreign_keys=[target_user_id])


class SecurityEvent(Base):
    """Security monitoring events: logins, failures, suspicious activity."""
    __tablename__ = "security_events"

    id          = Column(Integer, primary_key=True, index=True)
    tenant_id   = Column(Integer, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)
    user_id     = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    event_type  = Column(String(50), nullable=False)
    ip_address  = Column(String(45), nullable=True)
    user_agent  = Column(String(500), nullable=True)
    details     = Column(Text, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    tenant = relationship("Tenant")
    user   = relationship("User")


class BillingEvent(Base):
    """Audit log for billing webhook events from payment providers."""
    __tablename__ = "billing_events"

    id           = Column(Integer, primary_key=True, index=True)
    provider     = Column(String(20), nullable=False, index=True)   # stripe / razorpay / manual
    event_id     = Column(String(255), nullable=True, index=True)   # provider's unique event id (idempotency)
    event_type   = Column(String(100), nullable=False, index=True)  # invoice.paid, subscription.activated, etc.
    tenant_id    = Column(Integer, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    raw_payload  = Column(Text, nullable=True)                       # JSON-serialised event payload
    result       = Column(String(20), nullable=False, default="pending")  # success / error / ignored
    error_detail = Column(Text, nullable=True)                       # Error message if result=error
    processed_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at   = Column(DateTime(timezone=True), server_default=func.now())

    tenant = relationship("Tenant")

    __table_args__ = (
        UniqueConstraint("provider", "event_id", name="uq_billing_event_provider_event_id"),
    )


class Invoice(Base):
    """Invoice record generated on successful payment."""
    __tablename__ = "invoices"

    id           = Column(Integer, primary_key=True, index=True)
    tenant_id    = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    invoice_number = Column(String(50), unique=True, nullable=False)  # e.g., INV-2026-00001
    status       = Column(String(20), nullable=False, default="paid")  # draft, pending, paid, void, refunded
    amount       = Column(Integer, nullable=False)                     # cents
    currency     = Column(String(3), nullable=False, default="usd")
    description  = Column(String(500), nullable=True)
    line_items   = Column(JSON, default=list)  # [{"description": "Pro Plan - Monthly", "amount": 4900, "quantity": 1}]

    # Payment reference
    payment_provider    = Column(String(20), nullable=True)   # stripe, razorpay, manual
    provider_invoice_id = Column(String(255), nullable=True)  # stripe invoice ID etc.

    # Dates
    period_start = Column(DateTime(timezone=True), nullable=True)
    period_end   = Column(DateTime(timezone=True), nullable=True)
    issued_at    = Column(DateTime(timezone=True), server_default=func.now())
    paid_at      = Column(DateTime(timezone=True), nullable=True)

    tenant = relationship("Tenant", backref="invoices")

    __table_args__ = (
        Index("ix_invoices_tenant_issued", "tenant_id", "issued_at"),
        Index(
            "uq_invoice_provider_invoice_id",
            "payment_provider",
            "provider_invoice_id",
            unique=True,
            sqlite_where=text("provider_invoice_id IS NOT NULL"),
            postgresql_where=text("provider_invoice_id IS NOT NULL"),
        ),
    )


class InvoiceYearCounter(Base):
    """Atomic yearly invoice-number allocator (SELECT FOR UPDATE / unique year PK)."""
    __tablename__ = "invoice_year_counters"

    year = Column(Integer, primary_key=True)
    last_value = Column(Integer, nullable=False, default=0)


class DunningRecord(Base):
    """Tracks failed payment retry attempts and dunning state for tenants."""
    __tablename__ = "dunning_records"

    id              = Column(String(36), primary_key=True)
    tenant_id       = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    status          = Column(String(20), nullable=False, default="active")   # active | resolved | exhausted
    retry_count     = Column(Integer, nullable=False, default=0)
    max_retries     = Column(Integer, nullable=False, default=4)
    next_retry_at   = Column(DateTime(timezone=True), nullable=True)
    last_retry_at   = Column(DateTime(timezone=True), nullable=True)
    failure_reason  = Column(String(500), nullable=True)
    resolved_at     = Column(DateTime(timezone=True), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    tenant = relationship("Tenant", backref="dunning_records")


class PlanFeature(Base):
    """Maps subscription plans to feature flag entitlements."""
    __tablename__ = "plan_features"

    id               = Column(Integer, primary_key=True, index=True)
    plan_id          = Column(Integer, ForeignKey("subscription_plans.id", ondelete="CASCADE"), nullable=False)
    feature_flag_id  = Column(Integer, ForeignKey("feature_flags.id", ondelete="CASCADE"), nullable=False)
    enabled          = Column(Boolean, nullable=False, default=True)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    plan         = relationship("SubscriptionPlan", back_populates="plan_features")
    feature_flag = relationship("FeatureFlag", back_populates="plan_features")

    __table_args__ = (UniqueConstraint("plan_id", "feature_flag_id", name="uq_plan_feature"),)


class ErasureLog(Base):
    """Audit trail for GDPR data erasure requests."""
    __tablename__ = "erasure_logs"

    id               = Column(Integer, primary_key=True, index=True)
    tenant_id        = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    actor_user_id    = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status           = Column(String(20), nullable=False, default="requested")
    started_at       = Column(DateTime(timezone=True), nullable=True)
    completed_at     = Column(DateTime(timezone=True), nullable=True)
    records_affected = Column(Integer, nullable=False, default=0)
    details          = Column(Text, nullable=True)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    tenant = relationship("Tenant")
    actor  = relationship("User")


class SSOConfig(Base):
    __tablename__ = "sso_configs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), unique=True, index=True)
    provider_type = Column(String(20), default="saml2")  # saml2, oidc (future)

    # SAML settings
    idp_entity_id = Column(String(500), nullable=False)
    idp_sso_url = Column(String(500), nullable=False)  # IdP login URL
    idp_slo_url = Column(String(500), nullable=True)   # IdP logout URL (optional)
    idp_certificate = Column(Text, nullable=False)     # X.509 cert for signature verification

    # SP settings (auto-generated)
    sp_entity_id = Column(String(500), nullable=True)  # Our entity ID
    sp_acs_url = Column(String(500), nullable=True)    # Assertion Consumer Service URL

    # Behavior
    enforce_sso = Column(Boolean, default=False)       # If true, password login disabled for this tenant
    auto_provision = Column(Boolean, default=True)     # Auto-create user on first SSO login
    default_role = Column(String(50), default="viewer") # Default role for auto-provisioned users
    groups_attribute = Column(String(100), nullable=True, server_default="groups")

    # Status
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class SSOGroupRoleMapping(Base):
    """Map IdP group names to tenant roles on SSO login."""
    __tablename__ = "sso_group_role_mappings"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    idp_group = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False)  # admin | recruiter | viewer
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    tenant = relationship("Tenant")

    __table_args__ = (UniqueConstraint("tenant_id", "idp_group", name="uq_sso_group_mapping"),)


class HandoffShareLink(Base):
    """Tokenized HM magic links for read-only handoff packages."""
    __tablename__ = "handoff_share_links"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String(64), unique=True, nullable=True, index=True)
    token_hash = Column(String(64), unique=True, nullable=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    role_template_id = Column(Integer, ForeignKey("role_templates.id", ondelete="CASCADE"), nullable=True, index=True)
    requisition_id = Column(Integer, ForeignKey("requisitions.id", ondelete="CASCADE"), nullable=True, index=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    label = Column(String(200), nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    view_count = Column(Integer, nullable=False, default=0)
    passcode_hash = Column(String(128), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    role_template = relationship("RoleTemplate")
    requisition = relationship("Requisition")
    created_by_user = relationship("User")


class PendingObjectDeletion(Base):
    """Retryable object-storage deletion after GDPR/hard-delete failures."""
    __tablename__ = "pending_object_deletions"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    storage_key = Column(String(500), nullable=False)
    candidate_id = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False, default="pending", server_default="pending", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_error = Column(Text, nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    dead_lettered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ─── Historical learning system ────────────────────────────────────────────────

class HiringOutcome(Base):
    """Tracks hiring decisions for historical learning and model calibration."""
    __tablename__ = "hiring_outcomes"

    id                   = Column(Integer, primary_key=True, index=True)
    tenant_id            = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    screening_result_id  = Column(Integer, ForeignKey("screening_results.id"), unique=True, nullable=False)
    candidate_id         = Column(Integer, ForeignKey("candidates.id"), nullable=False, index=True)
    role_template_id     = Column(Integer, ForeignKey("role_templates.id"), nullable=True, index=True)
    decision             = Column(String(20), nullable=False)          # hired|rejected|withdrawn|no_decision
    decision_stage       = Column(String(50), nullable=True)           # screening|phone_screen|interview|offer|onboarded
    decision_date        = Column(DateTime, nullable=True)
    decision_by_user_id  = Column(Integer, ForeignKey("users.id"), nullable=True)
    feedback_rating      = Column(Integer, nullable=True)              # 1-5
    feedback_notes       = Column(Text, nullable=True)
    source               = Column(String(20), server_default="manual") # manual|ats_webhook
    metadata_json        = Column(Text, nullable=True)
    created_at           = Column(DateTime(timezone=True), server_default=func.now())
    updated_at           = Column(DateTime(timezone=True), onupdate=func.now())

    tenant            = relationship("Tenant")
    screening_result  = relationship("ScreeningResult")
    candidate         = relationship("Candidate")
    role_template     = relationship("RoleTemplate")
    decision_by_user  = relationship("User")

    __table_args__ = (
        Index("ix_hiring_outcomes_tenant_template", "tenant_id", "role_template_id"),
        Index("ix_hiring_outcomes_tenant_decision_date", "tenant_id", "decision", "created_at"),
    )


class TeamSkillProfile(Base):
    """Team-level skill profile for gap analysis and benchmarking."""
    __tablename__ = "team_skill_profiles"

    id                  = Column(Integer, primary_key=True, index=True)
    tenant_id           = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    team_name           = Column(String(200), nullable=False)
    skills_json         = Column(Text, nullable=True)                  # JSON array
    job_functions       = Column(Text, nullable=True)                  # JSON array
    member_count        = Column(Integer, nullable=True)
    created_by_user_id  = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), onupdate=func.now())

    tenant          = relationship("Tenant")
    created_by_user = relationship("User")


class SkillTrendSnapshot(Base):
    """Periodic snapshot of skill demand/supply trends for analytics."""
    __tablename__ = "skill_trend_snapshots"

    id                   = Column(Integer, primary_key=True, index=True)
    tenant_id            = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    role_category        = Column(String(50), nullable=True)
    skill_name           = Column(String(200), nullable=False)
    period_date          = Column(Date, nullable=False)
    jd_mention_count     = Column(Integer, default=0)
    resume_present_count = Column(Integer, default=0)
    hired_with_skill     = Column(Integer, default=0)
    total_hired          = Column(Integer, default=0)
    trend_direction      = Column(String(10), nullable=True)           # rising|falling|stable
    growth_pct           = Column(Float, nullable=True)
    created_at           = Column(DateTime(timezone=True), server_default=func.now())

    tenant = relationship("Tenant")

    __table_args__ = (
        Index("ix_skill_trends_tenant_category_date", "tenant_id", "role_category", "period_date"),
        Index("ix_skill_trends_tenant_skill_date", "tenant_id", "skill_name", "period_date"),
    )


class OutcomeSkillPattern(Base):
    """Statistical correlation between skills and hiring outcomes."""
    __tablename__ = "outcome_skill_patterns"

    id                      = Column(Integer, primary_key=True, index=True)
    tenant_id               = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    role_template_id        = Column(Integer, ForeignKey("role_templates.id"), nullable=True, index=True)
    role_category           = Column(String(50), nullable=True)
    skill_name              = Column(String(200), nullable=False)
    correlation_score       = Column(Float, nullable=True)
    present_in_hired_pct    = Column(Float, nullable=True)
    present_in_rejected_pct = Column(Float, nullable=True)
    sample_size             = Column(Integer, nullable=True)
    last_computed_at        = Column(DateTime, nullable=True)
    created_at              = Column(DateTime(timezone=True), server_default=func.now())

    tenant        = relationship("Tenant")
    role_template = relationship("RoleTemplate")

    __table_args__ = (
        Index("ix_outcome_patterns_tenant_template", "tenant_id", "role_template_id"),
    )


# ─── Usage alerts ────────────────────────────────────────────────────────────────

class AdminNotification(Base):
    """Platform admin notification center entries."""
    __tablename__ = "admin_notifications"

    id         = Column(Integer, primary_key=True, index=True)
    type       = Column(String(50), nullable=False)   # quota_warning, dunning_exhausted, webhook_failure
    severity   = Column(String(20), nullable=False, server_default='info')  # info, warning, critical
    title      = Column(String(200), nullable=False)
    message    = Column(Text, nullable=False)
    tenant_id  = Column(Integer, ForeignKey('tenants.id'), nullable=True)
    is_read    = Column(Boolean, nullable=False, server_default='false', default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    tenant = relationship('Tenant', backref='admin_notifications')


class UsageAlert(Base):
    """Tracks usage threshold alerts sent to tenants."""
    __tablename__ = "usage_alerts"

    id                 = Column(String(36), primary_key=True)
    tenant_id          = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    alert_type         = Column(String(50), nullable=False)   # analyses_80, analyses_100, storage_80, storage_100, team_members_100
    threshold_percent  = Column(Integer, nullable=False)      # 80 or 100
    metric_name        = Column(String(50), nullable=False)   # analyses_per_month, storage_gb, team_members
    current_value      = Column(Integer, nullable=False)
    limit_value        = Column(Integer, nullable=False)
    notified_at        = Column(DateTime(timezone=True), server_default=func.now())
    period_key         = Column(String(10), nullable=False)   # e.g., "2026-05" — prevents duplicate alerts per period

    tenant = relationship("Tenant", backref="usage_alerts")

    __table_args__ = (
        UniqueConstraint("tenant_id", "alert_type", "period_key", name="uq_usage_alert_per_period"),
    )


# ─── Voice Screening ───────────────────────────────────────────────────────────

class VoiceTenantConfig(Base):
    """Per-tenant voice screening bot configuration."""
    __tablename__ = "voice_tenant_configs"

    id                        = Column(Integer, primary_key=True, index=True)
    tenant_id                 = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    bot_name                  = Column(String(100), nullable=False, server_default="ARIA Assistant")
    bot_voice_gender          = Column(String(10), nullable=False, server_default="female")  # male / female
    bot_voice_sample_url      = Column(Text, nullable=True)  # S3 URL to voice cloning audio
    outbound_phone_number     = Column(String(20), nullable=True)  # E.164 format e.g. +14155551234
    caller_id_name            = Column(String(100), nullable=True)
    business_hours_start      = Column(String(5), nullable=False, server_default="09:00")  # HH:MM
    business_hours_end        = Column(String(5), nullable=False, server_default="18:00")  # HH:MM
    allowed_days              = Column(JSON, nullable=False, server_default="[1,2,3,4,5]")  # Mon=1 .. Sun=7
    timezone                  = Column(String(50), nullable=False, server_default="UTC")
    consent_script            = Column(Text, nullable=True)  # NULL = use default
    interview_opening_script  = Column(Text, nullable=True)  # NULL = use default opening
    use_custom_interview_opening = Column(Boolean, nullable=False, server_default="false", default=False)
    company_about_blurb       = Column(Text, nullable=True)  # optional context for AI draft helper
    greeting_style            = Column(String(20), nullable=False, server_default="professional")  # professional / casual / friendly
    call_duration_min         = Column(Integer, nullable=False, server_default="5")
    call_duration_max         = Column(Integer, nullable=False, server_default="7")
    max_retries               = Column(Integer, nullable=False, server_default="3")
    retry_intervals           = Column(JSON, nullable=False, server_default="[24,48]")  # hours between retries
    escalation_contact_id     = Column(Integer, ForeignKey("team_members.id", ondelete="SET NULL"), nullable=True)
    assessment_detail_level   = Column(String(10), nullable=False, server_default="full")  # brief / full
    auto_update_status        = Column(Boolean, nullable=False, server_default="true", default=True)
    follow_up_aggressiveness  = Column(String(10), nullable=False, server_default="medium")  # low / medium / high
    # Adaptive depth escalation
    auto_escalation_enabled   = Column(Boolean, nullable=False, server_default="false", default=False)
    auto_escalation_threshold = Column(Integer, nullable=False, server_default="70", default=70)
    created_at                = Column(DateTime(timezone=True), server_default=func.now())
    updated_at                = Column(DateTime(timezone=True), onupdate=func.now())

    tenant              = relationship("Tenant", backref="voice_config", uselist=False)
    escalation_contact  = relationship("TeamMember", foreign_keys=[escalation_contact_id])


class VoiceScreeningSession(Base):
    """A single voice screening call session (outbound or inbound callback)."""
    __tablename__ = "voice_screening_sessions"

    id                = Column(Integer, primary_key=True, index=True)
    tenant_id         = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id      = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    jd_id             = Column(Integer, ForeignKey("role_templates.id", ondelete="SET NULL"), nullable=True)
    phone_number      = Column(String(20), nullable=False)  # E.164
    direction         = Column(String(10), nullable=False, server_default="outbound")  # outbound / inbound
    callback_of_id    = Column(Integer, ForeignKey("voice_screening_sessions.id", ondelete="SET NULL"), nullable=True)  # links callback to original
    status            = Column(String(20), nullable=False, server_default="scheduled", index=True)  # scheduled/ringing/in_progress/completed/failed/no_answer/voicemail
    interview_depth   = Column(String(10), default="quick", server_default="quick", nullable=False)  # quick / deep
    scheduled_at      = Column(DateTime(timezone=True), nullable=True)
    started_at        = Column(DateTime(timezone=True), nullable=True)
    ended_at          = Column(DateTime(timezone=True), nullable=True)
    transcript_json   = Column(Text, nullable=True)  # JSON array of transcript entries
    assessment_json   = Column(Text, nullable=True)  # JSON structured assessment
    duration_seconds  = Column(Integer, nullable=True)
    retry_count       = Column(Integer, nullable=False, server_default="0")
    consent_recorded  = Column(Boolean, nullable=False, server_default="false", default=False)
    consent_status    = Column(String(20), nullable=True)  # confirmed/denied/skipped (null when not yet asked)
    call_sid          = Column(String(100), nullable=True)  # LiveKit/Twilio call identifier
    result_generation = Column(Integer, nullable=False, default=1, server_default="1")
    completion_event_id = Column(String(100), nullable=True)
    error_log         = Column(Text, nullable=True)
    created_at        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at        = Column(DateTime(timezone=True), onupdate=func.now())

    tenant      = relationship("Tenant", backref="voice_sessions")
    candidate   = relationship("Candidate", backref="voice_sessions")
    jd          = relationship("RoleTemplate", backref="voice_sessions")
    callback_of = relationship("VoiceScreeningSession", remote_side=[id], foreign_keys=[callback_of_id])
    transcript_entries = relationship("VoiceTranscriptEntry", back_populates="session", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_voice_sessions_tenant_candidate", "tenant_id", "candidate_id"),
        Index("ix_voice_sessions_phone_status", "phone_number", "status"),
    )


class VoiceTranscriptEntry(Base):
    """A single speaker turn in a voice screening session transcript."""
    __tablename__ = "voice_transcript_entries"

    id         = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("voice_screening_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    speaker    = Column(String(10), nullable=False)  # bot / candidate
    text       = Column(Text, nullable=False)
    timestamp  = Column(DateTime(timezone=True), nullable=False)
    audio_url  = Column(Text, nullable=True)  # URL to audio clip for this turn
    question_id = Column(String(36), nullable=True)  # Links to RecruiterInterviewQuestion.id for deterministic pairing

    session = relationship("VoiceScreeningSession", back_populates="transcript_entries")

    __table_args__ = (
        Index("ix_voice_transcript_session_ts", "session_id", "timestamp"),
    )


# ─── AI Recruiter ────────────────────────────────────────────────────────────

class RecruiterInterviewSession(Base):
    """A single AI recruiter interview session and its lifecycle state."""
    __tablename__ = "recruiter_interview_sessions"

    id                    = Column(String(36), primary_key=True, default=uuid.uuid4)
    tenant_id             = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id          = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    jd_id                 = Column(Integer, ForeignKey("role_templates.id", ondelete="CASCADE"), nullable=False)
    screening_result_id   = Column(Integer, ForeignKey("screening_results.id", ondelete="SET NULL"), nullable=True)
    voice_session_id      = Column(Integer, ForeignKey("voice_screening_sessions.id", ondelete="SET NULL"), nullable=True)
    trigger_type          = Column(String(20), nullable=False, default="manual", server_default="manual")
    status                = Column(String(30), nullable=False, default="pending_strategy", server_default="pending_strategy")
    consent_status        = Column(String(20), nullable=False, default="pending", server_default="pending")  # pending/confirmed/denied/skipped
    interview_strategy_json = Column(Text, nullable=True)  # JSON: generated interview plan
    interview_config_json   = Column(Text, nullable=True)  # JSON: runtime interview config
    started_at            = Column(DateTime(timezone=True), nullable=True)
    ended_at              = Column(DateTime(timezone=True), nullable=True)
    duration_seconds      = Column(Integer, nullable=True)
    created_by            = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at            = Column(DateTime(timezone=True), server_default=func.now())
    updated_at            = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant            = relationship("Tenant", backref="recruiter_interview_sessions")
    candidate         = relationship("Candidate", backref="recruiter_interview_sessions")
    jd                = relationship("RoleTemplate", backref="recruiter_interview_sessions")
    screening_result  = relationship("ScreeningResult", backref="recruiter_interview_sessions")
    voice_session     = relationship("VoiceScreeningSession", backref="recruiter_interview_sessions")
    creator           = relationship("User", foreign_keys=[created_by])
    questions         = relationship("RecruiterInterviewQuestion", back_populates="session", cascade="all, delete-orphan")
    scorecard         = relationship("RecruiterScorecard", back_populates="session", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_recruiter_sessions_tenant_status", "tenant_id", "status"),
        Index("ix_recruiter_sessions_tenant_candidate", "tenant_id", "candidate_id"),
        Index("ix_recruiter_sessions_candidate_jd", "candidate_id", "jd_id"),
    )


class RecruiterInterviewQuestion(Base):
    """A single question (or follow-up) asked during an AI recruiter interview."""
    __tablename__ = "recruiter_interview_questions"

    id                        = Column(String(36), primary_key=True, default=uuid.uuid4)
    session_id                = Column(String(36), ForeignKey("recruiter_interview_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence_number           = Column(Integer, nullable=False)
    category                  = Column(String(30), nullable=False)  # technical / behavioral / communication / cultural_fit / risk_validation / gap_probe / motivation
    question_text             = Column(Text, nullable=False)
    question_context          = Column(Text, nullable=True)
    candidate_response        = Column(Text, nullable=True)
    response_duration_seconds = Column(Float, nullable=True)
    evaluation_json           = Column(Text, nullable=True)  # JSON: LLM evaluation of response
    is_follow_up              = Column(Boolean, nullable=False, default=False, server_default="false")
    parent_question_id        = Column(String(36), ForeignKey("recruiter_interview_questions.id", ondelete="SET NULL"), nullable=True)
    created_at                = Column(DateTime(timezone=True), server_default=func.now())

    session         = relationship("RecruiterInterviewSession", back_populates="questions")
    parent_question = relationship("RecruiterInterviewQuestion", remote_side=[id], foreign_keys=[parent_question_id], backref="follow_ups")

    __table_args__ = (
        Index("ix_recruiter_questions_session_seq", "session_id", "sequence_number"),
    )

    def _evaluation_dict(self) -> dict[str, Any]:
        if not self.evaluation_json:
            return {}
        try:
            data = json.loads(self.evaluation_json)
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_evaluation_dict(self, data: dict[str, Any]) -> None:
        self.evaluation_json = json.dumps(data, default=str) if data else None

    @property
    def answer_score(self) -> int | None:
        score = self._evaluation_dict().get("answer_score")
        return int(score) if score is not None else None

    @answer_score.setter
    def answer_score(self, value: int | None) -> None:
        data = self._evaluation_dict()
        if value is None:
            data.pop("answer_score", None)
        else:
            data["answer_score"] = int(value)
        self._write_evaluation_dict(data)

    @property
    def copilot_observation(self) -> Any:
        return self._evaluation_dict().get("copilot_observation")

    @copilot_observation.setter
    def copilot_observation(self, value: Any) -> None:
        data = self._evaluation_dict()
        if value is None:
            data.pop("copilot_observation", None)
        elif isinstance(value, str):
            try:
                data["copilot_observation"] = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                data["copilot_observation"] = value
        else:
            data["copilot_observation"] = value
        self._write_evaluation_dict(data)


class RecruiterScorecard(Base):
    """Final structured scorecard produced for an AI recruiter interview session."""
    __tablename__ = "recruiter_scorecards"

    id                       = Column(String(36), primary_key=True, default=uuid.uuid4)
    session_id               = Column(String(36), ForeignKey("recruiter_interview_sessions.id", ondelete="CASCADE"), nullable=False, unique=True)
    tenant_id                = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id             = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)

    technical_score          = Column(Integer, nullable=True)
    technical_evidence       = Column(Text, nullable=True)  # JSON
    behavioral_score         = Column(Integer, nullable=True)
    behavioral_evidence      = Column(Text, nullable=True)  # JSON
    communication_score      = Column(Integer, nullable=True)
    communication_evidence   = Column(Text, nullable=True)  # JSON
    cultural_fit_score       = Column(Integer, nullable=True)
    cultural_fit_evidence    = Column(Text, nullable=True)  # JSON
    motivation_score         = Column(Integer, nullable=True)
    motivation_evidence      = Column(Text, nullable=True)  # JSON

    risk_signals_validated   = Column(Text, nullable=True)  # JSON
    gaps_explained           = Column(Text, nullable=True)  # JSON

    original_fit_score       = Column(Integer, nullable=True)
    adjusted_fit_score       = Column(Integer, nullable=True)
    adjustment_reasoning     = Column(Text, nullable=True)

    overall_score            = Column(Integer, nullable=True)
    confidence_level         = Column(String(10), nullable=True)  # high / medium / low
    recommendation           = Column(String(20), nullable=True)  # strong_hire / hire / maybe / no_hire / strong_no_hire
    recommendation_reasoning = Column(Text, nullable=True)
    executive_summary        = Column(Text, nullable=True)

    created_at               = Column(DateTime(timezone=True), server_default=func.now())
    updated_at               = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    session   = relationship("RecruiterInterviewSession", back_populates="scorecard")
    tenant    = relationship("Tenant", backref="recruiter_scorecards")
    candidate = relationship("Candidate", backref="recruiter_scorecards")

    __table_args__ = (
        Index("ix_recruiter_scorecards_tenant_candidate", "tenant_id", "candidate_id"),
    )


class RecruiterAutoTriggerConfig(Base):
    """Per-tenant configuration for automatically triggering AI recruiter interviews."""
    __tablename__ = "recruiter_auto_trigger_configs"

    id                          = Column(String(36), primary_key=True, default=uuid.uuid4)
    tenant_id                   = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    enabled                     = Column(Boolean, nullable=False, default=False, server_default="false")
    trigger_pipeline_stage      = Column(String(20), nullable=False, default="in_review", server_default="in_review")
    min_fit_score_threshold     = Column(Integer, nullable=False, default=40, server_default="40")
    max_fit_score_threshold     = Column(Integer, nullable=False, default=85, server_default="85")
    auto_schedule_delay_minutes = Column(Integer, nullable=False, default=60, server_default="60")
    interview_duration_target   = Column(Integer, nullable=False, default=15, server_default="15")
    focus_areas                 = Column(Text, nullable=True)  # JSON array of focus area strings
    auto_status_update_enabled  = Column(Boolean, nullable=False, default=False, server_default="false")
    auto_status_mapping_json    = Column(Text, nullable=True)  # JSON: {"strong_hire": "shortlisted", ...}
    require_consent             = Column(Boolean, nullable=False, default=True, server_default="true")
    evaluator_model_json        = Column(Text, nullable=True)  # JSON: {"strategy": "model_name", "technical": "...", "behavioral": "...", "copilot": "..."}
    created_at                  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at                  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant = relationship("Tenant", backref="recruiter_auto_trigger_config", uselist=False)


# ─── Screening Projects ──────────────────────────────────────────────────────

class ScreeningProject(Base):
    """Lightweight grouping of candidates for a specific hiring push.

    Tied to a RoleTemplate (JD) but not a full ATS requisition — supports
    per-project candidate pipelines without the overhead of req management.
    """
    __tablename__ = "screening_projects"

    id              = Column(Integer, primary_key=True, index=True)
    tenant_id       = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    role_template_id = Column(Integer, ForeignKey("role_templates.id", ondelete="CASCADE"), nullable=True, index=True)
    name            = Column(String(200), nullable=False)
    description     = Column(Text, nullable=True)
    status          = Column(String(20), nullable=False, default="draft", server_default="draft")  # draft/active/paused/closed
    must_ask_questions_json = Column(Text, nullable=True)  # JSON: [{"question": "...", "category": "...", "rationale": "..."}]
    created_by      = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    closed_at       = Column(DateTime(timezone=True), nullable=True)

    tenant         = relationship("Tenant", backref="screening_projects")
    role_template  = relationship("RoleTemplate", backref="screening_projects")
    creator        = relationship("User", foreign_keys=[created_by])
    project_candidates = relationship("ScreeningProjectCandidate", back_populates="project", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_screening_projects_tenant_status", "tenant_id", "status"),
    )


class ScreeningProjectCandidate(Base):
    """Per-project candidate entry with project-specific status.

    This decouples candidate pipeline status from the global ScreeningResult.status,
    allowing the same candidate to be in different stages across multiple projects.
    """
    __tablename__ = "screening_project_candidates"

    id                  = Column(Integer, primary_key=True, index=True)
    project_id          = Column(Integer, ForeignKey("screening_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id        = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    screening_result_id = Column(Integer, ForeignKey("screening_results.id", ondelete="SET NULL"), nullable=True)
    status              = Column(String(50), nullable=False, default="pending", server_default="pending")  # pending/shortlisted/rejected/in-review/hired
    added_by            = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    added_at            = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    project           = relationship("ScreeningProject", back_populates="project_candidates")
    candidate         = relationship("Candidate", backref="project_memberships")
    screening_result  = relationship("ScreeningResult", backref="project_memberships")
    adder             = relationship("User", foreign_keys=[added_by])

    __table_args__ = (
        UniqueConstraint("project_id", "candidate_id", name="uq_project_candidate"),
        Index("ix_project_candidates_project_status", "project_id", "status"),
    )


# ─── ATS Connectors ──────────────────────────────────────────────────────────

class ATSConnection(Base):
    """Per-tenant ATS integration configuration (Greenhouse, Lever, Workday, etc.)."""
    __tablename__ = "ats_connections"

    id                  = Column(Integer, primary_key=True, index=True)
    tenant_id           = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    provider            = Column(String(30), nullable=False)  # greenhouse / lever / workday / generic
    label               = Column(String(100), nullable=False)  # human-friendly name
    api_key             = Column(Text, nullable=True)  # encrypted at rest by caller
    api_secret          = Column(Text, nullable=True)
    base_url            = Column(String(500), nullable=True)  # override for self-hosted
    webhook_url         = Column(String(500), nullable=True)  # inbound webhook endpoint
    webhook_secret      = Column(Text, nullable=True)  # HMAC secret for inbound verification (enc:v1 ciphertext)
    is_active           = Column(Boolean, nullable=False, default=True, server_default="true")
    sync_direction      = Column(String(10), nullable=False, default="push", server_default="push")  # push / pull / bidirectional
    status_mapping_json = Column(Text, nullable=True)  # JSON: {"shortlisted": "Greenhouse:active", ...}
    last_sync_at        = Column(DateTime(timezone=True), nullable=True)
    last_sync_status    = Column(String(20), nullable=True)  # success / failed / partial
    last_error          = Column(Text, nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant = relationship("Tenant", backref="ats_connections")

    __table_args__ = (
        Index("ix_ats_connections_tenant_provider", "tenant_id", "provider"),
    )


class ATSSyncLog(Base):
    """Record of a single ATS sync operation (push or pull)."""
    __tablename__ = "ats_sync_logs"

    id                  = Column(Integer, primary_key=True, index=True)
    connection_id       = Column(Integer, ForeignKey("ats_connections.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id           = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    direction           = Column(String(10), nullable=False)  # push / pull
    entity_type         = Column(String(30), nullable=False)  # candidate_status / requisition / application
    entity_id           = Column(String(100), nullable=True)  # external ID
    candidate_id        = Column(Integer, ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True)
    screening_result_id = Column(Integer, ForeignKey("screening_results.id", ondelete="SET NULL"), nullable=True)
    payload             = Column(Text, nullable=True)  # JSON: what was sent/received
    response_status     = Column(Integer, nullable=True)
    response_body       = Column(Text, nullable=True)
    success             = Column(Boolean, nullable=False, default=False)
    error_message       = Column(Text, nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    connection = relationship("ATSConnection", backref="sync_logs")

    __table_args__ = (
        Index("ix_ats_sync_logs_conn_created", "connection_id", "created_at"),
    )


# ─── Interview Templates ─────────────────────────────────────────────────────

class InterviewTemplate(Base):
    """Reusable interview question template that can be attached to ScreeningProjects.

    Templates contain must-ask questions that are merged into the AI-generated
    interview strategy at session creation time.
    """
    __tablename__ = "interview_templates"

    id              = Column(Integer, primary_key=True, index=True)
    tenant_id       = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id      = Column(Integer, ForeignKey("screening_projects.id", ondelete="CASCADE"), nullable=True, index=True)
    name            = Column(String(200), nullable=False)
    description     = Column(Text, nullable=True)
    questions_json  = Column(Text, nullable=False)  # JSON: [{"question": "...", "category": "...", "rationale": "...", "sequence_number": 1}]
    is_active       = Column(Boolean, nullable=False, default=True, server_default="true")
    created_by      = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tenant  = relationship("Tenant", backref="interview_templates")
    project = relationship("ScreeningProject", backref="interview_templates")
    creator = relationship("User", foreign_keys=[created_by])

    __table_args__ = (
        Index("ix_interview_templates_tenant_project", "tenant_id", "project_id"),
    )


# ─── Scoring cache for batch consistency ──────────────────────────────────────

class ScreeningCache(Base):
    """Caches screening results by content hash for batch scoring consistency.

    Ensures the same resume+JD combination always produces identical scores,
    which is critical for audit compliance and reproducibility.
    """
    __tablename__ = "screening_cache"

    id          = Column(Integer, primary_key=True, index=True)
    cache_key   = Column(String(128), unique=True, index=True, nullable=False)
    tenant_id   = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    result_json = Column(Text, nullable=False)
    resume_hash = Column(String(32), index=True, nullable=False)
    jd_hash     = Column(String(32), index=True, nullable=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_screening_cache_tenant_created", "tenant_id", "created_at"),
    )


# ─── Compliance & AI Governance ───────────────────────────────────────────────

class CandidateConsent(Base):
    """
    Records candidate consent for AI-based processing (GDPR Art. 6/22, EU AI Act).
    One row per (candidate, consent_type).
    """
    __tablename__ = "candidate_consents"

    id             = Column(Integer, primary_key=True, index=True)
    tenant_id      = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id   = Column(Integer, ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True)
    consent_type   = Column(String(50), nullable=False)  # ai_screening / voice_interview / video_analysis / data_retention
    consented      = Column(Boolean, nullable=False, default=False)
    consented_at   = Column(DateTime(timezone=True), nullable=True)
    consent_version = Column(String(20), nullable=False, default="1.0")
    consent_ip     = Column(String(64), nullable=True)
    withdrawal_at  = Column(DateTime(timezone=True), nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("candidate_id", "consent_type", name="uq_candidate_consent_type"),
        Index("ix_candidate_consent_tenant", "tenant_id", "candidate_id"),
    )


class AIDecisionLog(Base):
    """
    Auditable record of every AI screening decision (GDPR Art. 22, EU AI Act
    Art. 13/14). Captures model, prompt version, guardrail activity, and the
    inputs-to-output chain so decisions can be explained and audited.
    """
    __tablename__ = "ai_decision_logs"

    # INTEGER on SQLite so the PK autoincrements; BIGINT on Postgres.
    id                     = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True, index=True)
    tenant_id              = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    screening_result_id    = Column(Integer, ForeignKey("screening_results.id", ondelete="SET NULL"), nullable=True, index=True)
    candidate_id           = Column(Integer, ForeignKey("candidates.id", ondelete="SET NULL"), nullable=True, index=True)
    model_name             = Column(String(100), nullable=True)
    model_version          = Column(String(50), nullable=True)
    prompt_template_version = Column(String(20), nullable=True)
    prompt_hash            = Column(String(64), nullable=True)
    raw_llm_output         = Column(Text, nullable=True)
    guardrails_triggered   = Column(JSON, nullable=True, default=list)
    fallback_used          = Column(Boolean, nullable=False, default=False)
    deterministic_score    = Column(Float, nullable=True)
    llm_score              = Column(Float, nullable=True)
    final_score            = Column(Float, nullable=True)
    created_at             = Column(DateTime(timezone=True), server_default=func.now())
    decision_type          = Column(String(32), nullable=True, index=True)
    scoring_weights        = Column(JSON, nullable=True)
    algorithm_version      = Column(String(32), nullable=True)
    actor_id               = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    recommendation         = Column(String(50), nullable=True)
    screening_decision_id  = Column(Integer, nullable=True, index=True)

    __table_args__ = (
        Index("ix_ai_decision_tenant_created", "tenant_id", "created_at"),
    )
    screening_decision = relationship(
        "ScreeningDecision",
        foreign_keys=lambda: [AIDecisionLog.screening_decision_id],
        primaryjoin="AIDecisionLog.screening_decision_id==ScreeningDecision.id",
    )


class IdempotencyKey(Base):
    """Stores processed idempotency keys to de-duplicate mutating requests."""
    __tablename__ = "idempotency_keys"

    key             = Column(String(128), primary_key=True)
    tenant_id       = Column(Integer, primary_key=True)
    endpoint        = Column(String(200), primary_key=True)
    request_fingerprint = Column(String(64), nullable=False)
    response_status = Column(Integer, nullable=True)
    response_body   = Column(JSON, nullable=True)
    state           = Column(String(20), nullable=False, default="completed")
    owner_token     = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    expires_at      = Column(DateTime(timezone=True), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("key", "tenant_id", "endpoint", name="uq_idempotency_key_tenant_endpoint"),
        Index("ix_idempotency_tenant", "tenant_id"),
    )


class TrainingRun(Base):
    __tablename__ = "training_runs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True)
    state = Column(String(32), nullable=False, default="idle")
    model_name = Column(String(200), nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    role = Column(String(64), primary_key=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)
    detail = Column(String(200), nullable=True)


class BreachLog(Base):
    """Data breach incident register (GDPR Art. 33)."""
    __tablename__ = "breach_logs"

    id                        = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True, index=True)
    tenant_id                 = Column(Integer, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    detected_at               = Column(DateTime(timezone=True), server_default=func.now())
    breach_type               = Column(String(100), nullable=False)
    affected_records_count    = Column(Integer, nullable=True)
    affected_data_categories  = Column(JSON, nullable=True)
    reported_to_authority_at  = Column(DateTime(timezone=True), nullable=True)
    notified_subjects_at      = Column(DateTime(timezone=True), nullable=True)
    description               = Column(Text, nullable=True)
    remediation_steps         = Column(Text, nullable=True)
    created_by                = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at                = Column(DateTime(timezone=True), server_default=func.now())


class DataRetentionPolicy(Base):
    """Per-tenant data retention configuration (GDPR Art. 5(1)(e))."""
    __tablename__ = "data_retention_policies"

    id                                = Column(Integer, primary_key=True, index=True)
    tenant_id                         = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    candidate_retention_days          = Column(Integer, nullable=False, default=730)
    screening_result_retention_days   = Column(Integer, nullable=False, default=1095)
    voice_transcript_retention_days   = Column(Integer, nullable=False, default=365)
    ai_decision_log_retention_days    = Column(Integer, nullable=False, default=2555)
    created_at                        = Column(DateTime(timezone=True), server_default=func.now())
    updated_at                        = Column(DateTime(timezone=True), onupdate=func.now())


# ─── Analysis Queue (scalable job queue) ──────────────────────────────────────
# These models back the async analysis queue. They live here (rather than in
# services/queue_manager.py where the QueueManager logic lives) so that all ORM
# table definitions are colocated and discoverable by Alembic/tooling.
import uuid as _uuid


class AnalysisJob(Base):
    __tablename__ = 'analysis_jobs'

    id = Column(Uuid(as_uuid=True), primary_key=True, default=_uuid.uuid4)
    tenant_id = Column(Integer, ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    candidate_id = Column(Integer, ForeignKey('candidates.id', ondelete='SET NULL'), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id', ondelete='SET NULL'), nullable=True)

    job_type = Column(String(50), nullable=False, default='resume_screening', index=True)
    resume_hash = Column(String(64), nullable=False, index=True)
    jd_hash = Column(String(64), nullable=False, index=True)
    input_hash = Column(String(64), nullable=False, unique=True)

    status = Column(String(20), nullable=False, default='queued', index=True)
    priority = Column(Integer, nullable=False, default=5, index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    queued_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True, index=True)

    worker_id = Column(String(100), nullable=True, index=True)
    worker_heartbeat = Column(DateTime(timezone=True), nullable=True)

    artifact_id = Column(Uuid(as_uuid=True), ForeignKey('analysis_artifacts.id', ondelete='SET NULL'), nullable=True)

    processing_stage = Column(String(50), nullable=True)
    progress_percent = Column(Integer, nullable=False, default=0)
    estimated_completion = Column(DateTime(timezone=True), nullable=True)

    error_message = Column(Text, nullable=True)
    error_type = Column(String(100), nullable=True)
    error_stack_trace = Column(Text, nullable=True)
    error_context = Column(JSON, nullable=True)
    last_error_category = Column(String(40), nullable=True)
    last_error_at = Column(DateTime(timezone=True), nullable=True)

    result_id = Column(Uuid(as_uuid=True), ForeignKey('analysis_results.id', ondelete='SET NULL'), nullable=True)
    job_config = Column(JSON, nullable=True)

    leased_until = Column(DateTime(timezone=True), nullable=True, index=True)
    content_hash = Column(String(64), nullable=True, index=True)


class AnalysisResult(Base):
    __tablename__ = 'analysis_results'

    id = Column(Uuid(as_uuid=True), primary_key=True, default=_uuid.uuid4)
    job_id = Column(Uuid(as_uuid=True), ForeignKey('analysis_jobs.id', ondelete='CASCADE'), nullable=False, unique=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)
    candidate_id = Column(Integer, ForeignKey('candidates.id', ondelete='SET NULL'), nullable=True, index=True)

    fit_score = Column(Integer, nullable=False)
    final_recommendation = Column(String(50), nullable=False)
    risk_level = Column(String(20), nullable=True)

    analysis_data = Column(JSON, nullable=False)
    parsed_resume = Column(JSON, nullable=False)
    parsed_jd = Column(JSON, nullable=False)

    narrative_status = Column(String(20), nullable=False, default='pending')
    narrative_data = Column(JSON, nullable=True)
    narrative_generated_at = Column(DateTime(timezone=True), nullable=True)
    ai_enhanced = Column(Boolean, nullable=False, default=False)

    analysis_version = Column(String(20), nullable=False, default='1.0')
    model_used = Column(String(100), nullable=True)
    processing_time_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    analysis_quality = Column(String(20), nullable=False, default='medium')
    confidence_score = Column(Float, nullable=True)

    artifact_id = Column(Uuid(as_uuid=True), ForeignKey('analysis_artifacts.id', ondelete='SET NULL'), nullable=True)


class DeadLetterJob(Base):
    """Dead letter queue - stores jobs that failed after all retries."""
    __tablename__ = 'dead_letter_jobs'

    id = Column(Uuid(as_uuid=True), primary_key=True, default=_uuid.uuid4)
    original_job_id = Column(Uuid(as_uuid=True), nullable=False, unique=True, index=True)
    tenant_id = Column(Integer, nullable=False, index=True)
    candidate_id = Column(Integer, nullable=True)
    user_id = Column(Integer, nullable=True)

    job_type = Column(String(50), nullable=False)
    resume_hash = Column(String(64), nullable=False)
    jd_hash = Column(String(64), nullable=False)
    input_hash = Column(String(64), nullable=False)

    job_config = Column(JSON, nullable=True)
    artifact_id = Column(Uuid(as_uuid=True), ForeignKey('analysis_artifacts.id', ondelete='SET NULL'), nullable=True)

    failure_reason = Column(Text, nullable=False)
    failure_type = Column(String(100), nullable=True)
    last_error_message = Column(Text, nullable=True)
    last_error_trace = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)

    original_created_at = Column(DateTime(timezone=True), nullable=False)
    failed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    reprocessed_at = Column(DateTime(timezone=True), nullable=True)

    status = Column(String(20), nullable=False, default='pending', index=True)
    reprocessed_job_id = Column(Uuid(as_uuid=True), ForeignKey('analysis_jobs.id', ondelete='SET NULL'), nullable=True)


class AnalysisArtifact(Base):
    __tablename__ = 'analysis_artifacts'

    id = Column(Uuid(as_uuid=True), primary_key=True, default=_uuid.uuid4)
    tenant_id = Column(Integer, ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)

    resume_filename = Column(String(255), nullable=False)
    resume_size_bytes = Column(Integer, nullable=False)
    resume_hash = Column(String(64), nullable=False, index=True)
    resume_mime_type = Column(String(100), nullable=True)

    jd_filename = Column(String(255), nullable=True)
    jd_size_bytes = Column(Integer, nullable=True)
    jd_hash = Column(String(64), nullable=False, index=True)
    jd_text = Column(Text, nullable=False)

    resume_storage_path = Column(String(500), nullable=True)
    resume_storage_bucket = Column(String(100), nullable=True)

    resume_text = Column(Text, nullable=False)
    resume_text_length = Column(Integer, nullable=False)

    parsed_resume_cache = Column(JSON, nullable=True)
    parsed_jd_cache = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)
    access_count = Column(Integer, nullable=False, default=0)
    last_accessed_at = Column(DateTime(timezone=True), nullable=True)


class SavedAnalyticsView(Base):
    """User-pinned analytics filters and explore slice preferences."""
    __tablename__ = "saved_analytics_views"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    view_type = Column(String(20), nullable=False, default="explore")
    slice = Column(String(30), nullable=True)
    filters = Column(JSON, nullable=False, default=dict)
    is_default = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class SavedReport(Base):
    """Custom report definition saved by a user."""
    __tablename__ = "saved_reports"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    definition = Column(JSON, nullable=False, default=dict)
    shared_with_tenant = Column(Boolean, nullable=False, default=False)
    share_token = Column(String(64), nullable=True, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class ScheduledReport(Base):
    """Scheduled delivery of a saved report via email."""
    __tablename__ = "scheduled_reports"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    saved_report_id = Column(Integer, ForeignKey("saved_reports.id", ondelete="CASCADE"), nullable=False, index=True)
    schedule = Column(String(20), nullable=False)
    recipients = Column(JSON, nullable=False, default=list)
    enabled = Column(Boolean, nullable=False, default=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class JobMetrics(Base):
    __tablename__ = 'job_metrics'

    id = Column(Uuid(as_uuid=True), primary_key=True, default=_uuid.uuid4)
    job_id = Column(Uuid(as_uuid=True), ForeignKey('analysis_jobs.id', ondelete='CASCADE'), nullable=False, index=True)
    tenant_id = Column(Integer, ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False, index=True)

    queue_wait_time_ms = Column(Integer, nullable=True)
    parsing_time_ms = Column(Integer, nullable=True)
    llm_time_ms = Column(Integer, nullable=True)
    narrative_time_ms = Column(Integer, nullable=True)
    total_time_ms = Column(Integer, nullable=False)

    llm_tokens_input = Column(Integer, nullable=True)
    llm_tokens_output = Column(Integer, nullable=True)
    llm_calls_count = Column(Integer, nullable=True)
    memory_peak_mb = Column(Integer, nullable=True)

    parsing_confidence = Column(Float, nullable=True)
    analysis_confidence = Column(Float, nullable=True)
    json_parse_retries = Column(Integer, nullable=False, default=0)

    stage_timings = Column(JSON, nullable=True)

    error_stage = Column(String(50), nullable=True)
    retry_attempts = Column(Integer, nullable=False, default=0)

    worker_id = Column(String(100), nullable=True)
    worker_version = Column(String(50), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

