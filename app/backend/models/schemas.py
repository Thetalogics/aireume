from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator
from typing import List, Optional, Dict, Any, Union
from datetime import datetime


# ─── Candidate deduplication & re-analysis ────────────────────────────────────

class DuplicateCandidateInfo(BaseModel):
    """Returned when an uploaded resume matches an existing candidate in the DB."""
    id:              int
    name:            Optional[str]  = None
    email:           Optional[str]  = None
    current_role:    Optional[str]  = None
    current_company: Optional[str]  = None
    total_years_exp: Optional[float] = None
    skills_snapshot: List[str]      = []
    result_count:    int            = 0
    last_analyzed:   Optional[str]  = None  # ISO format datetime string
    profile_quality: Optional[str]  = None


class AnalyzeJdRequest(BaseModel):
    """Body for POST /api/candidates/{id}/analyze-jd (no file upload needed)."""
    job_description: Optional[str] = None
    requisition_id: Optional[int] = None
    scoring_weights: Optional[Dict[str, float]] = None

    @model_validator(mode='after')
    def require_jd_or_requisition(self):
        if not self.job_description and not self.requisition_id:
            raise ValueError('job_description or requisition_id is required')
        return self

class ProficiencySkill(BaseModel):
    """A skill with an optional proficiency level."""
    skill: str
    proficiency: Optional[str] = None  # basic | intermediate | advanced | expert


class RescoreRequest(BaseModel):
    """Body for POST /api/analyze/{result_id}/rescore — override skill classification."""
    required_skills: List[Any]  # List[str] or List[ProficiencySkill-like dicts]
    nice_to_have_skills: List[Any] = []


# ─── Core analysis ────────────────────────────────────────────────────────────

class EmploymentGap(BaseModel):
    start_date: str
    end_date: str
    duration_months: int
    severity: Optional[str] = None   # negligible | minor | moderate | critical


class RiskSignal(BaseModel):
    type: str
    description: str
    severity: Optional[str] = None   # low | medium | high


class SkillMatchBreakdown(BaseModel):
    """Nested breakdown for skill_match dimension with confidence metadata."""
    score: Optional[int] = 0
    confidence_weighted: Optional[bool] = False
    avg_confidence: Optional[float] = 1.0


class ExperienceMatchBreakdown(BaseModel):
    """Nested breakdown for experience_match dimension with year details."""
    score: Optional[int] = 0
    actual_years: Optional[float] = None
    required_years: Optional[float] = None
    explanation: Optional[str] = None


class ScoreBreakdown(BaseModel):
    # Backward-compat fields (always populated)
    skill_match:      Optional[SkillMatchBreakdown] = Field(default_factory=SkillMatchBreakdown)
    experience_match: Optional[Union[int, ExperienceMatchBreakdown]] = 0
    stability:        Optional[int] = 0   # mapped from timeline score
    education:        Optional[int] = 0
    # New LangGraph dimensions
    architecture:     Optional[int] = None
    domain_fit:       Optional[int] = None
    timeline:         Optional[int] = None
    risk_penalty:     Optional[int] = None


class ScoringCriteria(BaseModel):
    """Per-question scoring guidance for interviewer evaluation."""
    strong: str = ""
    adequate: str = ""
    weak: str = ""


class ProbeTarget(BaseModel):
    type: str = ""
    skill: str = ""
    company: str = ""
    hypothesis_id: str = ""
    probe_category: str = ""


class InterviewQuestion(BaseModel):
    """Single interview question with evaluation guidance."""
    text: str
    spoken_text: Optional[str] = None
    intent: Optional[str] = None
    probe_target: Optional[ProbeTarget] = None
    what_to_listen_for: List[str] = []
    follow_ups: List[str] = []
    follow_up_intents: List[str] = []
    scoring_criteria: Optional[ScoringCriteria] = None

    def spoken_line(self) -> str:
        return (self.spoken_text or self.text or "").strip()

class CandidateBriefing(BaseModel):
    """Pre-interview snapshot for the recruiter."""
    profile_snapshot: str = ""
    strengths_to_confirm: List[str] = []
    areas_to_probe: List[str] = []
    context_notes: List[str] = []

class InterviewQuestions(BaseModel):
    technical_questions: List[InterviewQuestion] = []
    behavioral_questions: List[InterviewQuestion] = []
    culture_fit_questions: List[InterviewQuestion] = []
    experience_deep_dive_questions: List[InterviewQuestion] = []
    candidate_briefing: Optional[CandidateBriefing] = None

    @field_validator('technical_questions', 'behavioral_questions', 'culture_fit_questions', 'experience_deep_dive_questions', mode='before')
    @classmethod
    def coerce_questions(cls, v):
        """Backward-compatible: convert old str items and new dict/object items."""
        if not isinstance(v, list):
            return []
        result = []
        for item in v:
            if isinstance(item, str):
                result.append(InterviewQuestion(text=item))
            elif isinstance(item, dict):
                # Handle dicts — ensure 'text' key exists
                if 'text' not in item:
                    # Old format or malformed — try to salvage
                    result.append(InterviewQuestion(text=str(item)))
                else:
                    result.append(InterviewQuestion(**{k: v for k, v in item.items() if k in InterviewQuestion.model_fields}))
            elif isinstance(item, InterviewQuestion):
                result.append(item)
            else:
                result.append(InterviewQuestion(text=str(item)))
        return result


class ScoringWeights(BaseModel):
    # Updated defaults to match the new 7-dimension formula
    skills:       float = 0.30
    experience:   float = 0.20
    architecture: float = 0.15
    education:    float = 0.10
    timeline:     float = 0.10
    domain:       float = 0.10
    risk:         float = 0.15


class ExplainabilityDetail(BaseModel):
    skill_rationale:      Optional[str] = None
    experience_rationale: Optional[str] = None
    education_rationale:  Optional[str] = None
    timeline_rationale:   Optional[str] = None
    overall_rationale:    Optional[str] = None


class AnalysisResponse(BaseModel):
    model_config = {"extra": "ignore"}   # silently drop any LLM-produced extra keys

    # ── Core backward-compat fields ──
    fit_score:            Optional[int] = None  # null = "Pending" state
    job_role:             Optional[str] = None
    strengths:            List[str] = []
    weaknesses:           List[str] = []
    employment_gaps:      List[Any] = []
    education_analysis:   Optional[str] = None
    risk_signals:         List[Any] = []
    final_recommendation: str = "Pending"       # Shortlist | Consider | Reject | Pending
    score_breakdown:      Optional[ScoreBreakdown] = None
    matched_skills:       Optional[List[str]] = []
    missing_skills:       Optional[List[str]] = []
    risk_level:           Optional[str] = "Low"
    interview_questions:  Optional[InterviewQuestions] = None
    required_skills_count: Optional[int] = 0
    result_id:            Optional[int] = None
    candidate_id:         Optional[int] = None
    candidate_name:       Optional[str] = None
    requisition_id:       Optional[int] = None
    parse_confidence:     Optional[Dict[str, Any]] = None
    skill_evidence:       Optional[List[Dict[str, Any]]] = None
    work_experience:      Optional[List[Any]] = []
    contact_info:         Optional[Dict[str, Any]] = None
    certifications:       Optional[List[str]] = []
    languages:            Optional[List[dict]] = []
    professional_summary: Optional[str] = ""
    # ── New hybrid pipeline fields ──
    jd_analysis:              Optional[Dict[str, Any]] = None
    candidate_profile:        Optional[Dict[str, Any]] = None
    skill_analysis:           Optional[Dict[str, Any]] = None
    edu_timeline_analysis:    Optional[Dict[str, Any]] = None
    explainability:           Optional[ExplainabilityDetail] = None
    recommendation_rationale: Optional[str] = None
    adjacent_skills:          Optional[List[str]] = []
    pipeline_errors:          Optional[List[str]] = []
    # ── Production hardening fields ──
    analysis_quality:   Optional[str] = None   # high | medium | low
    narrative_pending:  Optional[bool] = False  # True if LLM timed out, Python scores still valid
    duplicate_candidate: Optional[DuplicateCandidateInfo] = None
    # ── Deterministic engine fields ──
    deterministic_score:  Optional[int] = None
    decision_explanation: Optional[Dict[str, Any]] = None
    jd_domain:            Optional[Dict[str, Any]] = None
    candidate_domain:     Optional[Dict[str, Any]] = None
    eligibility:          Optional[Dict[str, Any]] = None
    deterministic_features: Optional[Dict[str, Any]] = None


class BatchAnalysisResult(BaseModel):
    rank: int
    filename: str
    result: AnalysisResponse


class BatchFailedItem(BaseModel):
    """Represents a failed file in batch processing."""
    filename: str
    error: str


class BatchAnalysisResponse(BaseModel):
    results: List[BatchAnalysisResult]
    failed: List[BatchFailedItem] = []
    total: int
    successful: int = 0
    failed_count: int = 0


class BatchStreamEvent(BaseModel):
    """SSE event payload for the /api/analyze/batch-stream endpoint."""
    event: str  # "processing", "result", "failed", "done"
    index: int
    total: int
    filename: Optional[str] = None
    result: Optional[dict] = None
    screening_result_id: Optional[int] = None
    error: Optional[str] = None
    successful: Optional[int] = None
    failed_count: Optional[int] = None


# ─── Auth ─────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    company_name: str
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str
    tenant_slug: Optional[str] = None
    mfa_code: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: Dict[str, Any]
    tenant: Dict[str, Any]


class RefreshRequest(BaseModel):
    refresh_token: Optional[str] = None


class UserOut(BaseModel):
    id: int
    email: str
    role: str
    tenant_id: int

    class Config:
        from_attributes = True


# ─── Screening results ────────────────────────────────────────────────────────

class ScreeningResultResponse(BaseModel):
    id: int
    timestamp: datetime
    analysis_result: dict
    status: Optional[str] = "pending"
    candidate_id: Optional[int] = None

    class Config:
        from_attributes = True


class StatusUpdate(BaseModel):
    status: str  # pending / shortlisted / rejected / in-review / hired


# ─── Candidates ───────────────────────────────────────────────────────────────

class CandidateNameUpdate(BaseModel):
    name: str


class CandidateOut(BaseModel):
    id: int
    name: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    created_at: datetime
    result_count: Optional[int] = 0

    class Config:
        from_attributes = True


# ─── Templates ───────────────────────────────────────────────────────────────

class TemplateCreate(BaseModel):
    name: str
    jd_text: str
    scoring_weights: Optional[Dict[str, float]] = None
    tags: Optional[str] = None
    required_skills_override: Optional[str] = None
    nice_to_have_skills_override: Optional[str] = None


class TemplateUpdate(BaseModel):
    """Partial update for templates — only supplied fields are changed."""
    name: Optional[str] = None
    jd_text: Optional[str] = None
    scoring_weights: Optional[Dict[str, float]] = None
    tags: Optional[str] = None
    required_skills_override: Optional[str] = None
    nice_to_have_skills_override: Optional[str] = None


class TemplateOut(BaseModel):
    id: int
    name: str
    jd_text: str
    scoring_weights: Optional[str] = None
    tags: Optional[str] = None
    required_skills_override: Optional[str] = None
    nice_to_have_skills_override: Optional[str] = None
    created_by: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ─── Email generation ─────────────────────────────────────────────────────────

class EmailGenRequest(BaseModel):
    candidate_id: int
    type: str   # shortlist / rejection / screening_call


class EmailGenResponse(BaseModel):
    subject: str
    body: str


# ─── JD URL extraction ────────────────────────────────────────────────────────

class JdUrlRequest(BaseModel):
    url: str


class JdUrlResponse(BaseModel):
    jd_text: str
    source_url: str


# ─── Team ─────────────────────────────────────────────────────────────────────

class InviteRequest(BaseModel):
    email: str
    role: str = "recruiter"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v):
        allowed = {"admin", "recruiter", "viewer", "hiring_manager", "ta_lead"}
        if v not in allowed:
            raise ValueError(f"role must be one of {allowed}")
        return v


class CommentCreate(BaseModel):
    text: str


class CommentOut(BaseModel):
    id: int
    text: str
    created_at: datetime
    author_email: Optional[str] = None

    class Config:
        from_attributes = True


# ─── Compare ─────────────────────────────────────────────────────────────────

class CompareRequest(BaseModel):
    candidate_ids: List[int]


class CandidateSkillCompareRequest(BaseModel):
    candidate_ids: List[int]
    jd_analysis: Optional[Dict] = None
    screening_result_id: Optional[int] = None
    team_gaps: Optional[List[str]] = []


# ─── Training ────────────────────────────────────────────────────────────────

class LabelRequest(BaseModel):
    screening_result_id: int
    outcome: str    # hired / rejected
    feedback: Optional[str] = ""


class TrainingStatusResponse(BaseModel):
    labeled_count: int
    trained: bool
    model_name: Optional[str] = None
    last_trained: Optional[datetime] = None


# ─── Transcript analysis ──────────────────────────────────────────────────────

class JdAlignmentItem(BaseModel):
    requirement: str
    demonstrated: bool
    evidence: Optional[str] = None


class TranscriptAnalysisResult(BaseModel):
    fit_score: int
    technical_depth: int
    communication_quality: int
    jd_alignment: List[JdAlignmentItem]
    strengths: List[str]
    areas_for_improvement: List[str]
    bias_note: str
    recommendation: str   # proceed / hold / reject


class TranscriptAnalysisResponse(BaseModel):
    id: int
    candidate_id: Optional[int] = None
    candidate_name: Optional[str] = None
    role_template_id: Optional[int] = None
    role_template_name: Optional[str] = None
    source_platform: Optional[str] = None
    analysis_result: TranscriptAnalysisResult
    created_at: datetime

    class Config:
        from_attributes = True


class TranscriptAnalysisListItem(BaseModel):
    id: int
    candidate_id: Optional[int] = None
    candidate_name: Optional[str] = None
    role_template_id: Optional[int] = None
    role_template_name: Optional[str] = None
    source_platform: Optional[str] = None
    fit_score: Optional[int] = None
    recommendation: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ─── Subscription ───────────────────────────────────────────────────────────────

class PlanInfo(BaseModel):
    """Subscription plan information."""
    id: int
    name: str
    display_name: str
    description: str
    price_monthly: int
    price_yearly: int
    currency: str
    features: List[str]
    limits: Dict[str, Any]


class UsageStats(BaseModel):
    """Current usage statistics for a tenant."""
    analyses_used: int
    analyses_limit: int
    storage_used_mb: float
    storage_limit_gb: int
    team_members_count: int
    team_members_limit: int
    percent_used: float


class SubscriptionResponse(BaseModel):
    """Full subscription response for current tenant."""
    current_plan: PlanInfo
    status: str  # active, trialing, cancelled, past_due
    billing_cycle: str  # monthly, yearly
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    price: int
    usage: UsageStats
    available_plans: List[PlanInfo]
    days_until_reset: int


# ── Interview Evaluation Schemas ──────────────────────────────────────

class EvaluationUpsert(BaseModel):
    """Request body for creating/updating a question evaluation."""
    question_category: str      # technical / behavioral / culture_fit
    question_index: int         # 0-based index within category
    rating: Optional[str] = None       # strong / adequate / weak
    notes: Optional[str] = None

    @field_validator('question_category')
    @classmethod
    def validate_category(cls, v):
        allowed = {'technical', 'behavioral', 'culture_fit', 'experience_deep_dive'}
        if v not in allowed:
            raise ValueError(f'question_category must be one of {allowed}')
        return v

    @field_validator('rating')
    @classmethod
    def validate_rating(cls, v):
        if v is not None:
            allowed = {'strong', 'adequate', 'weak'}
            if v not in allowed:
                raise ValueError(f'rating must be one of {allowed}')
        return v

class EvaluationOut(BaseModel):
    """Response for a single evaluation."""
    id: int
    question_category: str
    question_index: int
    rating: Optional[str] = None
    notes: Optional[str] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

class ScoreFeedbackRequest(BaseModel):
    """Recruiter feedback on whether the AI fit score felt accurate."""
    sentiment: str  # right | high | low

    @field_validator('sentiment')
    @classmethod
    def validate_sentiment(cls, v):
        allowed = {'right', 'high', 'low'}
        if v not in allowed:
            raise ValueError(f'sentiment must be one of: {", ".join(sorted(allowed))}')
        return v


class DebriefRequest(BaseModel):
    """Request body for LLM debrief generation."""
    conversation_summary: str
    recommendation: Optional[str] = None  # strong_hire | lean_hire | no_decision | lean_no_hire | strong_no_hire

    @field_validator('conversation_summary')
    @classmethod
    def validate_summary_length(cls, v):
        if len(v.strip()) < 20:
            raise ValueError('Conversation summary must be at least 20 characters')
        return v.strip()

    @field_validator('recommendation')
    @classmethod
    def validate_recommendation(cls, v):
        if v is None:
            return v
        allowed = {'strong_hire', 'lean_hire', 'no_decision', 'lean_no_hire', 'strong_no_hire'}
        if v not in allowed:
            raise ValueError(f'recommendation must be one of {allowed}')
        return v


class DebriefContent(BaseModel):
    """Structured LLM-generated debrief content."""
    overview: str = ""
    strengths: str = ""
    concerns: str = ""
    recommendation_rationale: str = ""


class DebriefResponse(BaseModel):
    """Response from debrief generation endpoint."""
    debrief: DebriefContent
    recruiter_score: int
    recommendation: str  # "Advance" | "Hold" | "Reject"


class OverallAssessmentUpsert(BaseModel):
    """Request body for overall recruiter assessment."""
    overall_assessment: str
    recruiter_recommendation: Optional[str] = None   # advance / hold / reject

    @field_validator('recruiter_recommendation')
    @classmethod
    def validate_recommendation(cls, v):
        if v is not None:
            allowed = {'advance', 'hold', 'reject'}
            if v not in allowed:
                raise ValueError(f'recruiter_recommendation must be one of {allowed}')
        return v

class EvaluatorInfo(BaseModel):
    """Attribution info for a single question evaluation."""
    user_id: int
    email: str
    rating: str
    question_index: int
    notes: Optional[str] = None


class ScorecardDimension(BaseModel):
    """Summary of one evaluation dimension."""
    category: str
    total_questions: int = 0
    evaluated_count: int = 0
    strong_count: int = 0
    adequate_count: int = 0
    weak_count: int = 0
    key_notes: List[str] = []
    evaluators: List[EvaluatorInfo] = []  # Who rated what

class ScorecardOut(BaseModel):
    """HM-facing scorecard aggregating evaluations."""
    candidate_name: str
    role_title: str
    fit_score: Optional[int] = None
    recommendation: Optional[str] = None
    evaluator_email: str
    evaluated_at: Optional[datetime] = None
    technical_summary: ScorecardDimension
    behavioral_summary: ScorecardDimension
    culture_fit_summary: ScorecardDimension
    experience_deep_dive_summary: ScorecardDimension
    overall_assessment: Optional[str] = None
    recruiter_recommendation: Optional[str] = None
    strengths_confirmed: List[str] = []
    concerns_identified: List[str] = []
    debrief: Optional[DebriefContent] = None
    recruiter_score: Optional[int] = None


# ─── Voice Screening ───────────────────────────────────────────────────────────

class VoiceTenantConfigUpdate(BaseModel):
    """Body for PUT /api/voice/settings — tenant updates their voice bot config."""
    bot_name: Optional[str] = None
    bot_voice_gender: Optional[str] = None  # male / female
    bot_voice_sample_url: Optional[str] = None
    outbound_phone_number: Optional[str] = None
    caller_id_name: Optional[str] = None
    business_hours_start: Optional[str] = None  # HH:MM
    business_hours_end: Optional[str] = None  # HH:MM
    allowed_days: Optional[List[int]] = None  # 1=Mon .. 7=Sun
    timezone: Optional[str] = None
    consent_script: Optional[str] = None  # null = use default
    interview_opening_script: Optional[str] = None
    use_custom_interview_opening: Optional[bool] = None
    company_about_blurb: Optional[str] = None
    greeting_style: Optional[str] = None  # professional / casual / friendly
    call_duration_min: Optional[int] = None
    call_duration_max: Optional[int] = None
    max_retries: Optional[int] = None
    retry_intervals: Optional[List[int]] = None  # hours
    escalation_contact_id: Optional[int] = None
    assessment_detail_level: Optional[str] = None  # brief / full
    auto_update_status: Optional[bool] = None
    follow_up_aggressiveness: Optional[str] = None  # low / medium / high
    auto_escalation_enabled: Optional[bool] = None
    auto_escalation_threshold: Optional[int] = None


class VoiceTenantConfigOut(BaseModel):
    """Response for GET /api/voice/settings."""
    id: int
    tenant_id: int
    bot_name: str
    bot_voice_gender: str
    bot_voice_sample_url: Optional[str] = None
    outbound_phone_number: Optional[str] = None
    caller_id_name: Optional[str] = None
    business_hours_start: str
    business_hours_end: str
    allowed_days: List[int]
    timezone: str
    consent_script: Optional[str] = None
    interview_opening_script: Optional[str] = None
    use_custom_interview_opening: bool = False
    company_about_blurb: Optional[str] = None
    greeting_style: str
    call_duration_min: int
    call_duration_max: int
    max_retries: int
    retry_intervals: List[int]
    escalation_contact_id: Optional[int] = None
    assessment_detail_level: str
    auto_update_status: bool
    follow_up_aggressiveness: str
    auto_escalation_enabled: bool = False
    auto_escalation_threshold: int = 70
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class InterviewOpeningSuggestRequest(BaseModel):
    """Body for POST /api/voice/settings/suggest-opening (admin only)."""
    company_about: Optional[str] = None
    tone: Optional[str] = "professional"


class InterviewOpeningSuggestResponse(BaseModel):
    script: str


class VoiceTranscriptEntryOut(BaseModel):
    """A single speaker turn in a transcript."""
    id: int
    session_id: int
    speaker: str  # bot / candidate
    text: str
    timestamp: datetime
    audio_url: Optional[str] = None

    model_config = {"from_attributes": True}


class VoiceScreeningSessionOut(BaseModel):
    """Response for a voice screening session."""
    id: int
    tenant_id: int
    candidate_id: int
    jd_id: Optional[int] = None
    phone_number: str
    direction: str  # outbound / inbound
    callback_of_id: Optional[int] = None
    status: str
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    transcript_json: Optional[str] = None
    assessment_json: Optional[str] = None
    duration_seconds: Optional[int] = None
    retry_count: int
    consent_recorded: bool
    call_sid: Optional[str] = None
    error_log: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    candidate_name: Optional[str] = None
    candidate_email: Optional[str] = None
    jd_title: Optional[str] = None
    call_count: Optional[int] = None
    match_score: Optional[int] = None
    interview_depth: Optional[str] = "quick"
    recruiter_session_id: Optional[str] = None
    recruiter_status: Optional[str] = None

    model_config = {"from_attributes": True}


class ScheduleVoiceCallRequest(BaseModel):
    """Body for POST /api/voice/schedule — recruiter schedules a screening call."""
    candidate_id: int
    jd_id: Optional[int] = None
    phone_number: str  # E.164
    scheduled_at: Optional[datetime] = None  # UTC; None = schedule immediately


class RescheduleVoiceCallRequest(BaseModel):
    """Body for POST /api/voice/sessions/{id}/reschedule."""
    phone_number: Optional[str] = None  # E.164; None = keep existing
    scheduled_at: Optional[datetime] = None  # UTC; None = schedule immediately
    jd_id: Optional[int] = None


class BulkCancelRequest(BaseModel):
    """Body for POST /api/voice/sessions/bulk-cancel."""
    session_ids: List[int]


class ScheduleVoiceCallResponse(BaseModel):
    """Response after scheduling a voice call."""
    session_id: int
    status: str
    scheduled_at: Optional[datetime] = None
    phone_number: str


class InterviewCreateRequest(BaseModel):
    """Body for POST /api/interviews/sessions — create a unified interview."""
    candidate_id: int
    jd_id: Optional[int] = None
    requisition_id: Optional[int] = None
    depth: str = "quick"
    screening_result_id: Optional[int] = None
    phone_number: Optional[str] = None
    scheduled_at: Optional[str] = None
    focus_areas: Optional[List[str]] = None
    duration_minutes: Optional[int] = Field(None, ge=5, le=60)

    @model_validator(mode='after')
    def require_role_context(self):
        if not self.jd_id and not self.requisition_id:
            raise ValueError('jd_id or requisition_id is required')
        return self

    @field_validator('depth')
    @classmethod
    def validate_depth(cls, v):
        allowed = {'quick', 'standard', 'deep'}
        if v not in allowed:
            raise ValueError(f'depth must be one of {allowed}')
        return v


# ─── AI Recruiter ───────────────────────────────────────────────────────────

class RecruiterSessionCreate(BaseModel):
    """Body for POST /api/recruiter/sessions — initiate an AI recruiter interview."""
    candidate_id: int
    jd_id: int
    screening_result_id: Optional[int] = None
    voice_session_id: Optional[int] = None
    trigger_type: Optional[str] = "manual"
    scheduled_at: Optional[str] = None
    timezone: Optional[str] = None
    duration_minutes: Optional[int] = None
    focus_areas: Optional[List[str]] = None
    phone_number: Optional[str] = None
    interview_config_json: Optional[Dict[str, Any]] = None

    @field_validator('trigger_type')
    @classmethod
    def validate_trigger_type(cls, v):
        allowed = {'manual', 'auto_pipeline', 're_interview'}
        if v not in allowed:
            raise ValueError(f'trigger_type must be one of {allowed}')
        return v


class RecruiterSessionOut(BaseModel):
    """Summary response for an AI recruiter interview session."""
    id: str
    tenant_id: int
    candidate_id: int
    jd_id: int
    screening_result_id: Optional[int] = None
    voice_session_id: Optional[int] = None
    trigger_type: str
    status: str
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    candidate_name: Optional[str] = None
    jd_title: Optional[str] = None
    scheduled_at: Optional[datetime] = None  # mirrored from voice_session
    phone_number: Optional[str] = None        # mirrored from voice_session

    model_config = {"from_attributes": True}


class RecruiterSessionDetail(RecruiterSessionOut):
    """Full session detail including strategy and configuration JSON."""
    interview_strategy_json: Optional[Dict[str, Any]] = None
    interview_config_json: Optional[Dict[str, Any]] = None


class RecruiterQuestionOut(BaseModel):
    """A single AI recruiter interview question with optional response/evaluation."""
    id: str
    session_id: str
    sequence_number: int
    category: str
    question_text: str
    question_context: Optional[str] = None
    candidate_response: Optional[str] = None
    response_duration_seconds: Optional[float] = None
    evaluation_json: Optional[Dict[str, Any]] = None
    is_follow_up: bool
    parent_question_id: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_validator('category')
    @classmethod
    def validate_category(cls, v):
        allowed = {'technical', 'behavioral', 'communication', 'cultural_fit', 'risk_validation', 'gap_probe', 'motivation'}
        if v not in allowed:
            raise ValueError(f'category must be one of {allowed}')
        return v


class RecruiterScorecardOut(BaseModel):
    """Full structured scorecard for an AI recruiter interview."""
    id: str
    session_id: str
    tenant_id: int
    candidate_id: int

    technical_score: Optional[int] = None
    technical_evidence: Optional[Dict[str, Any]] = None
    behavioral_score: Optional[int] = None
    behavioral_evidence: Optional[Dict[str, Any]] = None
    communication_score: Optional[int] = None
    communication_evidence: Optional[Dict[str, Any]] = None
    cultural_fit_score: Optional[int] = None
    cultural_fit_evidence: Optional[Dict[str, Any]] = None
    motivation_score: Optional[int] = None
    motivation_evidence: Optional[Dict[str, Any]] = None

    risk_signals_validated: Optional[Dict[str, Any]] = None
    gaps_explained: Optional[Dict[str, Any]] = None

    original_fit_score: Optional[int] = None
    adjusted_fit_score: Optional[int] = None
    adjustment_reasoning: Optional[str] = None

    overall_score: Optional[int] = None
    confidence_level: Optional[str] = None
    recommendation: Optional[str] = None
    recommendation_reasoning: Optional[str] = None
    executive_summary: Optional[str] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class RecruiterAutoTriggerConfigOut(BaseModel):
    """Per-tenant auto-trigger configuration response."""
    id: str
    tenant_id: int
    enabled: bool
    trigger_pipeline_stage: str
    min_fit_score_threshold: int
    max_fit_score_threshold: int
    auto_schedule_delay_minutes: int
    interview_duration_target: int
    focus_areas: Optional[List[str]] = None
    auto_status_update_enabled: bool = False
    auto_status_mapping_json: Optional[Dict[str, str]] = None
    require_consent: bool = True
    evaluator_model_json: Optional[Dict[str, str]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class RecruiterAutoTriggerConfigUpdate(BaseModel):
    """Partial update body for /api/recruiter/auto-trigger-config."""
    enabled: Optional[bool] = None
    trigger_pipeline_stage: Optional[str] = None
    min_fit_score_threshold: Optional[int] = None
    max_fit_score_threshold: Optional[int] = None
    auto_schedule_delay_minutes: Optional[int] = None
    interview_duration_target: Optional[int] = None
    focus_areas: Optional[List[str]] = None
    auto_status_update_enabled: Optional[bool] = None
    auto_status_mapping_json: Optional[Dict[str, str]] = None
    require_consent: Optional[bool] = None
    evaluator_model_json: Optional[Dict[str, str]] = None

    @field_validator('trigger_pipeline_stage')
    @classmethod
    def validate_stage(cls, v):
        if v is None:
            return v
        allowed = {'pending', 'shortlisted', 'in_review'}
        if v not in allowed:
            raise ValueError(f'trigger_pipeline_stage must be one of {allowed}')
        return v


class RecruiterAnalyticsOut(BaseModel):
    """Aggregated AI recruiter analytics for a tenant."""
    tenant_id: int
    total_sessions: int
    completed_sessions: int
    failed_sessions: int
    cancelled_sessions: int
    average_duration_seconds: Optional[float] = None
    average_overall_score: Optional[float] = None
    recommendation_distribution: Optional[Dict[str, int]] = None
    sessions_by_status: Optional[Dict[str, int]] = None
    score_distribution: Optional[Dict[str, int]] = None
    sessions_this_month: int = 0

    model_config = {"from_attributes": True}


class ScreeningAnalyticsOut(BaseModel):
    """Unified screening analytics payload."""
    period: str
    start_date: str
    end_date: str
    generated_at: str
    total_analyzed: int = 0
    avg_fit_score: float = 0
    recommendation_distribution: Dict[str, int] = {}
    recommendation_shortlist_rate: float = 0
    pipeline_shortlist_rate: float = 0
    hired_rate: float = 0
    analyses_by_day: List[Dict[str, Any]] = []
    top_skill_gaps: List[Dict[str, Any]] = []
    score_distribution: List[Dict[str, Any]] = []
    pass_through_rates: Dict[str, float] = {}
    jd_effectiveness: List[Dict[str, Any]] = []
    filters: Optional[Dict[str, Any]] = None
    comparison: Optional[Dict[str, Any]] = None


class AnalyticsHubOut(BaseModel):
    """Analytics hub interactive payload."""
    period: str
    start_date: str
    end_date: str
    generated_at: str
    filters: Dict[str, Any] = {}
    filter_options: Dict[str, Any] = {}
    attention: Dict[str, Any] = {}
    slices: Dict[str, Any] = {}


# ─── Screening Project schemas ───────────────────────────────────────────────

class ScreeningProjectCreate(BaseModel):
    name: str
    role_template_id: int
    description: Optional[str] = None
    status: str = "draft"

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        allowed = {"draft", "active", "paused", "closed"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v


class ScreeningProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    role_template_id: Optional[int] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v is None:
            return v
        allowed = {"draft", "active", "paused", "closed"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v


class ScreeningProjectOut(BaseModel):
    id: int
    tenant_id: int
    role_template_id: int
    name: str
    description: Optional[str] = None
    status: str
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    candidate_count: int = 0

    model_config = {"from_attributes": True}


class ScreeningProjectCandidateAdd(BaseModel):
    candidate_ids: List[int]
    screening_result_ids: Optional[Dict[int, int]] = None  # candidate_id -> screening_result_id


class ScreeningProjectCandidateStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        allowed = {"pending", "shortlisted", "rejected", "in-review", "hired"}
        if v not in allowed:
            raise ValueError(f"status must be one of {allowed}")
        return v


class ScreeningProjectCandidateOut(BaseModel):
    id: int
    project_id: int
    candidate_id: int
    screening_result_id: Optional[int] = None
    status: str
    added_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    candidate_name: Optional[str] = None
    candidate_email: Optional[str] = None
    fit_score: Optional[int] = None

    model_config = {"from_attributes": True}


# ─── Requisitions ─────────────────────────────────────────────────────────────

_REQ_STATUSES = {
    "draft", "intake_in_progress", "calibrated", "sourcing",
    "interviewing", "offer", "filled", "cancelled",
}
_INTAKE_STATUSES = {"draft", "pending_hm", "approved", "changes_requested"}
_PIPELINE_STATUSES = {"pending", "shortlisted", "rejected", "in-review", "hired"}
_SUBMISSION_STATUSES = {"none", "draft", "submitted", "reviewed"}
_HM_OUTCOMES = {"advance", "hold", "reject", "hire"}
_INTAKE_GATE_MODES = {"block", "warn", "optional"}
_SCREENING_MODES = {"requisition_required", "allow_ad_hoc"}
_HM_PIPELINE_PERMS = {"view_only", "shortlist_reject", "full"}


class RequisitionCreate(BaseModel):
    title: str
    jd_text: str
    description: Optional[str] = None
    client_name: Optional[str] = None
    headcount: Optional[int] = None
    location: Optional[str] = None
    scoring_weights: Optional[Dict[str, float]] = None
    tags: Optional[str] = None
    required_skills_override: Optional[List[Any]] = None
    nice_to_have_skills_override: Optional[List[Any]] = None
    primary_hiring_manager_id: Optional[int] = None
    hiring_manager_ids: Optional[List[int]] = None
    assigned_recruiter_id: Optional[int] = None
    opened_on_behalf_of_hm_id: Optional[int] = None
    routing_policy_json: Optional[Dict[str, Any]] = None
    status: str = "draft"

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v not in _REQ_STATUSES:
            raise ValueError(f"status must be one of {_REQ_STATUSES}")
        return v


class RequisitionUpdate(BaseModel):
    title: Optional[str] = None
    jd_text: Optional[str] = None
    description: Optional[str] = None
    client_name: Optional[str] = None
    headcount: Optional[int] = None
    location: Optional[str] = None
    status: Optional[str] = None
    scoring_weights: Optional[Dict[str, float]] = None
    tags: Optional[str] = None
    required_skills_override: Optional[List[Any]] = None
    nice_to_have_skills_override: Optional[List[Any]] = None
    primary_hiring_manager_id: Optional[int] = None
    assigned_recruiter_id: Optional[int] = None
    opened_on_behalf_of_hm_id: Optional[int] = None
    routing_policy_json: Optional[Dict[str, Any]] = None
    search_brief_json: Optional[Dict[str, Any]] = None
    must_ask_questions_json: Optional[List[Dict[str, Any]]] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v):
        if v is None:
            return v
        if v not in _REQ_STATUSES:
            raise ValueError(f"status must be one of {_REQ_STATUSES}")
        return v


class RequisitionIntakeUpdate(BaseModel):
    intake_json: Dict[str, Any]
    intake_status: Optional[str] = None

    @field_validator("intake_status")
    @classmethod
    def validate_intake_status(cls, v):
        if v is None:
            return v
        if v not in _INTAKE_STATUSES:
            raise ValueError(f"intake_status must be one of {_INTAKE_STATUSES}")
        return v


class RequisitionCalibrateRequest(BaseModel):
    criteria_json: Optional[Dict[str, Any]] = None
    merge_jd_parse: bool = True


class RequisitionCriteriaUpdate(BaseModel):
    """Manual criteria edit — creates a new version (source: manual_edit)."""
    must_haves: Optional[List[str]] = None
    good_to_haves: Optional[List[str]] = None
    deal_breakers: Optional[List[str]] = None
    environment: Optional[str] = None
    seniority_bar: Optional[str] = None
    team_context: Optional[str] = None
    success_criteria_90d: Optional[str] = None


class RequisitionHmApproval(BaseModel):
    approved: bool
    notes: Optional[str] = None
    intake_json: Optional[Dict[str, Any]] = None


class RequisitionOpenRequestCreate(BaseModel):
    title: str
    jd_text: str
    notes: Optional[str] = None
    location: Optional[str] = None
    headcount: Optional[int] = None


class RequisitionOpenRequestAssign(BaseModel):
    assigned_recruiter_id: int
    primary_hiring_manager_id: Optional[int] = None


class RequisitionAssignRecruiter(BaseModel):
    assigned_recruiter_id: int


class RequisitionFeedbackApply(BaseModel):
    suggestions: Dict[str, Any]
    recalibrate: bool = False


class RequisitionHmRequestCreate(BaseModel):
    email: str
    notes: Optional[str] = None


class RequisitionHmRequestDecision(BaseModel):
    notes: Optional[str] = None


class RequisitionCandidateAdd(BaseModel):
    candidate_ids: List[int]
    screening_result_ids: Optional[Dict[int, int]] = None


class RequisitionCandidateStatusUpdate(BaseModel):
    pipeline_status: str

    @field_validator("pipeline_status")
    @classmethod
    def validate_status(cls, v):
        if v not in _PIPELINE_STATUSES:
            raise ValueError(f"pipeline_status must be one of {_PIPELINE_STATUSES}")
        return v


class RequisitionSubmissionCreate(BaseModel):
    submission_json: Dict[str, Any]


class RequisitionOutcomeUpdate(BaseModel):
    hm_outcome: str
    outcome_reason_code: Optional[str] = None
    outcome_notes: Optional[str] = None

    @field_validator("hm_outcome")
    @classmethod
    def validate_outcome(cls, v):
        if v not in _HM_OUTCOMES:
            raise ValueError(f"hm_outcome must be one of {_HM_OUTCOMES}")
        return v


class TenantRequisitionSettingsUpdate(BaseModel):
    intake_gate_mode: Optional[str] = None
    screening_mode: Optional[str] = None
    hm_pipeline_permission: Optional[str] = None
    hiring_signal_weights: Optional[Dict[str, float]] = None

    @field_validator("intake_gate_mode")
    @classmethod
    def validate_gate(cls, v):
        if v is None:
            return v
        if v not in _INTAKE_GATE_MODES:
            raise ValueError(f"intake_gate_mode must be one of {_INTAKE_GATE_MODES}")
        return v

    @field_validator("screening_mode")
    @classmethod
    def validate_screening_mode(cls, v):
        if v is None:
            return v
        if v not in _SCREENING_MODES:
            raise ValueError(f"screening_mode must be one of {_SCREENING_MODES}")
        return v

    @field_validator("hm_pipeline_permission")
    @classmethod
    def validate_hm_perm(cls, v):
        if v is None:
            return v
        if v not in _HM_PIPELINE_PERMS:
            raise ValueError(f"hm_pipeline_permission must be one of {_HM_PIPELINE_PERMS}")
        return v


class RequisitionOut(BaseModel):
    id: int
    tenant_id: int
    title: str
    jd_text: str
    description: Optional[str] = None
    client_name: Optional[str] = None
    headcount: Optional[int] = None
    location: Optional[str] = None
    status: str
    intake_status: str
    intake_json: Optional[Dict[str, Any]] = None
    search_brief_json: Optional[Dict[str, Any]] = None
    calibrated_criteria_json: Optional[Dict[str, Any]] = None
    current_criteria_version: int = 0
    scoring_weights: Optional[Dict[str, float]] = None
    tags: Optional[str] = None
    required_skills_override: Optional[List[Any]] = None
    nice_to_have_skills_override: Optional[List[Any]] = None
    must_ask_questions_json: Optional[List[Dict[str, Any]]] = None
    primary_hiring_manager_id: Optional[int] = None
    primary_hiring_manager_email: Optional[str] = None
    hiring_manager_ids: List[int] = []
    created_by: Optional[int] = None
    legacy_role_template_id: Optional[int] = None
    external_ats_id: Optional[str] = None
    ats_provider: Optional[str] = None
    hm_approved_at: Optional[datetime] = None
    calibrated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    candidate_count: int = 0
    intake_gate_warning: Optional[str] = None
    is_calibrated: bool = False
    hm_request_email: Optional[str] = None
    hm_request_status: Optional[str] = None
    hm_requested_by: Optional[int] = None
    hm_requested_by_email: Optional[str] = None
    hm_requested_at: Optional[datetime] = None
    hm_request_notes: Optional[str] = None
    assigned_recruiter_id: Optional[int] = None
    assigned_recruiter_email: Optional[str] = None
    opened_on_behalf_of_hm_id: Optional[int] = None
    opened_on_behalf_of_hm_email: Optional[str] = None
    routing_policy_json: Optional[Dict[str, Any]] = None

    model_config = {"from_attributes": True}


class RequisitionOpenRequestOut(BaseModel):
    id: int
    tenant_id: int
    title: str
    jd_text: str
    notes: Optional[str] = None
    location: Optional[str] = None
    headcount: Optional[int] = None
    status: str
    requested_by: Optional[int] = None
    requester_email: Optional[str] = None
    assigned_recruiter_id: Optional[int] = None
    assigned_recruiter_email: Optional[str] = None
    requisition_id: Optional[int] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class RequisitionCandidateOut(BaseModel):
    id: int
    requisition_id: int
    candidate_id: int
    screening_result_id: Optional[int] = None
    pipeline_status: str
    submission_status: str
    hm_outcome: Optional[str] = None
    outcome_reason_code: Optional[str] = None
    outcome_notes: Optional[str] = None
    submission_json: Optional[Dict[str, Any]] = None
    parse_confidence_json: Optional[Dict[str, Any]] = None
    added_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    outcome_at: Optional[datetime] = None
    candidate_name: Optional[str] = None
    candidate_email: Optional[str] = None
    fit_score: Optional[int] = None
    call_fit_score: Optional[int] = None
    call_source: Optional[str] = None
    suggested_action: Optional[str] = None

    model_config = {"from_attributes": True}


class RequisitionCriteriaVersionOut(BaseModel):
    id: int
    requisition_id: int
    version: int
    criteria_json: Dict[str, Any]
    source: str
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TenantRequisitionSettingsOut(BaseModel):
    tenant_id: int
    intake_gate_mode: str
    screening_mode: str = "requisition_required"
    hm_pipeline_permission: str
    hiring_signal_weights: Optional[Dict[str, float]] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ─── ATS Connector Schemas ────────────────────────────────────────────────────

class ATSConnectionCreate(BaseModel):
    provider: str = Field(..., description="greenhouse / lever / workday / generic")
    label: str
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    base_url: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    sync_direction: str = Field("push", description="push / pull / bidirectional")
    status_mapping_json: Optional[Dict[str, str]] = None

    @field_validator('provider')
    @classmethod
    def validate_provider(cls, v):
        allowed = {'greenhouse', 'lever', 'workday', 'generic'}
        if v not in allowed:
            raise ValueError(f'provider must be one of {allowed}')
        return v

    @field_validator('sync_direction')
    @classmethod
    def validate_direction(cls, v):
        allowed = {'push', 'pull', 'bidirectional'}
        if v not in allowed:
            raise ValueError(f'sync_direction must be one of {allowed}')
        return v


class ATSConnectionUpdate(BaseModel):
    label: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    base_url: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    is_active: Optional[bool] = None
    sync_direction: Optional[str] = None
    status_mapping_json: Optional[Dict[str, str]] = None

    @field_validator('sync_direction')
    @classmethod
    def validate_direction(cls, v):
        if v is None:
            return v
        allowed = {'push', 'pull', 'bidirectional'}
        if v not in allowed:
            raise ValueError(f'sync_direction must be one of {allowed}')
        return v


class ATSConnectionOut(BaseModel):
    id: int
    tenant_id: int
    provider: str
    label: str
    api_key_configured: bool = False
    webhook_secret_configured: bool = False
    base_url: Optional[str] = None
    webhook_url: Optional[str] = None
    is_active: bool
    sync_direction: str
    status_mapping_json: Optional[Dict[str, str]] = None
    last_sync_at: Optional[datetime] = None
    last_sync_status: Optional[str] = None
    last_error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ATSSyncLogOut(BaseModel):
    id: int
    connection_id: int
    direction: str
    entity_type: str
    entity_id: Optional[str] = None
    candidate_id: Optional[int] = None
    screening_result_id: Optional[int] = None
    success: bool
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ATSPushRequest(BaseModel):
    candidate_id: int
    screening_result_id: Optional[int] = None
    external_id: Optional[str] = Field(None, description="External ATS candidate/application ID")
    status: Optional[str] = Field(None, description="Status to push (uses mapping if omitted)")


# ─── Interview Template Schemas ───────────────────────────────────────────────

class InterviewTemplateQuestion(BaseModel):
    question: str
    category: str = "technical"
    rationale: Optional[str] = None
    sequence_number: Optional[int] = None


class InterviewTemplateCreate(BaseModel):
    name: str
    description: Optional[str] = None
    project_id: Optional[int] = None
    questions: List[InterviewTemplateQuestion]


class InterviewTemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    project_id: Optional[int] = None
    questions: Optional[List[InterviewTemplateQuestion]] = None
    is_active: Optional[bool] = None


class InterviewTemplateOut(BaseModel):
    id: int
    tenant_id: int
    project_id: Optional[int] = None
    name: str
    description: Optional[str] = None
    questions: List[Dict[str, Any]] = []
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
