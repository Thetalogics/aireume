# ARIA Resume Intelligence Platform - Complete Product Specification

**Version**: 2.2.0  
**Company**: ThetaLogics  
**License**: MIT (Open Source)  
**Architecture**: Multi-tenant SaaS (cloud-managed)  
**Last Updated**: August 2, 2026

---

## Executive Summary

ARIA is an enterprise-grade, AI-powered resume intelligence platform designed for modern hiring teams. The default deployment is a managed multi-tenant SaaS stack with cloud LLM inference (Ollama Cloud and/or Google Gemini). Optional dedicated deployment models may be offered for regulated enterprise customers.

### Core Value Propositions

1. **Requisition-centric hiring** — Openings, HM/TA collaboration, screening, sourcing feedback, and handoff in one workflow
2. **Managed AI Screening** — Cloud-hosted multi-tenant SaaS with explainable fit scores, narratives, and interview automation
3. **Enterprise-Grade Compliance** — EEOC/GDPR tooling with PII redaction, evidence-based decisions, and full audit trails
4. **AI-Powered Intelligence** — Advanced NLP for skills extraction, gap detection, fit scoring, and narrative generation
5. **Multi-Tenant SaaS** — Support for unlimited organizations with complete data isolation
6. **Transparent AI Processing** — Resume and job data processed by configured cloud AI providers (Ollama Cloud, optional Gemini); disclosed at onboarding and in Settings
7. **Production-Ready** — Comprehensive tests, CI/CD pipeline, monitoring, and enterprise admin capabilities

---

## Platform Architecture

### Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Backend Framework** | FastAPI 0.115.5 (Python 3.11) | RESTful API development |
| **ASGI Server** | Uvicorn 0.32.1 (4 workers) | Production server |
| **Database (Prod)** | PostgreSQL 16 | Primary datastore with multi-tenant isolation |
| **ORM** | SQLAlchemy 2.0.36 | Database abstraction |
| **Migrations** | Alembic 1.14.0 | Schema versioning (67+ migrations) |
| **Frontend Framework** | React 18.3.1 | Modern UI library |
| **Build Tool** | Vite 6.0.5 | Frontend bundling |
| **Styling** | TailwindCSS 3.4.17 | Utility-first CSS |
| **Charts** | Recharts 3.x | Data visualization |
| **LLM Runtime** | Ollama (Cloud + Local) | AI inference engine |
| **LLM Models** | gemma4:31b-cloud (default), configurable | AI analysis |
| **LLM Framework** | LangChain + LangGraph | LLM orchestration |
| **Authentication** | python-jose + bcrypt | JWT tokens + password hashing |
| **PDF Parsing** | pdfplumber + PyMuPDF | Document extraction |
| **NLP** | spaCy 3.8.2 + rapidfuzz | Text processing & fuzzy matching |
| **Video Processing** | faster-whisper 1.1.0 + yt-dlp | Transcription |
| **PII Redaction** | Microsoft Presidio | Enterprise-grade PII detection |
| **Export** | pandas (Excel export) | Data export |
| **Monitoring** | Prometheus + FastAPI Instrumentator | Metrics collection |
| **Task Queue** | Celery 5.4.0 + Redis 5.2.0 | Background task processing |
| **Scheduling** | APScheduler 3.10.4 | Task scheduling |
| **Testing** | pytest 8.3.4 + vitest 2.1.8 | Backend + frontend testing |
| **Reverse Proxy** | Nginx | SSL termination, static files, load balancing |

### Deployment Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CLIENT LAYER                          │
│  [Browser - Tenant A] [Browser - Tenant B] [Browser - Admin]│
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   REVERSE PROXY (Nginx)                      │
│              SSL Termination + Static Files                  │
└──────────────────────────┬──────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│   FRONTEND   │   │   BACKEND    │   │  PROMETHEUS  │
│  React/Vite  │   │ FastAPI      │   │  /metrics    │
│  Nginx       │   │ 4 Workers    │   │              │
└──────────────┘   └──────┬───────┘   └──────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌──────────────┐  ┌────────────┐  ┌──────────────┐
│   OLLAMA     │  │ PostgreSQL │  │  FILE STORE  │
│ gemma4:31b   │  │ 16 DB      │  │  Uploads     │
│ 8 Cores/8GB  │  │ 1.5GB buf  │  │  Videos      │
└──────────────┘  └────────────┘  └──────────────┘
```

---

## Core Feature Modules

### 1. Resume Analysis Engine

#### 1.1 Single Resume Analysis
- **Supported Formats**: PDF, DOCX, DOC, TXT, RTF, ODT
- **File Validation**: Magic-byte signature verification, binary detection for .txt
- **Real-time Streaming**: Server-Sent Events (SSE) for progressive results
- **Processing Time**: ~1-2s for Python scoring, ~40-60s for LLM narrative (background)

#### 1.2 Batch Processing
- **Batch Size**: Up to 50 files per batch
- **Concurrency Control**: Configurable semaphore (default: 30 concurrent)
- **Progressive Streaming**: Real-time progress updates via SSE
- **Error Handling**: Graceful handling of individual failures within batch
- **Background Processing**: LLM narratives generated asynchronously

#### 1.3 Analysis Pipeline (Hybrid Architecture)

**Phase 1 - Deterministic Python Scoring (~1-2s)**:
- Resume parsing with contact info extraction
- Skills matching against 951+ skill registry with fuzzy matching
- Employment gap detection with severity classification (negligible/minor/moderate/critical)
- Education validation with degree relevance scoring
- Experience calculation with timeline analysis
- Domain similarity using cosine similarity of domain vectors
- Eligibility gate checks (automatic caps for ineligible candidates)
- Risk signal detection (job hopping, credential inflation, fake patterns)
- Fit score computation (0-100) with configurable weights

**Phase 2 - LLM Narrative Generation (~40-60s, Background)**:
- Strengths and weaknesses identification
- Interview question generation (technical, behavioral, role-specific)
- Fit summary and recommendation rationale
- Dealbreakers and differentiators analysis
- Adjacent skills discovery
- Professional summary generation
- Hiring decision explanation

**Fallback Mechanism**: If LLM times out or fails, deterministic Python fallback provides complete analysis without LLM narrative.

#### 1.4 AI Weight Suggestion System
- **Endpoint**: `POST /api/analyze/suggest-weights`
- **Functionality**: LLM analyzes job description and suggests optimal scoring weights
- **Role Categories**: Technical, Sales, HR, Marketing, Operations, Leadership
- **Seniority Detection**: Entry, Mid, Senior, Executive level identification
- **7-Weight Schema**:
  - Core Competencies (0.30 default)
  - Experience (0.20)
  - Domain Fit (0.20)
  - Education (0.10)
  - Career Trajectory (0.10)
  - Role Excellence (0.10) - role-specific differentiator
  - Risk (-0.10) - red flags penalty
- **User Control**: AI suggestions require explicit acceptance; users can manually adjust
- **Presets**: Balanced, Skill-Heavy, Experience-Heavy, Domain-Focused

#### 1.5 Deterministic Scoring & Eligibility
- **Eligibility Gates**: Candidates failing critical requirements receive graduated penalties (not hard caps)
- **Domain Mismatch Penalty**: Low domain similarity (<30%) applies graduated penalty to final score
- **Core Skills Penalty**: Low core skills (<30%) applies graduated penalty to final score
- **Graduated Penalty Model**: Scoring penalties scale proportionally with severity rather than capping at fixed thresholds
- **Score Clamping**: Final scores always clamped to 0-100 range
- **Transparent Decision Engine**: All scoring factors explainable with evidence
- **Universal 7-Weight Schema**: All stored weights (tenant-level, template-level) are normalized to the universal schema on write via `validate_and_normalize_weights()`
- **Weight Migration**: `migrate_stored_weights()` utility converts legacy 4-weight and old backend 7-weight schemas to universal schema

### 2. Candidate Management

#### 2.1 Candidate Profiles
- **Enriched Data Storage**: Skills, education, work experience, gaps stored once
- **Profile Fields**: Name, email, phone, current role, current company, total years experience
- **Profile Quality**: Automated quality assessment (high/medium/low)
- **AI Professional Summary**: LLM-generated candidate summary
- **Resume Storage**: Original file stored as BLOB in PostgreSQL (BYTEA)
- **PDF Conversion**: Automatic DOC to PDF conversion for browser viewing
- **Parser Snapshot**: Full parse_resume() output for audit and re-analysis

#### 2.2 Deduplication System (3-Layer)
1. **Email Match**: Exact email comparison
2. **File Hash Match**: MD5 hash of resume file bytes
3. **Name + Phone Match**: Fuzzy matching on name and phone

#### 2.3 JD Re-Analysis
- **Re-analyze**: Re-run scoring with different JD using stored candidate profile
- **Version Tracking**: Screening results track version numbers for re-analysis
- **Screening Projects**: Lightweight grouping of candidates for a specific hiring push
  - **Endpoint**: `POST/GET/PUT/DELETE /api/projects`
  - **Per-Project Pipeline**: Kanban view grouped by candidate status (pending/shortlisted/rejected/in-review/hired)
  - **Project-Candidate Linkage**: `POST /api/projects/{id}/candidates` adds candidates with optional screening result
  - **Per-Project Status**: Candidate status is tracked per-project, independent of global ScreeningResult.status
  - **Auto-Link on Analyze**: Pass `project_id` to `/api/analyze` or `/api/analyze/stream` to auto-link results
  - **JD Template Binding**: Each project is tied to a RoleTemplate (JD) for consistent scoring
- **No Re-Upload Required**: Evaluate existing candidates against new job descriptions
- **Stored Profile Reuse**: Uses enriched candidate profile from database
- **Version Tracking**: Each re-analysis creates new version with version_number
- **Analysis History**: Full history of all JD evaluations per candidate

#### 2.4 Candidate Status Management
- **Statuses**: pending, shortlisted, rejected, in-review, hired
- **Bulk Updates**: Batch status changes for multiple candidates
- **Status Tracking**: Timestamp tracking for status changes
- **Filter & Search**: Search by name, email, skill, status

#### 2.5 Candidate Notes
- **Team Collaboration**: Add notes to candidate profiles
- **User Attribution**: Notes linked to author
- **Timestamp Tracking**: Created and updated timestamps

### 3. Requisitions, Job Descriptions & Hiring Workflow

#### 3.1 Requisition Lifecycle (Shipped)
- **Primary object**: Requisition (opening) — replaces JD Library as the day-to-day work surface (`/requisitions`; legacy `/jd-library` redirects)
- **Statuses**: Intake → criteria locked → sourcing → interviewing (auto-advance helpers after intake ready / HM advance)
- **Assignment**: `assigned_recruiter_id`; create/assign can set `opened_on_behalf_of_hm_id`
- **Plan gates**: `requisitions`, `hm_workflow` (and related interview features)

#### 3.2 Open Requests & TA Lead
- **HM open requests**: Hiring managers request a new opening (`/requisitions/open-requests`)
- **TA Lead / Admin**: Assign recruiter and materialize the requisition
- **Role** `ta_lead`: Assign recruiters, approve HM access requests, view all requisitions (no substitute for admin billing ownership)

#### 3.3 Collaborative Intake & Criteria
- **HM edit + approve**: Intake payload approval; `changes_requested` banner when revisions needed
- **Criteria lock**: Stepper advances at criteria v1+ (“Criteria locked”)
- **Sourcing brief**: Search brief UI + apply HM reject feedback via `apply-feedback`

#### 3.4 Screening Against Openings
- **Tenant `screening_mode`**: `requisition_required` (default) or `allow_ad_hoc`
- **Analyze UI**: Requisition-first; “Quick screen” when ad-hoc allowed
- **Enforcement**: Backend gate on `POST /api/analyze` when mode is requisition-required and no `requisition_id` (batch/stream paths may still need the same gate — treat single analyze as the primary enforced entry)
- **Linking**: Screening results link to requisition candidates / pipeline

#### 3.5 Submit to HM, Outcomes & Handoff
- **Submit**: Recruiter submits candidates with optional note; email + tenant event to HM
- **Outcomes**: Shortlist / reject with required `outcome_reason_code`; feedback suggestions update sourcing brief
- **Handoff pack**: Consolidated fields for submitted/shortlisted candidates (`/requisitions/:id/handoff`, public `/handoff/:token`)
- **`hm_pipeline_permission`**: Tenant setting respected in frontend HM pipeline access

#### 3.6 JD Input Methods (Ad-hoc / Intake)
- **Manual Entry**: Direct text input
- **File Upload**: PDF and DOCX support
- **URL Scraping**: LinkedIn, Indeed, and other job boards (jd_scraper service)
- **JD Validation**: Minimum 80 words requirement
- **Size Limit**: Maximum 50KB

#### 3.7 JD Caching
- **MD5-Keyed Cache**: Cache key based on MD5 of first 2000 characters
- **Database Storage**: Shared across all workers (JdCache table)
- **30-Day Retention**: Automatic cleanup of expired entries
- **Cross-Worker Sharing**: All 4 FastAPI workers share same cache

#### 3.8 Role Templates
- **Save JDs**: Store frequently-used job descriptions
- **Scoring Weights**: Per-template custom weights
- **Tags**: Organize templates with tags
- **Tenant Isolation**: Templates scoped to tenant

#### 3.9 Known gaps (documented, not blocked for core ICP)
- External job-board / LinkedIn posting APIs (explicitly deferred)
- Full post-shortlist interview scheduling
- Adverse-action PDF polish and deeper ATS partner certification (separate track)

### 4. Side-by-Side Candidate Comparison

#### 4.1 Comparison Features
- **Multi-Candidate**: Compare 2-5 candidates simultaneously
- **Score Breakdown**: Detailed comparison across all metrics
- **Matched & Missing Skills**: Side-by-side skills comparison
- **Strengths & Weaknesses**: Top 3 per candidate
- **Employment Gaps**: Gap count comparison
- **Score Dimensions**: Skill match, experience, education, domain fit, timeline
- **Interview Questions Preview**: Technical questions for each candidate
- **Analysis Quality**: Quality indicator per analysis

#### 4.2 Enhanced Comparison Fields
- **Current Role & Company**: Professional context
- **Total Years Experience**: Experience level comparison
- **Fit Summary**: AI-generated narrative summary
- **Recommendation Rationale**: Detailed reasoning
- **Dealbreakers**: Top 3 dealbreakers per candidate
- **Differentiators**: Top 3 unique strengths
- **Hiring Decision**: AI recommendation with explanation
- **Adjacent Skills**: Related capabilities (top 5)

### 5. Video Interview Analysis

#### 5.1 Video Upload
- **Supported Formats**: MP4, WebM, AVI, MOV, MKV, M4V
- **Size Limit**: 200MB maximum
- **Processing**: Auto-transcription with faster-whisper
- **Analysis**: Communication quality evaluation against JD

#### 5.2 Video URL Processing
- **Supported Platforms**: Zoom, Microsoft Teams, Loom, Google Drive
- **Download**: yt-dlp integration for public URLs
- **Analysis**: Same pipeline as uploaded videos

#### 5.3 Video Analysis Output
- **Transcript Generation**: High-quality transcription
- **Communication Analysis**: Speaking quality assessment
- **Content Evaluation**: Alignment with job requirements
- **Structured Insights**: Communication patterns and content analysis

### 6. Transcript Analysis (Enterprise-Grade)

#### 6.1 Transcript Input
- **File Upload**: TXT, VTT, SRT formats
- **Size Limit**: 5MB maximum
- **Direct Text**: Paste transcript text directly
- **Source Platform**: Tag source (Zoom, Teams, Manual)

#### 6.2 PII Redaction Service (Microsoft Presidio)
- **Entity Types**: PERSON, EMAIL, PHONE, LOCATION, ORG, URL, SSN, CREDIT_CARD
- **Confidence Scoring**: Per-entity detection confidence
- **Fallback Mode**: Regex patterns if Presidio unavailable
- **Audit Trail**: Complete redaction map with counts
- **Validation Metrics**: Preservation ratio and quality assessment
- **Automatic**: Enabled by default before LLM analysis

#### 6.3 Evidence Validation Service
- **Claim Verification**: All LLM claims validated against transcript
- **Matching Strategies**:
  - Exact match
  - Fuzzy match (75% similarity threshold)
  - Keyword match
- **Hallucination Detection**: Identifies unsupported claims
- **Quality Scoring**: Evidence quality score (0-100)
- **Validation Report**: Detailed breakdown of verified vs hallucinated claims
- **Thresholds**:
  - 90-100: Excellent
  - 75-89: Good
  - 60-74: Fair
  - <60: Poor

#### 6.4 Transcript Analysis Output
- **Fit Score**: 0-100 evaluation
- **JD Alignment**: Requirement-by-requirement with evidence citations
- **Strengths**: With supporting quotes from transcript
- **Areas for Improvement**: With reasoning
- **Red Flags**: With severity levels (low/medium/high)
- **Recommendation**: proceed/hold/reject with rationale
- **Bias Note**: Confirmation of anonymized evaluation
- **Evidence Quality Score**: Reliability indicator
- **PII Redaction Metadata**: Count and status

#### 6.5 Compliance & Legal Defensibility
- **EEOC Compliance**: PII redacted, evidence-based decisions, no demographic factors
- **GDPR Compliance**: PII never sent to LLM, data minimization, right to explanation
- **Legal Defensibility**: Every claim backed by evidence, full audit trail
- **Adverse Action Reports**: Legally compliant documentation (in progress)
- **Calibration & Drift Detection**: Consistent scoring over time (in progress)

### 7. Interview Kit & Scorecard

#### 7.1 Per-Question Evaluation
- **Question Categories**: Technical, Behavioral, Role-specific
- **Rating System**: Per-question rating and notes
- **User Attribution**: Evaluations linked to interviewer
- **Upsert Capability**: Update existing evaluations
- **Unique Constraint**: One evaluation per question per user

#### 7.2 Overall Assessment
- **Recruiter Notes**: Comprehensive assessment text
- **Recommendation**: Recruiter recommendation (proceed/hold/reject)
- **Per-User**: Each interviewer provides own assessment
- **Timestamp Tracking**: Created and updated timestamps

#### 7.3 HM Handoff Package
- **Comparison Matrix**: Skill match, experience, education, domain fit, timeline
- **Interview Scores**: Aggregated per-category interview evaluation summary
- **Overall Assessments**: Recruiter recommendations
- **Export Ready**: Formatted for hiring manager review

#### 7.4 Interview Kit v3 (Recruiter Voice Personalization)

**Kit version:** `kit_version: 3` (v2 kits remain supported via `spoken_text || text` fallback)

**Step fields:**

| Field | Purpose |
|-------|---------|
| `intent` | Internal coaching — what the recruiter is trying to validate |
| `spoken_text` | Exact line for live screen / voice bot (15–35 words) |
| `follow_up_intents` | Coaching bullets when answers are vague |
| `probe_target` | Link to probe area / skill being validated |
| `what_to_listen_for` | Scoring signals for recruiter and evaluators |

**Kit-level fields:** `threads`, `hypotheses`, `thread_transitions`, `candidate_briefing`, `resume_anchors`

**Generation pipeline:**

1. Python screening produces probe areas and `CandidateIntelligenceService` artifact
2. Deterministic thread skeleton from hypotheses + gaps
3. `RecruiterVoicePersonalizer` batch LLM rewrite
4. `lint_interview_kit()` quality gate before persist
5. Voice bot: pre-generated `spoken_text` + `TurnPersonalizer` for follow-ups/transitions only

**API:** `POST /api/interview-kit/{screening_result_id}/regenerate-step` — re-personalize one step (recruiter/admin, tenant-scoped)

### 8. Team Collaboration

#### 8.1 Multi-User Tenants
- **Role-Based Access Control (RBAC)**:
  - **Admin** (`admin`): Full tenant access, user management, billing
  - **TA Lead** (`ta_lead`): Assign recruiters to requisitions, approve HM access requests, view all reqs
  - **Recruiter** (`recruiter`): Intake, screening, pipeline, submit to HM (own or assigned requisitions)
  - **Hiring Manager** (`hiring_manager`): Approve/edit intake, review submitted candidates, shortlist/reject with feedback
  - **Viewer** (`viewer`): Read-only access
- **Member Invitations**: Email-based team onboarding (SSO/invite allowlists include `ta_lead`)
- **Team Members**: Dedicated TeamMember table for tenant-user relationships
- **Workspace Onboarding**: Guided wizard — organization, plan, invites, checklist / sample seed

#### 8.2 Comments System
- **Inline Comments**: Discuss screening results
- **User Attribution**: Comments linked to author
- **Result-Scoped**: Comments attached to specific screening results
- **Timestamp Tracking**: Creation time

#### 8.3 Shared Lists
- **Collaborative Shortlists**: Team-wide candidate shortlists
- **Status Sharing**: Visible candidate statuses across team
- **Requisition Pipelines**: Candidates grouped by opening / requisition

### 9. Email Generation & Communication

#### 9.1 AI Email Generation
- **Email Types**:
  - Shortlist email (with score and strengths)
  - Rejection email (empathetic, professional)
  - Screening call invitation (with scheduling link placeholder)
- **LLM-Powered**: Uses Ollama for personalized content
- **Fallback Templates**: Deterministic templates if LLM unavailable
- **JSON Format**: Structured output (subject + body)
- **Customization**: Includes candidate name, role, score, strengths

#### 9.2 Tenant Email Configuration
- **Per-Tenant SMTP**: Each tenant can configure own email server
- **Encryption Support**: TLS, SSL, or none
- **Configuration Fields**:
  - SMTP host, port, user, password (Fernet-encrypted)
  - From address, from name, reply-to
  - Encryption type
  - Active/inactive status
- **Test Capability**: Send test emails to verify configuration
- **Test History**: Last test timestamp and success status

#### 9.3 Platform Email Service
- **Global SMTP**: System-wide email configuration
- **Status Check**: Configuration status endpoint
- **Test Email**: Admin test email functionality

### 10. Export & Integration

#### 10.1 Data Export
- **CSV Export**: Spreadsheet-compatible format
- **Excel Export**: Formatted workbook with openpyxl
- **Selective Export**: Export specific results by ID
- **Comprehensive Fields**:
  - Candidate name, email, phone
  - Fit score, recommendation, risk level, status
  - Score breakdowns (skill match, experience, stability, education)
  - Matched and missing skills
  - Strengths and weaknesses

#### 10.2 ATS Integration (Webhooks)
- **Tenant Webhooks**: Per-tenant webhook configuration
- **Event Subscriptions**: Configurable event types
- **Delivery Tracking**: Full delivery history with status
- **Retry Logic**: Automatic retries on failure
- **Failure Counting**: Track consecutive failures
- **HMAC Signing**: Webhook secret for payload verification
- **Delivery Metadata**: Response status, response body, attempt count

#### 10.3 JD URL Scraping
- **Job Board Support**: LinkedIn, Indeed, and others
- **Automatic Extraction**: Parse JD from URL
- **Validation**: Content size and format checks

### 11. Custom AI Training

#### 11.1 Outcome Labeling
- **Tag Screenings**: Mark results as hired or rejected
- **Feedback Storage**: Optional feedback text
- **Tenant Isolation**: Training data scoped to tenant

#### 11.2 Prompt / model personalization
- **Labeled examples**: Recruiter outcomes used as prompt context (not hosted fine-tuning)
- **Minimum examples**: 10+ labeled examples required for the personalization path
- **Per-tenant prompts**: Organization-specific wording, not a separate trained model
- **Training status**: Track personalization job status

### 12. Dashboard & Analytics

#### 12.1 Dashboard Summary
- **Action Items**: Pending review count, in-progress analyses, shortlisted count
- **Pipeline by JD**: Candidates grouped by job description with status breakdown
- **Weekly Metrics**: Analysis trends over last 7 days
- **Average Fit Scores**: Per-JD average scores
- **Status Distribution**: Pending, shortlisted, rejected counts

#### 12.2 Screening Analytics
- **Time Period Filters**: Last 7/30/90 days
- **Score Distribution**: Fit score histograms
- **Recommendation Breakdown**: Strong hire, hire, no hire, strong no hire
- **Risk Level Analysis**: Low, medium, high risk distribution
- **Top Skills**: Most common matched and missing skills
- **Trend Analysis**: Screening volume over time

### 13. Subscription & Billing

#### 13.1 Subscription Plans
- **Plan Tiers**: Free, Pro, Enterprise (configurable)
- **Plan Features**:
  - Monthly price (cents), yearly price (cents)
  - Currency support (ISO currency code)
  - Usage limits: analyses/month, storage, team members
  - Feature flags per plan
  - Display name and description
  - Sort order for display
  - Active/inactive status

#### 13.2 Usage Tracking
- **Detailed Logs**: UsageLog table with action, quantity, details
- **Monthly Tracking**: Analyses count per month per tenant
- **Storage Tracking**: Storage used in bytes
- **Monthly Reset**: Automatic usage reset at billing period
- **User Attribution**: Usage linked to specific users

#### 13.3 Billing Integration
- **Payment Providers**: Pluggable architecture (Stripe, Razorpay support)
- **Provider Factory**: get_payment_provider() abstraction
- **Checkout Sessions**: Create payment checkout sessions
- **Webhook Handling**: Incoming webhook events from payment providers
- **Signature Verification**: Webhook payload validation
- **Subscription Status**: Real-time subscription status retrieval
- **Cancellation**: Subscription cancellation support

#### 13.4 Quota Enforcement
- **Pre-Analysis Check**: Quota check before analysis
- **Plan Limits**: Enforce analyses/month, batch size, storage
- **Usage Recording**: Record usage after successful operations
- **Error Handling**: Clear error messages when quota exceeded

### 14. Enterprise Platform Administration

#### 14.1 Granular Admin Roles
- **Super Admin**: Full platform access
- **Billing Admin**: Billing and subscription management
- **Support**: Tenant impersonation for debugging
- **Security Admin**: Security events and audit logs
- **Readonly Platform**: View-only platform access

#### 14.2 Tenant Management
- **List Tenants**: Paginated list with search, filters (plan, status)
- **Tenant Details**: Full tenant information with users, usage, audit logs
- **Create Tenant**: Provision new tenants
- **Update Tenant**: Modify tenant settings including scoring weights
- **Suspend Tenant**: Suspend with reason tracking
- **Reactivate Tenant**: Restore suspended tenants
- **Change Plan**: Move tenant between subscription plans
- **Adjust Usage**: Manual usage count adjustment
- **Delete Tenant**: Remove tenant and associated data

#### 14.3 User Management
- **Add Users**: Add users to tenants with role assignment
- **Platform Roles**: Assign granular platform roles
- **Remove Users**: Delete users from tenants
- **User Details**: Email, role, active status, creation date

#### 14.4 Audit Logging
- **Comprehensive Trail**: All admin actions logged
- **Actor Tracking**: User ID and email of actor
- **Action Types**: tenant.create, tenant.suspend, user.add, etc.
- **Resource Tracking**: Resource type and ID
- **Details JSON**: Full context in JSON format
- **IP Address**: Request IP logging
- **Pagination**: Paginated audit log retrieval
- **Search & Filter**: Filter by action, resource type, date range

#### 14.5 Feature Flags
- **Global Flags**: Enable/disable features platform-wide
- **Per-Tenant Overrides**: Override global flags per tenant
- **Plan Entitlements**: Map features to subscription plans
- **Feature Flag Model**:
  - Key, display name, description
  - Enabled globally (boolean)
  - Tenant-specific overrides
  - Plan feature mappings

#### 14.6 Security Events
- **Event Monitoring**: Login attempts, failures, suspicious activity
- **Tenant Tracking**: Events scoped to tenant
- **User Tracking**: Events linked to user
- **Event Types**: login_success, login_failure, suspicious_activity, etc.
- **IP & User Agent**: Full request context
- **Details JSON**: Additional event metadata
- **Retrieval**: Filtered by tenant, user, event type, date range

#### 14.7 Admin Impersonation
- **Support Debugging**: Admins can impersonate tenant users
- **Session Tracking**: ImpersonationSession table
- **Token Hash**: Secure token storage (hashed)
- **Expiration**: Sessions expire automatically
- **IP Tracking**: Impersonation origin IP
- **Active Sessions**: List all active impersonation sessions
- **Revocation**: Revoke impersonation sessions
- **Audit Trail**: Impersonation actions logged

#### 14.8 Data Erasure (GDPR)
- **Erasure Requests**: Initiate data erasure for tenant
- **Status Tracking**: requested, in_progress, completed, failed
- **Records Affected**: Count of deleted records
- **Audit Trail**: ErasureLog with full details
- **Actor Tracking**: User who initiated erasure
- **Timestamp Tracking**: Started and completed times

#### 14.9 Rate Limiting
- **Per-Tenant Config**: Custom rate limits per tenant
- **Requests Per Minute**: Configurable RPM limit (default: 60)
- **LLM Concurrency**: Max concurrent LLM calls (default: 2)
- **Middleware**: RateLimitMiddleware applied globally
- **Tenant Isolation**: Limits enforced per tenant

#### 14.10 Billing Configuration
- **Provider Config**: Platform-level billing provider settings
- **PlatformConfig Table**: Key-value configuration storage
- **Sensitive Data Masking**: API keys and secrets masked in UI
- **Provider Registry**: List available billing providers with required config
- **Audit Logging**: Configuration changes logged

#### 14.11 Notification Management
- **SMTP Status**: Email configuration status check
- **Test Email**: Send test emails from admin panel
- **Tenant Config Management**: View/edit tenant email configs
- **Encryption**: Passwords encrypted with Fernet

### 15. O*NET Occupation Database

#### 15.1 O*NET Integration
- **Occupation Validation**: Validate occupations against O*NET database
- **Background Sync**: Automatic sync every 30 days
- **Local Cache**: SQLite cache for fast lookups
- **Domain Mapping**: Map occupations to skill domains
- **Validation Service**: Verify job titles and occupations

#### 15.2 O*NET Services
- **Sync Service**: Download and cache O*NET data
- **Validator**: Validate occupation titles
- **Domain Service**: Extract domain information
- **Onet Cache DB**: Local SQLite database

### 16. Queue System (Background Processing)

#### 16.1 Analysis Queue
- **Queue Manager**: queue_manager.py service
- **Background Worker**: Asynchronous queue worker
- **Task Types**: LLM narrative generation, batch processing
- **Start/Stop**: Graceful worker lifecycle management
- **API Endpoints**: Queue status and management endpoints

#### 16.2 Background LLM Narrative
- **Async Processing**: LLM narratives generated in background
- **Narrative Status**: pending, processing, ready, failed
- **Error Tracking**: narrative_error field for failures
- **Polling Endpoint**: GET /api/analysis/{id}/narrative
- **Task Registration**: Track background tasks for graceful shutdown

### 17. Security & Compliance

#### 17.1 Authentication & Authorization
- **JWT Tokens**: Access tokens (60min) + refresh tokens (30 days)
- **Token Revocation**: RevokedToken table tracks logged-out tokens
- **Automatic Cleanup**: Expired revoked tokens deleted every 24 hours
- **Bcrypt Hashing**: Industry-standard password storage
- **RBAC**: Tenant roles `admin`, `ta_lead`, `recruiter`, `hiring_manager`, `viewer`
- **Platform Roles**: Granular platform admin roles
- **Tenant Isolation**: All queries scoped by tenant_id
- **SSO/SAML 2.0**: Enterprise single sign-on support per tenant

#### 17.2 Data Protection
- **Multi-Tenant Isolation**: Complete data separation by tenant_id
- **CSRF Protection**: CSRFMiddleware for state-changing operations
- **CORS Configuration**: Origin whitelist enforcement
- **Request Correlation IDs**: X-Request-ID header for full request tracing
- **Structured Logging**: JSON logging in production
- **IP Address Logging**: Audit logs capture request IP

#### 17.3 AI Safety
- **Prompt Injection Sanitization**: 10+ regex patterns to detect injection attempts
- **Input Truncation**: Resume max 50KB, JD max 20KB
- **Content Filtering**: Injection patterns replaced with [FILTERED]
- **Deterministic Fallbacks**: Rule-based backup when LLM fails
- **Output Validation**: Schema-enforced response parsing
- **Evidence Validation**: All LLM claims verified against source
- **PII Redaction**: Sensitive data removed before LLM processing

#### 17.4 Guardrail Framework (4-Phase)
- **Phase 1**: Input sanitization and validation
- **Phase 2**: PII redaction and evidence requirements
- **Phase 3**: Output validation and schema enforcement
- **Phase 4**: Post-processing quality checks and audit logging

#### 17.5 Compliance Features
- **EEOC Compliance**: No demographic factors, evidence-based decisions
- **GDPR Compliance**: PII protection, right to explanation, data erasure
- **Audit Trail**: Complete logging of all actions
- **Data Minimization**: Only necessary data processed
- **Adverse Action Reports**: Legally compliant rejection documentation (in progress)
- **Calibration & Drift Detection**: Consistent scoring over time (in progress)

### 18. Monitoring & Observability

#### 18.1 Prometheus Metrics
- **Request Metrics**: HTTP request duration, status codes
- **LLM Metrics**: Call duration, fallback counts
- **Custom Metrics**: Business-specific metrics
- **/metrics Endpoint**: Prometheus scrape endpoint
- **Excluded Handlers**: /health and /metrics excluded from metrics

#### 18.2 Health Checks
- **Shallow Health** (`/health`): Fast (<10ms), process alive check
- **Deep Health** (`/api/health/deep`): Comprehensive dependency check
  - Database connectivity with latency
  - Ollama sentinel state
  - Disk space monitoring
  - Status: healthy, degraded, unhealthy
- **LLM Status** (`/api/llm-status`): Detailed LLM diagnostic
  - Ollama URL and mode (cloud/local)
  - Pulled models and running models
  - Narrative model readiness
  - Fast model readiness
  - Sentinel status
  - Plain-English diagnosis

#### 18.3 Startup Checks
- **Database**: Connection verification
- **Skills Registry**: Seed and load verification
- **Ollama Reachability**: API endpoint check
- **Model Status**: Pulled and hot (in RAM) model verification
- **Environment**: Runtime environment display
- **Startup Banner**: Visual status table in logs

#### 18.4 Logging
- **Structured JSON**: Production logging in JSON format
- **Correlation IDs**: Request ID in all log entries
- **Log Levels**: INFO, WARNING, ERROR tracking
- **Component Loggers**: Separate loggers per module (aria.analysis, aria.hybrid, etc.)
- **Exception Logging**: Full stack traces on errors

### 19. Database Schema (Key Tables)

#### 19.1 Multi-Tenancy
- **SubscriptionPlan**: Plan definitions with limits, pricing, features
- **Tenant**: Organization entity with subscription, usage tracking, scoring weights
- **User**: User accounts with tenant_id, role, platform_role
- **UsageLog**: Detailed usage tracking with action, quantity, user attribution
- **RateLimitConfig**: Per-tenant rate limiting configuration
- **FeatureFlag**: Global feature flags
- **TenantFeatureOverride**: Per-tenant feature overrides
- **PlanFeature**: Plan-to-feature entitlement mapping

#### 19.2 Candidate & Results
- **Candidate**: Enriched profiles with resume storage, parsing results
- **ScreeningResult**: Analysis results with fit scores, narratives, status, versioning; optional requisition linkage
- **CandidateNote**: Team notes on candidates
- **RoleTemplate**: Saved job descriptions with custom weights
- **TrainingExample**: Labeled outcomes for model training

#### 19.2b Requisitions & HM Workflow
- **Requisition**: Opening with intake, criteria version, status, `assigned_recruiter_id`, `opened_on_behalf_of_hm_id`, routing policy JSON, sourcing brief
- **RequisitionOpenRequest**: HM-initiated opening requests pending assignment
- **RequisitionCandidate** / pipeline linkage: Submit, outcome reason codes, HM feedback apply path
- **TenantRequisitionSettings**: `screening_mode` (`requisition_required` | `allow_ad_hoc`), `hm_pipeline_permission`

#### 19.3 Collaboration
- **TeamMember**: Tenant-user relationships with roles
- **Comment**: Inline comments on screening results
- **InterviewEvaluation**: Per-question interview ratings and notes
- **OverallAssessment**: Recruiter overall assessments

#### 19.4 Analysis & Transcript
- **TranscriptAnalysis**: Interview transcript analyses with PII redaction metadata
- **JdCache**: JD parse cache with MD5 keys
- **Skill**: Dynamic skills registry (951+ skills) with aliases, domains, frequency

#### 19.5 Administration
- **AuditLog**: Platform admin audit trail
- **SecurityEvent**: Security monitoring events
- **ImpersonationSession**: Active admin impersonation sessions
- **ErasureLog**: GDPR data erasure audit trail
- **Webhook**: Tenant webhook configurations
- **WebhookDelivery**: Webhook delivery attempt history
- **PlatformConfig**: Platform-level key-value configuration
- **TenantEmailConfig**: Per-tenant SMTP settings

#### 19.6 Caches & Tokens
- **RevokedToken**: Revoked JWT tokens with expiration
- **JdCache**: Job description parse cache
- **PasswordResetToken**: Password reset tokens with expiration

#### 19.7 SSO & Authentication
- **SSOConfig**: Per-tenant SSO/SAML 2.0 configuration

#### 19.8 Billing & Invoicing
- **BillingEvent**: Billing event tracking
- **Invoice**: Invoice records with status tracking
- **DunningRecord**: Failed payment retry tracking
- **UsageAlert**: Usage threshold alerts

### 20. API Endpoints (Complete Reference)

#### 20.1 Authentication (`/api/auth`)
- `POST /api/auth/register` - Create new account
- `POST /api/auth/login` - Authenticate user
- `POST /api/auth/refresh` - Refresh access token
- `GET /api/auth/me` - Get current user
- `POST /api/auth/logout` - Revoke tokens
- `POST /api/auth/forgot-password` - Request password reset token
- `POST /api/auth/reset-password` - Reset password with token

#### 20.1b SSO (`/api/sso`)
- `GET /api/sso/config/{tenant_slug}` - Get SSO configuration (public)
- `GET /api/sso/login/{tenant_slug}` - Initiate SSO login
- `POST /api/sso/callback/{tenant_slug}` - SSO callback handler
- `GET /api/sso/metadata/{tenant_slug}` - Get SAML metadata

#### 20.2 Analysis (`/api/analyze`)
- `POST /api/analyze` - Single resume analysis
- `POST /api/analyze/stream` - SSE streaming analysis
- `POST /api/analyze/batch` - Batch processing (up to 50)
- `POST /api/analyze/batch-stream` - Batch with streaming progress
- `POST /api/analyze/batch-chunked` - Chunked batch processing
- `POST /api/analyze/suggest-weights` - AI weight suggestions
- `GET /api/history` - Analysis history
- `GET /api/analysis/{id}/narrative` - Get narrative result
- `PUT /api/results/{id}/status` - Update candidate status

#### 20.3 Candidates (`/api/candidates`)
- `GET /api/candidates` - List candidates (paginated, search, filters)
- `GET /api/candidates/{id}` - Get candidate details
- `PATCH /api/candidates/{id}` - Update candidate
- `POST /api/candidates/{id}/analyze-jd` - Re-analyze against JD
- `POST /api/candidates/{id}/notes` - Add candidate note
- `GET /api/candidates/{id}/notes` - List candidate notes

#### 20.4 Comparison (`/api/compare`)
- `POST /api/compare` - Compare 2-5 candidates

#### 20.5 Templates (`/api/templates`)
- `GET /api/templates` - List JD templates
- `POST /api/templates` - Create template
- `PUT /api/templates/{id}` - Update template
- `DELETE /api/templates/{id}` - Delete template

#### 20.5b Requisitions (`/api/requisitions`)
- `GET/POST /api/requisitions` - List / create openings
- `GET/PATCH /api/requisitions/{id}` - Get / update
- `PUT /api/requisitions/{id}/assign-recruiter` - Assign recruiter (`admin` / `ta_lead`)
- `GET/POST /api/requisitions/open-requests` - HM open-request queue
- `POST /api/requisitions/open-requests/{id}/assign` - Assign open request
- `POST /api/requisitions/{id}/hm-approval` - HM approve / request changes
- Intake / suggest / calibrate / criteria endpoints on requisition detail
- Pipeline, submit, outcome, apply-feedback, handoff, share-links
- `GET/PUT /api/requisitions/settings` - Screening mode & HM pipeline permission
- Analytics endpoints for requisition funnel metrics

#### 20.6 Team & Collaboration (`/api/team`, `/api/invites`)
- `GET /api/team` - List team members
- `POST /api/invites` - Invite team member (roles include `ta_lead`, `hiring_manager`)
- `DELETE /api/team/{user_id}` - Remove member
- `GET /api/results/{id}/comments` - Get comments
- `POST /api/results/{id}/comments` - Add comment

#### 20.7 Video & Transcript (`/api/analyze`, `/api/transcript`)
- `POST /api/analyze/video` - Upload video
- `POST /api/analyze/video-url` - Process video URL
- `POST /api/transcript/analyze` - Analyze transcript
- `GET /api/transcript/analyses` - List analyses
- `GET /api/transcript/analyses/{id}` - Get analysis details

#### 20.8 Interview Kit (`/api/results`)
- `PUT /api/results/{id}/evaluations` - Upsert question evaluation
- `GET /api/results/{id}/evaluations` - Get all evaluations
- `PUT /api/results/{id}/evaluations/overall` - Save overall assessment
- `GET /api/results/{id}/scorecard` - Generate scorecard

#### 20.9 Email Generation (`/api/email`)
- `POST /api/email/generate` - Generate email (shortlist/rejection/screening)

#### 20.10 Export (`/api/export`)
- `GET /api/export/csv` - Export as CSV
- `GET /api/export/excel` - Export as Excel
- `GET /api/export/{result_id}/pdf-report` - Generate PDF interview kit report

#### 20.11 Training (`/api/training`)
- `POST /api/training/label` - Label outcome
- `POST /api/training/train` - Trigger prompt personalization job
- `GET /api/training/status` - Training status

#### 20.12 Subscription (`/api/subscription`)
- `GET /api/subscription` - Current subscription
- `GET /api/subscription/plans` - Available plans
- `GET /api/subscription/usage` - Usage statistics
- `POST /api/subscription/upgrade` - Upgrade plan

#### 20.13 Billing (`/api/billing`)
- `POST /api/billing/checkout` - Create checkout session
- `POST /api/billing/webhook` - Handle payment webhook
- `GET /api/billing/subscription/{tenant_id}` - Get subscription status
- `POST /api/billing/cancel/{tenant_id}` - Cancel subscription

#### 20.14 Onboarding (`/api/onboarding`)
- `GET /api/onboarding/status` - Get onboarding completion status
- `POST /api/onboarding/organization` - Set up organization details
- `POST /api/onboarding/plan` - Select subscription plan
- `POST /api/onboarding/first-jd` - Create first job description
- `POST /api/onboarding/complete` - Mark onboarding as complete

#### 20.15 Dashboard (`/api/dashboard`)
- `GET /api/dashboard/summary` - Dashboard summary and metrics
- `GET /api/analytics/screening` - Screening analytics

#### 20.16 Queue Management (`/api/queue`)
- `GET /api/queue/status` - Queue worker status
- `POST /api/queue/process` - Trigger queue processing

#### 20.17 Admin (`/api/admin`)
- `GET /api/admin/tenants` - List all tenants
- `GET /api/admin/tenants/{id}` - Tenant details
- `POST /api/admin/tenants` - Create tenant
- `PUT /api/admin/tenants/{id}` - Update tenant
- `DELETE /api/admin/tenants/{id}` - Delete tenant
- `POST /api/admin/tenants/{id}/suspend` - Suspend tenant
- `POST /api/admin/tenants/{id}/reactivate` - Reactivate tenant
- `POST /api/admin/tenants/{id}/change-plan` - Change tenant plan
- `POST /api/admin/tenants/{id}/adjust-usage` - Adjust usage
- `GET /api/admin/tenants/{id}/usage-history` - Usage history
- `POST /api/admin/tenants/{id}/users` - Add user to tenant
- `DELETE /api/admin/tenants/{id}/users/{id}` - Remove user
- `GET /api/admin/audit-logs` - List audit logs
- `GET /api/admin/feature-flags` - List feature flags
- `PUT /api/admin/feature-flags/{id}` - Update feature flag
- `GET /api/admin/security-events` - List security events
- `GET /api/admin/impersonation/sessions` - List active impersonations
- `POST /api/admin/impersonation/start` - Start impersonation
- `POST /api/admin/impersonation/revoke` - Revoke impersonation
- `POST /api/admin/erasure` - Request data erasure
- `GET /api/admin/erasure/{id}` - Erasure status
- `GET /api/admin/billing/config` - Billing configuration
- `PUT /api/admin/billing/config` - Update billing config
- `GET /api/admin/billing/providers` - List billing providers
- `GET /api/admin/notifications/config` - Email config status
- `POST /api/admin/notifications/test` - Send test email
- `GET /api/admin/tenants/{id}/email-config` - Tenant email config
- `PUT /api/admin/tenants/{id}/email-config` - Update tenant email config
- `GET /api/admin/tenants/{id}/webhooks` - List webhooks
- `POST /api/admin/tenants/{id}/webhooks` - Create webhook
- `DELETE /api/admin/tenants/{id}/webhooks/{id}` - Delete webhook
- `GET /api/admin/tenants/{id}/webhooks/{id}/deliveries` - Webhook deliveries
- `POST /api/admin/tenants/{id}/impersonate` - Impersonate tenant user
- `GET /api/admin/rate-limits` - List rate limit configs
- `PUT /api/admin/rate-limits/{tenant_id}` - Update rate limit

#### 20.18 Upload (`/api/upload`)
- `POST /api/upload/chunk` - Upload file chunk
- `POST /api/upload/finalize` - Finalize chunked upload

#### 20.19 Health & Monitoring
- `GET /health` - Basic health check
- `GET /api/health/deep` - Deep health check
- `GET /api/llm-status` - LLM service status
- `GET /metrics` - Prometheus metrics

### 21. Frontend Features

#### 21.1 Pages (primary product surfaces)
- **Home / Dashboard**: Overview with action items, pipeline, metrics
- **Requisitions**: Opening list (HM: “My Openings”); create / request opening
- **Open Requests**: TA Lead / Admin assignment queue
- **Requisition Detail**: Intake, criteria, sourcing panel, pipeline, HM review, handoff
- **Screen / Analyze**: Requisition-first resume screening (`/analyze`; batch redirects here)
- **Report**: Screening result detail with scores, narrative, interview actions
- **Candidates** / **Candidate Profile**: Directory and enriched profile
- **Pipeline (Kanban)**: Visual candidate board (plan-gated)
- **Comparison**: Side-by-side candidate comparison
- **AI Interviews** / **Voice Screening** / **Recruiter Interviews**: Interview automation surfaces
- **Video** / **Transcript**: Async interview review (transcript not always in primary nav)
- **Analytics Hub**: Explore, report builder, docs
- **Team** / **Team Skills**: Members & skills gap (team-skills may be unlinked from primary nav)
- **Settings**: Subscription, requisition workflow, ATS, interviews, branding (admin)
- **Onboarding Wizard**: First-run workspace setup
- **Public Handoff**: Magic-link HM pack (`/handoff/:token`)
- **Platform Admin**: Tenants, plans, SSO, billing ops, audit, erasure, etc.
- **Auth**: Login, register, forgot/reset password, verify email
- **Legacy redirects**: `/jd-library`, `/projects`, `/templates` → requisitions / analyze as appropriate

#### 21.2 Components (selected)
- **ResultCard**: Screening result display with scores
- **InterviewScorecard**: Interview evaluation interface
- **UniversalWeightsPanel**: Weight adjustment UI
- **WeightSuggestionPanel**: AI weight suggestions display
- **VersionHistory**: Weight version tracking
- **ReanalyzeModal**: JD re-analysis modal
- **ComparisonView**: Candidate comparison view
- **UploadForm**: File upload form with validation
- **StatusBadge**: Candidate status display
- **ScoreGauge**: Fit score visualization
- **SkillRadar**: Skills radar chart
- **TimelineChart**: Employment timeline
- **CommentThread**: Inline comments
- **PipelineBoard**: Candidates by JD pipeline
- **AnalyticsChart**: Data visualization charts
- **AdminTenantList**: Tenant management table
- **AuditLogViewer**: Audit log display

#### 21.3 Contexts & Hooks
- **AuthContext**: Authentication state management
- **useAuth**: Authentication hook
- **useApi**: API client hook
- **useDebounce**: Debounce utility hook

#### 21.4 API Client
- **axios-based**: HTTP client with interceptors
- **Token Management**: Automatic JWT token attachment
- **Error Handling**: Centralized error handling
- **Request/Response Interceptors**: Token refresh on 401

### 22. Deployment & DevOps

#### 22.1 Docker Configuration
- **Backend Dockerfile**: Python 3.11, FastAPI, Uvicorn
- **Frontend Dockerfile**: Node.js build, Nginx serve
- **Nginx Dockerfile**: Custom Nginx configuration
- **Docker Compose**: Multi-service orchestration
- **Production Compose**: docker-compose.prod.yml for production

#### 22.2 CI/CD Pipeline
- **GitHub Actions**: CI workflow on PRs and pushes
- **Backend Tests**: pytest with coverage reporting
- **Frontend Tests**: vitest with coverage reporting
- **Codecov Integration**: Coverage tracking
- **Lint Checks**: Code quality validation
- **Auto Deployment**: Push to main triggers deployment
- **Docker Hub**: Versioned image tags
- **VPS Deployment**: SSH-based deployment
- **Watchtower**: Auto-update with 60s polling

#### 22.3 Environment Variables
**Required**:
- `DATABASE_URL`: PostgreSQL connection string
- `JWT_SECRET_KEY`: JWT signing secret
- `POSTGRES_PASSWORD`: PostgreSQL root password
- `OLLAMA_BASE_URL`: Ollama API endpoint
- `OLLAMA_MODEL`: Primary LLM model
- `ENVIRONMENT`: production or development

**Optional** (60+ configurable variables):
- `OLLAMA_API_KEY`: Ollama Cloud API key
- `OLLAMA_MAX_CONCURRENT`: Max concurrent LLM requests (default: 8)
- `LLM_NARRATIVE_TIMEOUT`: LLM timeout (default: 500s)
- `BATCH_MAX_CONCURRENT`: Batch concurrency (default: 30)
- `CORS_ORIGINS`: Allowed CORS origins
- `ACCESS_TOKEN_EXPIRE_MINUTES`: JWT access token lifetime
- `REFRESH_TOKEN_EXPIRE_DAYS`: JWT refresh token lifetime
- `ENABLE_PII_REDACTION`: PII redaction toggle (default: true)
- `ENABLE_EVIDENCE_VALIDATION`: Evidence validation toggle (default: true)
- `EVIDENCE_FUZZY_THRESHOLD`: Fuzzy match threshold (default: 0.75)

#### 22.4 Database Migrations
- **34 Migrations**: From initial schema to enterprise platform admin with password reset tokens
- **Alembic**: Migration management
- **Idempotent Migrations**: Safe to run multiple times
- **Production Migration**: alembic upgrade head

#### 22.5 Testing
- **Backend Tests**: 59 test files
- **Frontend Tests**: 6 test files
- **Total**: 65+ tests
- **Coverage**: pytest --cov=app --cov-report=html
- **Test Types**: Unit, integration, API, service tests

### 23. Performance & Scalability

#### 23.1 Production Requirements
- **CPU**: 4 cores minimum, 8 cores recommended
- **RAM**: 8GB minimum, 16GB recommended
- **Storage**: 50GB SSD minimum, 100GB SSD recommended
- **OS**: Ubuntu 22.04/24.04 LTS
- **Database**: PostgreSQL 16 with 200 connections, 1.5GB buffer

#### 23.2 Performance Characteristics
- **Single Analysis**: 1-2s Python scoring, 40-60s LLM narrative (background)
- **Batch Processing**: Up to 50 files, 30 concurrent
- **Transcript Analysis**: ~65s with PII redaction and evidence validation
- **Video Processing**: Depends on video length and complexity
- **Database**: Optimized queries with indexes on tenant_id, created_at, status

#### 23.3 Caching Strategy
- **JD Cache**: MD5-keyed database cache, 30-day retention
- **Skills Registry**: In-memory cache with database seeding
- **Ollama Models**: Hot models kept in RAM
- **Sentinel Health**: Background health probes with state tracking

#### 23.4 Concurrency
- **FastAPI Workers**: 4 Uvicorn workers
- **Async/Await**: Non-blocking I/O throughout
- **Background Tasks**: LLM narratives processed asynchronously
- **Queue Worker**: Dedicated queue processing
- **Semaphore Control**: Configurable batch concurrency

### 24. Extensibility & Customization

#### 24.1 Pluggable Architecture
- **Billing Providers**: Factory pattern for Stripe, Razorpay, custom providers
- **LLM Models**: Configurable models via environment variables
- **Skill Registry**: Dynamic skills with discovery and frequency tracking
- **Feature Flags**: Runtime feature toggling
- **Webhooks**: Event-driven integrations

#### 24.2 Customization Points
- **Scoring Weights**: Per-tenant, per-template, per-analysis customization
- **Email Templates**: AI-generated with fallback templates
- **Role Templates**: Custom JDs with tailored weights
- **Subscription Plans**: Configurable limits and features
- **Rate Limits**: Per-tenant configuration

#### 24.3 Integration Options
- **REST API**: Complete RESTful API with OpenAPI docs
- **Webhooks**: Event notifications to external systems
- **Export**: CSV/Excel for ATS integration
- **SSO Ready**: JWT-based authentication extensible to SAML/OAuth

### 25. Compliance & Certifications Readiness

#### 25.1 Data Privacy
- **GDPR**: PII redaction, right to explanation, data erasure
- **CCPA**: Data access and deletion capabilities
- **Data Minimization**: Only necessary data processed
- **Consent Tracking**: Usage logs with user attribution

#### 25.2 Employment Law
- **EEOC**: Evidence-based decisions, no demographic factors
- **Adverse Action**: Compliant rejection documentation (in progress)
- **Audit Trail**: Complete decision logging
- **Fair Hiring**: Bias elimination through PII redaction

#### 25.3 Security Standards
- **OWASP**: CSRF protection, input validation, injection prevention
- **JWT Best Practices**: Token revocation, expiration, secure signing
- **Password Security**: Bcrypt hashing
- **Encryption**: Fernet encryption for SMTP passwords
- **Audit Logging**: All admin actions logged with IP and user

#### 25.4 AI Ethics
- **Explainability**: All scores and decisions explained
- **Evidence-Based**: Claims backed by source data
- **Hallucination Prevention**: Validation against source
- **Deterministic Fallback**: Rule-based backup for reliability
- **Transparency**: Weight configuration visible and adjustable

### 26. Competitive Advantages

#### 26.1 Deployment & AI Processing
- **Cloud-First Default**: Ollama Cloud (and optional Google Gemini) for immediate setup, no GPU required
- **Transparent Subprocessors**: AI provider usage disclosed in onboarding and Settings → Security
- **Optional Local LLM (Advanced)**: Legacy/self-managed path for air-gapped lab environments only — not the default ThetaLogics SaaS deployment
- **Open Source**: Application code auditable by customers
- **MIT License**: Permissive licensing

#### 26.2 Enterprise Features
- **Multi-Tenant**: True SaaS architecture
- **Granular RBAC**: 5 tenant roles (`admin`, `ta_lead`, `recruiter`, `hiring_manager`, `viewer`) + platform admin roles
- **Audit Compliance**: Comprehensive logging
- **Feature Flags**: Runtime control
- **Webhooks**: Event-driven integrations
- **Rate Limiting**: Per-tenant control

#### 26.3 AI Quality
- **Hybrid Pipeline**: Deterministic + LLM best of both worlds
- **Evidence Validation**: Hallucination prevention
- **PII Redaction**: Bias elimination
- **Weight Customization**: Adaptive scoring per role
- **Fallback Mechanisms**: High availability

#### 26.4 Developer Experience
- **Complete API**: 100+ endpoints
- **OpenAPI Docs**: Auto-generated Swagger UI
- **Testing**: 663 tests with coverage
- **CI/CD**: Automated deployment
- **Monitoring**: Prometheus metrics
- **Health Checks**: Comprehensive diagnostics

### 27. Target Market & Use Cases

#### 27.1 Primary Markets
- **Mid-Market Companies**: 50-500 employees, high-volume hiring
- **Enterprise**: 500+ employees, compliance requirements
- **Recruitment Agencies**: Multi-client candidate screening
- **Staffing Firms**: High-volume resume processing
- **Government**: Data sovereignty and compliance needs

#### 27.2 Use Cases
- **Technical Hiring**: Software engineers, data scientists, DevOps
- **Sales Hiring**: Sales reps, account managers, SDRs
- **Healthcare**: Nurses, physicians, allied health
- **Finance**: Analysts, accountants, compliance officers
- **Operations**: Project managers, operations managers
- **Leadership**: Directors, VPs, C-suite

#### 27.3 Industry Verticals
- **Technology**: SaaS, hardware, IT services
- **Healthcare**: Hospitals, clinics, health tech
- **Finance**: Banks, fintech, insurance
- **Manufacturing**: Production, supply chain
- **Retail**: E-commerce, brick-and-mortar
- **Government**: Regulated agencies (contact sales for dedicated deployment options)
- **Education**: Universities, edtech

### 27.4 Deployment Models

**Managed SaaS (Default — ThetaLogics hosted)**:
- Application: Multi-tenant FastAPI + React on customer-facing cloud infrastructure
- LLM Runtime: Ollama Cloud (https://ollama.com) and/or Google Gemini when configured
- Setup: Requires `OLLAMA_API_KEY` (and optional `GEMINI_API_KEY`)
- Data Flow: Resume/JD/interview content sent to configured AI providers for inference; results persisted in tenant-scoped PostgreSQL
- Privacy: Tenant isolation, encryption in transit, GDPR export/erasure, optional PII redaction before LLM calls

**Optional Local LLM Mode (Advanced / self-managed only)**:
- Not used in standard ThetaLogics SaaS staging or production stacks
- LLM Runtime: Local Ollama instance on customer infrastructure
- Use Case: Lab, air-gapped, or dedicated enterprise deployments negotiated separately
- Data Flow: Inference stays on customer network when this mode is explicitly configured

### 28. Pricing Strategy (Reference)

#### 28.1 Subscription Tiers
**Free Tier**:
- 50 analyses/month
- 1 GB storage
- 3 team members
- Core resume screening
- Basic analytics

**Pro Tier** ($49/month):
- 500 analyses/month
- 10 GB storage
- 10 team members
- Video & transcript analysis
- Advanced analytics
- Email support

**Enterprise Tier** (Custom pricing):
- Unlimited analyses
- 100 GB+ storage
- Unlimited team members
- Custom AI training
- API access
- Priority support (24/7 phone)
- Custom integrations
- SLA guarantees

### 29. Implementation Status

#### 29.1 Production-Ready
✅ Resume analysis (single + batch; requisition-linked when configured)  
✅ Requisitions & ICP hiring workflow (open requests, TA lead assignment, HM intake/outcomes, sourcing brief, handoff)  
✅ Candidate management with deduplication  
✅ JD input + caching (ad-hoc when allowed; legacy library → requisitions)  
✅ Side-by-side comparison  
✅ Video interview analysis  
✅ Transcript analysis with PII redaction  
✅ Evidence validation  
✅ Interview kit & scorecard (incl. voice personalization)  
✅ AI / voice / recruiter interview modes  
✅ Team collaboration (5 tenant roles)  
✅ Workspace onboarding wizard  
✅ Email generation  
✅ Export (CSV/Excel)  
✅ Custom AI training  
✅ Dashboard & analytics / report builder  
✅ Subscription & billing  
✅ Platform administration  
✅ Audit logging  
✅ Security events  
✅ Feature flags  
✅ Webhooks / ATS hooks  
✅ Rate limiting  
✅ Data erasure  
✅ O*NET integration  
✅ Queue system  
✅ Monitoring & health checks  
✅ CI/CD pipeline  
✅ Automated backend + frontend test suites  

#### 29.2 In Progress / Partial
🔄 Adverse action report system  
🔄 Calibration & drift detection  
🔄 Multi-model consensus scoring  

#### 29.3 Planned
📋 Batch transcript processing  
📋 Custom calibration datasets per tenant  
📋 ML-based bias detection  
📋 Advanced ATS deep integrations  
📋 Mobile application  
📋 External job-board / LinkedIn sourcing APIs (explicitly deferred)

### 30. Key Differentiators Summary

1. **Managed SaaS + transparent AI**: Cloud-hosted platform with disclosed AI subprocessors — not a “data never leaves your server” product
2. **Workflows for audit/export/erasure**: supports GDPR-related workflows and evidence trails; not independently certified as “GDPR/EEOC compliant”
3. **AI Quality**: Hybrid pipeline, hallucination guards, PII redaction — not 100% accurate or bias-free
4. **Enterprise-Grade**: Multi-tenant, granular RBAC, webhooks, rate limiting
5. **Open Source**: MIT license, fully auditable
6. **Customizable**: Adaptive weights, custom training, feature flags
7. **Production-Ready**: Automated test suites, CI/CD, monitoring, health checks
8. **Comprehensive API**: 100+ endpoints, OpenAPI docs
9. **Explainability**: Transparent scoring, decision explanations
10. **Scalable**: Async architecture, queue system, caching

---

## Contact & Support

**Company**: ThetaLogics  
**Website**: https://thetalogics.com  
**Email**: contact@thetalogics.com  
**GitHub**: https://github.com/thetalogics/aria  
**Issues**: https://github.com/thetalogics/aria/issues  
**Discussions**: https://github.com/thetalogics/aria/discussions  

---

**Document Version**: 2.2.0  
**Created**: May 2026  
**Last Audited**: August 2, 2026  
**Status**: Complete and Production-Verified (docs synced to codebase August 2, 2026)  

This document is the single source of truth for ARIA's features, architecture, and capabilities. It is verified against the actual codebase and reflects the production-ready state as of August 2026.
