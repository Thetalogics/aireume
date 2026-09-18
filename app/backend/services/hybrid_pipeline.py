"""
Hybrid Analysis Pipeline — Python-first, single LLM call for narrative.

Architecture:
  Phase 1 (Python, ~1-2s): parse_jd_rules → parse_resume_rules → match_skills
                            → score_education/experience/domain → compute_fit_score
  Phase 2 (LLM, ~40s):     explain_with_llm (narrative only — strengths, concerns,
                            recommendation rationale)
  Phase 2b (background):   interview kit + voice strategy generated after narrative
  Fallback:                 if LLM times out, _build_fallback_narrative returns
                            deterministic text — result is ALWAYS returned.

Background Processing:
  The LLM narrative is generated as a background task and written to DB when complete.
  The immediate response includes Python scores with narrative_pending=True.
  Frontend polls GET /api/analysis/{id}/narrative to fetch the LLM narrative later.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from datetime import date, datetime
from decimal import Decimal
from typing import AsyncGenerator, Dict, Any, List, Optional, Callable

from app.backend.services.metrics import LLM_CALL_DURATION, LLM_FALLBACK_TOTAL
from app.backend.services.llm_service import get_ollama_semaphore
from app.backend.services.constants import (
    RECOMMENDATION_THRESHOLDS,
    SENIORITY_RANGES,
    DOMAIN_KEYWORDS,
    DEGREE_SCORES,
    FIELD_RELEVANCE,
    combine_skill_ratios,
)
from app.backend.services.domain_service import detect_domain_from_jd, detect_domain_from_resume
from app.backend.services.eligibility_service import check_eligibility
from app.backend.services.fit_scorer import compute_fit_score, compute_deterministic_score, explain_decision, align_decision_to_score
from app.backend.services.weight_mapper import convert_to_new_schema
from app.backend.services.skill_matcher import (
    skills_registry,
    _extract_skills_from_text,
    match_skills,
    match_skills_with_onet,
    JD_CACHE_VERSION,
)

log = logging.getLogger("aria.hybrid")


def _json_default(obj: Any) -> Any:
    """Handle non-serializable types for json.dumps (datetime, date, Decimal)."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# Feature flag for target-guided LLM resume skill extraction
# When enabled, the LLM prompt includes JD domain and required skills to guide extraction
ENABLE_TARGET_GUIDED_EXTRACTION = os.getenv("ENABLE_TARGET_GUIDED_EXTRACTION", "true").lower() == "true"

# Track background tasks for graceful shutdown
_background_tasks: set = set()


def _compute_domain_similarity(jd_domain: dict, candidate_domain: dict) -> float:
    """Compute cosine similarity between JD and candidate domain score vectors.

    Falls back to binary name comparison when score vectors are unavailable.
    """
    jd_scores = jd_domain.get("scores", {})
    cand_scores = candidate_domain.get("scores", {})

    if not jd_scores or not cand_scores:
        # Fallback to binary if scores not available
        if jd_domain.get("domain", "unknown") == candidate_domain.get("domain", "unknown"):
            return jd_domain.get("confidence", 0)
        return 0.0

    # Get union of all domains
    all_domains = set(jd_scores.keys()) | set(cand_scores.keys())

    # Compute cosine similarity
    dot_product = sum(jd_scores.get(d, 0) * cand_scores.get(d, 0) for d in all_domains)
    jd_magnitude = sum(v ** 2 for v in jd_scores.values()) ** 0.5
    cand_magnitude = sum(v ** 2 for v in cand_scores.values()) ** 0.5

    if jd_magnitude == 0 or cand_magnitude == 0:
        return 0.0

    return round(dot_product / (jd_magnitude * cand_magnitude), 3)



def register_background_task(task: asyncio.Task) -> None:
    """Register a background task for tracking."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def shutdown_background_tasks(timeout: float = 5.0) -> None:
    """Cancel and await all background tasks. Call during app shutdown."""
    tasks = list(_background_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            log.warning(
                "Timed out waiting for %s background tasks to cancel",
                len(tasks),
            )

# --- Prompt injection sanitization ---
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"ignore\s+(all\s+)?above\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?previous", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+a", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*:", re.IGNORECASE),
    re.compile(r"system\s*:", re.IGNORECASE),
    re.compile(r"assistant\s*:", re.IGNORECASE),
    re.compile(r"<\s*system\s*>", re.IGNORECASE),
    re.compile(r"\[INST\]", re.IGNORECASE),
    re.compile(r"\[/INST\]", re.IGNORECASE),
]

# Prompt token budget (char-based proxy). Configurable via env so operators can
# tune for their model's context window without a code change.
_MAX_RESUME_LENGTH = int(os.getenv("MAX_RESUME_CHARS", "50000"))   # ~50KB
_MAX_JD_LENGTH = int(os.getenv("MAX_JD_CHARS", "20000"))           # ~20KB


def _sanitize_input(text: str, max_length: int, label: str = "content") -> str:
    """Sanitize user-provided text to prevent prompt injection."""
    if not text:
        return text
    # Truncate excessively long inputs
    if len(text) > max_length:
        log.info("Truncated %s from %d to %d chars before LLM prompt", label, len(text), max_length)
        text = text[:max_length]
    # Strip known injection patterns, counting hits for observability
    hits = 0
    for pattern in _INJECTION_PATTERNS:
        text, n = pattern.subn("[FILTERED]", text)
        hits += n
    if hits:
        log.warning("Prompt-injection sanitization filtered %d pattern(s) in %s", hits, label)
        try:
            from app.backend.services.guardrail_service import emit_guardrail_event
            emit_guardrail_event("prompt_injection_blocked", metadata={"field": label, "hits": hits})
        except Exception:
            pass
    return text


def _wrap_user_content(resume_text: str, jd_text: str) -> tuple[str, str]:
    """Sanitize and wrap user content with clear delimiters."""
    resume_text = _sanitize_input(resume_text, _MAX_RESUME_LENGTH, "resume")
    jd_text = _sanitize_input(jd_text, _MAX_JD_LENGTH, "job_description")
    return resume_text, jd_text

# ─── LLM singleton ───────────────────────────────────────────────────────────
_REASONING_LLM = None


def reset_llm_singleton():
    """Force the LLM singleton to reinitialise on next call (e.g. after env change)."""
    global _REASONING_LLM
    _REASONING_LLM = None


def _is_ollama_cloud(base_url: str) -> bool:
    """Check if the base URL points to Ollama Cloud (ollama.com)."""
    return "ollama.com" in base_url.lower()


def _bind_num_predict(llm, num_predict: int):
    """Override num_predict for one invocation.

    langchain-ollama 1.0+ no longer accepts num_predict as a top-level chat()
    kwarg. Pass it inside the Ollama ``options`` dict instead.
    """
    if not hasattr(llm, "bind"):
        return llm
    if "google" in type(llm).__module__.lower():
        return llm.bind(max_output_tokens=num_predict)
    opts: dict = {"num_predict": num_predict}
    for key in ("num_ctx", "temperature", "top_p", "top_k", "repeat_penalty"):
        val = getattr(llm, key, None)
        if val is not None:
            opts[key] = val
    return llm.bind(options=opts)


def _get_llm():
    global _REASONING_LLM
    if _REASONING_LLM is None:
        try:
            from app.backend.services.llm_service import use_gemini_for_analysis, create_gemini_chat_llm, get_gemini_model

            if use_gemini_for_analysis():
                _REASONING_LLM = create_gemini_chat_llm(max_output_tokens=4000)
                log.info("Using Google Gemini for analysis LLM (model=%s)", get_gemini_model())
                return _REASONING_LLM

            from langchain_ollama import ChatOllama
            _base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            _llm_timeout = float(os.getenv("LLM_NARRATIVE_TIMEOUT", "500"))
            _is_cloud = _is_ollama_cloud(_base_url)
            
            # Optional: Enforce cloud-only mode (uncomment to enable)
            # REQUIRE_CLOUD = os.getenv("OLLAMA_REQUIRE_CLOUD", "false").lower() == "true"
            # if REQUIRE_CLOUD and not _is_cloud:
            #     raise RuntimeError(f"OLLAMA_REQUIRE_CLOUD is set but OLLAMA_BASE_URL points to local instance: {_base_url}")

            # num_predict: Reduced from 8000/6000 to 4000/3000 since we now generate 7-8 questions instead of 15
            # Cloud: 4000 tokens for 7-8 structured interview questions with candidate briefing
            # Local: 3000 tokens for 7-8 structured interview questions
            _num_predict = 4000 if _is_cloud else 3000

            # Build kwargs for ChatOllama
            # NOTE: "format": "json" is intentionally omitted. Ollama's constrained JSON
            # decoding mode aborts generation on any non-JSON token, causing empty/partial
            # responses. We rely on prompt instructions + robust _parse_llm_json_response()
            # for JSON extraction instead.
            from app.backend.services.llm_service import get_ollama_model

            _llm_kwargs = {
                "model": get_ollama_model(),
                "base_url": _base_url,
                "temperature": 0.1,
                "num_predict": _num_predict,
                # num_ctx: Cloud models need larger context for complex reasoning
                # 16384 for cloud to handle very large outputs, 8192 for local (15 structured questions)
                "num_ctx": 16384 if _is_cloud else 8192,
                # HTTP timeout must exceed LLM_NARRATIVE_TIMEOUT to let the
                # outer asyncio.wait_for control cancellation, not httpx.
                "request_timeout": _llm_timeout + 30,
            }

            # Add headers for Ollama Cloud authentication
            if _is_cloud:
                api_key = os.getenv("OLLAMA_API_KEY", "").strip()
                if api_key:
                    _llm_kwargs["headers"] = {"Authorization": f"Bearer {api_key}"}
                    log.info("Using Ollama Cloud with API key authentication (num_predict=%s, num_ctx=%s)", _num_predict, _llm_kwargs["num_ctx"])
                else:
                    log.warning("Ollama Cloud detected but OLLAMA_API_KEY is not set!")
            else:
                # Keep model always hot in RAM (-1 = never unload) — only for local Ollama
                _llm_kwargs["keep_alive"] = -1

            log.info("Initializing LLM with config: num_predict=%s, num_ctx=%s, is_cloud=%s", _num_predict, _llm_kwargs["num_ctx"], _is_cloud)
            _REASONING_LLM = ChatOllama(**_llm_kwargs)
        except Exception as e:
            log.warning("LLM init failed: %s", e)
    return _REASONING_LLM


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT 1: JD PARSER
# ═══════════════════════════════════════════════════════════════════════════════

YEARS_PATTERNS = [
    r'(\d+)\+\s*years?',
    r'minimum\s+(?:of\s+)?(\d+)\s*years?',
    r'at\s+least\s+(\d+)\s*years?',
    r'(\d+)\s*[-–]\s*\d+\s*years?',
    r'(\d+)\s+to\s+(\d+)\s*years?',
    r'(\d+)\s*years?\s+(?:of\s+)?experience',
    r'experience\s+(?:of\s+)?(\d+)\s*years?',
]

# Range patterns that capture both min and max years (e.g., "5-8 years", "5 to 8 years")
YEARS_RANGE_PATTERNS = [
    r'(\d+)\s*[-–]\s*(\d+)\s*years?',
    r'(\d+)\s+to\s+(\d+)\s*years?',
]

_NICE_TO_HAVE_RE = re.compile(
    r'(?:nice[\s\-]to[\s\-]have|preferred|bonus|plus|advantageous|desirable|'
    r'would be (?:a\s+)?(?:great|nice)|not required|optional|good to have)',
    re.IGNORECASE,
)

_TITLE_RE = re.compile(
    r'\b(?:senior|sr\.?|junior|jr\.?|lead|principal|staff|associate|mid[-\s]?level|'
    r'entry[\s\-]level)?\s*[\w/\.]+\s+'
    r'(?:engineer|developer|architect|analyst|scientist|manager|consultant|specialist|'
    r'designer|lead|director|officer|head|vp|president|intern|associate|researcher)',
    re.IGNORECASE,
)

# Explicit labels that announce the role title (e.g., "Job Title: Senior Accountant")
_TITLE_LABEL_RE = re.compile(
    r'^(?:job\s*title|position\s*title|role|title|position|designation|opening|vacancy|we\s*are\s*looking\s*for|we\s*are\s*hiring|hiring)'
    r'[:\s\-]*\s*(.+)$',
    re.IGNORECASE,
)

# Common role-ending words (tech and non-tech) used to validate a candidate title line
_ROLE_TITLE_KEYWORDS = {
    "engineer", "developer", "architect", "analyst", "scientist", "manager", "consultant",
    "specialist", "designer", "lead", "director", "officer", "head", "vp", "president",
    "intern", "associate", "researcher", "nurse", "physician", "attorney", "lawyer",
    "auditor", "accountant", "recruiter", "coordinator", "supervisor", "technician",
    "therapist", "advisor", "representative", "executive", "planner", "strategist",
    "administrator", "assistant", "coordinator", "operator", "agent", "salesperson",
    "marketer", "writer", "editor", "teacher", "professor", "instructor", "counselor",
    "pharmacist", "dentist", "surgeon", "therapist", "caregiver", "paramedic",
    "electrician", "plumber", "carpenter", "mechanic", "technician", "inspector",
    "controller", "treasurer", "bookkeeper", "clerk", "teller", "underwriter",
    "broker", "agent", "realtor", "appraiser", "adjuster", "actuary", "statistician",
    "economist", "mathematician", "physicist", "chemist", "biologist", "geologist",
    "psychologist", "sociologist", "anthropologist", "historian", "librarian", "curator",
    "archivist", "translator", "interpreter", "journalist", "reporter", "correspondent",
    "anchor", "producer", "director", "host", "musician", "composer", "artist",
    "photographer", "videographer", "designer", "illustrator", "animator", "actor",
    "dancer", "choreographer", "coach", "trainer", "athlete", "referee", "umpire",
    "chef", "cook", "baker", "waiter", "waitress", "bartender", "hostess", "maitre",
    "sommelier", "barista", "cashier", "receptionist", "concierge", "housekeeper",
    "custodian", "janitor", "security", "guard", "officer", "detective", "police",
    "firefighter", "paramedic", "emt", "soldier", "sailor", "pilot", "captain",
    "flight", "attendant", "driver", "dispatcher", "mechanic", "technician",
}


def _extract_role_title(jd_text: str) -> str:
    """Extract a role title from the first lines of a JD using labels, keywords, and regex.

    Returns the cleaned title or an empty string if none is found.
    """
    lines = [l.strip() for l in jd_text.split("\n") if l.strip()]
    if not lines:
        return ""

    # 1. Explicit title label (e.g., "Job Title: Senior Accountant")
    for line in lines[:20]:
        m = _TITLE_LABEL_RE.search(line)
        if m:
            title = m.group(1).strip()
            # Remove markdown heading markers
            title = re.sub(r'^[#\*\-]+\s*', '', title).strip()
            # Remove trailing punctuation / metadata
            title = re.sub(r'\s*[\|\-–].*$', '', title).strip()
            if title and len(title.split()) <= 12:
                return title

    # 2. First non-noisy line that contains a role keyword and is short enough
    for line in lines[:10]:
        # Strip markdown headings and bullets
        clean = re.sub(r'^[#\*\-]+\s*', '', line).strip()
        if re.search(r'[@|:/\(\)#,\d]{2,}', clean):
            continue
        if len(clean.split()) > 10:
            continue
        words = {w.strip(".,;:-").lower() for w in clean.split()}
        if words & _ROLE_TITLE_KEYWORDS:
            return clean

    # 3. Fallback regex on the first 500 chars
    m = _TITLE_RE.search(jd_text[:500])
    if m:
        return m.group(0).strip()

    return ""


def parse_jd_rules(jd_text: str, llm_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Parse a job description.  Uses rule-based extraction, optionally merged with an LLM profile.

    Args:
        jd_text: Raw job description text.
        llm_profile: Optional pre-computed LLM-extracted profile (see jd_profile_service).
    """
    from app.backend.services.constants import JOB_FUNCTION_KEYWORDS, GENERIC_SOFT_SKILLS

    text_lower = jd_text.lower()

    # ── Role title ──────────────────────────────────────────────────────────
    role_title = _extract_role_title(jd_text)

    # Lines used for responsibilities and soft-skill classification below
    lines = [l.strip() for l in jd_text.split("\n") if l.strip()]

    # ── Job function detection ─────────────────────────────────────────────
    job_function = "other"
    job_function_scores = {}
    for jf, keywords in JOB_FUNCTION_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text_lower)
        if hits:
            job_function_scores[jf] = hits

    # Also check role title for job function hints
    title_lower = role_title.lower()
    for jf, keywords in JOB_FUNCTION_KEYWORDS.items():
        if any(kw in title_lower for kw in keywords):
            job_function_scores[jf] = job_function_scores.get(jf, 0) + 3  # Boost title matches

    if job_function_scores:
        job_function = max(job_function_scores, key=job_function_scores.get)

    # ── Years required (min/max) ───────────────────────────────────────────
    min_required_years = 0
    max_required_years = 0
    required_years = 0

    # First try range patterns (e.g., 5-8 years, 5 to 8 years)
    for pat in YEARS_RANGE_PATTERNS:
        m = re.search(pat, text_lower)
        if m:
            try:
                min_required_years = int(m.group(1))
                max_required_years = int(m.group(2))
                required_years = min_required_years
                break
            except (ValueError, IndexError):
                pass

    # Fall back to single-value patterns
    if not min_required_years:
        for pat in YEARS_PATTERNS:
            m = re.search(pat, text_lower)
            if m:
                try:
                    required_years = int(m.group(1))
                    min_required_years = required_years
                    break
                except (ValueError, IndexError):
                    pass

    # ── Domain classification ────────────────────────────────────────────────
    domain_hits: Dict[str, int] = {}
    for domain, keywords in DOMAIN_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text_lower)
        if hits:
            domain_hits[domain] = hits
    domain = max(domain_hits, key=domain_hits.get) if domain_hits else "other"

    # ── Seniority ────────────────────────────────────────────────────────────
    title_lower = role_title.lower()
    if any(w in title_lower for w in ("principal", "staff", "distinguished", "fellow")):
        seniority = "lead"
    elif any(w in title_lower for w in ("senior", "sr.", "sr ", "lead", "architect", "head of")):
        seniority = "senior"
    elif any(w in title_lower for w in ("junior", "jr.", "jr ", "associate", "graduate", "entry")):
        seniority = "junior"
    elif any(w in jd_text[:500].lower() for w in ("lead ", "staff ", "principal ")):
        seniority = "lead"
    elif min_required_years >= 8:
        seniority = "lead"
    elif min_required_years >= 5:
        seniority = "senior"
    elif min_required_years >= 2:
        seniority = "mid"
    elif min_required_years > 0:
        seniority = "junior"
    else:
        seniority = "mid"

    # ── Skill extraction (required vs nice-to-have split) ───────────────────
    nice_start = len(jd_text)
    m_nice = _NICE_TO_HAVE_RE.search(jd_text)
    if m_nice:
        nice_start = m_nice.start()

    required_text   = jd_text[:nice_start]
    nice_have_text  = jd_text[nice_start:]

    required_skills  = _extract_skills_from_text(required_text)
    nice_have_skills = _extract_skills_from_text(nice_have_text)

    # Filter out generic soft skills from required (unless explicitly emphasized)
    required_skills_filtered = [
        skill for skill in required_skills
        if skill.lower() not in GENERIC_SOFT_SKILLS
    ]

    # If filtering removed too many skills, keep some if they appear multiple times
    if len(required_skills_filtered) < 2 and len(required_skills) > 2:
        # Soft skills mentioned frequently might be important
        soft_skills_in_jd = [
            skill for skill in required_skills
            if skill.lower() in GENERIC_SOFT_SKILLS
        ]
        # Keep at most 1-2 soft skills if they're emphasized
        required_skills_filtered = required_skills_filtered + soft_skills_in_jd[:2]

    # Remove overlap
    nice_have_skills = [s for s in nice_have_skills if s not in required_skills]

    # Add soft skills to nice-to-have if they were filtered from required
    filtered_soft_skills = set(required_skills) - set(required_skills_filtered)
    nice_have_skills = list(set(nice_have_skills + list(filtered_soft_skills)))

    # ── Key responsibilities (first 5 bullet lines starting with verbs) ─────
    resp_lines = []
    for line in lines:
        line_s = line.lstrip("-•*·▸▹►→ ").strip()
        if len(line_s) > 30 and re.match(r'^[A-Z][a-z]', line_s):
            resp_lines.append(line_s)
        if len(resp_lines) >= 6:
            break

    from app.backend.services.profile_text_sanitizer import sanitize_jd_analysis

    result = sanitize_jd_analysis({
        "role_title":        role_title or "Not specified",
        "job_function":      job_function,
        "domain":            domain,
        "seniority":         seniority,
        "required_skills":   required_skills_filtered,
        "required_years":    required_years,
        "min_required_years": min_required_years,
        "max_required_years": max_required_years,
        "nice_to_have_skills": nice_have_skills,
        "key_responsibilities": resp_lines,
        "domain_keywords":   [],
        "architecture_signals": [],
        "relevant_education_fields": [],
        "_profile_source":   "rules",
    })

    # Merge LLM profile if available
    if llm_profile:
        from app.backend.services.jd_profile_service import merge_jd_profile
        result = merge_jd_profile(result, llm_profile, jd_text=jd_text)

    return result


def _infer_total_years_from_resume_text(text: str) -> float:
    """
    When structured work_experience is empty or dates failed, recover years from prose
    (e.g. '8+ years of experience', 'over 5 years in embedded systems').
    """
    if not text:
        return 0.0
    snippet = text[:25000]
    best = 0.0
    patterns = [
        r"(?:over|more\s+than|at\s+least|>\s*|approximately|approx\.?)\s*(\d{1,2})\+?\s*(?:years?|yrs?\.?)",
        r"(\d{1,2})\+?\s*(?:years?|yrs?\.?)\s+(?:of\s+)?(?:professional\s+)?(?:relevant\s+)?(?:experience|exp\.?)\b",
        r"(?:experience|exp\.?)\s*[:\-–—]?\s*(?:of\s+)?(?:about|approx\.?)?\s*(\d{1,2})\+?\s*(?:years?|yrs?\.?)",
        r"\b(\d{1,2})\s*\+\s*years?\b",
        r"\b(\d{1,2})\s*-\s*years?\s+(?:of\s+)?experience\b",
    ]
    for pat in patterns:
        for m in re.finditer(pat, snippet, re.IGNORECASE):
            try:
                v = float(m.group(1))
                if 0.5 <= v <= 45:
                    best = max(best, v)
            except (ValueError, IndexError):
                pass
    return min(45.0, best)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT 2: RESUME PROFILE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def parse_resume_rules(
    parsed_data: Dict[str, Any],
    gap_analysis: Dict[str, Any],
    llm_resume_skills: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a structured candidate profile from parser output and gap analysis.

    If ``llm_resume_skills`` is provided, these LLM-extracted skills are merged
    into ``skills_identified`` (after deduplication) to capture domain-specific
    skills that the static FlashText registry may miss.
    """
    contact      = parsed_data.get("contact_info", {}) or {}
    work_exp     = parsed_data.get("work_experience", [])
    raw_text     = parsed_data.get("raw_text", "")

    # Skills — tiered confidence model
    parser_skills = [str(s).strip() for s in parsed_data.get("skills", []) if s]
    scanned_skills = _extract_skills_from_text(raw_text)

    # Tier 0: Structured parser output (HIGH confidence)
    structured = list(dict.fromkeys(parser_skills))

    # Tier 2: Text-scanned only (LOW confidence) -- exclude anything already in Tier 0
    structured_norm = {s.lower().strip() for s in structured}
    text_only = [s for s in scanned_skills if s.lower().strip() not in structured_norm]

    # skills_identified = ONLY structured by default
    # text_scanned_skills = tracked separately, validated during matching

    skills_identified = structured

    # Targeted skill confirmation: search resume text for each required skill
    try:
        from app.backend.services.skill_matcher import confirm_skills_in_text
        jd_target_skills = (
            list(gap_analysis.get("required_skills", [])) +
            list(gap_analysis.get("nice_to_have_skills", []))
        )
        if jd_target_skills and raw_text:
            confirmed = confirm_skills_in_text(jd_target_skills, raw_text)
            existing_lower = {s.lower() for s in skills_identified}
            for skill, result in confirmed.items():
                if result["found"] and skill.lower() not in existing_lower:
                    skills_identified.append(skill)
                    existing_lower.add(skill.lower())
    except Exception as e:
        log.debug("Targeted skill confirmation skipped: %s", e)

    # Layer 2: Merge LLM-extracted resume skills (semantic, domain-agnostic)
    if llm_resume_skills:
        existing_lower = {s.lower() for s in skills_identified}
        for skill in llm_resume_skills:
            if skill and skill.lower() not in existing_lower:
                skills_identified.append(skill)
                existing_lower.add(skill.lower())

    total_years = float(gap_analysis.get("total_years", 0.0) or 0.0)
    if total_years <= 0 and raw_text:
        inferred_y = _infer_total_years_from_resume_text(raw_text)
        if inferred_y > 0:
            total_years = inferred_y

    from app.backend.services.profile_text_sanitizer import (
        sanitize_candidate_profile,
        sanitize_skill_list,
        sanitize_work_experience,
    )

    work_exp = sanitize_work_experience(work_exp)
    skills_identified = sanitize_skill_list(skills_identified)
    structured = sanitize_skill_list(structured)
    text_only = sanitize_skill_list(text_only)

    # Truncate current_role and current_company to 255 chars to prevent DB truncation errors
    _raw_role = work_exp[0].get("title", "") if work_exp else ""
    _raw_company = work_exp[0].get("company", "") if work_exp else ""
    if _raw_role and len(_raw_role) > 255:
        log.warning("Truncating current_role from %d to 255 chars", len(_raw_role))
        _raw_role = _raw_role[:255]
    if _raw_company and len(_raw_company) > 255:
        log.warning("Truncating current_company from %d to 255 chars", len(_raw_company))
        _raw_company = _raw_company[:255]
    current_role    = _raw_role
    current_company = _raw_company

    career_summary = _build_career_summary(current_role, current_company, total_years)

    return sanitize_candidate_profile({
        "name":                  contact.get("name", ""),
        "email":                 contact.get("email", ""),
        "phone":                 contact.get("phone", ""),
        "structured_skills":     structured,       # Tier 0: parser output (HIGH confidence)
        "text_scanned_skills":   text_only,         # Tier 2: text-only (LOW confidence)
        "skills_identified":     skills_identified,  # structured + confirmed from text
        "education":             parsed_data.get("education", []),
        "work_experience":       work_exp,
        "career_summary":        career_summary,
        "total_effective_years": total_years,
        "current_role":          current_role,
        "current_company":       current_company,
    })


def _build_career_summary(role: str, company: str, years: float) -> str:
    parts = []
    if role and company:
        parts.append(f"{role} at {company}")
    elif role:
        parts.append(role)
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''} total experience")
    return " — ".join(parts) if parts else "No career summary available"


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT 4: EDUCATION SCORING
# ═══════════════════════════════════════════════════════════════════════════════

# DEGREE_SCORES and FIELD_RELEVANCE are now imported from constants.py


def score_education_rules(candidate_profile: Dict[str, Any], jd_analysis) -> int:
    """Return a 0-100 education score.

    Uses the JD profile's relevant_education_fields when available; otherwise
    falls back to the static FIELD_RELEVANCE mapping.  Accepts a plain domain
    string for backward compatibility.
    """
    # Backward compatibility: caller passed a plain domain string
    if isinstance(jd_analysis, str):
        jd_analysis = {"domain": jd_analysis}

    education = candidate_profile.get("education", [])
    if not education:
        return 60  # neutral default — no penalty for missing education data

    best_score = 0
    best_field = ""

    for edu in education:
        degree = str(edu.get("degree", "")).lower()
        field  = str(edu.get("field", "") or edu.get("degree", "")).lower()

        for key, pts in DEGREE_SCORES.items():
            if key in degree:
                if pts > best_score:
                    best_score = pts
                    best_field = field
                break

    if best_score == 0:
        return 60

    # Field relevance multiplier
    relevant_fields = jd_analysis.get("relevant_education_fields", []) or []
    if not relevant_fields:
        relevant_fields = FIELD_RELEVANCE.get(jd_analysis.get("domain", ""), FIELD_RELEVANCE["other"])

    multiplier = 0.70
    for rf in relevant_fields:
        rf_lower = rf.lower()
        if rf_lower in best_field:
            multiplier = 1.0
            break
        if any(word in best_field for word in rf_lower.split()):
            multiplier = max(multiplier, 0.85)

    return round(best_score * multiplier)


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT 5: EXPERIENCE & TIMELINE SCORING
# ═══════════════════════════════════════════════════════════════════════════════

def score_experience_rules(
    candidate_profile: Dict[str, Any],
    jd_analysis: Dict[str, Any],
    gap_analysis: Dict[str, Any],
) -> Dict[str, Any]:
    """Return experience_score, timeline_score, and a text timeline summary.

    Experience score rewards candidates whose years fall within the JD's
    requested range.  Over-qualified candidates are not over-rewarded.
    """
    actual_years   = candidate_profile.get("total_effective_years", 0.0)
    required_years = jd_analysis.get("required_years", 0)
    min_required_years = jd_analysis.get("min_required_years", required_years) or 0
    max_required_years = jd_analysis.get("max_required_years", 0) or 0

    # ── Fallback: if dates couldn't be parsed but work entries exist, estimate ─
    # Avoids showing 0% experience when the resume has jobs but ambiguous dates.
    if actual_years == 0.0:
        work_exp = candidate_profile.get("work_experience", [])
        if work_exp:
            # Conservative: 1.5 years per listed job role, capped at 15 years
            actual_years = float(min(15, len(work_exp) * 1.5))

    # ── Experience score ──────────────────────────────────────────────────────
    if min_required_years == 0:
        # No explicit requirement: score based on presence of experience
        exp_score = min(100, int(actual_years * 10))
    elif max_required_years and actual_years > max_required_years:
        # Within range is best; being above the max is still good but not over-rewarded
        exp_score = max(70, 100 - int((actual_years - max_required_years) * 3))
    elif actual_years >= min_required_years:
        # Ideal: within requested range
        exp_score = 95
    else:
        # Below minimum: proportional score
        exp_score = int((actual_years / min_required_years) * 70)
    exp_score = max(0, min(100, exp_score))

    # ── Timeline score (gap deductions) ──────────────────────────────────────
    # Reduced penalties: modern careers include contract work, breaks, and projects.
    t_score = 90
    employment_gaps = gap_analysis.get("employment_gaps", [])
    for gap in employment_gaps:
        severity = gap.get("severity", "negligible")
        if severity == "minor":     t_score -= 3
        elif severity == "moderate": t_score -= 7
        elif severity == "critical": t_score -= 14
    for _ in gap_analysis.get("short_stints", []):    t_score -= 3
    for _ in gap_analysis.get("overlapping_jobs", []): t_score -= 5
    t_score = max(10, min(100, t_score))

    # ── Timeline summary text ─────────────────────────────────────────────────
    if not employment_gaps:
        timeline_text = "Continuous employment — no significant gaps."
    else:
        n = len(employment_gaps)
        longest = max(g.get("duration_months", 0) for g in employment_gaps)
        severities = [g.get("severity", "") for g in employment_gaps]
        worst = (
            "critical" if "critical" in severities else
            "moderate" if "moderate" in severities else
            "minor"
        )
        timeline_text = (
            f"Career includes {n} gap{'s' if n > 1 else ''}: "
            f"{longest} month{'s' if longest > 1 else ''} longest. {worst.capitalize()} pattern."
        )

    return {
        "exp_score":       exp_score,
        "timeline_score":  t_score,
        "timeline_text":   timeline_text,
        "actual_years":    actual_years,
        "required_years":  required_years,
        "min_required_years": min_required_years,
        "max_required_years": max_required_years,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT 6: DOMAIN & ARCHITECTURE SCORING
# ═══════════════════════════════════════════════════════════════════════════════

ARCHITECTURE_SIGNALS = [
    "designed", "architected", "led design", "technical lead", "system design",
    "microservices", "distributed system", "scalable", "high availability",
    "event-driven", "message queue", "kafka", "rabbitmq", "tech lead",
    "principal engineer", "staff engineer", "engineering manager", "mentored",
    "technical decision", "proof of concept", "rfc", "adr", "design review",
    "led team", "drove", "established", "built from scratch", "greenfield",
]


def domain_architecture_rules(
    raw_text: str,
    jd_analysis,
    current_role: Optional[str],
) -> Dict[str, Any]:
    """Return domain_score and architecture_score using the JD profile.

    If the JD profile has domain_keywords and architecture_signals, those are
    used.  Otherwise, the legacy static lists are used as fallback.  Accepts
    a plain domain string for backward compatibility.
    """
    # Backward compatibility: caller passed a plain domain string
    if isinstance(jd_analysis, str):
        jd_analysis = {"domain": jd_analysis}

    text_lower = raw_text.lower()

    # ── Domain fit score ──────────────────────────────────────────────────────
    domain_keywords = jd_analysis.get("domain_keywords", []) or DOMAIN_KEYWORDS.get(jd_analysis.get("domain", ""), [])
    if not domain_keywords:
        # Generic fallback for unrecognized domains
        domain_keywords = [jd_analysis.get("domain", "").lower()]

    hits = sum(1 for kw in domain_keywords if kw and kw.lower() in text_lower)
    total = len(domain_keywords)
    if total == 0:
        domain_score = 50
    else:
        ratio = hits / total
        if   ratio >= 0.40: domain_score = 90
        elif ratio >= 0.25: domain_score = 75
        elif ratio >= 0.15: domain_score = 60
        elif ratio > 0.00:  domain_score = 45
        else:               domain_score = 30

    # Bonus/penalty from current role title
    if current_role:
        role_lower = current_role.lower()
        # Use the first few keywords as role indicators (e.g., "sap", "mm", "consultant")
        role_signals = [kw.lower() for kw in domain_keywords[:5]]
        if any(kw in role_lower for kw in role_signals if len(kw) > 2):
            domain_score = min(100, domain_score + 10)
        elif not any(w in role_lower for w in ("engineer", "developer", "analyst", "architect", "consultant", "manager", "specialist", "lead", "director")):
            domain_score = max(0, domain_score - 5)

    # ── Architecture / role-excellence score ────────────────────────────────────
    # Use JD-specific signals if available; otherwise fall back to generic signals
    architecture_signals = jd_analysis.get("architecture_signals", []) or ARCHITECTURE_SIGNALS
    arch_hits = sum(1 for sig in architecture_signals if sig and sig.lower() in text_lower)
    arch_score = min(100, 40 + arch_hits * 8)

    return {
        "domain_score":     domain_score,
        "arch_score":       arch_score,
        "domain_hits":      hits,
        "arch_hits":        arch_hits,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT 7: FIT SCORE & RISK SIGNALS
# ═══════════════════════════════════════════════════════════════════════════════

# DOMAIN_KEYWORDS is now imported from constants.py


def _assess_quality(candidate_profile: Dict[str, Any]) -> str:
    """Assess how complete the parsed resume data is."""
    skills_count = len(candidate_profile.get("skills_identified", []))
    exp_years    = candidate_profile.get("total_effective_years", 0)
    has_edu      = bool(candidate_profile.get("education"))
    if skills_count == 0 and exp_years == 0:
        return "low"
    if skills_count < 3 or (exp_years == 0 and not has_edu):
        return "medium"
    return "high"


# ═══════════════════════════════════════════════════════════════════════════════
# COMPONENT 8: LLM NARRATIVE (single call)
# ═══════════════════════════════════════════════════════════════════════════════


def _strip_llm_markdown_fences(text: str) -> str:
    """Remove common ```json fences before truncation checks."""
    clean = (text or "").strip()
    if clean and clean[0] == "\ufeff":
        clean = clean[1:].strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```\s*$", "", clean)
    return clean.strip()


def _llm_response_needs_compact_retry(raw: str) -> bool:
    """Return True only when the response is empty, too short, or genuinely incomplete."""
    if not raw or len(raw.strip()) < 20:
        return True
    if _parse_llm_json_response(raw) is not None:
        return False
    stripped = _strip_llm_markdown_fences(raw)
    if stripped.endswith("}"):
        return False
    if _extract_first_balanced_json_object(stripped):
        return False
    return True


def _narrative_output_token_limit() -> int:
    """Cap narrative output tokens — crisp JSON needs far fewer than full essays."""
    from app.backend.services.llm_service import use_gemini_for_analysis

    if use_gemini_for_analysis():
        return int(os.getenv("GEMINI_NARRATIVE_MAX_TOKENS", "2200"))
    if _is_ollama_cloud(os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")):
        return int(os.getenv("OLLAMA_NARRATIVE_MAX_TOKENS", "1800"))
    return int(os.getenv("OLLAMA_NARRATIVE_MAX_TOKENS", "1200"))


def _create_narrative_llm(*, max_output_tokens: int):
    """LLM tuned for short structured narrative JSON."""
    from app.backend.services.llm_service import use_gemini_for_analysis, create_gemini_chat_llm

    if use_gemini_for_analysis():
        return create_gemini_chat_llm(
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
        )

    llm = _get_llm()
    if llm is None:
        return None
    return _bind_num_predict(llm, max_output_tokens)


def _build_compact_retry_llm(*, num_predict: int):
    """LLM for compact narrative retry — stays on Gemini when GEMINI_API_KEY is set."""
    return _create_narrative_llm(max_output_tokens=num_predict)


def _build_narrative_prompt(
    *,
    lang_prefix: str,
    role_title: str,
    domain: str,
    seniority: str,
    candidate_name: str,
    years: float | int,
    current_role: str,
    current_company: str,
    skill_score: int,
    exp_score: int,
    edu_score: int,
    timeline_score: int,
    fit_score: int,
    recommendation: str,
    matched_must: list,
    missing_must: list,
    matched_nice: list,
    missing_nice: list,
    req_match_pct: float,
    nice_match_pct: float,
    risk_flags: str,
    score_rationales_summary: str,
    ultra_compact: bool = False,
) -> str:
    """Cost-efficient narrative prompt — terse input, strict output length caps."""
    matched_req = ", ".join(str(s) for s in matched_must[:6]) or "None"
    missing_req = ", ".join(str(s) for s in missing_must[:4]) or "None"
    matched_nice_s = ", ".join(str(s) for s in matched_nice[:3]) or "None"
    missing_nice_s = ", ".join(str(s) for s in missing_nice[:2]) or "None"

    if ultra_compact:
        limits = (
            "STRICT: fit_summary max 220 chars. candidate_profile_summary max 160 chars. "
            "Arrays max 2 items, each max 70 chars. One sentence per explainability field."
        )
        schema = """{
  "candidate_profile_summary": "2 short sentences, who they are + role fit",
  "fit_summary": "2-3 sentences: strengths, gaps, clear INTERVIEW or PASS verdict",
  "strengths": ["skill-specific", "skill-specific"],
  "concerns": ["gap-specific", "gap-specific"],
  "dealbreakers": ["must-have gap or empty array"],
  "differentiators": ["one differentiator or empty array"],
  "recommendation_rationale": "one sentence",
  "hiring_decision": {"verdict": "Shortlist|Reject|Consider", "confidence": 0.0, "key_factors": ["f1", "f2"], "action_items": ["a1"]},
  "explainability": {"skill_rationale": "one line", "experience_rationale": "one line", "overall_rationale": "one line"}
}"""
    else:
        limits = (
            "Keep copy tight. fit_summary max 380 chars. candidate_profile_summary max 240 chars. "
            "Arrays max 3 items, each max 90 chars. No markdown."
        )
        schema = """{
  "candidate_profile_summary": "2 sentences: background + fit for this role",
  "fit_summary": "3 sentences: top strengths, top gaps, INTERVIEW or PASS with reason",
  "strengths": ["specific, cite matched skills"],
  "concerns": ["specific, cite missing skills or risks"],
  "dealbreakers": ["critical must-have gap or []"],
  "differentiators": ["unique positive or negative signal or []"],
  "recommendation_rationale": "1-2 sentences tied to scores",
  "hiring_decision": {"verdict": "Shortlist|Reject|Consider", "confidence": 0.0, "key_factors": ["up to 3"], "action_items": ["up to 2 concrete next steps"]},
  "explainability": {"skill_rationale": "one line", "experience_rationale": "one line", "overall_rationale": "one line"}
}"""

    return f"""You are ARIA, a recruitment analyst. Return ONLY valid JSON.{lang_prefix}
{limits}
Use ONLY skills and facts from the data below — never invent employers, projects, or skills.

ROLE: {role_title} ({domain}, {seniority})
CANDIDATE: {candidate_name} | {years}y | {current_role} @ {current_company}
SCORES: skill={skill_score} exp={exp_score} edu={edu_score} timeline={timeline_score} fit={fit_score}/100
RECOMMENDATION: {recommendation}
MUST-HAVE: {len(matched_must)}/{len(matched_must) + len(missing_must)} ({req_match_pct:.0f}%) — matched [{matched_req}] missing [{missing_req}]
NICE-TO-HAVE: {len(matched_nice)}/{len(matched_nice) + len(missing_nice)} ({nice_match_pct:.0f}%) — matched [{matched_nice_s}] missing [{missing_nice_s}]
RISKS:
{risk_flags}
RATIONALES: {score_rationales_summary}

JSON schema:
{schema}"""


def _extract_first_balanced_json_object(text: str) -> str | None:
    """
    Return the substring of the first top-level `{ ... }` with balanced braces,
    respecting double-quoted strings (so `}` inside strings does not end the object).

    LLMs often emit broken JSON after the first object (extra `}`, truncated keys).
    Taking `find('{')`..`rfind('}')` includes that garbage and breaks `json.loads`.
    Stopping at the first balanced `}` yields a *partial* but usually valid object;
    missing keys are filled with defaults below.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_llm_json_response(raw: str) -> dict | None:
    """Parse JSON from LLM output; tolerate thinking tags, fences, and malformed tails.
    
    Extraction strategy (ordered by reliability):
    1. Try json.loads() on the raw response as-is
    2. Strip thinking tags (<think>, <redacted_thinking>) and markdown fences, then retry
    3. Find first { and last }, extract that substring, then retry
    4. Extract the first balanced JSON object and retry
    5. Fix common LLM mistakes (trailing commas, unescaped control chars, combined) and retry
    6. Regex-based brace extraction as last resort
    """
    if not raw or not raw.strip():
        return None

    # ── Strategy 1: Try parsing the raw response directly ────────────────────
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass  # Expected, continue to cleanup

    # ── Strategy 2: Strip thinking tags and markdown fences ──────────────────
    clean = raw.strip()

    # Strip Unicode BOM if present (some LLM APIs prepend it)
    if clean and clean[0] == '\ufeff':
        clean = clean[1:].strip()

    # Strip thinking/reasoning tags (Gemma uses <think>, our prompt uses <redacted_thinking>)
    clean = re.sub(r"<think>.*?</think>", "", clean, flags=re.DOTALL)
    clean = re.sub(r"<redacted_thinking>.*?</redacted_thinking>", "", clean, flags=re.DOTALL)
    # Also handle unclosed <think> tag (model started thinking but didn't close it)
    if "<think>" in clean and "</think>" not in clean:
        think_start = clean.find("<think>")
        after_think = clean[think_start + 7:]
        # If there's a JSON object after the unclosed think tag, keep it
        json_start = after_think.find("{")
        if json_start != -1:
            clean = after_think[json_start:]
        else:
            # Remove everything from <think> onwards
            clean = clean[:think_start]

    clean = clean.strip()

    # Strip markdown code fences
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    clean = clean.strip()

    # Strip leading/trailing non-JSON text (e.g., "Here is the JSON:\n{...}\nDone.")
    # Only strip if the cleaned text doesn't start with {
    if clean and not clean.startswith("{"):
        first_brace = clean.find("{")
        if first_brace != -1:
            clean = clean[first_brace:]

    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass  # Continue to next strategy

    # ── Strategy 3: Find first { and last }, extract substring ───────────────
    # Simple and effective: the LLM response likely IS a single JSON object
    # with possible extra whitespace or trailing text.
    first_brace = clean.find("{")
    last_brace = clean.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidate = clean[first_brace : last_brace + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            log.debug("First/last brace extraction failed at position %d: %s", e.pos, str(e)[:120])

    # ── Strategy 4: Extract first balanced JSON object ───────────────────────
    blob = _extract_first_balanced_json_object(clean)
    if blob:
        try:
            return json.loads(blob)
        except json.JSONDecodeError as e:
            log.debug("Balanced object parse failed at position %d: %s", e.pos, str(e)[:120])

            # ── Strategy 5a: Fix trailing commas ─────────────────────────────
            try:
                fixed = re.sub(r",\s*}", "}", blob)
                fixed = re.sub(r",\s*]", "]", fixed)
                parsed = json.loads(fixed)
                log.debug("Successfully parsed after fixing trailing commas")
                return parsed
            except json.JSONDecodeError as e2:
                log.debug("Parse failed after comma fix at position %d: %s", e2.pos, str(e2)[:120])

            # ── Strategy 5b: Fix unescaped control characters in strings ──────
            # LLMs sometimes emit literal newlines/tabs inside JSON string values
            try:
                fixed = _fix_unescaped_control_chars(blob)
                parsed = json.loads(fixed)
                log.debug("Successfully parsed after fixing unescaped control chars")
                return parsed
            except json.JSONDecodeError as e3:
                log.debug("Parse failed after control-char fix at position %d: %s", e3.pos, str(e3)[:120])

            # ── Strategy 5c: Combined fix — trailing commas + control chars ───
            # Both issues can coexist in the same response; applying only one fix
            # leaves the other unresolved, causing json.loads to fail on both 5a and 5b.
            try:
                fixed = _fix_unescaped_control_chars(blob)
                fixed = re.sub(r",\s*}", "}", fixed)
                fixed = re.sub(r",\s*]", "]", fixed)
                parsed = json.loads(fixed)
                log.debug("Successfully parsed after combined control-char + trailing-comma fix")
                return parsed
            except json.JSONDecodeError as e4:
                log.debug("Parse failed after combined fix at position %d: %s", e4.pos, str(e4)[:120])

            # ── Strategy 5d: Control chars + commas on first/last brace extract ─
            # Apply all fixes to the broader first/last brace extraction from Strategy 3
            if first_brace != -1 and last_brace > first_brace:
                candidate = clean[first_brace : last_brace + 1]
                try:
                    fixed = _fix_unescaped_control_chars(candidate)
                    fixed = re.sub(r",\s*}", "}", fixed)
                    fixed = re.sub(r",\s*]", "]", fixed)
                    parsed = json.loads(fixed)
                    log.debug("Successfully parsed after combined fix on first/last brace extract")
                    return parsed
                except json.JSONDecodeError as e5:
                    log.debug("Combined fix on first/last brace extract failed at position %d: %s", e5.pos, str(e5)[:120])
    else:
        log.debug("Could not extract balanced JSON object from response (len=%d)", len(clean))

    # ── Final attempt: regex-based brace extraction ──────────────────────────
    # Use balanced extraction on the full clean text as last resort — the old
    # regex `\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}` only handled 2 nesting levels
    # and silently failed on deeply nested interview_questions JSON (5+ levels).
    # _extract_first_balanced_json_object handles arbitrary depth correctly.
    if not blob:
        # Balanced extraction already failed above; try on the broader first/last slice
        if first_brace != -1 and last_brace > first_brace:
            candidate = clean[first_brace : last_brace + 1]
            balanced = _extract_first_balanced_json_object(candidate)
            if balanced:
                try:
                    return json.loads(balanced)
                except json.JSONDecodeError:
                    pass

    # Absolute last resort: simple regex for shallow JSON
    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', clean, flags=re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def _fix_unescaped_control_chars(text: str) -> str:
    """Replace literal control characters inside JSON string values with their escaped forms.
    
    LLMs (especially cloud models) sometimes emit raw newlines, tabs, or other
    control characters inside JSON string values, which is invalid per RFC 8259.
    This function walks the string respecting quoted regions and escapes any
    control characters found inside them.
    """
    result = []
    in_string = False
    escape = False
    for c in text:
        if in_string:
            if escape:
                escape = False
                result.append(c)
            elif c == '\\':
                escape = True
                result.append(c)
            elif c == '"':
                in_string = False
                result.append(c)
            elif ord(c) < 0x20:
                # Control character inside a string — escape it
                if c == '\n':
                    result.append('\\n')
                elif c == '\r':
                    result.append('\\r')
                elif c == '\t':
                    result.append('\\t')
                else:
                    result.append(f'\\u{ord(c):04x}')
            else:
                result.append(c)
        else:
            if c == '"':
                in_string = True
            result.append(c)
    return ''.join(result)


def _narrative_from_llm_context(context: Dict[str, Any], *, ai_enhanced: bool = False) -> Dict[str, Any]:
    """Deterministic narrative from scoring context — always succeeds."""
    scores = context.get("scores", {})
    python_result = {
        "fit_score": scores.get("fit_score", 0),
        "final_recommendation": scores.get("final_recommendation", "Pending"),
        "candidate_profile": context.get("candidate_profile", {}),
        "jd_analysis": context.get("jd_analysis", {}),
        "skill_analysis": context.get("skill_analysis", {}),
        "score_breakdown": {
            "experience_match": scores.get("exp_score", 0),
            "domain_fit": scores.get("domain_score", 0),
            "education": scores.get("edu_score", 0),
        },
        "score_rationales": context.get("score_rationales", {}),
        "risk_signals": context.get("risk_summary", {}).get("risk_flags", []),
        "_required_years": 0,
    }
    result = _build_fallback_narrative(python_result, context.get("skill_analysis", {}))
    result["ai_enhanced"] = ai_enhanced
    return result


def _llm_dict_to_narrative_result(data: Dict[str, Any]) -> Dict[str, Any]:
    """Map parsed LLM JSON to narrative result dict."""
    concerns = _ensure_str_list(data.get("concernes", data.get("concerns", data.get("weaknesses", []))))
    weaknesses = _ensure_str_list(data.get("weaknesses", concerns))
    hiring_decision = data.get("hiring_decision", {})
    if not isinstance(hiring_decision, dict):
        hiring_decision = {}

    return {
        "ai_enhanced": True,
        "candidate_profile_summary": data.get("candidate_profile_summary") or None,
        "fit_summary": str(data.get("fit_summary", "")),
        "strengths": _ensure_str_list(data.get("strengths", [])),
        "concerns": concerns,
        "weaknesses": weaknesses,
        "dealbreakers": _ensure_str_list(data.get("dealbreakers", [])),
        "differentiators": _ensure_str_list(data.get("differentiators", [])),
        "recommendation_rationale": str(data.get("recommendation_rationale", "")),
        "hiring_decision": {
            "verdict": hiring_decision.get("verdict", ""),
            "confidence": hiring_decision.get("confidence", 0.0),
            "key_factors": _ensure_str_list(hiring_decision.get("key_factors", [])),
            "action_items": _ensure_str_list(hiring_decision.get("action_items", [])),
        },
        "explainability": data.get("explainability", {}),
    }


async def explain_with_llm(context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate narrative via multi-tier LLM retries; always returns a complete narrative."""
    from app.backend.schemas.llm_structured import NarrativeLLMResponse, narrative_meets_minimum
    from app.backend.services.llm_json_service import invoke_llm_json_resilient

    _narrative_predict = _narrative_output_token_limit()

    jd       = context.get("jd_analysis", {})
    profile  = context.get("candidate_profile", {})
    scores   = context.get("scores", {})
    skill_a  = context.get("skill_analysis", {})
    score_rationales = context.get("score_rationales", {})
    risk_summary     = context.get("risk_summary", {})

    role_title = _sanitize_input(jd.get("role_title") or jd.get("title", "Unknown Role"), 200, "role_title")
    candidate_name = _sanitize_input(profile.get("name") or "Unknown", 100, "name")
    current_role = _sanitize_input(profile.get("current_role") or "N/A", 100, "current_role")
    current_company = _sanitize_input(profile.get("current_company") or "N/A", 100, "current_company")

    # Extract domain and seniority
    domain = jd.get("domain", "General")
    seniority = jd.get("seniority", "Not specified")

    # Extract experience years
    years = profile.get("total_effective_years", 0)

    # Extract scores
    skill_score = scores.get("skill_score", 0)
    exp_score = scores.get("exp_score", 0)
    edu_score = scores.get("edu_score", 0)
    timeline_score = scores.get("timeline_score", 0)
    fit_score = scores.get("fit_score", 0)
    recommendation = scores.get("final_recommendation", "Pending")

    # Extract matched and missing skills — separate must-have vs nice-to-have
    matched_must = skill_a.get("matched_required", [])
    missing_must = skill_a.get("missing_required", [])
    matched_nice = skill_a.get("matched_nice_to_have", [])
    missing_nice = skill_a.get("missing_nice_to_have", [])
    nice_match_pct = skill_a.get("nice_to_have_match_pct") or 0
    req_match_pct = skill_a.get("required_match_pct") or 0

    # Format risk flags — keep terse for token efficiency
    risk_flags_list = risk_summary.get("risk_flags", [])
    if risk_flags_list:
        risk_flags = "\n".join(
            f"  - {rf.get('flag', 'Unknown')}: {_sanitize_input(str(rf.get('detail', '')), 80, 'risk')}"
            for rf in risk_flags_list[:4]
        )
    else:
        risk_flags = "  None identified"

    # Score rationales — one-line each, capped
    if score_rationales:
        rationales_parts = []
        for key in ("skill_rationale", "experience_rationale", "overall_rationale"):
            val = score_rationales.get(key, "")
            if val:
                rationales_parts.append(
                    f"{key.split('_')[0]}: {_sanitize_input(str(val), 120, key)}"
                )
        score_rationales_summary = " | ".join(rationales_parts) if rationales_parts else "Not available"
    else:
        score_rationales_summary = "Not available"

    # Inject language instruction for multi-language support if detected
    _lang_ctx = context.get("language_context", {})
    _lang_instruction = _lang_ctx.get("llm_instruction", "")
    _lang_prefix = f"\n{_lang_instruction}\n" if _lang_instruction else ""

    _prompt_kwargs = dict(
        lang_prefix=_lang_prefix,
        role_title=role_title,
        domain=domain,
        seniority=seniority,
        candidate_name=candidate_name,
        years=years,
        current_role=current_role,
        current_company=current_company,
        skill_score=skill_score,
        exp_score=exp_score,
        edu_score=edu_score,
        timeline_score=timeline_score,
        fit_score=fit_score,
        recommendation=recommendation,
        matched_must=matched_must,
        missing_must=missing_must,
        matched_nice=matched_nice,
        missing_nice=missing_nice,
        req_match_pct=req_match_pct,
        nice_match_pct=nice_match_pct,
        risk_flags=risk_flags,
        score_rationales_summary=score_rationales_summary,
    )
    prompt = _build_narrative_prompt(**_prompt_kwargs, ultra_compact=False)
    compact_prompt = _build_narrative_prompt(**_prompt_kwargs, ultra_compact=True)

    minimal_prompt = compact_prompt + (
        "\n\nCRITICAL: Return ONLY the JSON object. No prose. fit_summary max 180 chars. Max 2 items per array."
    )

    data = await invoke_llm_json_resilient(
        [prompt, compact_prompt, minimal_prompt],
        max_output_tokens=_narrative_predict,
        log_label="narrative",
        output_type=NarrativeLLMResponse,
        validate_parsed=narrative_meets_minimum,
    )
    if data is not None:
        return _llm_dict_to_narrative_result(data)

    log.warning("Narrative LLM exhausted all tiers — using synthesized narrative from scores")
    return _narrative_from_llm_context(context, ai_enhanced=False)


def _ensure_str_list(v) -> List[str]:
    if not isinstance(v, list):
        return []
    return [item if isinstance(item, str) else str(item) for item in v]



def _build_executive_summary(score, recommendation, matched_skills, required_skills,
                              missing_skills, experience_years, current_role,
                              domain_fit_score, risk_signals, education_match,
                              candidate_name=None, current_company=None):
    """Build a rich executive summary from deterministic analysis data.

    Produces a concise, multi-dimensional narrative that helps hiring managers
    quickly understand the candidate's fit — even when the LLM narrative is
    unavailable or still processing.
    """
    parts = []

    # Opening: Name, role, and experience
    name_str = candidate_name or "Candidate"
    company_str = ""
    if current_company and current_company not in ("N/A", "", "Unknown"):
        company_str = f" at {current_company}"

    if current_role and current_role not in ("N/A", "", "Unknown"):
        parts.append(f"{name_str} is a {current_role}{company_str} with {experience_years or 0} years of experience.")
    elif experience_years:
        parts.append(f"{name_str} has {experience_years} years of professional experience.")
    else:
        parts.append(f"{name_str} has been submitted for evaluation.")

    # Specific strengths — matched must-have skills
    matched_count = len(matched_skills) if matched_skills else 0
    required_count = len(required_skills) if required_skills else 0
    if matched_count and required_count:
        skill_pct = int((matched_count / required_count) * 100)
        top_matched = ", ".join(matched_skills[:3])
        parts.append(f"Strengths include {top_matched} ({matched_count}/{required_count} required skills, {skill_pct}% coverage).")
    elif matched_count:
        top_matched = ", ".join(matched_skills[:3])
        parts.append(f"Strengths include {top_matched}.")

    # Specific weaknesses — missing must-have skills with proficiency context
    if missing_skills:
        top_missing = ", ".join(missing_skills[:3])
        if required_count and len(missing_skills) >= required_count * 0.5:
            parts.append(f"Key gaps: {top_missing} — these are critical for the role and should be probed in interview.")
        else:
            parts.append(f"Key gaps: {top_missing}.")

    # Domain and experience fit
    if domain_fit_score is not None:
        if domain_fit_score >= 60:
            parts.append(f"Strong domain alignment ({domain_fit_score}% fit).")
        else:
            parts.append(f"Limited domain overlap ({domain_fit_score}% fit) — may require ramp-up time.")

    # Education alignment
    if education_match is not None:
        if education_match >= 70:
            parts.append("Education background aligns well with role requirements.")
        elif education_match < 40:
            parts.append("Education profile may need supplementary assessment.")

    # Risk signals with severity context
    high_risks = [r for r in (risk_signals or [])
                  if isinstance(r, dict) and r.get('severity', '').lower() == 'high']
    if high_risks:
        risk_types = ", ".join(r.get('type', r.get('signal', 'unknown')) for r in high_risks[:2])
        parts.append(f"High-priority concerns: {risk_types}.")

    # Recommendation with specific reasoning
    rec_map = {
        'strong_yes': 'INTERVIEW',
        'Shortlist': 'INTERVIEW',
        'yes': 'INTERVIEW',
        'Consider': 'INTERVIEW with reservations',
        'consider': 'INTERVIEW with reservations',
        'no': 'PASS',
        'Reject': 'PASS',
    }
    verdict = rec_map.get(recommendation, f"ASSESS: {recommendation}")

    # Build the reason — pick the most important factor
    reason = ""
    if missing_skills:
        reason = f" — probe {missing_skills[0]} capability"
    elif high_risks:
        reason = f" — address {high_risks[0].get('type', 'risk flag')} concern"
    elif matched_count and required_count and matched_count >= required_count:
        reason = f" — strong skill match ({matched_count}/{required_count})"

    parts.append(f"Verdict: {verdict}{reason} (fit score: {score}/100).")

    return " ".join(parts)


def _short_resp_label(text: str, max_words: int = 8) -> str:
    """Return a brief responsibility phrase safe for spoken interview questions."""
    if not text or not isinstance(text, str):
        return "this role"
    clean = " ".join(text.split())
    if len(clean) > 60 or len(clean.split()) > max_words:
        return "this role"
    return clean.rstrip(".,;:")


def _build_fallback_narrative(python_result: Dict[str, Any], skill_analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic narrative when LLM is unavailable or timed out."""
    matched  = skill_analysis.get("matched_required") or skill_analysis.get("matched_skills", [])
    missing  = skill_analysis.get("missing_required") or skill_analysis.get("missing_skills", [])
    score    = python_result.get("fit_score", 0)
    req      = skill_analysis.get("required_count", 0)
    actual_y_raw = python_result.get("score_breakdown", {}).get("experience_match", 0)
    actual_y = actual_y_raw.get("score", 0) if isinstance(actual_y_raw, dict) else actual_y_raw
    req_y    = python_result.get("_required_years", 0)
    recommendation = python_result.get("final_recommendation", "Pending")
    score_rationales = python_result.get("score_rationales", {})

    # Extract richer context for the executive summary
    profile          = python_result.get("candidate_profile", {})
    jd_a             = python_result.get("jd_analysis", {})
    sb               = python_result.get("score_breakdown", {})
    domain_fit_raw   = sb.get("domain_fit", None)
    education_raw    = sb.get("education", None)
    risk_signals_val = python_result.get("risk_signals", [])
    required_skills_list = jd_a.get("required_skills", [])

    strengths = []
    if matched:
        strengths.append(f"Matches {len(matched)} required skills: {', '.join(matched[:4])}")
    if actual_y >= 70:
        strengths.append("Strong experience background")
    if not strengths:
        strengths.append("Profile submitted for review")

    concerns = []
    if missing:
        concerns.append(f"Missing key skills: {', '.join(missing[:4])}")
    if not concerns:
        concerns.append("Manual review recommended for full assessment")

    # Build rich executive summary using deterministic data
    fit_summary = _build_executive_summary(
        score=score,
        recommendation=recommendation,
        matched_skills=matched,
        required_skills=required_skills_list,
        missing_skills=missing,
        experience_years=profile.get("total_effective_years"),
        current_role=profile.get("current_role"),
        domain_fit_score=domain_fit_raw,
        risk_signals=risk_signals_val,
        education_match=education_raw,
        candidate_name=profile.get("name"),
        current_company=profile.get("current_company"),
    )

    # Use score_rationales for explainability if available, otherwise use defaults
    if score_rationales:
        explainability = {
            "skill_rationale":      score_rationales.get("skill_rationale", f"Matched {len(matched)} of {req} required skills."),
            "experience_rationale": score_rationales.get("experience_rationale", "Based on parsed employment timeline."),
            "education_rationale":  score_rationales.get("education_rationale", ""),
            "timeline_rationale":   score_rationales.get("timeline_rationale", ""),
            "domain_rationale":     score_rationales.get("domain_rationale", ""),
            "overall_rationale":    score_rationales.get("overall_rationale", f"Overall fit score: {score}/100."),
        }
    else:
        explainability = {
            "skill_rationale":      f"Matched {len(matched)} of {req} required skills.",
            "experience_rationale": "Based on parsed employment timeline.",
            "overall_rationale":    f"Overall fit score: {score}/100.",
        }

    # Targeted interview kit is generated independently in background_interview_kit.
    # Narrative fallback must not embed kit — keeps narrative and kit LLM paths separate.

    # Build fallback candidate_profile_summary from deterministic data
    cp = python_result.get("candidate_profile", {})
    jd_a = python_result.get("jd_analysis", {})
    sk_a = python_result.get("skill_analysis", {})
    sc = python_result.get("scores", {})

    _fallback_name = cp.get("name", "This candidate")
    _fallback_years = cp.get("total_effective_years", 0) or 0
    _fallback_domain = jd_a.get("domain", "their field")
    _fallback_matched = len(sk_a.get("matched_skills", []))
    _fallback_total = len(jd_a.get("required_skills", []))
    _fallback_rec = sc.get("final_recommendation", "Review")
    _fallback_role = jd_a.get("role_title", "this role")

    fallback_summary = (
        f"{_fallback_name} has {_fallback_years:.1f} years of experience in {_fallback_domain}. "
        f"Matched {_fallback_matched} of {_fallback_total} required skills. "
        f"Recommendation: {_fallback_rec} for {_fallback_role}."
    )

    # Return with both 'concerns' (new) and 'weaknesses' (backward compat)
    return {
        "ai_enhanced": False,  # Marks this as a fallback narrative, not LLM-generated
        "narrative_fallback": True,  # Explicit flag so frontend knows this is template-based
        "candidate_profile_summary": fallback_summary,
        "fit_summary": fit_summary,
        "strengths":   strengths,
        "concerns":    concerns,
        "weaknesses":  concerns,  # Backward compatibility alias
        "dealbreakers": concerns,  # Fallback: concerns become dealbreakers
        "differentiators": strengths,  # Fallback: strengths become differentiators
        "recommendation_rationale": (
            f"Candidate scored {score}/100. {len(matched)}/{req} required skills matched. "
            f"Automated narrative unavailable — manual review recommended."
        ),
        "hiring_decision": {
            "verdict": recommendation,
            "confidence": 0.5,
            "key_factors": [
                f"Fit score: {score}/100",
                f"Skills: {len(matched)}/{req} matched",
                f"Experience: {actual_y}/100",
            ],
            "action_items": ["Review full resume manually", "Verify missing skills in interview"],
        },
        "explainability": explainability,
    }


def _pending_interview_kit_shell() -> Dict[str, Any]:
    """Minimal shell while background kit personalization runs."""
    return {
        "kit_version": 3,
        "kit_status": "pending",
        "threads": [],
        "technical_questions": [],
        "behavioral_questions": [],
        "culture_fit_questions": [],
        "experience_deep_dive_questions": [],
        "candidate_briefing": {
            "profile_snapshot": "Generating a personalized interview screen for this candidate…",
            "strengths_to_confirm": [],
            "areas_to_probe": [],
            "context_notes": ["Interview kit is being personalized — refresh in a moment."],
        },
    }


def _placeholder_interview_kit(python_result: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic kit for sync fallback; prefer pending shell for immediate API response."""
    from app.backend.services.interview_kit_generator import generate_targeted_interview_kit
    from app.backend.services.candidate_intelligence_service import build_candidate_intelligence

    ci = build_candidate_intelligence(
        analysis_result=python_result,
        parsed_data=python_result.get("parsed_data"),
        gap_analysis=python_result.get("gap_analysis"),
        fit_score=python_result.get("fit_score"),
    )
    kit = generate_targeted_interview_kit(
        profile=python_result.get("candidate_profile", {}),
        jd_analysis=python_result.get("jd_analysis", {}),
        skill_analysis=python_result.get("skill_analysis", {}),
        parsed_data=python_result.get("parsed_data"),
        kit_inputs=python_result.get("kit_inputs"),
        candidate_intelligence=ci,
    )
    return kit


def _immediate_interview_kit_placeholder() -> Dict[str, Any]:
    return _pending_interview_kit_shell()


def _merge_immediate_pipeline_result(python_result: Dict[str, Any], narrative: Dict[str, Any]) -> Dict[str, Any]:
    """Merge narrative into python scoring; attach placeholder kit until background kit LLM completes."""
    merged = _merge_llm_into_result(python_result, narrative)
    merged["interview_questions"] = _immediate_interview_kit_placeholder()
    return merged


# ═══════════════════════════════════════════════════════════════════════════════
# SCORE RATIONALES & METADATA BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def _build_score_rationales(
    all_scores: Dict[str, Any],
    profile: Dict[str, Any],
    jd: Dict[str, Any],
    skill_a: Dict[str, Any],
    exp_r: Dict[str, Any],
    edu_s: int,
    dom_r: Dict[str, Any],
    gap_analysis: Dict[str, Any],
) -> Dict[str, str]:
    """Build human-readable rationale for each score dimension."""
    matched = skill_a.get("matched_required") or skill_a.get("matched_skills", [])
    missing = skill_a.get("missing_required") or skill_a.get("missing_skills", [])
    req_count = skill_a.get("required_count", 0)
    skill_score = skill_a.get("skill_score", 0)

    # Skill rationale
    if matched and missing:
        skill_rat = (f"Matched {len(matched)}/{req_count} required skills "
                     f"({', '.join(matched[:5])}). "
                     f"Missing: {', '.join(missing[:5])}. Score: {skill_score}/100.")
    elif matched:
        skill_rat = (f"All {len(matched)}/{req_count} required skills matched "
                     f"({', '.join(matched[:5])}). Score: {skill_score}/100.")
    elif missing:
        skill_rat = (f"None of {req_count} required skills matched. "
                     f"Missing: {', '.join(missing[:5])}. Score: {skill_score}/100.")
    else:
        skill_rat = f"No required skills specified in job description. Score: {skill_score}/100."

    # Experience rationale
    actual_y = exp_r.get("actual_years", 0)
    req_y = exp_r.get("required_years", 0)
    exp_score = exp_r.get("exp_score", 0)
    seniority = jd.get("seniority", "unknown")
    if req_y > 0 and actual_y < req_y * 0.6:
        exp_qualifier = "Significantly underqualified"
    elif req_y > 0 and actual_y < req_y:
        exp_qualifier = "Slightly below requirement"
    elif req_y > 0 and actual_y > req_y * 2:
        exp_qualifier = "Overqualified"
    elif req_y > 0:
        exp_qualifier = "Meets requirement"
    else:
        exp_qualifier = "Experience level noted"
    exp_rat = (f"{actual_y:.1f}y experience vs {req_y}y+ expected for {seniority} role. "
               f"{exp_qualifier}. Score: {exp_score}/100.")

    # Education rationale
    education = profile.get("education", [])
    if education:
        best_edu = education[0]  # first is typically highest
        degree = best_edu.get("degree", "Unknown degree")
        field = best_edu.get("field", "")
        domain = jd.get("domain", "general")
        edu_rat = (f"{degree}{(' in ' + field) if field else ''} — "
                   f"{'relevant' if edu_s >= 65 else 'partially relevant' if edu_s >= 45 else 'limited relevance'} "
                   f"for {domain} domain. Score: {edu_s}/100.")
    else:
        edu_rat = f"No education data found in resume. Default score: {edu_s}/100."

    # Timeline rationale
    timeline_score = exp_r.get("timeline_score", 85)
    gaps = gap_analysis.get("employment_gaps", [])
    stints = gap_analysis.get("short_stints", [])
    critical_gaps = [g for g in gaps if g.get("severity") == "critical"]
    parts = []
    if critical_gaps:
        parts.append(f"{len(critical_gaps)} critical gap(s) (12+ months)")
    elif gaps:
        parts.append(f"{len(gaps)} employment gap(s)")
    else:
        parts.append("No employment gaps")
    if stints:
        parts.append(f"{len(stints)} short stint(s) (<6 months)")
    timeline_rat = f"{'. '.join(parts)}. Score: {timeline_score}/100."

    # Domain rationale
    domain_score = dom_r.get("domain_score", 50)
    arch_score = dom_r.get("arch_score", 50)
    domain = jd.get("domain", "general")
    current_role = profile.get("current_role", "Unknown")
    domain_rat = (f"Domain fit for {domain}: {domain_score}/100. "
                  f"Architecture alignment: {arch_score}/100. "
                  f"Current role: {current_role}.")

    # Overall rationale
    fit_score = all_scores.get("fit_score", 0)
    recommendation = all_scores.get("final_recommendation", "Pending")
    # Build a concise overall explanation
    top_strength = "strong skill match" if skill_score >= 70 else "adequate skills" if skill_score >= 40 else "weak skill match"
    top_concern = ""
    if exp_score < 40:
        top_concern = "insufficient experience for seniority level"
    elif len(missing) > len(matched):
        top_concern = "more required skills missing than matched"
    elif critical_gaps:
        top_concern = "critical employment gap(s)"
    elif domain_score < 40:
        top_concern = "domain mismatch"

    overall_rat = f"Fit score: {fit_score}/100 — {recommendation}. "
    if top_concern:
        overall_rat += f"Key strength: {top_strength}. Key concern: {top_concern}."
    else:
        overall_rat += f"Key strength: {top_strength}. No critical concerns identified."

    return {
        "skill_rationale": skill_rat,
        "experience_rationale": exp_rat,
        "education_rationale": edu_rat,
        "timeline_rationale": timeline_rat,
        "domain_rationale": domain_rat,
        "overall_rationale": overall_rat,
    }


def _build_risk_summary(
    risk_signals: List[Dict[str, Any]],
    gap_analysis: Dict[str, Any],
    exp_r: Dict[str, Any],
    profile: Dict[str, Any],
    jd: Dict[str, Any],
) -> Dict[str, Any]:
    """Build structured risk and alignment summary."""
    # Risk flags — convert existing risk_signals to user-friendly format
    risk_flags = []
    for rs in risk_signals:
        risk_flags.append({
            "flag": rs.get("type", "unknown").replace("_", " ").title(),
            "detail": rs.get("description", ""),
            "severity": rs.get("severity", "low"),
        })

    # Seniority alignment
    actual_y = exp_r.get("actual_years", 0)
    seniority = jd.get("seniority", "unknown")
    seniority_ranges = SENIORITY_RANGES
    lo, hi = seniority_ranges.get(seniority.lower(), (0, 100))
    if actual_y < lo:
        seniority_alignment = f"Underqualified — {actual_y:.1f}y experience, {seniority} typically requires {lo}-{hi}y"
    elif actual_y > hi:
        seniority_alignment = f"Overqualified — {actual_y:.1f}y experience, {seniority} typically requires {lo}-{hi}y"
    else:
        seniority_alignment = f"Aligned — {actual_y:.1f}y experience fits {seniority} range ({lo}-{hi}y)"

    # Career trajectory — look at work experience progression
    work_exp = profile.get("work_experience", [])
    if len(work_exp) >= 2:
        # Simple heuristic: compare first and last role titles for progression keywords
        first_title = str(work_exp[-1].get("title", "")).lower() if work_exp else ""
        last_title = str(work_exp[0].get("title", "")).lower() if work_exp else ""
        senior_keywords = {"senior", "lead", "principal", "staff", "head", "director", "manager", "architect", "vp", "chief"}
        junior_keywords = {"intern", "trainee", "junior", "associate", "entry"}
        last_is_senior = any(k in last_title for k in senior_keywords)
        first_is_junior = any(k in first_title for k in junior_keywords)
        if last_is_senior and first_is_junior:
            trajectory = f"Strong upward — progressed from '{work_exp[-1].get('title', 'N/A')}' to '{work_exp[0].get('title', 'N/A')}'"
        elif last_is_senior or (len(work_exp) >= 3):
            trajectory = f"Upward — current role: '{work_exp[0].get('title', 'N/A')}' across {len(work_exp)} positions"
        else:
            trajectory = f"Early career — {len(work_exp)} position(s), current: '{work_exp[0].get('title', 'N/A')}'"
    elif len(work_exp) == 1:
        trajectory = f"Single role — '{work_exp[0].get('title', 'N/A')}'"
    else:
        trajectory = "No work experience data available"

    # Stability assessment
    gaps = gap_analysis.get("employment_gaps", [])
    stints = gap_analysis.get("short_stints", [])
    critical_gaps = sum(1 for g in gaps if g.get("severity") == "critical")
    if critical_gaps:
        stability = f"Unstable — {critical_gaps} critical gap(s) (12+ months)"
    elif len(stints) >= 3:
        stability = f"Concerning — {len(stints)} short stints (<6 months), potential job-hopping pattern"
    elif len(stints) >= 1 or len(gaps) >= 1:
        stability = f"Moderate — {len(gaps)} gap(s), {len(stints)} short stint(s)"
    else:
        stability = "Stable — no gaps or short stints detected"

    return {
        "risk_flags": risk_flags,
        "seniority_alignment": seniority_alignment,
        "career_trajectory": trajectory,
        "stability_assessment": stability,
    }


def _compute_skill_depth(
    raw_text: str,
    matched_skills: List[str],
    missing_skills: List[str],
) -> Dict[str, int]:
    """Count how many times each skill appears in the resume text."""
    text_lower = raw_text.lower()
    depth = {}
    for skill in matched_skills:
        # Count occurrences (case-insensitive)
        count = text_lower.count(skill.lower())
        if count > 0:
            depth[skill] = count
    # Also note missing skills with 0
    for skill in missing_skills:
        depth[skill] = 0
    return depth


# ═══════════════════════════════════════════════════════════════════════════════
# PROFICIENCY-AWARE SCORING
# ═══════════════════════════════════════════════════════════════════════════════

PROFICIENCY_LEVELS = {"basic": 1, "intermediate": 2, "advanced": 3, "expert": 4}


def compute_recency_decay(last_used_year: int) -> float:
    """Compute decay factor based on how recently a skill was used."""
    from datetime import datetime
    current_year = datetime.now().year
    years_ago = current_year - last_used_year
    if years_ago <= 1:
        return 1.0  # Current or last year — no decay
    elif years_ago <= 2:
        return 0.9
    elif years_ago <= 3:
        return 0.78
    else:
        return max(0.4, 1.0 - (0.12 * years_ago))  # 12% decay/year, min 40%


def _estimate_candidate_proficiency(skill: str, candidate_skills_data: dict) -> str:
    """
    Estimate candidate's proficiency level for a specific skill.

    Priority 1: LLM-extracted skill-years with recency decay (evidence-based)
    Priority 2: Existing heuristic (total years + mention count) as fallback
    """
    # Priority 1: Check LLM-extracted skills_with_experience
    skills_with_exp = candidate_skills_data.get("skills_with_experience", [])
    skill_lower = skill.lower()

    skill_data = None
    for s in skills_with_exp:
        if s.get("skill", "").lower() == skill_lower:
            skill_data = s
            break

    if skill_data and skill_data.get("years"):
        years = skill_data["years"]
        last_used = skill_data.get("last_used", 2024)
        recency = compute_recency_decay(last_used)
        effective_years = years * recency
    else:
        # Priority 2: Fallback to existing heuristic
        # (Keep the existing logic that uses total_effective_years and mention count)
        total_years = candidate_skills_data.get("total_effective_years", 0)
        skills_list = candidate_skills_data.get("skills_identified", [])
        work_exp = candidate_skills_data.get("work_experience", [])

        # Existing heuristic: count mentions in work experience
        mention_count = 0
        years_with_skill = 0.0
        for exp in work_exp:
            if not isinstance(exp, dict):
                continue
            desc = (exp.get("description", "") or "").lower()
            title = (exp.get("title", "") or "").lower()
            if skill_lower in desc or skill_lower in title:
                dur = exp.get("duration_months") or exp.get("duration")
                if isinstance(dur, (int, float)):
                    years_with_skill += dur / 12.0
                else:
                    years_with_skill += 0.5
                mention_count += 1

        # Also count mentions in skills list
        for s in skills_list:
            if isinstance(s, str) and s.lower() == skill_lower:
                mention_count += 1
            elif isinstance(s, dict):
                s_name = s.get("skill", s.get("name", ""))
                if isinstance(s_name, str) and s_name.lower() == skill_lower:
                    years_val = s.get("years")
                    if isinstance(years_val, (int, float)):
                        years_with_skill = max(years_with_skill, float(years_val))
                    mention_count += 1

        # If no specific skill-year data, use total experience as proxy
        effective_years = years_with_skill if years_with_skill > 0 else total_years * 0.3

        # If we have mention count, boost the estimate
        if mention_count >= 5:
            effective_years = max(effective_years, 3.0)
        elif mention_count >= 3:
            effective_years = max(effective_years, 1.5)

    # Map to proficiency level
    if effective_years >= 5:
        return "expert"
    elif effective_years >= 3:
        return "advanced"
    elif effective_years >= 1:
        return "intermediate"
    else:
        return "basic"


def _compute_proficiency_score(
    matched_skills: list,
    candidate_skills_data: dict,
    proficiency_requirements: dict,
) -> float:
    """Score skills based on proficiency match, not just presence.

    Returns a factor between 0.0 and 1.0 that scales the binary match ratio.
    Returns 1.0 when all proficiency requirements are met or exceeded.
    """
    if not proficiency_requirements or not matched_skills:
        return 1.0

    total_score = 0.0
    for skill in matched_skills:
        required_level = proficiency_requirements.get(
            skill.lower() if isinstance(skill, str) else str(skill).lower(), None
        )
        if required_level is None:
            total_score += 1.0  # No proficiency requirement = full match
            continue

        # Estimate candidate proficiency from resume data
        candidate_level = _estimate_candidate_proficiency(skill, candidate_skills_data)

        req_rank = PROFICIENCY_LEVELS.get(required_level, 2)
        cand_rank = PROFICIENCY_LEVELS.get(candidate_level, 2)

        if cand_rank >= req_rank:
            total_score += 1.0       # Match or exceed
        elif cand_rank == req_rank - 1:
            total_score += 0.6       # One level below
        else:
            total_score += 0.3       # Two+ levels below

    return total_score / max(len(matched_skills), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATORS
# ═══════════════════════════════════════════════════════════════════════════════

def _run_python_phase(
    resume_text: str,
    job_description: str,
    parsed_data: Dict[str, Any],
    gap_analysis: Dict[str, Any],
    scoring_weights: Optional[Dict],
    jd_analysis: Optional[Dict],
    phase3_context: Optional[Dict] = None,
    llm_resume_skills: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute all deterministic Python components. Returns a rich result dict."""
    # Sanitize user-provided text to prevent prompt injection
    resume_text, job_description = _wrap_user_content(resume_text, job_description)

    jd       = jd_analysis or parse_jd_rules(job_description)

    # Layer 1: Inject JD skills into gap_analysis so confirm_skills_in_text
    # can search the resume text for JD-specific skills (e.g. "SAP MM",
    # "Material Master", "LSMW") that the static FlashText registry may miss.
    gap_analysis = dict(gap_analysis)  # shallow copy — don't mutate caller's dict
    gap_analysis.setdefault("required_skills", jd.get("required_skills", []))
    gap_analysis.setdefault("nice_to_have_skills", jd.get("nice_to_have_skills", []))

    profile  = parse_resume_rules(parsed_data, gap_analysis, llm_resume_skills=llm_resume_skills)
    skill_a  = match_skills_with_onet(
        profile.get("skills_identified", []),
        jd.get("required_skills", []),
        jd.get("nice_to_have_skills", []),
        job_title=jd.get("role_title"),
        structured_skills=profile.get("structured_skills", []),
        text_scanned_skills=profile.get("text_scanned_skills", []),
    )

    # ── Augment skill_analysis with tiered fields ────────────────────────────
    required_skills = jd.get("required_skills", []) or []
    nice_to_have_skills = jd.get("nice_to_have_skills", []) or []

    matched_required = list(skill_a.get("matched_skills", []))
    missing_required = list(skill_a.get("missing_skills", []))
    matched_nice_to_have = list(skill_a.get("adjacent_skills", []))

    # Compute missing nice-to-have (case-insensitive)
    matched_nice_lower = {m.lower() for m in matched_nice_to_have if isinstance(m, str)}
    missing_nice_to_have = [
        s for s in nice_to_have_skills
        if isinstance(s, str) and s.lower() not in matched_nice_lower
    ]

    required_match_pct = skill_a.get("core_match_ratio", 0) * 100
    nice_to_have_match_pct = skill_a.get("secondary_match_ratio", 0) * 100

    # Update skill_score with 70/30 weighting when nice-to-have skills exist
    # Apply proficiency-aware scoring when proficiency requirements are present
    proficiency_requirements = jd.get("skill_proficiency_requirements")
    proficiency_analysis = {}
    prof_factor = None

    if proficiency_requirements and matched_required:
        # Build candidate skills data for proficiency estimation
        candidate_skills_data = {
            "skills_identified": profile.get("skills_identified", []),
            "total_effective_years": profile.get("total_effective_years", 0),
            "work_experience": parsed_data.get("work_experience", []),
            "skills_with_experience": profile.get("skills_with_experience", []),
        }
        prof_factor = _compute_proficiency_score(
            matched_required, candidate_skills_data, proficiency_requirements,
        )
        # Build proficiency_analysis details
        for skill in matched_required:
            req_level = proficiency_requirements.get(skill.lower() if isinstance(skill, str) else str(skill).lower())
            if req_level:
                cand_level = _estimate_candidate_proficiency(skill, candidate_skills_data)
                req_rank = PROFICIENCY_LEVELS.get(req_level, 2)
                cand_rank = PROFICIENCY_LEVELS.get(cand_level, 2)
                if cand_rank >= req_rank:
                    match_factor = 1.0
                elif cand_rank == req_rank - 1:
                    match_factor = 0.6
                else:
                    match_factor = 0.3
                proficiency_analysis[skill] = {
                    "required": req_level,
                    "estimated_candidate": cand_level,
                    "match_factor": match_factor,
                }

    if nice_to_have_skills:
        req_pct = required_match_pct
        if prof_factor is not None:
            req_pct = required_match_pct * prof_factor
        skill_a["skill_score"] = combine_skill_ratios(req_pct, nice_to_have_match_pct)
    elif prof_factor is not None:
        skill_a["skill_score"] = round(required_match_pct * prof_factor)

    # Build unions for backward compatibility
    skill_a["matched_skills"] = matched_required + matched_nice_to_have
    skill_a["missing_skills"] = missing_required + missing_nice_to_have
    skill_a["matched_required"] = matched_required
    skill_a["missing_required"] = missing_required
    skill_a["matched_nice_to_have"] = matched_nice_to_have
    skill_a["missing_nice_to_have"] = missing_nice_to_have
    skill_a["required_match_pct"] = required_match_pct
    skill_a["nice_to_have_match_pct"] = nice_to_have_match_pct
    if proficiency_analysis:
        skill_a["proficiency_analysis"] = proficiency_analysis

    edu_s    = score_education_rules(profile, jd)
    exp_r    = score_experience_rules(profile, jd, gap_analysis)
    dom_r    = domain_architecture_rules(resume_text, jd, profile.get("current_role"))

    all_scores = {
        "skill_score":    skill_a["skill_score"],
        "exp_score":      exp_r["exp_score"],
        "arch_score":     dom_r["arch_score"],
        "edu_score":      edu_s,
        "timeline_score": exp_r["timeline_score"],
        "domain_score":   dom_r["domain_score"],
        "actual_years":   exp_r["actual_years"],
        "required_years": exp_r["required_years"],
        "matched_skills": skill_a["matched_skills"],
        "missing_skills": skill_a["missing_skills"],
        "required_count": skill_a["required_count"],
        "employment_gaps": gap_analysis.get("employment_gaps", []),
        "short_stints":    gap_analysis.get("short_stints", []),
        "fit_score": 0,
    }

    # Convert incoming weights to new schema, then map to internal 7-key schema
    # (mirrors agent_pipeline.py scorer_node — frontend may send legacy or new keys)
    log.debug("Raw scoring_weights received: %s", scoring_weights)
    new_weights = convert_to_new_schema(scoring_weights)
    log.debug("Converted to new schema: %s", new_weights)
    internal_weights = {
        "skills":       new_weights.get("core_competencies", 0.30),
        "experience":   new_weights.get("experience", 0.20),
        "architecture": new_weights.get("role_excellence", 0.15),
        "education":    new_weights.get("education", 0.10),
        "timeline":     new_weights.get("career_trajectory", 0.10),
        "domain":       new_weights.get("domain_fit", 0.10),
        "risk":         new_weights.get("risk", 0.15),
    }
    from app.backend.services.scoring_weights import normalize_risk_weight
    internal_weights["risk"] = normalize_risk_weight(internal_weights.get("risk"))
    log.debug("Internal weights for compute_fit_score: %s", internal_weights)

    # Extract industry from JD analysis for industry-specific weights
    industry = jd.get("domain") if jd else None
    fit_r = compute_fit_score(
        all_scores,
        internal_weights,
        jd_analysis=jd,
        phase3_context=phase3_context,
        skill_match_result=skill_a,
        industry=industry,
    )
    log.info("compute_fit_score result: fit_score=%s", fit_r["fit_score"])

    # ── Deterministic engine (domain → eligibility → deterministic score) ─────
    jd_domain = {"domain": "unknown", "confidence": 0.0, "scores": {}}
    candidate_domain = {"domain": "unknown", "confidence": 0.0, "scores": {}}
    eligibility = None
    deterministic_score = fit_r["fit_score"]
    decision_explanation = {}
    deterministic_features = {}
    try:
        # Domain-agnostic detection: use the JD profile's keywords when available
        jd_domain = detect_domain_from_jd(job_description, jd_analysis=jd)
        candidate_domain = detect_domain_from_resume(
            skills=profile.get("skills_identified", []),
            resume_text=resume_text,
            jd_domain=jd_domain,
        )

        # Domain match: how many of the JD domain keywords appear in the candidate profile
        domain_match = candidate_domain.get("confidence", 0.0)
        if not domain_match:
            domain_match = _compute_domain_similarity(jd_domain, candidate_domain)

        required_years = jd.get("min_required_years", jd.get("required_years", 1)) or 1
        relevant_exp_ratio = min(profile.get("total_effective_years", 0) / required_years, 1.0)
        deterministic_features = {
            "core_skill_match": skill_a.get("core_match_ratio", 0) if isinstance(skill_a, dict) else 0,
            "secondary_skill_match": skill_a.get("secondary_match_ratio", 0) if isinstance(skill_a, dict) else 0,
            "domain_match": domain_match,
            "relevant_experience": relevant_exp_ratio,
            "total_experience": profile.get("total_effective_years", 0),
        }

        # Skip eligibility only if we have no meaningful domain signal at all
        if not jd_domain.get("domain") or jd_domain.get("domain") == "unknown":
            log.debug("Skipping eligibility check — domain detection incomplete")
            from app.backend.services.eligibility_service import EligibilityResult
            eligibility = EligibilityResult(eligible=True, reason="Domain detection incomplete — defaulting to eligible")
        else:
            eligibility = check_eligibility(
                jd_domain=jd_domain,
                candidate_domain=candidate_domain,
                core_skill_match=deterministic_features["core_skill_match"],
                relevant_experience=deterministic_features["relevant_experience"],
            )

        # Pass the new_weights (converted schema) to deterministic scorer
        # This ensures custom/AI weights are properly used
        log.debug("Passing new_weights to compute_deterministic_score: %s", new_weights)
        deterministic_score = compute_deterministic_score(deterministic_features, eligibility, new_weights)
        log.debug("compute_deterministic_score result: %s (was fit_score: %s)", deterministic_score, fit_r["fit_score"])
        decision_explanation = explain_decision(deterministic_features, eligibility)
    except Exception as e:
        log.warning("Deterministic engine failed, falling back to legacy fit_score: %s", e)
        import traceback
        log.debug("Deterministic engine traceback: %s", traceback.format_exc())

    # Blend deterministic score with the nuanced fit_score.
    # The deterministic engine uses only 4 crude 0-1 ratios (core_skill_match,
    # secondary_skill_match, domain_match, relevant_experience) and ignores the
    # carefully computed skill_score (with confidence weighting, proficiency),
    # education, timeline, and architecture scores.  Using it as the sole score
    # unfairly penalises eligible candidates whose skill names don't exactly match.
    if eligibility is not None and eligibility.eligible:
        final_score = int(0.6 * fit_r["fit_score"] + 0.4 * deterministic_score)
    else:
        final_score = deterministic_score
    final_score = max(0, min(100, final_score))

    log.info(
        "Final score: %s (fit_score=%s, deterministic=%s, eligible=%s)",
        final_score, fit_r["fit_score"], deterministic_score,
        eligibility.eligible if eligibility else "N/A",
    )
    all_scores["fit_score"] = final_score
    rec, decision_explanation = align_decision_to_score(final_score, decision_explanation)
    all_scores["final_recommendation"] = rec

    rationales = _build_score_rationales(all_scores, profile, jd, skill_a, exp_r, edu_s, dom_r, gap_analysis)
    risk_summary = _build_risk_summary(fit_r["risk_signals"], gap_analysis, exp_r, profile, jd)
    skill_depth = _compute_skill_depth(resume_text, skill_a["matched_skills"], skill_a["missing_skills"])

    quality = _assess_quality(profile)

    # ── Enterprise enrichment (non-breaking, additive fields) ────────────────
    language_context = {}
    proficiency_data = {}
    fraud_check_result = {}
    enrichment_data = {}

    try:
        from app.backend.services.language_service import get_resume_language_context
        language_context = get_resume_language_context(resume_text)
    except Exception as e:
        log.debug("Language detection failed (non-fatal): %s", e)

    try:
        from app.backend.services.proficiency_service import detect_proficiency
        proficiency_data = detect_proficiency(resume_text)
    except Exception as e:
        log.debug("Proficiency detection failed (non-fatal): %s", e)

    try:
        from app.backend.services.fraud_detection_service import run_fraud_check
        fraud_check_result = run_fraud_check(
            resume_text=resume_text,
            matched_skills=skill_a.get("matched_skills_detailed", []),
            candidate_profile=profile,
            gap_analysis=gap_analysis,
            jd_required_skills=jd.get("required_skills", []),
        )
    except Exception as e:
        log.debug("Fraud detection failed (non-fatal): %s", e)

    try:
        from app.backend.services.resume_enrichment_service import enrich_resume
        enrichment_data = enrich_resume(resume_text)
    except Exception as e:
        log.debug("Resume enrichment failed (non-fatal): %s", e)

    return {
        "jd_analysis":         jd,
        "candidate_profile":   profile,
        "skill_analysis":      skill_a,
        "edu_timeline_analysis": {
            "education_score":  edu_s,
            "timeline_text":    exp_r["timeline_text"],
            "employment_gaps":  gap_analysis.get("employment_gaps", []),
            "overlapping_jobs": gap_analysis.get("overlapping_jobs", []),
            "short_stints":     gap_analysis.get("short_stints", []),
        },
        # Top-level fields for AnalysisResponse schema
        "fit_score":            final_score,
        "job_role":             jd["role_title"],
        "final_recommendation": all_scores["final_recommendation"],
        "risk_level":           fit_r["risk_level"],
        "risk_signals":         fit_r["risk_signals"],
        "score_breakdown":      fit_r["score_breakdown"],
        "matched_skills":       skill_a["matched_skills"],
        "missing_skills":       skill_a["missing_skills"],
        "adjacent_skills":      skill_a["adjacent_skills"],
        "required_skills_count": skill_a["required_count"],
        "work_experience":      parsed_data.get("work_experience", []),
        "contact_info":         parsed_data.get("contact_info", {}),
        "employment_gaps":      gap_analysis.get("employment_gaps", []),
        "education_analysis":   None,
        "analysis_quality":     quality,
        "narrative_pending":    False,
        "pipeline_errors":      [],
        "score_rationales":     rationales,
        "risk_summary":         risk_summary,
        "skill_depth":          skill_depth,
        # Deterministic engine outputs
        "deterministic_score":  deterministic_score,
        "decision_explanation": decision_explanation,
        "jd_domain":            jd_domain,
        "candidate_domain":     candidate_domain,
        "eligibility":          {
            "eligible": eligibility.eligible,
            "reason": eligibility.reason,
            "details": eligibility.details,
        } if eligibility else {"eligible": True, "reason": None, "details": {}},
        "deterministic_features": deterministic_features,
        # O*NET occupation-aware validation (optional — absent when DB not synced)
        "onet_occupation":     skill_a.get("onet_validation", {}).get("occupation_title", ""),
        "onet_soc_code":      skill_a.get("onet_validation", {}).get("soc_code", ""),
        "onet_match_ratio":   skill_a.get("onet_validation", {}).get("occupation_match_ratio", 0.0),
        "onet_hot_skills":    [
            s["skill"] for s in skill_a.get("onet_validation", {}).get("validated", [])
            if s.get("is_hot")
        ],
        # ── Enterprise enrichment fields (additive, non-breaking) ─────────────
        "language_context":     language_context,
        "proficiency_assessment": proficiency_data,
        "fraud_check":          fraud_check_result,
        "resume_enrichment":    enrichment_data,
        # Internal — used by fallback
        "_required_years":      exp_r["required_years"],
        "_scores":              all_scores,
    }


def _merge_llm_into_result(
    python_result: Dict[str, Any],
    llm_result: Dict[str, Any],
    source_text: str | None = None,
) -> Dict[str, Any]:
    """Merge LLM narrative into the Python result dict."""
    merged = dict(python_result)

    # Handle concerns/weaknesses for backward compatibility
    # If LLM returns concerns (new format), use it for both fields
    # If LLM returns weaknesses (old format), use it for both fields
    concerns = llm_result.get("concerns", [])
    weaknesses = llm_result.get("weaknesses", [])

    # Normalize: ensure both fields exist and are consistent
    if concerns and not weaknesses:
        # New format: concerns provided, weaknesses not
        final_concerns = concerns
        final_weaknesses = concerns
    elif weaknesses and not concerns:
        # Old format: weaknesses provided, concerns not
        final_concerns = weaknesses
        final_weaknesses = weaknesses
    elif concerns:
        # Both provided - prefer concerns for concerns, but ensure consistency
        final_concerns = concerns
        final_weaknesses = weaknesses if weaknesses else concerns
    else:
        # Neither provided - use empty lists
        final_concerns = []
        final_weaknesses = []

    merged.update({
        "ai_enhanced":            llm_result.get("ai_enhanced", False),  # True for LLM, False for fallback
        "candidate_profile_summary": llm_result.get("candidate_profile_summary"),
        "fit_summary":            llm_result.get("fit_summary", ""),
        "strengths":              llm_result.get("strengths", []),
        "concerns":               final_concerns,
        "weaknesses":             final_weaknesses,
        "recommendation_rationale": llm_result.get("recommendation_rationale", ""),
        "explainability":         llm_result.get("explainability", {}),
        "education_analysis":     llm_result.get("education_analysis", "") or llm_result.get("explainability", {}).get("education_rationale", ""),
    })
    source = source_text or python_result.get("raw_resume_text") or ""
    if not source:
        parsed = python_result.get("parsed_data") or {}
        if isinstance(parsed, dict):
            source = parsed.get("raw_text") or parsed.get("raw_resume_text") or ""
    if source:
        merged = _filter_narrative_against_resume(merged, source)
    if llm_result.get("interview_questions") is not None:
        merged["interview_questions"] = llm_result.get("interview_questions")
    # Remove internal keys
    merged.pop("_required_years", None)
    merged.pop("_scores", None)
    return merged


_NARRATIVE_STOPWORDS = {
    "with", "from", "that", "this", "have", "been", "strong", "experience",
    "years", "year", "skills", "skill", "using", "used", "well", "good",
    "great", "also", "their", "they", "them", "into", "over", "more",
    "work", "working", "candidate", "resume", "role", "team",
}


def _narrative_item_text(item) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("strength") or item.get("concern") or item.get("text") or item.get("claim") or "")
    return str(item or "")


def _filter_narrative_against_resume(merged: Dict[str, Any], resume_text: str) -> Dict[str, Any]:
    """Drop narrative claims whose distinctive tokens are absent from the resume."""
    source = (resume_text or "").lower()
    if not source.strip():
        return merged

    def _keep(item) -> bool:
        text = _narrative_item_text(item)
        if not text:
            return False
        evidence = str(item.get("evidence") or "") if isinstance(item, dict) else ""
        blob = f"{text} {evidence}".strip()
        if blob.lower() in source:
            return True
        tokens = [w for w in re.findall(r"[a-zA-Z]{4,}", blob.lower()) if w not in _NARRATIVE_STOPWORDS]
        if not tokens:
            return True
        hits = sum(1 for t in tokens if t in source)
        return (hits / len(tokens)) >= 0.35

    for key in ("strengths", "concerns", "weaknesses"):
        items = merged.get(key)
        if isinstance(items, list):
            merged[key] = [item for item in items if _keep(item)]
    return merged


# ═══════════════════════════════════════════════════════════════════════════════
# BACKGROUND LLM NARRATIVE TASK
# ═══════════════════════════════════════════════════════════════════════════════

async def _background_llm_narrative(
    screening_result_id: int,
    tenant_id: int,
    llm_context: Dict[str, Any],
    python_result: Dict[str, Any],
    expected_analysis_generation: int,
) -> None:
    """
    Background task that generates LLM narrative and writes to DB.
    
    This runs independently after the Python results are returned.
    Creates its own DB session to avoid sharing with request.
    """
    # Import here to avoid circular imports
    from app.backend.db.database import SessionLocal
    from app.backend.models.db_models import ScreeningResult, Candidate
    from app.backend.services.reliability.stale import apply_narrative_if_generation

    # Helper to write status to DB
    async def _write_status(status: str, error: Optional[str] = None) -> bool:
        """Write narrative_status only when generation still matches."""
        try:
            db = SessionLocal()
            try:
                updated = db.query(ScreeningResult).filter(
                    ScreeningResult.id == screening_result_id,
                    ScreeningResult.tenant_id == tenant_id,
                    ScreeningResult.analysis_generation == expected_analysis_generation,
                ).update(
                    {"narrative_status": status, "narrative_error": error},
                    synchronize_session=False,
                )
                if updated != 1:
                    db.rollback()
                    return False
                db.commit()
                return True
            finally:
                db.close()
        except Exception as db_err:
            log.error("Failed to write status to DB: %s", str(db_err)[:200])
        return False

    # Helper to write narrative with retry
    async def _write_narrative_with_retry(
        narrative: Dict[str, Any],
        status: str,
        error: Optional[str] = None,
    ) -> bool:
        """Write narrative_json, status, and error to DB with one retry on failure.
        
        Also updates analysis_result with merged narrative data so the complete
        report is available when viewing from the Candidates page.
        """
        for attempt in range(2):
            try:
                db = SessionLocal()
                try:
                    result = db.query(ScreeningResult).filter(
                        ScreeningResult.id == screening_result_id,
                        ScreeningResult.tenant_id == tenant_id,
                        ScreeningResult.analysis_generation == expected_analysis_generation,
                    ).first()
                    if result:
                        # Merge narrative into analysis_result for complete report persistence
                        # This ensures the Candidates page shows the full report, not "PENDING"
                        current_analysis: Dict[str, Any] = {}
                        merged_analysis: Dict[str, Any] | None = None
                        try:
                            current_analysis = json.loads(result.analysis_result or "{}")
                            # If analysis_result is empty or missing critical fields,
                            # use python_result as the base (available from outer scope)
                            if not current_analysis.get("fit_score") and python_result.get("fit_score") is not None:
                                log.warning(
                                    "analysis_result for screening_result_id=%s is empty/missing fit_score "
                                    "(%d keys). Using python_result as base for narrative merge.",
                                    screening_result_id, len(current_analysis),
                                )
                                base = {k: v for k, v in python_result.items() if not k.startswith("_")}
                                current_analysis = base
                            merged_analysis = _merge_llm_into_result(current_analysis, narrative)
                        except Exception as merge_err:
                            log.warning(
                                "Failed to merge narrative into analysis_result for screening_result_id=%s: %s",
                                screening_result_id,
                                str(merge_err)[:200],
                            )
                            # Continue even if merge fails - narrative_json is still saved

                        outcome = apply_narrative_if_generation(
                            db,
                            screening_result_id=screening_result_id,
                            tenant_id=tenant_id,
                            expected_generation=expected_analysis_generation,
                            narrative=narrative,
                            status=status,
                            error=error,
                            merge_analysis=merged_analysis,
                        )
                        if outcome.stale:
                            db.rollback()
                            return True

                        # Backfill denormalized columns if still NULL (edge case
                        # where early save happened without pipeline_result)
                        if result.deterministic_score is None:
                            base_for_cols = current_analysis if current_analysis.get("fit_score") else python_result
                            try:
                                result.deterministic_score = base_for_cols.get("deterministic_score") or base_for_cols.get("fit_score")
                                skill_a = base_for_cols.get("skill_analysis", {})
                                if isinstance(skill_a, dict):
                                    result.core_skill_score = skill_a.get("core_match_ratio")
                                cand_dom = base_for_cols.get("candidate_domain", {})
                                if isinstance(cand_dom, dict):
                                    result.domain_match_score = cand_dom.get("confidence")
                                elig = base_for_cols.get("eligibility", {})
                                if isinstance(elig, dict):
                                    result.eligibility_status = elig.get("eligible")
                                    result.eligibility_reason = elig.get("reason")
                            except Exception as col_err:
                                log.warning("Non-critical: Failed to backfill denormalized columns: %s", col_err)

                        # Cache candidate_profile_summary to Candidate.ai_professional_summary
                        summary = narrative.get("candidate_profile_summary")
                        if summary and result.candidate_id:
                            try:
                                candidate = db.query(Candidate).filter(
                                    Candidate.id == result.candidate_id
                                ).first()
                                if candidate:
                                    candidate.ai_professional_summary = summary
                            except Exception as cache_err:
                                log.warning(
                                    "Failed to cache ai_professional_summary for candidate_id=%s: %s",
                                    result.candidate_id,
                                    str(cache_err)[:200],
                                )

                        db.commit()
                        log.info(
                            "Wrote narrative_json (status=%s) to screening_result_id=%s",
                            status,
                            screening_result_id,
                        )
                        return True
                    else:
                        from app.backend.services.reliability.stale import discard_stale
                        current = db.query(ScreeningResult.analysis_generation).filter(
                            ScreeningResult.id == screening_result_id,
                            ScreeningResult.tenant_id == tenant_id,
                        ).scalar()
                        discard_stale(
                            job_type="llm_narrative",
                            job_id=f"narrative-{screening_result_id}-{expected_analysis_generation}",
                            target_id=screening_result_id,
                            expected_version=int(expected_analysis_generation or 0),
                            current_version=current,
                            tenant_id=tenant_id,
                        )
                        return True
                finally:
                    db.close()
            except Exception as db_err:
                if attempt == 0:
                    log.warning(
                        "DB write failed for screening_result_id=%s, retrying in 2s: %s",
                        screening_result_id,
                        str(db_err)[:200],
                    )
                    await asyncio.sleep(2)
                else:
                    log.error(
                        "Failed to write narrative to DB for screening_result_id=%s after retry: %s",
                        screening_result_id,
                        str(db_err)[:200],
                    )
        return False

    # Track status and error for final write
    narrative_status = "pending"
    narrative_error: Optional[str] = None
    llm_result: Optional[Dict[str, Any]] = None
    _start_time: Optional[float] = None

    log.info(
        "Background LLM task started for screening_result_id=%s (timeout=%ss)",
        screening_result_id,
        os.getenv("LLM_NARRATIVE_TIMEOUT", "500"),
    )

    try:
        _bg_timeout = float(os.getenv("LLM_NARRATIVE_TIMEOUT", "500"))
        sem = get_ollama_semaphore()
        if sem.locked():
            log.info(
                "Waiting for Ollama slot for screening_result_id=%s (another request in progress)...",
                screening_result_id,
            )
        async with sem:
            log.info("Acquired Ollama slot for screening_result_id=%s", screening_result_id)
            # Write 'processing' status before starting LLM call
            await _write_status("processing")

            _start_time = time.monotonic()
            llm_result = await asyncio.wait_for(explain_with_llm(llm_context), timeout=_bg_timeout)
            elapsed = time.monotonic() - _start_time
            LLM_CALL_DURATION.observe(elapsed)
            log.info(
                "LLM call completed for screening_result_id=%s in %.1fs (response keys: %s)",
                screening_result_id,
                elapsed,
                list(llm_result.keys()) if isinstance(llm_result, dict) else "N/A",
            )

        # Success path
        narrative_status = "ready"
        narrative_error = None
        log.info(
            "Background LLM narrative succeeded for screening_result_id=%s",
            screening_result_id,
        )
    except asyncio.CancelledError:
        log.info("Background LLM task cancelled for screening_result_id=%s", screening_result_id)
        # Write failed status to DB before returning
        await _write_status("failed", "Analysis was cancelled")
        return
    except asyncio.TimeoutError:
        elapsed = (time.monotonic() - _start_time) if _start_time is not None else _bg_timeout
        log.warning(
            "Background LLM narrative timed out for screening_result_id=%s after %.0fs — synthesizing from scores",
            screening_result_id,
            elapsed,
        )
        llm_result = _narrative_from_llm_context(llm_context, ai_enhanced=False)
        narrative_status = "ready"
        narrative_error = None
    except Exception as e:
        log.warning(
            "Background LLM narrative error for screening_result_id=%s: %s: %s — synthesizing from scores",
            screening_result_id,
            type(e).__name__,
            str(e)[:200],
        )
        llm_result = _narrative_from_llm_context(llm_context, ai_enhanced=False)
        narrative_status = "ready"
        narrative_error = None

    # Write final result to DB with retry
    if llm_result:
        write_ok = await _write_narrative_with_retry(llm_result, narrative_status, narrative_error)
        if not write_ok:
            log.error(
                "Critical: failed to persist narrative for screening_result_id=%s. "
                "Attempting status-only write.",
                screening_result_id,
            )
            await _write_status(narrative_status, narrative_error)
        else:
            from app.backend.services.background_enrichment import schedule_post_narrative_enrichment

            schedule_post_narrative_enrichment(
                screening_result_id,
                tenant_id,
                llm_context,
                python_result,
                expected_generation=expected_analysis_generation,
                narrative_status=narrative_status,
                narrative_payload=llm_result,
            )


def _persist_merged_jd_profile(db_session, job_description: str, jd_analysis: Dict[str, Any]) -> None:
    """Persist an LLM-merged JD profile back to the JdCache table.

    Non-critical: failures are logged and ignored so scoring never fails because
    of cache writes.
    """
    if db_session is None:
        return
    if jd_analysis.get("_profile_source") != "merged":
        return
    try:
        from app.backend.models.db_models import JdCache
        jd_hash = hashlib.md5(job_description.encode()).hexdigest()
        jd_analysis["_cache_version"] = jd_analysis.get("_cache_version", JD_CACHE_VERSION)
        db_session.merge(JdCache(hash=jd_hash, result_json=json.dumps(jd_analysis, default=_json_default)))
        db_session.commit()
    except Exception as e:
        log.warning("Non-critical: Failed to persist merged JD profile: %s", e)
        try:
            db_session.rollback()
        except Exception as rollback_err:
            log.warning("Non-critical: Rollback also failed: %s", rollback_err)


def _sync_jd_skills_to_role_template(db_session, job_description: str, jd_analysis: Dict[str, Any]) -> None:
    """Sync enriched JD required/nice-to-have skills back to RoleTemplate.

    This ensures the voice screening service uses the LLM-enriched skill list
    when generating screening questions, instead of stale or empty overrides.

    Non-critical: failures are logged and ignored.  Only updates templates
    where ``required_skills_override`` is empty (no manual user override).
    """
    if db_session is None:
        return
    if jd_analysis.get("_profile_source") not in ("llm", "merged"):
        return
    try:
        from app.backend.models.db_models import RoleTemplate
        enriched_required = jd_analysis.get("required_skills", [])
        enriched_nice = jd_analysis.get("nice_to_have_skills", [])
        if not enriched_required:
            return

        templates = db_session.query(RoleTemplate).filter(
            RoleTemplate.jd_text == job_description
        ).all()

        for template in templates:
            if not template.required_skills_override:
                template.required_skills_override = json.dumps(enriched_required, default=_json_default)
                template.nice_to_have_skills_override = json.dumps(enriched_nice, default=_json_default)
                log.info(
                    "Synced enriched JD skills to RoleTemplate %d: %d required, %d nice-to-have",
                    template.id, len(enriched_required), len(enriched_nice),
                )

        db_session.commit()
    except Exception as e:
        log.warning("Non-critical: Failed to sync JD skills to RoleTemplate: %s", e)
        try:
            db_session.rollback()
        except Exception as rollback_err:
            log.warning("Non-critical: Rollback also failed: %s", rollback_err)


async def run_hybrid_pipeline(
    resume_text: str,
    job_description: str,
    parsed_data: Dict[str, Any],
    gap_analysis: Dict[str, Any],
    scoring_weights: Optional[Dict] = None,
    jd_analysis: Optional[Dict] = None,
    screening_result_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
    expected_analysis_generation: Optional[int] = None,
    phase3_context: Optional[Dict] = None,
    db_session=None,
    industry: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Non-streaming version. Returns Python scoring results immediately.

    If screening_result_id and tenant_id are provided, spawns a background
    task to generate LLM narrative and write to DB. The immediate result
    includes narrative_pending=True and a fallback narrative.

    If screening_result_id is None, falls back to synchronous LLM call
    (for backward compatibility with batch and existing tests).

    When jd_analysis is not pre-computed, an LLM-driven domain-agnostic JD profile
    is extracted before the Python phase.  If db_session is provided, the merged
    JD profile is persisted back to the JdCache so later requests skip re-extraction.
    """
    # ── Scoring cache check (batch consistency) ──────────────────────────────
    try:
        from app.backend.services.scoring_cache_service import get_cached_result
        cached = get_cached_result(resume_text, job_description, scoring_weights, tenant_id)
        if cached is not None:
            log.info("Scoring cache hit — returning cached result (tenant=%s)", tenant_id)
            return cached
    except Exception as e:
        log.debug("Scoring cache check failed (non-fatal): %s", e)

    # ── Domain-agnostic JD profile extraction (LLM) ─────────────────────────
    llm_resume_skills: List[str] = []

    # Extract JD domain and target skills for guided resume extraction
    # Use incoming jd_analysis if available, otherwise do a quick parse
    jd_domain = None
    target_skills = []
    if ENABLE_TARGET_GUIDED_EXTRACTION:
        # If jd_analysis is provided with required_skills, use those directly
        if jd_analysis and jd_analysis.get("required_skills"):
            jd_domain = jd_analysis.get("domain", "").lower() or None
            target_skills = jd_analysis.get("required_skills", []) or []
        elif jd_analysis and jd_analysis.get("_profile_source") in ("llm", "merged"):
            jd_domain = jd_analysis.get("domain", "").lower() or None
            target_skills = jd_analysis.get("required_skills", []) or []
        else:
            # Quick parse to get domain and skills for guided extraction
            try:
                quick_jd = parse_jd_rules(job_description)
                jd_domain = quick_jd.get("domain", "").lower() or None
                target_skills = quick_jd.get("required_skills", []) or []
            except Exception:
                pass

    if jd_analysis is None or jd_analysis.get("_profile_source") not in ("llm", "merged"):
        try:
            from app.backend.services.jd_profile_service import extract_jd_profile, merge_jd_profile

            # Run JD profile extraction only (resume skills use Python-based extraction)
            llm_profile = await extract_jd_profile(job_description)
            log.info("LLM JD profile extracted for domain=%s", jd_domain or "none")

            rules_jd = jd_analysis or parse_jd_rules(job_description)
            jd_analysis = merge_jd_profile(rules_jd, llm_profile, jd_text=job_description)
            _persist_merged_jd_profile(db_session, job_description, jd_analysis)
            _sync_jd_skills_to_role_template(db_session, job_description, jd_analysis)
        except Exception as e:
            log.warning("LLM JD profile extraction failed, falling back to rules: %s", e)
            if jd_analysis is None:
                jd_analysis = parse_jd_rules(job_description)
    else:
        log.info("JD profile already cached/merged, skipping LLM extraction")

    python_result = _run_python_phase(
        resume_text, job_description, parsed_data, gap_analysis, scoring_weights, jd_analysis,
        phase3_context=phase3_context,
        llm_resume_skills=llm_resume_skills,
    )

    llm_context = {
        "jd_analysis":       python_result["jd_analysis"],
        "candidate_profile": python_result["candidate_profile"],
        "skill_analysis":    python_result["skill_analysis"],
        "scores": {
            **python_result["_scores"],
            "fit_score":            python_result["fit_score"],
            "final_recommendation": python_result["final_recommendation"],
        },
        # Enriched Python data from Task 17
        "score_rationales":  python_result.get("score_rationales", {}),
        "risk_summary":      python_result.get("risk_summary", {}),
        "skill_depth":       python_result.get("skill_depth", {}),
        # Enterprise enrichment
        "language_context":  python_result.get("language_context", {}),
        "fraud_check":       python_result.get("fraud_check", {}),
    }

    # If screening_result_id provided, spawn background task and return immediately
    if screening_result_id is not None and tenant_id is not None:
        if expected_analysis_generation is None:
            raise ValueError("expected_analysis_generation is required for background narrative")
        fallback = _build_fallback_narrative(python_result, python_result["skill_analysis"])
        python_result["narrative_pending"] = True
        
        # Spawn background LLM task
        task = asyncio.create_task(
            _background_llm_narrative(
                screening_result_id=screening_result_id,
                tenant_id=tenant_id,
                llm_context=llm_context,
                python_result=python_result,
                expected_analysis_generation=expected_analysis_generation,
            )
        )
        register_background_task(task)
        
        return _merge_immediate_pipeline_result(python_result, fallback)

    # Legacy synchronous mode (for batch processing and tests without DB persistence)
    _LLM_TIMEOUT = float(os.getenv("LLM_NARRATIVE_TIMEOUT", "500"))

    try:
        sem = get_ollama_semaphore()
        if sem.locked():
            log.info("Waiting for Ollama slot (another request in progress)...")
        async with sem:
            start = time.monotonic()
            llm_result = await asyncio.wait_for(explain_with_llm(llm_context), timeout=_LLM_TIMEOUT)
            LLM_CALL_DURATION.observe(time.monotonic() - start)
        log.info("LLM narrative succeeded for fit_score=%s", python_result.get("fit_score"))
    except asyncio.TimeoutError:
        log.warning(
            "LLM explain timed out after %.0fs — using fallback narrative. "
            "Model may still be loading. Consider increasing LLM_NARRATIVE_TIMEOUT env var.",
            _LLM_TIMEOUT,
        )
        LLM_FALLBACK_TOTAL.inc()
        llm_result = _build_fallback_narrative(python_result, python_result["skill_analysis"])
        python_result["narrative_pending"] = True
    except Exception as e:
        log.warning(
            "LLM explain failed (%s: %s) — using fallback narrative (OLLAMA_BASE_URL=%s OLLAMA_MODEL=%s)",
            type(e).__name__,
            str(e)[:200],
            os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            os.getenv("OLLAMA_MODEL") or os.getenv("OLLAMA_MODEL_BACKEND") or "(unset)",
        )
        LLM_FALLBACK_TOTAL.inc()
        llm_result = _build_fallback_narrative(python_result, python_result["skill_analysis"])
        python_result["narrative_pending"] = True

    final_result = _merge_immediate_pipeline_result(python_result, llm_result)

    # ── Cache the result for batch consistency ───────────────────────────────
    try:
        from app.backend.services.scoring_cache_service import cache_result
        cache_result(resume_text, job_description, final_result, scoring_weights, tenant_id)
    except Exception as e:
        log.debug("Scoring cache store failed (non-fatal): %s", e)

    return final_result


async def astream_hybrid_pipeline(
    resume_text: str,
    job_description: str,
    parsed_data: Dict[str, Any],
    gap_analysis: Dict[str, Any],
    scoring_weights: Optional[Dict] = None,
    jd_analysis: Optional[Dict] = None,
    screening_result_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
    expected_analysis_generation: Optional[int] = None,
    phase3_context: Optional[Dict] = None,
    db_session=None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    SSE streaming version.

    If screening_result_id and tenant_id are provided:
      - Yields Python results immediately with narrative_pending=True
      - Spawns background LLM task that writes to DB
      - Frontend polls GET /api/analysis/{id}/narrative for the LLM narrative

    If screening_result_id is None (legacy mode):
      - Yields:
        {"stage": "parsing",  "result": {all Python scores}}  — within 2s
        {"stage": "scoring",  "result": {LLM narrative}}       — after ~40s
        {"stage": "complete", "result": {full merged result}}

    When jd_analysis is not pre-computed, an LLM-driven domain-agnostic JD profile
    is extracted before the Python phase.
    """
    # ── Scoring cache check (batch consistency) ──────────────────────────────
    try:
        from app.backend.services.scoring_cache_service import get_cached_result
        cached = get_cached_result(resume_text, job_description, scoring_weights, tenant_id)
        if cached is not None:
            log.info("Scoring cache hit (stream) — returning cached result (tenant=%s)", tenant_id)
            yield {"stage": "complete", "result": cached}
            return
    except Exception as e:
        log.debug("Scoring cache check failed (non-fatal): %s", e)

    # ── Domain-agnostic JD profile extraction (LLM) ─────────────────────────
    llm_resume_skills: List[str] = []

    # Extract JD domain and target skills for guided resume extraction
    jd_domain = None
    target_skills = []
    if ENABLE_TARGET_GUIDED_EXTRACTION:
        # If jd_analysis is provided with required_skills, use those directly
        if jd_analysis and jd_analysis.get("required_skills"):
            jd_domain = jd_analysis.get("domain", "").lower() or None
            target_skills = jd_analysis.get("required_skills", []) or []
        elif jd_analysis and jd_analysis.get("_profile_source") in ("llm", "merged"):
            jd_domain = jd_analysis.get("domain", "").lower() or None
            target_skills = jd_analysis.get("required_skills", []) or []
        else:
            try:
                quick_jd = parse_jd_rules(job_description)
                jd_domain = quick_jd.get("domain", "").lower() or None
                target_skills = quick_jd.get("required_skills", []) or []
            except Exception:
                pass

    if jd_analysis is None or jd_analysis.get("_profile_source") not in ("llm", "merged"):
        try:
            from app.backend.services.jd_profile_service import extract_jd_profile, merge_jd_profile

            # Run JD profile extraction only (resume skills use Python-based extraction)
            llm_profile = await extract_jd_profile(job_description)
            log.info("LLM JD profile extracted (stream) for domain=%s", jd_domain or "none")

            rules_jd = jd_analysis or parse_jd_rules(job_description)
            jd_analysis = merge_jd_profile(rules_jd, llm_profile, jd_text=job_description)
            _persist_merged_jd_profile(db_session, job_description, jd_analysis)
            _sync_jd_skills_to_role_template(db_session, job_description, jd_analysis)
        except Exception as e:
            log.warning("LLM JD profile extraction failed, falling back to rules: %s", e)
            if jd_analysis is None:
                jd_analysis = parse_jd_rules(job_description)
    else:
        log.info("JD profile already cached/merged (stream), skipping LLM extraction")

    # Phase 1 — Python (instant)
    python_result = _run_python_phase(
        resume_text, job_description, parsed_data, gap_analysis, scoring_weights, jd_analysis,
        phase3_context=phase3_context,
        llm_resume_skills=llm_resume_skills,
    )

    llm_context = {
        "jd_analysis":       python_result["jd_analysis"],
        "candidate_profile": python_result["candidate_profile"],
        "skill_analysis":    python_result["skill_analysis"],
        "scores": {
            **python_result["_scores"],
            "fit_score":            python_result["fit_score"],
            "final_recommendation": python_result["final_recommendation"],
        },
        # Enriched Python data from Task 17
        "score_rationales":  python_result.get("score_rationales", {}),
        "risk_summary":      python_result.get("risk_summary", {}),
        "skill_depth":       python_result.get("skill_depth", {}),
        # Enterprise enrichment
        "language_context":  python_result.get("language_context", {}),
        "fraud_check":       python_result.get("fraud_check", {}),
    }

    # If screening_result_id provided, spawn background task and return immediately
    if screening_result_id is not None and tenant_id is not None:
        if expected_analysis_generation is None:
            raise ValueError("expected_analysis_generation is required for background narrative")
        fallback = _build_fallback_narrative(python_result, python_result["skill_analysis"])
        python_result["narrative_pending"] = True
        final = _merge_immediate_pipeline_result(python_result, fallback)
        
        # Strip internal keys for the SSE payload
        parsing_payload = {k: v for k, v in python_result.items()
                           if not k.startswith("_")}
        
        # Yield parsing stage with Python results
        yield {"stage": "parsing", "result": parsing_payload}
        
        # Spawn background LLM task
        task = asyncio.create_task(
            _background_llm_narrative(
                screening_result_id=screening_result_id,
                tenant_id=tenant_id,
                llm_context=llm_context,
                python_result=python_result,
                expected_analysis_generation=expected_analysis_generation,
            )
        )
        register_background_task(task)
        
        # Yield complete with fallback narrative and analysis_id for polling
        final["analysis_id"] = screening_result_id
        yield {"stage": "complete", "result": final}
        return

    # Legacy synchronous streaming mode (for backward compatibility)
    # Strip internal keys for the SSE payload
    parsing_payload = {k: v for k, v in python_result.items()
                       if not k.startswith("_")}
    yield {"stage": "parsing", "result": parsing_payload}

    # Phase 2 — LLM with heartbeat pings to keep Cloudflare/Nginx alive
    llm_queue: asyncio.Queue = asyncio.Queue()

    _LLM_TIMEOUT_STREAM = float(os.getenv("LLM_NARRATIVE_TIMEOUT", "500"))

    async def _llm_task():
        try:
            sem = get_ollama_semaphore()
            if sem.locked():
                log.info("Waiting for Ollama slot (another request in progress)...")
            async with sem:
                start = time.monotonic()
                result = await asyncio.wait_for(explain_with_llm(llm_context), timeout=_LLM_TIMEOUT_STREAM)
                LLM_CALL_DURATION.observe(time.monotonic() - start)
            log.info("LLM stream narrative succeeded")
            await llm_queue.put(("ok", result))
        except asyncio.TimeoutError:
            log.warning(
                "LLM stream timed out after %.0fs — using fallback. "
                "Increase LLM_NARRATIVE_TIMEOUT if model is still loading.",
                _LLM_TIMEOUT_STREAM,
            )
            LLM_FALLBACK_TOTAL.inc()
            fallback = _build_fallback_narrative(python_result, python_result["skill_analysis"])
            python_result["narrative_pending"] = True
            await llm_queue.put(("fallback", fallback))
        except Exception as e:
            log.warning(
                "LLM stream failed (%s: %s) — using fallback (OLLAMA_BASE_URL=%s OLLAMA_MODEL=%s)",
                type(e).__name__,
                str(e)[:200],
                os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
                os.getenv("OLLAMA_MODEL") or os.getenv("OLLAMA_MODEL_BACKEND") or "(unset)",
            )
            LLM_FALLBACK_TOTAL.inc()
            fallback = _build_fallback_narrative(python_result, python_result["skill_analysis"])
            python_result["narrative_pending"] = True
            await llm_queue.put(("fallback", fallback))

    task = asyncio.create_task(_llm_task())
    llm_result = None
    try:
        while True:
            try:
                status, llm_result = await asyncio.wait_for(llm_queue.get(), timeout=5.0)
                break
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # SSE comment — keeps connection alive during LLM wait
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    yield {"stage": "scoring", "result": llm_result or {}}

    narrative_payload = llm_result or _build_fallback_narrative(python_result, python_result["skill_analysis"])
    final = _merge_immediate_pipeline_result(python_result, narrative_payload)

    # ── Cache the result for batch consistency ───────────────────────────────
    try:
        from app.backend.services.scoring_cache_service import cache_result
        cache_result(resume_text, job_description, final, scoring_weights, tenant_id)
    except Exception as e:
        log.debug("Scoring cache store failed (non-fatal): %s", e)

    yield {"stage": "complete", "result": final}
