# ARIA v2.2.0

<p align="center">
  <img src="https://img.shields.io/badge/version-2.2.0-blue.svg" alt="Version">
  <img src="https://img.shields.io/badge/python-3.11-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/react-18.3.1-61dafb.svg" alt="React">
  <img src="https://img.shields.io/badge/fastapi-0.115.6-009688.svg" alt="FastAPI">
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License">
</p>

<p align="center">
  <strong>AI Resume Intelligence by ThetaLogics</strong><br>
  Multi-tenant SaaS for AI-assisted resume screening and candidate evaluation.
</p>

<p align="center">
  <em>Managed AI screening for hiring teams — explainable scores and tenant isolation. Operational readiness depends on how you deploy it.</em>
</p>

---

## Overview

ARIA is a comprehensive AI-powered recruitment platform designed for modern hiring teams. It combines structured resume parsing, explainable fit scoring, and interview automation in a multi-tenant SaaS workspace hosted by ThetaLogics.

### Key Value Propositions

- **Requisition-centric hiring** — Openings, intake calibration, screening, and HM handoff in one workflow
- **Explainable AI Screening** — Fit scores, skill matching, gap detection, and narrative reports recruiters can trust
- **Tenant isolation** — Multi-tenant workspaces, RBAC (5 tenant roles), admin MFA, and GDPR export/erasure **workflows** (not a legal compliance certification)
- **Interview assistance** — AI screen calls and live screen kits integrated with your pipeline
- **Multi-tenant SaaS architecture** — Organizations are isolated by `tenant_id` in application queries
- **Hardened toward production** — tests, CI/CD, worker isolation, Redis, and monitoring. What is actually verified lives in `docs/PRODUCTION_READINESS.md`.

---

## Features

### Requisitions & Hiring Workflow
- **Requisitions** — Create and manage openings (intake → criteria lock → sourcing → interviewing)
- **Open Requests** — Hiring managers request openings; TA Lead / Admin assigns a recruiter
- **Collaborative Intake** — HM edit + approve; `changes_requested` loop; criteria lock stepper
- **Recruiter Assignment** — `ta_lead` / admin assign recruiters; assignees can manage the req
- **Screening Modes** — Tenant setting `requisition_required` (default) or `allow_ad_hoc` quick screen
- **Submit to HM** — Pipeline submit with recruiter note; email + tenant event notification
- **HM Outcomes** — Shortlist / reject with required reason codes; feedback applied to sourcing brief
- **Sourcing Panel** — In-app search brief and manual channel checklist (no external job-board APIs)
- **Handoff Pack** — Consolidated candidate pack for HM (`/requisitions/:id/handoff`, public `/handoff/:token`)
- **Plan Gates** — Features `requisitions`, `hm_workflow`, and related interview entitlements

### Resume Analysis
- **Requisition-first Screen** — Analyze attaches results to an opening when screening mode requires it
- **Single-File Analysis** — Upload PDF/DOCX with real-time SSE streaming results
- **Batch Processing** — Process up to 50 files simultaneously with concurrency control
- **Fit Scoring** — 0-100 score with detailed breakdown (skill/experience may be detailed objects)
- **Strengths & Weaknesses** — AI-identified candidate attributes
- **Risk Signal Detection** — Fake patterns, job hopping, credential inflation
- **Employment Gap Analysis** — Severity classification (negligible/minor/moderate/critical)
- **Education Validation** — Degree relevance and institution assessment
- **Skills Matching** — Against skills registry with fuzzy matching and confidence-weighted scores
- **AI Narrative Generation** — Background LLM processing with deterministic fallback

### Candidate Management
- **Centralized Database** — Tenant-scoped candidate profiles
- **3-Layer Deduplication** — Email → File hash → Name+Phone matching
- **Enriched Profiles** — Skills, education, work experience, gaps
- **Quick Re-Analysis** — Evaluate candidates against new JDs without re-upload
- **Status Tracking** — pending / shortlisted / rejected / in-review / hired

### Job Description Handling
- **Requisition JD** — Primary path: opening intake and criteria (legacy JD library redirects to `/requisitions`)
- **Manual Entry** — Direct text input (ad-hoc when tenant allows)
- **File Upload** — PDF and DOCX support
- **URL Scraping** — LinkedIn, Indeed, and other job boards
- **JD Caching** — MD5-keyed cache with 30-day retention
- **Role Templates** — Saved templates still available via API / migration paths

### Side-by-Side Comparison
- **Multi-Candidate Compare** — Evaluate 2-5 candidates simultaneously
- **Score Breakdown** — Detailed comparison across all metrics
- **Interview Questions** — AI-generated role-specific questions
- **Adjacent Skills** — Related capabilities analysis

### Video Interview Analysis
- **Video Upload** — MP4, WebM, AVI, MOV, MKV, M4V (200MB max)
- **Video URL Processing** — Zoom, Teams, Loom, Google Drive
- **Auto-Transcription** — Powered by faster-whisper
- **Communication Analysis** — LLM-evaluated speaking quality

### Transcript Analysis
- **File Upload** — TXT, VTT, SRT formats (5MB max)
- **Interview Analysis** — Evaluate against job descriptions
- **Structured Insights** — Communication patterns and content analysis

### AI Recruiter (Intelligent Interview Agent)
- **Voice-based AI interviews** extending existing LiveKit/Twilio voice infrastructure
- **Interview Orchestrator**: Real-time adaptive interview engine with 7 structured stages
  - **Introduction** — Identity verification, process explanation, consent, mic check
  - **Resume Verification** — Compare answers against resume claims, flag inconsistencies
  - **Technical** — Adaptive difficulty based on real-time answer scoring (0-100)
  - **Behavioral** — STAR-format questions with LLM evaluation
  - **Communication** — Real-time metrics: speaking speed, fillers, silence, confidence
  - **Motivation** — Career goals, why this company, salary expectations
  - **Closing** — Candidate questions, next steps
- **Dynamic Question Planner**: Generates questions in real-time based on stage, answer scores, conversation history, resume data, and time remaining
- **Real-time Answer Evaluation**: LLM scores each answer 0-100, adjusts difficulty (harder/same/simpler/switch_topic)
- **Kokoro TTS**: Professional, human-like voice (CPU-based, no GPU required)
- **VAD-driven speech segmentation**: Natural turn-taking, eliminates fixed audio buffer latency
- **Recruiter Copilot**: Per-answer observations with confidence level, concerns, and suggested follow-ups
- **7-dimension scoring**: Technical, Behavioral, Communication, Cultural Fit, Motivation, Integrity, Confidence
- **Fitment score verification & adjustment** against existing screening analysis
- **Auto-trigger**: Configurable to automatically interview candidates at specific pipeline stages
- **Independent scorecard** with hiring recommendation (strong_hire → strong_no_hire)
- **Full transcript viewer** with per-question evaluation annotations
- Access via: `/ai-interviews` (Navigation → **Interviews**)

### Interview modes (recruiter-facing)

| Mode | Who calls | Where in app |
|------|-----------|----------------|
| **AI Screen Call** | ARIA bot → candidate | Report actions, `/ai-interviews` |
| **Live Screen Kit** | Recruiter → candidate | Report → split view (resume + questions + debrief) |
| **Video / transcript review** | Async | `/video`, `/transcript` |

The Live Screen Kit requires background **interview kit enrichment**. If questions are still generating, wait for enrichment to complete before starting a live call.

**Debugging kit fallback:** Check `interview_kit_status` on the report API (`ready` = Gemini kit, `fallback` = template kit). When fallback, `interview_kit_error` explains why (e.g. `timeout:`, `empty_kit:`, `ValueError:`). Search backend logs for `Interview kit LLM` + `screening_result_id`. Env: `LLM_INTERVIEW_KIT_TIMEOUT` (default 180s), `LLM_INTERVIEW_KIT_RETRIES` (default 2).

### Team Collaboration
- **Multi-User Tenants** — RBAC roles: `admin`, `ta_lead`, `recruiter`, `hiring_manager`, `viewer`
- **Role-aware Nav** — Recruiters: Home / Requisitions / Screen / Candidates; HMs: My Openings / Candidates
- **Member Invitations** — Email-based team onboarding (includes `ta_lead` / HM invites)
- **Comments** — Discuss screening results inline
- **Shared Lists** — Collaborative candidate shortlists
- **Workspace Onboarding** — Guided wizard (org, plan, invites, sample seed / checklist)

### Reporting & Export
- **Detailed Reports** — Score gauges, skill radar charts, timelines
- **PDF Generation** — Professional screening reports
- **CSV Export** — Spreadsheet-compatible data
- **Excel Export** — Formatted workbook output
- **Email Templates** — Shortlist / rejection / screening call generation

### Custom AI Training
- **Outcome Labeling** — Tag past screenings (hired/rejected)
- **Model Fine-Tuning** — Ollama Modelfile customization (10+ examples required)
- **Per-Tenant Models** — Organization-specific AI tuning

### Subscription & Billing
- **Tiered Plans** — Free / Pro / Enterprise with configurable limits
- **Usage Tracking** — Analyses/month, storage, team member limits
- **Stripe Integration** — Production-ready payment processing

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              CLIENT LAYER                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │   Browser   │  │   Browser   │  │   Browser   │  │   Browser   │    │
│  │  (Tenant A) │  │  (Tenant B) │  │  (Tenant C) │  │  (Admin)    │    │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘    │
└─────────┼────────────────┼────────────────┼────────────────┼───────────┘
          │                │                │                │
          └────────────────┴────────────────┴────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           REVERSE PROXY                                  │
│                     ┌─────────────────────┐                              │
│                     │   Nginx (443/SSL)   │                              │
│                     │  SSL Termination    │                              │
│                     │  Static File Serve  │                              │
│                     └─────────────────────┘                              │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          ▼                        ▼                        ▼
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│   FRONTEND      │      │     BACKEND     │      │   PROMETHEUS    │
│  ┌───────────┐  │      │  ┌───────────┐  │      │  ┌───────────┐  │
│  │   React   │  │      │  │  FastAPI  │  │      │  │  Metrics  │  │
│  │   Vite    │  │      │  │  Uvicorn  │  │      │  │  /metrics │  │
│  │  Nginx    │  │      │  │  4 Workers│  │      │  └───────────┘  │
│  └───────────┘  │      │  └─────┬─────┘  │      └─────────────────┘
└─────────────────┘      │        │        │
                         │  ┌─────┴─────┐  │
                         │  │           │  │
                         ▼  ▼           ▼  ▼
                ┌─────────────────────────────────────┐
                │           SERVICE LAYER              │
                │  ┌─────────┐ ┌─────────┐ ┌────────┐ │
                │  │  LLM    │ │ Parser  │ │  Gap   │ │
                │  │ Service │ │ Service │ │Detector│ │
                │  └─────────┘ └─────────┘ └────────┘ │
                │  ┌─────────┐ ┌─────────┐ ┌────────┐ │
                │  │  Video  │ │Analysis │ │Export  │ │
                │  │ Service │ │ Service │ │Service │ │
                │  └─────────┘ └─────────┘ └────────┘ │
                └─────────────────────────────────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
┌─────────────────┐ ┌──────────┐ ┌─────────────────┐
│     OLLAMA      │ │PostgreSQL│ │   FILE STORE    │
│  ┌───────────┐  │ │  ┌────┐  │ │  ┌───────────┐  │
│  │gemma4:31b │  │ │  │16  │  │ │  │  Uploads  │  │
│  │  (LLM)    │  │ │  │GB  │  │ │  │  Resumes  │  │
│  │ 8 Cores   │  │ │  └────┘  │ │  │  Videos   │  │
│  │ 8GB RAM   │  │ │ 200 conn │ │  └───────────┘  │
│  └───────────┘  │ │1.5GB buf│  │                 │
└─────────────────┘ └──────────┘ └─────────────────┘
```

---

## Tech Stack

| Layer | Technology | Version | Purpose |
|-------|-----------|---------|---------|
| **Language** | Python | 3.11 | Backend runtime |
| **Backend Framework** | FastAPI | 0.115.6 | API development |
| **ASGI Server** | Uvicorn | 0.34.0 | Production server |
| **Database (Prod)** | PostgreSQL | 16 | Primary datastore |
| **Database (Dev)** | SQLite | 3.x | Local development |
| **ORM** | SQLAlchemy | 2.0.38 | Database abstraction |
| **Migrations** | Alembic | 1.13.3 | Schema versioning |
| **Frontend Framework** | React | 18.3.1 | UI library |
| **Build Tool** | Vite | 6.0.5 | Frontend bundling |
| **Styling** | TailwindCSS | 3.4.17 | Utility-first CSS |
| **Charts** | Recharts | 3.x | Data visualization |
| **Icons** | Lucide React | latest | Icon library |
| **LLM Runtime** | Ollama | latest | Local inference |
| **LLM Model** | gemma4:31b-cloud | latest | Primary cloud model |
| **LLM Framework** | LangChain + LangGraph | 0.2.0+ / 0.3.0+ | LLM orchestration |
| **Authentication** | python-jose + bcrypt | 3.3.0 | JWT + password hashing |
| **PDF Parsing** | pdfplumber + PyMuPDF | 0.11.5 | Document extraction |
| **NLP** | spaCy + rapidfuzz | 3.7+ / 3.6+ | Text processing |
| **Video Processing** | faster-whisper + yt-dlp | 1.1.0 | Transcription |
| **Export** | pandas + openpyxl | 2.2.3 | Data export |
| **Monitoring** | Prometheus | latest | Metrics collection |
| **Testing (Backend)** | pytest + pytest-asyncio | 8.3.4 | Test framework |
| **Testing (Frontend)** | vitest + React Testing Library | 2.1.8 | Test framework |

---

## AI & Data Processing

ARIA is a **managed cloud SaaS** product. Resume, job description, and interview content are processed by configured AI providers to generate scores, narratives, and interview kits.

| Provider | Purpose |
|----------|---------|
| **Ollama Cloud** | Narratives, JD profiling, voice metadata (default) |
| **Google Gemini** | Optional analysis backend when `GEMINI_API_KEY` is set |
| **LiveKit** | Voice screening calls |

Candidate and screening data are stored in your **tenant-isolated** PostgreSQL workspace. GDPR export and erasure tools are available in the product. Users acknowledge AI processing during onboarding; details appear under **Settings → Security → AI & data processing**.

### Optional: Local Ollama (Advanced)

Local Ollama is supported for **self-managed / lab** deployments only. It is **not** the default for ThetaLogics-hosted staging or production. See [Using Local Ollama (Optional)](#using-local-ollama-optional-advanced) if you operate your own stack.

---

### Prerequisites

- Docker 24.0+ and Docker Compose
- Ollama Cloud API key (get it free from [ollama.com/settings/keys](https://ollama.com/settings/keys))
- Git

> **Note:** Ollama Cloud is the default for ThetaLogics SaaS. Local Ollama is an optional advanced path — see [Using Local Ollama (Optional, Advanced)](#using-local-ollama-optional-advanced).

### Quick Start (Ollama Cloud — Default)

1. **Get your API key** from [ollama.com/settings/keys](https://ollama.com/settings/keys)

2. **Copy and configure environment:**
```bash
cp .env.example .env
# Edit .env and set your OLLAMA_API_KEY
```

3. **Start ARIA:**
```bash
docker-compose up --build
```

The local Ollama container will still start but won't be used. To disable it entirely, see [Production with Ollama Cloud](#production-with-ollama-cloud).

### Using Local Ollama (Optional, Advanced)

For **self-managed** deployments where you operate your own Ollama instance (not standard ThetaLogics SaaS):

1. **Update your `.env` file:**
```bash
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=gemma4:31b-cloud
LLM_NARRATIVE_TIMEOUT=180
# OLLAMA_API_KEY can be left empty for local
```

2. **Pull the model:**
```bash
docker exec -it resume-screener-ollama ollama pull gemma4:31b
```

3. **Restart to use local Ollama.**

**Requirements for Local Ollama:**
- 8GB+ RAM available for Docker
- GPU recommended (CPU inference supported but slower)

**Pros & Cons:**

| Aspect | Ollama Cloud (Default) | Local Ollama (Advanced) |
|--------|------------------------|-------------------------|
| **Setup** | Instant, no GPU needed | Requires GPU/CPU resources on your infra |
| **Data flow** | Inference via Ollama Cloud API | Inference on your network |
| **Model Quality** | Access to cloud models (e.g. gemma4) | Limited by local hardware |
| **Typical use** | ThetaLogics SaaS production | Lab, air-gapped, dedicated enterprise |

**Note:** `/api/training` stores labeled examples for **prompt/model personalization**. It does not train or ship a fine-tuned model in the hosted SaaS path.

### Development Setup

```bash
# Clone the repository
git clone https://github.com/thetalogics/aria.git
cd aria

# Copy environment template
cp .env.example .env

# Start all services
docker-compose up --build

# In a separate terminal, pull the LLM model
docker exec -it resume-ai-ollama-1 ollama pull gemma4:31b
```

### Access Points

| Service | URL | Description |
|---------|-----|-------------|
| Frontend | http://localhost:80 | React application |
| API Docs | http://localhost:80/docs | Swagger UI |
| Health | http://localhost:80/health | Service health |
| Metrics | http://localhost:80/metrics | Prometheus metrics |

---

## Production Deployment

### VPS Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 4 cores | 8 cores |
| RAM | 8 GB | 16 GB |
| Storage | 50 GB SSD | 100 GB SSD |
| OS | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |

### Environment Setup

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker

# 2. Create application directory
sudo mkdir -p /opt/aria
sudo chown $USER:$USER /opt/aria

# 3. Install Certbot
sudo apt update
sudo apt install -y certbot

# 4. Obtain SSL certificate
sudo certbot certonly --standalone \
  -d your-domain.com \
  --email admin@your-domain.com \
  --agree-tos \
  --non-interactive
```

### Production Docker Compose

```bash
# Copy production compose and environment
cp docker-compose.prod.yml /opt/aria/
cp .env.production /opt/aria/.env

# Edit environment variables
nano /opt/aria/.env

# Start production stack
cd /opt/aria
docker-compose -f docker-compose.prod.yml up -d
```

### Production with Local Ollama (Optional)

For production deployments with a dedicated GPU and full data privacy:

```bash
# 1. Edit /opt/aria/.env with local settings:
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=gemma4:31b-cloud
LLM_NARRATIVE_TIMEOUT=180
# OLLAMA_API_KEY can be omitted

# 2. Start with local Ollama service
cd /opt/aria
docker-compose -f docker-compose.prod.yml up -d

# 3. Pull the model
docker exec resume-screener-ollama ollama pull gemma4:31b

# 4. Verify connectivity
curl http://localhost:8080/api/llm-status
```

### GitHub Actions Setup

Configure these secrets in your repository (Settings → Secrets → Actions):

| Secret | Description |
|--------|-------------|
| `DOCKERHUB_USERNAME` | Docker Hub username |
| `DOCKERHUB_TOKEN` | Docker Hub access token |
| `VPS_HOST` | Production server IP or domain |
| `VPS_USERNAME` | SSH username (e.g., `ubuntu`) |
| `VPS_SSH_KEY` | SSH private key (full content) |

### SSH Key Generation

```bash
# Generate key pair
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/github_actions_vps

# Add public key to VPS
cat ~/.ssh/github_actions_vps.pub | ssh user@your-vps "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"

# Copy private key for GitHub secret
cat ~/.ssh/github_actions_vps
```

---

## Configuration

### Required Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://aria:password@db:5432/aria_db` |
| `JWT_SECRET_KEY` | Secret for JWT signing | Generate with `openssl rand -hex 32` |
| `POSTGRES_PASSWORD` | PostgreSQL root password | Strong password |
| `OLLAMA_API_KEY` | Ollama Cloud API key (default) | `ollama_xxxxxxxxxxxxxxxx` |
| `OLLAMA_BASE_URL` | Ollama API endpoint | `https://ollama.com` or `http://ollama:11434` |
| `OLLAMA_MODEL` | Primary LLM model | `gemma4:31b-cloud` |
| `ENVIRONMENT` | Runtime environment | `production` or `development` |

### Optional Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CORS_ORIGINS` | `http://localhost,http://localhost:80` | Allowed CORS origins |
| `OLLAMA_STARTUP_REQUIRED` | `1` | Wait for Ollama on startup (auto-skipped for cloud) |
| `LLM_NARRATIVE_TIMEOUT` | `500` | LLM generation timeout (seconds) |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | JWT access token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `30` | JWT refresh token lifetime |
| `POSTGRES_USER` | `aria` | PostgreSQL username |
| `POSTGRES_DB` | `aria_db` | PostgreSQL database name |
| `OLLAMA_MAX_CONCURRENT` | `8` | Max concurrent LLM requests |

### Local Ollama Configuration (Optional)

For self-hosted Ollama instead of cloud:

```bash
# Local Ollama settings (replaces cloud defaults)
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODEL=gemma4:31b-cloud
LLM_NARRATIVE_TIMEOUT=180
# OLLAMA_API_KEY can be omitted for local
```

---

## API Reference

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register` | Create new account |
| POST | `/api/auth/login` | Authenticate user |
| POST | `/api/auth/refresh` | Refresh access token |
| GET | `/api/auth/me` | Get current user |
| POST | `/api/auth/logout` | Revoke tokens |

### Analysis

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/analyze` | Single resume analysis |
| POST | `/api/analyze/stream` | SSE streaming analysis |
| POST | `/api/analyze/batch` | Batch processing (up to 50) |
| GET | `/api/history` | Analysis history |
| GET | `/api/analysis/{id}/narrative` | Get narrative result |
| PUT | `/api/results/{id}/status` | Update candidate status |

### Candidates

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/candidates` | List candidates (paginated) |
| GET | `/api/candidates/{id}` | Get candidate details |
| PATCH | `/api/candidates/{id}` | Update candidate |
| POST | `/api/candidates/{id}/analyze-jd` | Re-analyze against JD |

### Comparison

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/compare` | Compare 2-5 candidates |

### Requisitions & Hiring Workflow

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/api/requisitions` | List / create requisitions |
| GET/PATCH | `/api/requisitions/{id}` | Get / update requisition |
| PUT | `/api/requisitions/{id}/assign-recruiter` | Assign recruiter (`admin` / `ta_lead`) |
| GET/POST | `/api/requisitions/open-requests` | HM open-request queue |
| POST | `/api/requisitions/open-requests/{id}/assign` | Assign open request → requisition |
| POST | `/api/requisitions/{id}/hm-approval` | HM approve / request changes |
| POST | `/api/requisitions/{id}/candidates/{cid}/submit` | Submit candidate to HM |
| POST | `/api/requisitions/{id}/candidates/{cid}/outcome` | HM shortlist / reject (+ reason) |
| POST | `/api/requisitions/{id}/apply-feedback` | Apply reject feedback to sourcing brief |
| GET | `/api/requisitions/{id}/handoff` | HM handoff package |
| GET/PUT | `/api/requisitions/settings` | Tenant screening mode & HM pipeline permission |

### Templates

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/templates` | List JD templates |
| POST | `/api/templates` | Create template |
| PUT | `/api/templates/{id}` | Update template |
| DELETE | `/api/templates/{id}` | Delete template |

### Team & Collaboration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/team` | List team members |
| POST | `/api/invites` | Invite team member (roles include `ta_lead`, `hiring_manager`) |
| DELETE | `/api/team/{user_id}` | Remove member |
| GET | `/api/results/{id}/comments` | Get comments |
| POST | `/api/results/{id}/comments` | Add comment |

### Subscription

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/subscription` | Current subscription |
| GET | `/api/subscription/available-plans` | Available plans |
| POST | `/api/subscription/upgrade` | Upgrade plan |

### Export

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/export/csv` | Export as CSV |
| GET | `/api/export/excel` | Export as Excel |

### Video & Transcript

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/analyze/video` | Upload video |
| POST | `/api/analyze/video-url` | Process video URL |
| POST | `/api/transcript/analyze` | Analyze transcript |
| GET | `/api/transcript/analyses` | List analyses |

### Training

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/training/label` | Label outcome |
| POST | `/api/training/train` | Trigger prompt personalization job |
| GET | `/api/training/status` | Training status |

### Health & Monitoring

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Basic health check |
| GET | `/api/health/deep` | Deep health check |
| GET | `/api/llm-status` | LLM service status |
| GET | `/metrics` | Prometheus metrics |

---

## Database Schema

### Core Models

**Tenant**
- `id`, `name`, `slug`, `created_at`, `subscription_plan_id`
- Multi-tenancy root entity

**User**
- `id`, `email`, `hashed_password`, `role` (`admin` / `ta_lead` / `recruiter` / `hiring_manager` / `viewer`)
- `tenant_id`, `is_active`, `created_at`
- RBAC-enabled user accounts

**Candidate**
- `id`, `tenant_id`, `email`, `phone`, `name`
- `file_hash`, `parsed_data`, `skills`, `education`, `experience`
- Enriched candidate profile with deduplication fields

**Requisition**
- Opening lifecycle fields: intake, criteria version, status, assigned recruiter, opened-on-behalf HM
- Routing policy JSON, search/sourcing brief, HM pipeline permission (tenant settings)

**RequisitionOpenRequest**
- HM-initiated opening requests pending TA Lead / Admin assignment

**ScreeningResult**
- `id`, `candidate_id`, `job_description`, `fit_score`
- `strengths`, `weaknesses`, `employment_gaps`, `risk_signals`
- `final_recommendation`, `narrative`, `status`, `created_at`
- Optional link to requisition / pipeline candidate

**Skill**
- `id`, `name`, `category`, `aliases`
- Dynamic skills registry (seeded + discovered)

**SubscriptionPlan**
- `id`, `name`, `price_monthly`, `max_analyses`, `max_storage`
- `max_team_members`, `features` (JSON) — includes `requisitions`, `hm_workflow`, etc.

**TranscriptAnalysis**
- `id`, `candidate_id`, `transcript_text`, `analysis_result`
- Video interview analysis results

**TrainingExample**
- `id`, `tenant_id`, `screening_result_id`, `outcome` (hired/rejected)
- Custom model training data

---

## Security

### Authentication & Authorization
- **JWT Tokens** — Access tokens (60min) + refresh tokens (30 days)
- **Token Revocation** — Secure logout with revoked token tracking
- **Bcrypt Hashing** — Industry-standard password storage
- **RBAC** — Tenant roles: `admin`, `ta_lead`, `recruiter`, `hiring_manager`, `viewer`
- **Admin MFA** — TOTP required for tenant admins and platform roles (password login)
- **SSO/SAML** — Not a shipped customer feature in this release; do not enable for paying tenants

### Data Protection
- **Multi-Tenant Isolation** — All queries scoped by `tenant_id`
- **CSRF Protection** — Middleware for state-changing operations
- **CORS Configuration** — Origin whitelist enforcement
- **Request Correlation IDs** — Full request tracing

### AI Safety
- **Prompt Injection Sanitization** — Input validation and filtering
- **Deterministic Fallbacks** — Rule-based backup when LLM fails
- **Output Validation** — Schema-enforced response parsing

---

## Testing

### Backend Tests

```bash
# Run all backend tests
cd app/backend
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=app --cov-report=html

# Run specific test file
pytest tests/test_analysis_service.py -v

# Run with asyncio support
pytest tests/ -v --asyncio-mode=auto
```

**Test Coverage:** 572 tests across 19 test files

### Frontend Tests

```bash
cd app/frontend

# Run all tests
npm test

# Run with coverage
npm run test:coverage

# Run in watch mode
npm run test:watch
```

**Test Coverage:** 91 tests across 6 test files

### Total Test Suite: 663 tests

---

## CI/CD Pipeline

### Continuous Integration

Triggered on PRs and pushes to `main`/`staging`:

1. **Backend Tests** — pytest with coverage reporting
2. **Frontend Tests** — vitest with coverage reporting
3. **Codecov Upload** — Coverage tracking
4. **Lint Checks** — Code quality validation

### Continuous Deployment

Triggered on push to `main`:

1. **Build Images** — Backend, frontend, nginx
2. **Push to Docker Hub** — Versioned tags
3. **Deploy to VPS** — SSH-based deployment
4. **Watchtower Auto-Update** — 60s polling interval

### Workflow Files

- `.github/workflows/ci.yml` — Pull request validation
- `.github/workflows/cd.yml` — Production deployment

---

## Subscription Plans

| Feature | Free | Pro | Enterprise |
|---------|------|-----|------------|
| **Price** | $0/mo | $49/mo | Custom |
| **Analyses/Month** | 50 | 500 | Unlimited |
| **Storage** | 1 GB | 10 GB | 100 GB |
| **Team Members** | 3 | 10 | Unlimited |
| **Batch Processing** | ✓ | ✓ | ✓ |
| **Video Analysis** | — | ✓ | ✓ |
| **Custom Training** | — | — | ✓ |
| **API Access** | — | — | ✓ |
| **Priority Support** | — | Email | 24/7 Phone |

---

## Project Structure

```
.
├── .github/
│   └── workflows/
│       ├── ci.yml              # CI pipeline
│       └── cd.yml              # CD pipeline
├── alembic/
│   ├── versions/               # Database migrations
│   │   ├── 001_enrich_candidates_add_caches.py
│   │   ├── 002_parser_snapshot_json.py
│   │   ├── 003_subscription_system.py
│   │   ├── 004_narrative_json.py
│   │   ├── 005_revoked_tokens.py
│   │   └── 006_indexes_and_jdcache_created_at.py
│   ├── env.py
│   └── script.py.mako
├── app/
│   ├── backend/
│   │   ├── db/
│   │   │   └── database.py     # Database connection
│   │   ├── middleware/
│   │   │   ├── auth.py         # JWT middleware
│   │   │   └── csrf.py         # CSRF protection
│   │   ├── models/
│   │   │   ├── db_models.py    # SQLAlchemy models
│   │   │   └── schemas.py      # Pydantic schemas
│   │   ├── routes/
│   │   │   ├── analyze.py      # Analysis endpoints
│   │   │   ├── auth.py         # Auth endpoints
│   │   │   ├── candidates.py   # Candidate management
│   │   │   ├── compare.py      # Comparison endpoints
│   │   │   ├── email_gen.py    # Email generation
│   │   │   ├── export.py       # Export endpoints
│   │   │   ├── jd_url.py       # JD scraping
│   │   │   ├── subscription.py # Subscription endpoints
│   │   │   ├── team.py         # Team management
│   │   │   ├── templates.py    # JD templates
│   │   │   ├── training.py     # AI training
│   │   │   ├── transcript.py   # Transcript analysis
│   │   │   └── video.py        # Video analysis
│   │   ├── services/
│   │   │   ├── agent_pipeline.py    # LangGraph agents
│   │   │   ├── analysis_service.py  # Core analysis
│   │   │   ├── gap_detector.py      # Gap detection
│   │   │   ├── hybrid_pipeline.py   # Fallback logic
│   │   │   ├── jd_scraper.py        # URL scraping
│   │   │   ├── llm_service.py       # LLM wrapper
│   │   │   ├── metrics.py           # Prometheus metrics
│   │   │   ├── parser_service.py    # Resume parsing
│   │   │   ├── transcript_service.py # Transcription
│   │   │   ├── video_downloader.py  # Video download
│   │   │   └── video_service.py     # Video processing
│   │   ├── tests/              # 572 backend tests
│   │   ├── Dockerfile
│   │   └── main.py             # FastAPI entry point
│   ├── frontend/
│   │   ├── src/
│   │   │   ├── __tests__/      # 91 frontend tests
│   │   │   ├── components/     # React components
│   │   │   ├── contexts/       # React contexts
│   │   │   ├── hooks/          # Custom hooks
│   │   │   ├── lib/            # API client
│   │   │   ├── pages/          # Page components
│   │   │   ├── App.jsx
│   │   │   ├── main.jsx
│   │   │   └── index.css
│   │   ├── Dockerfile
│   │   ├── package.json
│   │   ├── vite.config.js
│   │   └── tailwind.config.js
│   └── nginx/
│       ├── nginx.conf          # Dev config
│       └── nginx.prod.conf     # Production config
├── ollama/
│   ├── Modelfile               # Custom model definition
│   └── setup-recruiter-model.sh
├── scripts/
│   ├── pre-commit-check.ps1
│   ├── run-full-tests.bat
│   └── run-full-tests.sh
├── docker-compose.yml          # Development
├── docker-compose.prod.yml     # Production
├── requirements.txt
├── alembic.ini
└── README.md
```

---

## Contributing

We welcome contributions! Please follow these guidelines:

1. **Fork & Branch** — Create a feature branch from `main`
2. **Code Style** — Follow PEP 8 for Python, ESLint for JavaScript
3. **Tests** — Add tests for new features; ensure all tests pass
4. **Documentation** — Update README for API changes
5. **Commit Messages** — Use conventional commits format
6. **Pull Request** — Fill out the PR template; request review

### Development Workflow

```bash
# Create feature branch
git checkout -b feature/your-feature-name

# Make changes and test
pytest app/backend/tests/ -v
cd app/frontend && npm test

# Commit and push
git commit -m "feat: add new feature"
git push origin feature/your-feature-name

# Open Pull Request
```

---

## License

MIT License — Copyright (c) 2024 ThetaLogics

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.

---

<p align="center">
  <strong>Built with by ThetaLogics</strong><br>
  <a href="https://thetalogics.com">Website</a> •
  <a href="https://github.com/thetalogics/aria/issues">Issues</a> •
  <a href="https://github.com/thetalogics/aria/discussions">Discussions</a>
</p>
