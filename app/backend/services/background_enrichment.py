"""
Background enrichment after narrative completes.

Phase 2a: Interview kit (screen questions JSON) — merged into report silently.
Phase 2b: Voice interview strategy — pre-built for Shortlist/Consider candidates.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from typing import Any, Dict, Optional

log = logging.getLogger("aria.enrichment")

DEFAULT_VOICE_STRATEGY_CONFIG = {"duration_minutes": 20, "question_count": 12}
INTERVIEW_KIT_TIMEOUT = float(os.getenv("LLM_INTERVIEW_KIT_TIMEOUT", "180"))
KIT_LLM_MAX_ATTEMPTS = max(1, int(os.getenv("LLM_INTERVIEW_KIT_RETRIES", "2")))
MIN_USABLE_KIT_QUESTIONS = max(1, int(os.getenv("INTERVIEW_KIT_MIN_QUESTIONS", "4")))
VOICE_STRATEGY_TIMEOUT = float(os.getenv("LLM_VOICE_STRATEGY_TIMEOUT", "180"))
ENRICHMENT_JOB_TYPES = {"llm_narrative", "interview_kit", "voice_strategy"}


def _prebuild_voice_strategy_enabled() -> bool:
    """Pre-build recruiter voice strategy during analysis (off by default to save LLM calls)."""
    return os.getenv("PREBUILD_VOICE_STRATEGY", "0").strip().lower() in ("1", "true", "yes")


def voice_strategy_config_hash(config: dict[str, Any] | None = None) -> str:
    cfg = config or DEFAULT_VOICE_STRATEGY_CONFIG
    normalized = {
        "duration_minutes": int(cfg.get("duration_minutes", 20)),
        "question_count": int(cfg.get("question_count", 12)),
    }
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()[:32]


def build_llm_prompt_context(context: Dict[str, Any]) -> Dict[str, Any]:
    """Shared prompt variables for narrative and interview-kit LLM calls."""
    from app.backend.services.hybrid_pipeline import _sanitize_input
    from app.backend.services.profile_text_sanitizer import (
        sanitize_jd_responsibilities,
        sanitize_profile_text,
        sanitize_skill_list,
    )

    jd = context.get("jd_analysis", {})
    profile = context.get("candidate_profile", {})
    scores = context.get("scores", {})
    skill_a = context.get("skill_analysis", {})
    score_rationales = context.get("score_rationales", {})
    risk_summary = context.get("risk_summary", {})

    career_snippet = _sanitize_input(
        sanitize_profile_text((profile.get("career_summary") or "")[:300]), 400, "career_snippet"
    )
    role_title = _sanitize_input(
        sanitize_profile_text(jd.get("role_title") or jd.get("title", "Unknown Role")), 200, "role_title"
    )
    candidate_name = _sanitize_input(sanitize_profile_text(profile.get("name") or "Unknown"), 100, "name")
    current_role = _sanitize_input(
        sanitize_profile_text(profile.get("current_role") or "N/A"), 100, "current_role"
    )
    current_company = _sanitize_input(
        sanitize_profile_text(profile.get("current_company") or "N/A"), 100, "current_company"
    )

    domain = jd.get("domain", "General")
    seniority = jd.get("seniority", "Not specified")
    years = profile.get("total_effective_years", 0)

    skill_score = scores.get("skill_score", 0)
    exp_score = scores.get("exp_score", 0)
    edu_score = scores.get("edu_score", 0)
    timeline_score = scores.get("timeline_score", 0)
    fit_score = scores.get("fit_score", 0)
    recommendation = scores.get("final_recommendation", "Pending")

    matched_must = sanitize_skill_list(skill_a.get("matched_required", []))
    missing_must = sanitize_skill_list(skill_a.get("missing_required", []))
    matched_nice = sanitize_skill_list(skill_a.get("matched_nice_to_have", []))
    missing_nice = sanitize_skill_list(skill_a.get("missing_nice_to_have", []))
    nice_match_pct = skill_a.get("nice_to_have_match_pct") or 0
    req_match_pct = skill_a.get("required_match_pct") or 0

    key_resp = sanitize_jd_responsibilities(jd.get("key_responsibilities", [])[:6])
    resp_text = "; ".join(key_resp) if key_resp else "Not specified"

    prof_analysis = skill_a.get("proficiency_analysis", {})
    prof_lines = [
        f"{sname}: req={pdata.get('required', '?')} cand={pdata.get('estimated_candidate', '?')}"
        for sname, pdata in list(prof_analysis.items())[:8]
    ]
    prof_text = "; ".join(prof_lines) if prof_lines else "Not assessed"

    education = profile.get("education", [])
    edu_parts = []
    for edu in (education if isinstance(education, list) else [])[:3]:
        deg = edu.get("degree", "") if isinstance(edu, dict) else ""
        fld = edu.get("field", "") if isinstance(edu, dict) else ""
        inst = edu.get("institution", "") if isinstance(edu, dict) else ""
        if deg or fld:
            part = f"{deg} {fld}".strip()
            if inst:
                part = f"{part} ({inst})"
            edu_parts.append(part)
        elif inst:
            edu_parts.append(inst)
    edu_text = "; ".join(edu_parts) if edu_parts else "Not available"

    seniority_alignment = risk_summary.get("seniority_alignment", "Not assessed")
    risk_flags_list = risk_summary.get("risk_flags", [])
    if risk_flags_list:
        risk_flags = "\n".join(
            f"  - {rf.get('flag', 'Unknown')}: {rf.get('detail', '')} (severity: {rf.get('severity', 'low')})"
            for rf in risk_flags_list[:6]
        )
    else:
        risk_flags = "  None identified"

    if score_rationales:
        rationales_parts = []
        for key in ["skill_rationale", "experience_rationale", "education_rationale", "timeline_rationale"]:
            val = score_rationales.get(key, "")
            if val:
                rationales_parts.append(f"{key.split('_')[0]}: {val}")
        score_rationales_summary = " | ".join(rationales_parts) if rationales_parts else "Not available"
    else:
        score_rationales_summary = "Not available"

    must_have_ctx_lines = []
    for sk in missing_must[:6]:
        best_resp = key_resp[0] if key_resp else "this role"
        prof_entry = prof_analysis.get(sk, {})
        cand_lvl = prof_entry.get("estimated_candidate", "not assessed") if prof_entry else "not assessed"
        must_have_ctx_lines.append(
            f"- {sk}: MISSING | Candidate level: {cand_lvl} | Needed for: \"{best_resp}\""
        )
    for sk in matched_must[:4]:
        prof_entry = prof_analysis.get(sk, {})
        req_lvl = prof_entry.get("required", "not specified") if prof_entry else "not specified"
        cand_lvl = prof_entry.get("estimated_candidate", "not assessed") if prof_entry else "not assessed"
        gap_note = " [PROFICIENCY GAP]" if prof_entry and prof_entry.get("match_factor", 1.0) < 1.0 else ""
        best_resp = key_resp[0] if key_resp else "this role"
        must_have_ctx_lines.append(
            f"- {sk}: Matched{gap_note} | Required: {req_lvl}, Candidate: {cand_lvl} | Needed for: \"{best_resp}\""
        )
    must_have_ctx = "\n".join(must_have_ctx_lines) if must_have_ctx_lines else "  No must-have skills data available"

    hm_topics = context.get("hm_screen_topics") or []
    hm_lines = []
    for t in hm_topics[:8]:
        if isinstance(t, dict):
            q = (t.get("question") or "").strip()
            cat = (t.get("category") or "hm_focus").strip()
            if q:
                hm_lines.append(f"- [{cat}] {q}")
        elif isinstance(t, str) and t.strip():
            hm_lines.append(f"- [hm_focus] {t.strip()}")
    hm_screen_ctx = "\n".join(hm_lines) if hm_lines else "  None — use calibrated must-haves and skill gaps"

    deal_breakers = context.get("deal_breakers") or []
    deal_text = "; ".join(str(d) for d in deal_breakers[:6]) if deal_breakers else "None specified"

    calibrated_must = context.get("calibrated_must_haves") or []
    cal_text = ", ".join(str(s) for s in calibrated_must[:12]) if calibrated_must else "See must-have skills above"

    hm_notes = (context.get("hm_notes") or "").strip() or "None"
    success_90d = (context.get("success_criteria_90d") or "").strip() or "None"

    lang_ctx = context.get("language_context", {})
    lang_instruction = lang_ctx.get("llm_instruction", "")
    lang_prefix = f"\n{lang_instruction}\n" if lang_instruction else ""

    from app.backend.services.interview_kit_context import (
        build_probe_areas_from_analysis,
        build_resume_anchors,
        format_resume_anchors_text,
    )
    probe_areas = context.get("probe_areas")
    if probe_areas is None:
        gap_analysis = context.get("gap_analysis") or {}
        probe_areas = build_probe_areas_from_analysis(
            matched=matched_must,
            missing=missing_must,
            gap_analysis=gap_analysis,
            risk_signals=risk_flags_list,
        )
    resume_anchors = context.get("resume_anchors") or build_resume_anchors(
        profile, context.get("parsed_data"), probe_areas
    )
    resume_anchors_text = format_resume_anchors_text(resume_anchors)
    probe_areas_text = "\n".join(
        f"  - [{p.get('priority', 'medium').upper()}] {p.get('category')}: {p.get('reasoning', '')}"
        for p in (probe_areas or [])[:8]
        if isinstance(p, dict)
    ) or "  None"

    return {
        "role_title": role_title,
        "domain": domain,
        "seniority": seniority,
        "candidate_name": candidate_name,
        "years": years,
        "current_role": current_role,
        "current_company": current_company,
        "career_snippet": career_snippet,
        "skill_score": skill_score,
        "exp_score": exp_score,
        "edu_score": edu_score,
        "timeline_score": timeline_score,
        "fit_score": fit_score,
        "recommendation": recommendation,
        "matched_must": matched_must,
        "missing_must": missing_must,
        "matched_nice": matched_nice,
        "missing_nice": missing_nice,
        "nice_match_pct": nice_match_pct,
        "req_match_pct": req_match_pct,
        "resp_text": resp_text,
        "prof_text": prof_text,
        "edu_text": edu_text,
        "seniority_alignment": seniority_alignment,
        "risk_flags": risk_flags,
        "score_rationales_summary": score_rationales_summary,
        "must_have_ctx": must_have_ctx,
        "hm_screen_ctx": hm_screen_ctx,
        "deal_breakers_text": deal_text,
        "calibrated_must_text": cal_text,
        "hm_notes": hm_notes,
        "success_criteria_90d": success_90d,
        "lang_prefix": lang_prefix,
        "resume_anchors_text": resume_anchors_text,
        "probe_areas_text": probe_areas_text,
        "probe_areas": probe_areas,
        "resume_anchors": resume_anchors,
    }


def _kit_context_block(ctx: Dict[str, Any]) -> str:
    """Shared screening context for all interview-kit prompt tiers."""
    return f"""ROLE: {ctx["role_title"]} | {ctx["domain"]} | {ctx["seniority"]}
CANDIDATE: {ctx["candidate_name"]} | {ctx["years"]}y experience | {ctx["current_role"]} at {ctx["current_company"]}
SCORES: skill={ctx["skill_score"]} exp={ctx["exp_score"]} fit={ctx["fit_score"]} /100
RECOMMENDATION: {ctx["recommendation"]}
MATCHED MUST-HAVE: {', '.join(ctx["matched_must"][:10]) if ctx["matched_must"] else 'None'}
MISSING MUST-HAVE: {', '.join(ctx["missing_must"][:6]) if ctx["missing_must"] else 'None'}
KEY RESPONSIBILITIES: {ctx["resp_text"]}
PROFICIENCY GAPS: {ctx["prof_text"]}
RISK FLAGS:
{ctx["risk_flags"]}

MUST-HAVE SKILLS CONTEXT:
{ctx["must_have_ctx"]}

HM SCREEN-FOCUS TOPICS (PRIMARY — build threads from these first):
{ctx["hm_screen_ctx"]}

CALIBRATED MUST-HAVES: {ctx["calibrated_must_text"]}
DEAL-BREAKERS TO VERIFY: {ctx["deal_breakers_text"]}
HM NOTES: {ctx["hm_notes"]}
90-DAY SUCCESS: {ctx["success_criteria_90d"]}

RESUME ANCHORS (reference in every ownership/risk step):
{ctx.get("resume_anchors_text", "")}

PROBE AREAS (prioritize in thread order):
{ctx.get("probe_areas_text", "")}"""


def _build_interview_kit_prompt_questions_first(ctx: Dict[str, Any]) -> str:
    """Tier 1 — questions-first, senior TA phone-screen voice. Metadata is minimal."""
    company = ctx["current_company"] or "their current company"
    return f"""IMPORTANT: Respond with ONLY valid JSON. No markdown.{ctx["lang_prefix"]}

You are a senior Talent Acquisition partner writing a LIVE 30-minute phone screen script.
Write like an experienced recruiter who has read the resume and JD — conversational, specific, never robotic.

{_kit_context_block(ctx)}

HARD REQUIREMENTS (response is INVALID if violated):
- Minimum {MIN_USABLE_KIT_QUESTIONS} spoken questions total across threads[].steps
- threads MUST NOT be empty; every thread MUST have at least 1 step with non-empty "text"
- Populate technical_questions, experience_deep_dive_questions, and behavioral_questions as flattened copies of steps
- Reference {company} and the candidate's role in ownership/risk questions

Return ONLY this JSON:
{{
  "interview_questions": {{
    "kit_version": 3,
    "screen_objective": "one sentence hiring goal for this screen",
    "threads": [
      {{
        "id": "thread_hm_focus",
        "title": "HM priorities",
        "kind": "technical",
        "steps": [
          {{
            "text": "15-35 word phone question",
            "spoken_text": "same as text",
            "intent": "what you are validating",
            "what_to_listen_for": ["specific signal"],
            "follow_ups": ["if vague"],
            "follow_up_intents": ["coaching bullet"]
          }}
        ]
      }},
      {{
        "id": "thread_ownership",
        "title": "Ownership at {company}",
        "kind": "ownership",
        "steps": [{{"text": "...", "spoken_text": "...", "intent": "...", "what_to_listen_for": ["..."], "follow_ups": ["..."], "follow_up_intents": ["..."]}}]
      }},
      {{
        "id": "thread_risk",
        "title": "Gap verification",
        "kind": "risk",
        "steps": [{{"text": "...", "spoken_text": "...", "intent": "...", "what_to_listen_for": ["..."], "follow_ups": ["..."], "follow_up_intents": ["..."]}}]
      }}
    ],
    "technical_questions": [],
    "behavioral_questions": [],
    "experience_deep_dive_questions": []
  }}
}}

SENIOR RECRUITER RULES:
- 3-4 threads, 8-12 total steps; HM screen-focus thread first when topics provided
- Thread 2: what THEY personally owned at {company} — if they say "we", follow up on their part
- Thread 3: top MISSING must-have — curious tone, not accusatory
- Max 1 STAR/behavioral question; domain-appropriate language (TA, engineering, finance, etc.)
- Varied phrasing; no repeated "Walk me through" stems
- FORBIDDEN: "This role needs", "The role calls for", "Describe a production scenario", "isn't on your resume"
- Each step: 15-35 words, under 200 characters, phone-ready"""


def _build_interview_kit_prompt_compact(ctx: Dict[str, Any]) -> str:
    """Tier 2 — tighter schema when tier 1 returns empty or invalid structure."""
    return f"""IMPORTANT: Return ONLY valid JSON.{ctx["lang_prefix"]}

You are a senior recruiter. The prior attempt returned no usable questions — try again.

{_kit_context_block(ctx)}

MANDATORY: At least 3 threads, at least {MIN_USABLE_KIT_QUESTIONS} steps total. threads[].steps CANNOT be [].

Schema:
{{
  "interview_questions": {{
    "kit_version": 3,
    "screen_objective": "one sentence",
    "threads": [
      {{"id": "thread_1", "title": "...", "kind": "ownership", "steps": [{{"text": "question?", "spoken_text": "question?", "intent": "...", "what_to_listen_for": ["..."], "follow_ups": ["..."]}}]}},
      {{"id": "thread_2", "title": "...", "kind": "risk", "steps": [{{"text": "...", "spoken_text": "...", "intent": "...", "what_to_listen_for": ["..."], "follow_ups": ["..."]}}]}},
      {{"id": "thread_3", "title": "...", "kind": "judgment", "steps": [{{"text": "...", "spoken_text": "...", "intent": "...", "what_to_listen_for": ["..."], "follow_ups": ["..."]}}]}}
    ],
    "technical_questions": [],
    "behavioral_questions": [],
    "experience_deep_dive_questions": []
  }}
}}

Keep each spoken line under 120 characters. Copy steps into legacy arrays."""


def _build_interview_kit_prompt(ctx: Dict[str, Any]) -> str:
    """Tier 3 — full playbook with briefing and HM debrief (last resort)."""
    return f"""IMPORTANT: Respond with ONLY a valid JSON object. No markdown, no code blocks.{ctx["lang_prefix"]}

You are a senior Talent Acquisition partner building a complete screen playbook.

{_kit_context_block(ctx)}

Generate a recruiter SCREEN PLAYBOOK. threads[].steps are MANDATORY — minimum {MIN_USABLE_KIT_QUESTIONS} steps total.

Return ONLY this JSON:
{{
  "interview_questions": {{
    "kit_version": 3,
    "screen_objective": "one sentence hiring goal",
    "candidate_briefing": {{
      "profile_snapshot": "2-3 sentence summary",
      "strengths_to_confirm": ["skill 1", "skill 2"],
      "areas_to_probe": ["gap with severity"],
      "context_notes": ["recruiter coaching notes"]
    }},
    "hypotheses": [
      {{"id": "H1", "label": "hiring hypothesis", "priority": "must_have|risk|nice_to_have|gate", "why": "reason"}}
    ],
    "open": {{
      "script": "",
      "listen_for": ["signal 1", "signal 2"],
      "recruiter_owned": true
    }},
    "threads": [
      {{
        "id": "thread_ownership",
        "title": "thread title",
        "kind": "ownership|risk|judgment",
        "hypothesis_ids": ["H1"],
        "time_minutes": 6,
        "priority": "must_have",
        "steps": [
          {{"text": "spoken question", "spoken_text": "spoken question", "intent": "...", "what_to_listen_for": ["signal"], "follow_ups": ["if vague ask"], "follow_up_intents": ["coaching"], "scoring_criteria": {{"strong": "...", "adequate": "...", "weak": "..."}}}}
        ]
      }}
    ],
    "close": {{
      "script": "motivation and next steps",
      "logistics": ["notice period", "travel"]
    }},
    "hm_debrief_template": {{
      "fit_summary_prompt": "prompt for HM summary",
      "must_haves": [{{"requirement": "skill", "status": "pending"}}],
      "hm_focus_if_proceed": ["technical focus 1"],
      "residual_risks": ["risk 1"]
    }},
    "technical_questions": [],
    "behavioral_questions": [],
    "culture_fit_questions": [],
    "experience_deep_dive_questions": []
  }}
}}

RULES:
- HM SCREEN-FOCUS topics are PRIMARY: dedicate the first thread to them when provided.
- 3-4 conversation THREADS. Each thread has 1-3 steps. 8-12 total steps REQUIRED.
- Also populate technical_questions / experience_deep_dive_questions / behavioral_questions as flattened copies.
- Sound like a senior recruiter on a live call — not a keyword checklist or HR bot.
- Reference candidate company/role from RESUME ANCHORS in ownership and risk steps.
- FORBIDDEN: "This role needs", "The role calls for", "Describe a production scenario".
- Skip culture_fit (empty array)."""


async def _invoke_llm_prompt(prompt: str, *, num_predict: int) -> str:
    from app.backend.services.hybrid_pipeline import _bind_num_predict, _get_llm
    from langchain_core.messages import HumanMessage

    llm = _get_llm()
    if llm is None:
        raise RuntimeError("LLM not available")

    bound = _bind_num_predict(llm, num_predict)

    response = await bound.ainvoke([HumanMessage(content=prompt)])
    raw = response.content if hasattr(response, "content") else str(response)
    return (raw or "").strip()


def _coerce_kit_step(item: Any) -> dict | None:
    """Normalize a single step/question item from LLM output."""
    if isinstance(item, dict):
        text = (
            item.get("spoken_text")
            or item.get("text")
            or item.get("question")
            or ""
        ).strip()
        if not text:
            return None
        step = dict(item)
        step["text"] = text
        step.setdefault("spoken_text", text)
        step.setdefault("what_to_listen_for", item.get("what_to_listen_for") or [])
        step.setdefault("follow_ups", item.get("follow_ups") or [])
        return step
    if isinstance(item, str) and item.strip():
        text = item.strip()
        return {"text": text, "spoken_text": text, "what_to_listen_for": [], "follow_ups": []}
    return None


def _coerce_kit_threads(raw_threads: Any) -> list:
    """Normalize thread list; map questions → steps and alternate keys."""
    if not isinstance(raw_threads, list):
        return []
    out: list = []
    for thread in raw_threads:
        if not isinstance(thread, dict):
            continue
        steps_raw = (
            thread.get("steps")
            or thread.get("questions")
            or thread.get("items")
            or []
        )
        steps = [s for s in (_coerce_kit_step(x) for x in steps_raw) if s]
        if not steps:
            continue
        coerced = dict(thread)
        coerced["steps"] = steps
        out.append(coerced)
    return out


def _threads_from_legacy_lists(iq: dict) -> list:
    """Build synthetic threads when LLM only populated legacy question arrays."""
    from app.backend.services.interview_kit_quality import get_spoken_line

    buckets = (
        ("technical_questions", "thread_technical", "technical"),
        ("experience_deep_dive_questions", "thread_experience", "ownership"),
        ("behavioral_questions", "thread_behavioral", "judgment"),
    )
    threads: list = []
    for key, thread_id, kind in buckets:
        items = iq.get(key)
        if not isinstance(items, list) or not items:
            continue
        steps = [s for s in (_coerce_kit_step(x) for x in items) if s]
        if not steps:
            continue
        threads.append({
            "id": thread_id,
            "title": key.replace("_", " ").title(),
            "kind": kind,
            "steps": steps,
        })
    return threads


def _log_interview_kit_rejection(parsed: dict, tier: int, raw_text: str) -> None:
    """Log structure diagnostics when parsed kit fails validation."""
    from app.backend.services.interview_kit_generator import count_kit_questions

    iq = parsed.get("interview_questions", parsed)
    if not isinstance(iq, dict):
        iq = {}
    threads = iq.get("threads") if isinstance(iq.get("threads"), list) else []
    step_counts = []
    for thread in threads:
        if isinstance(thread, dict):
            steps = thread.get("steps") or thread.get("questions") or []
            step_counts.append(len(steps) if isinstance(steps, list) else 0)
    normalized = _normalize_interview_kit(parsed)
    log.warning(
        "interview_kit tier %s rejected: keys=%s thread_count=%d step_counts=%s "
        "legacy_counts=(tech=%d exp=%d beh=%d) normalized_questions=%d raw_chars=%d",
        tier,
        sorted(iq.keys()) if isinstance(iq, dict) else [],
        len(threads),
        step_counts,
        len(normalized.get("technical_questions") or []),
        len(normalized.get("experience_deep_dive_questions") or []),
        len(normalized.get("behavioral_questions") or []),
        count_kit_questions(normalized),
        len(raw_text),
    )


def _normalize_interview_kit(data: dict) -> dict:
    def _ensure_question_list(v) -> list:
        if not isinstance(v, list):
            return []
        result = []
        for item in v:
            step = _coerce_kit_step(item)
            if step:
                result.append(step)
        return result

    iq = data.get("interview_questions", data)
    if isinstance(iq, str):
        log.warning("interview_kit interview_questions was a string — discarding")
        iq = {}
    if not isinstance(iq, dict):
        iq = {}

    threads = _coerce_kit_threads(
        iq.get("threads")
        or iq.get("conversation_threads")
        or iq.get("interview_threads")
    )

    normalized = {
        "kit_version": iq.get("kit_version", 3),
        "screen_objective": iq.get("screen_objective", ""),
        "candidate_briefing": iq.get("candidate_briefing") if isinstance(iq.get("candidate_briefing"), dict) else {},
        "hypotheses": iq.get("hypotheses") if isinstance(iq.get("hypotheses"), list) else [],
        "open": iq.get("open") if isinstance(iq.get("open"), dict) else {},
        "threads": threads,
        "close": iq.get("close") if isinstance(iq.get("close"), dict) else {},
        "hm_debrief_template": iq.get("hm_debrief_template") if isinstance(iq.get("hm_debrief_template"), dict) else {},
        "recruiter_signals": iq.get("recruiter_signals") if isinstance(iq.get("recruiter_signals"), dict) else {},
        "technical_questions": _ensure_question_list(iq.get("technical_questions", [])),
        "behavioral_questions": _ensure_question_list(iq.get("behavioral_questions", [])),
        "culture_fit_questions": _ensure_question_list(iq.get("culture_fit_questions", [])),
        "experience_deep_dive_questions": _ensure_question_list(iq.get("experience_deep_dive_questions", [])),
    }

    if not normalized["threads"]:
        normalized["threads"] = _threads_from_legacy_lists(normalized)

    # Flatten threads into legacy lists when LLM omitted them
    if normalized["threads"] and not normalized["technical_questions"] and not normalized["experience_deep_dive_questions"]:
        from app.backend.services.interview_kit_generator import _playbook_to_legacy
        legacy = _playbook_to_legacy(normalized["threads"])
        for key, items in legacy.items():
            if items and not normalized.get(key):
                normalized[key] = items

    return normalized


def _kit_meets_minimum(parsed: dict) -> bool:
    from app.backend.services.interview_kit_generator import count_kit_questions

    normalized = _normalize_interview_kit(parsed)
    return count_kit_questions(normalized) >= MIN_USABLE_KIT_QUESTIONS


async def generate_interview_kit_with_llm(context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate interview kit JSON only (separate from narrative). Retries on transient failures."""
    from app.backend.schemas.llm_structured import InterviewKitLLMResponse
    from app.backend.services.llm_json_service import invoke_llm_json_resilient

    ctx = build_llm_prompt_context(context)
    prompts = [
        _build_interview_kit_prompt_questions_first(ctx),
        _build_interview_kit_prompt_compact(ctx),
        _build_interview_kit_prompt(ctx),
    ]

    parsed = await invoke_llm_json_resilient(
        prompts,
        max_output_tokens=int(os.getenv("INTERVIEW_KIT_MAX_TOKENS", "4096")),
        log_label="interview_kit",
        output_type=InterviewKitLLMResponse,
        validate_parsed=_kit_meets_minimum,
        on_rejected=_log_interview_kit_rejection,
    )
    if parsed is not None:
        from app.backend.services.interview_kit_generator import count_kit_questions

        normalized = _normalize_interview_kit(parsed)
        normalized["kit_source"] = "llm"
        normalized["kit_version"] = normalized.get("kit_version") or 3
        log.info(
            "Interview kit LLM accepted with %d questions",
            count_kit_questions(normalized),
        )
        return normalized

    raise RuntimeError(
        f"Interview kit LLM returned no usable kit after retries "
        f"(minimum {MIN_USABLE_KIT_QUESTIONS} questions required)"
    )


def _update_screening_fields(
    screening_result_id: int,
    tenant_id: int,
    *,
    expected_generation: int,
    **fields: Any,
) -> bool:
    from app.backend.db.database import SessionLocal
    from app.backend.models.db_models import ScreeningResult

    try:
        db = SessionLocal()
        try:
            updated = (
                db.query(ScreeningResult)
                .filter(
                    ScreeningResult.id == screening_result_id,
                    ScreeningResult.tenant_id == tenant_id,
                    ScreeningResult.analysis_generation == expected_generation,
                )
                .update(fields, synchronize_session=False)
            )
            if updated != 1:
                db.rollback()
                return False
            db.commit()
            return True
        finally:
            db.close()
    except Exception as err:
        log.warning(
            "Failed to update screening_result_id=%s fields %s: %s",
            screening_result_id,
            list(fields.keys()),
            str(err)[:200],
        )
        return False


def _merge_interview_kit(
    screening_result_id: int,
    tenant_id: int,
    interview_questions: dict,
    kit_status: str,
    *,
    expected_generation: int,
    kit_error: Optional[str] = None,
) -> bool:
    from app.backend.db.database import SessionLocal
    from app.backend.models.db_models import ScreeningResult
    from app.backend.services.hybrid_pipeline import _merge_llm_into_result

    try:
        db = SessionLocal()
        try:
            result = db.query(ScreeningResult).filter(
                ScreeningResult.id == screening_result_id,
                ScreeningResult.tenant_id == tenant_id,
                ScreeningResult.analysis_generation == expected_generation,
            ).with_for_update().first()
            if not result:
                return False

            narrative = {}
            if result.narrative_json:
                try:
                    narrative = json.loads(result.narrative_json)
                except json.JSONDecodeError:
                    narrative = {}

            narrative["interview_questions"] = interview_questions
            result.narrative_json = json.dumps(narrative, default=str)
            result.interview_kit_status = kit_status
            result.interview_kit_error = kit_error if kit_status == "fallback" else None

            analysis = {}
            if result.analysis_result:
                try:
                    analysis = json.loads(result.analysis_result)
                except json.JSONDecodeError:
                    analysis = {}
            analysis["interview_questions"] = interview_questions
            merged = _merge_llm_into_result(analysis, narrative)
            result.analysis_result = json.dumps(merged, default=str)
            db.commit()
            log.info(
                "Interview kit merged (status=%s) for screening_result_id=%s",
                kit_status,
                screening_result_id,
            )
            return True
        finally:
            db.close()
    except Exception as err:
        log.warning(
            "Failed to merge interview kit for screening_result_id=%s: %s",
            screening_result_id,
            str(err)[:200],
        )
        return False


def _deterministic_interview_kit(python_result: Dict[str, Any]) -> Dict[str, Any]:
    """Python-generated kit when LLM path fails."""
    from app.backend.services.hybrid_pipeline import _placeholder_interview_kit

    kit = _placeholder_interview_kit(python_result)
    kit["kit_source"] = "deterministic"
    return kit


async def _finalize_interview_kit(
    interview_questions: dict,
    llm_context: dict,
    python_result: dict,
    tenant_id: int,
) -> tuple[dict, str, str | None]:
    """Personalize, lint, and normalize kit before persist."""
    from app.backend.db.database import SessionLocal
    from app.backend.services.interview_kit_quality import lint_interview_kit
    from app.backend.services.recruiter_voice_personalizer import personalize_kit
    from app.backend.services.interview_kit_generator import generate_targeted_interview_kit
    from app.backend.services.candidate_intelligence_service import (
        build_candidate_intelligence,
        merge_ci_into_kit_context,
    )
    from app.backend.services.interview_opening_service import apply_tenant_opening_to_kit

    ci = build_candidate_intelligence(
        analysis_result=python_result,
        parsed_data=python_result.get("parsed_data"),
        gap_analysis=python_result.get("gap_analysis"),
        fit_score=python_result.get("fit_score"),
        probe_areas=llm_context.get("probe_areas"),
    )
    ctx = merge_ci_into_kit_context({**llm_context, **python_result}, ci)

    if not interview_questions or not interview_questions.get("threads"):
        interview_questions = generate_targeted_interview_kit(
            profile=python_result.get("candidate_profile", {}),
            jd_analysis=python_result.get("jd_analysis", {}),
            skill_analysis=python_result.get("skill_analysis", {}),
            parsed_data=python_result.get("parsed_data"),
            kit_inputs=python_result.get("kit_inputs"),
            candidate_intelligence=ci,
        )

    kit_from_llm = interview_questions.get("kit_source") == "llm"
    if kit_from_llm:
        interview_questions = await personalize_kit(interview_questions, ctx)
    else:
        from app.backend.services.recruiter_voice_personalizer import _apply_minimal_personalization

        log.info("Skipping kit LLM personalizer — using deterministic kit with rule-based polish")
        interview_questions = _apply_minimal_personalization(interview_questions, ctx)
    db = SessionLocal()
    try:
        profile = python_result.get("candidate_profile") or {}
        role_title = (
            llm_context.get("role_title")
            or python_result.get("jd_analysis", {}).get("title")
            or profile.get("target_role")
        )
        interview_questions = apply_tenant_opening_to_kit(
            interview_questions,
            db,
            tenant_id,
            candidate_name=profile.get("name"),
            role_title=role_title,
        )
    finally:
        db.close()
    lint = lint_interview_kit(interview_questions)
    kit_error = None
    kit_status = "ready"

    if not lint["ok"]:
        log.warning("Kit lint failed after personalizer: %s", lint["issues"][:3])
        from app.backend.services.recruiter_voice_personalizer import _apply_minimal_personalization

        interview_questions = _deterministic_interview_kit(python_result)
        interview_questions = _apply_minimal_personalization(interview_questions, ctx)
        lint = lint_interview_kit(interview_questions)
        if not lint["ok"]:
            kit_status = "fallback"
            kit_error = "; ".join(i["message"] for i in lint["issues"][:3])

    interview_questions["kit_version"] = interview_questions.get("kit_version") or 3
    interview_questions["kit_status"] = kit_status
    interview_questions["resume_anchors"] = ci.get("resume_anchors")
    return interview_questions, kit_status, kit_error


async def background_interview_kit(
    screening_result_id: int,
    tenant_id: int,
    llm_context: Dict[str, Any],
    python_result: Dict[str, Any],
    *,
    expected_generation: int,
) -> None:
    """Background task: generate interview kit and merge into stored report."""
    from app.backend.db.database import SessionLocal
    from app.backend.services.interview_kit_generator import count_kit_questions
    from app.backend.services.interview_kit_context import load_kit_inputs_for_screening

    log.info("Interview kit background task started for screening_result_id=%s", screening_result_id)
    kit_start_delay = float(os.getenv("INTERVIEW_KIT_START_DELAY_S", "2.0"))
    if kit_start_delay > 0:
        await asyncio.sleep(kit_start_delay)
    _update_screening_fields(
        screening_result_id,
        tenant_id,
        expected_generation=expected_generation,
        interview_kit_status="processing",
        interview_kit_error=None,
    )

    db = SessionLocal()
    try:
        kit_inputs = load_kit_inputs_for_screening(db, screening_result_id, tenant_id)
    finally:
        db.close()

    if kit_inputs:
        llm_context = {**llm_context, **kit_inputs}
        python_result = {**python_result, "kit_inputs": kit_inputs}

    kit_status = "ready"
    kit_error: Optional[str] = None
    interview_questions = None

    try:
        kit = await asyncio.wait_for(
            generate_interview_kit_with_llm(llm_context),
            timeout=INTERVIEW_KIT_TIMEOUT,
        )
        if count_kit_questions(kit) <= 0:
            log.warning(
                "Interview kit LLM returned no questions for screening_result_id=%s — using deterministic kit",
                screening_result_id,
            )
            interview_questions = _deterministic_interview_kit(python_result)
        else:
            interview_questions = kit
            log.info("Interview kit LLM succeeded for screening_result_id=%s", screening_result_id)
    except asyncio.TimeoutError:
        log.warning(
            "Interview kit LLM timed out for screening_result_id=%s after %.0fs — using deterministic kit",
            screening_result_id,
            INTERVIEW_KIT_TIMEOUT,
        )
        interview_questions = _deterministic_interview_kit(python_result)
    except Exception as err:
        log.warning(
            "Interview kit LLM failed for screening_result_id=%s: %s: %s — using deterministic kit",
            screening_result_id,
            type(err).__name__,
            str(err)[:200],
        )
        interview_questions = _deterministic_interview_kit(python_result)

    if interview_questions:
        from app.backend.services.candidate_intelligence_service import build_candidate_intelligence

        ci = build_candidate_intelligence(
            analysis_result=python_result,
            parsed_data=python_result.get("parsed_data"),
            gap_analysis=python_result.get("gap_analysis"),
            fit_score=python_result.get("fit_score"),
            probe_areas=llm_context.get("probe_areas"),
        )
        _update_screening_fields(
            screening_result_id,
            tenant_id,
            expected_generation=expected_generation,
            candidate_intelligence_json=json.dumps(ci, default=str),
            candidate_intelligence_status="ready",
        )
        interview_questions, kit_status, kit_error = await _finalize_interview_kit(
            interview_questions, llm_context, python_result, tenant_id
        )
        _merge_interview_kit(
            screening_result_id,
            tenant_id,
            interview_questions,
            kit_status,
            expected_generation=expected_generation,
            kit_error=kit_error,
        )


async def background_voice_strategy(
    screening_result_id: int,
    tenant_id: int,
    *,
    expected_generation: int,
) -> None:
    """Pre-build voice interview strategy for candidates likely to be screened."""
    from app.backend.db.database import SessionLocal
    from app.backend.models.db_models import ScreeningResult
    from app.backend.services.recruiter.context_engine import InterviewContextEngine
    from app.backend.services.recruiter.strategy_agent import InterviewStrategyAgent

    log.info("Voice strategy pre-build started for screening_result_id=%s", screening_result_id)

    db = SessionLocal()
    try:
        row = db.query(ScreeningResult).filter(
            ScreeningResult.id == screening_result_id,
            ScreeningResult.tenant_id == tenant_id,
            ScreeningResult.analysis_generation == expected_generation,
        ).first()
        if not row or not row.candidate_id:
            log.info("Skipping voice strategy pre-build: missing candidate on screening_result_id=%s", screening_result_id)
            return
        jd_id = row.role_template_id
        if not jd_id and row.requisition_id:
            from app.backend.services.requisition_service import resolve_role_picker_id
            _, _, jd_id, _ = resolve_role_picker_id(db, tenant_id, row.requisition_id)
        if not jd_id:
            log.info("Skipping voice strategy pre-build: missing JD on screening_result_id=%s", screening_result_id)
            return

        row.voice_strategy_status = "processing"
        db.commit()

        context_engine = InterviewContextEngine()
        agent = InterviewStrategyAgent()
        context = context_engine.build_context(
            db,
            candidate_id=row.candidate_id,
            screening_result_id=screening_result_id,
            jd_id=jd_id,
        )
        config_hash = voice_strategy_config_hash(DEFAULT_VOICE_STRATEGY_CONFIG)

        try:
            strategy = await asyncio.wait_for(
                agent.generate_strategy(context, DEFAULT_VOICE_STRATEGY_CONFIG),
                timeout=VOICE_STRATEGY_TIMEOUT,
            )
            db.refresh(row, with_for_update=True)
            if row.analysis_generation != expected_generation:
                db.rollback()
                return
            row.voice_strategy_json = json.dumps(strategy, default=str)
            row.voice_strategy_status = "ready"
            row.voice_strategy_config_hash = config_hash
            db.commit()
            log.info("Voice strategy pre-built for screening_result_id=%s", screening_result_id)
        except Exception as err:
            log.warning(
                "Voice strategy pre-build failed for screening_result_id=%s: %s: %s",
                screening_result_id,
                type(err).__name__,
                str(err)[:200],
            )
            fallback = agent._build_fallback_strategy(context, DEFAULT_VOICE_STRATEGY_CONFIG)
            db.refresh(row, with_for_update=True)
            if row.analysis_generation != expected_generation:
                db.rollback()
                return
            row.voice_strategy_json = json.dumps(fallback, default=str)
            row.voice_strategy_status = "fallback"
            row.voice_strategy_config_hash = config_hash
            db.commit()
    finally:
        db.close()


def _enrichment_input_hash(
    job_type: str,
    tenant_id: int,
    screening_result_id: int,
    expected_generation: int,
) -> str:
    raw = f"enrichment:{job_type}:{tenant_id}:{screening_result_id}:{expected_generation}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def enqueue_enrichment_job(
    db,
    *,
    job_type: str,
    screening_result_id: int,
    tenant_id: int,
    expected_generation: int,
    llm_context: Optional[Dict[str, Any]] = None,
    python_result: Optional[Dict[str, Any]] = None,
    screening_decision_id: int | None = None,
    priority: int = 8,
) -> bool:
    """Create a durable enrichment job unless one already exists."""
    if job_type not in ENRICHMENT_JOB_TYPES:
        raise ValueError(f"Unsupported enrichment job type: {job_type}")
    from app.backend.models.db_models import AnalysisJob, ScreeningResult

    input_hash = _enrichment_input_hash(
        job_type,
        tenant_id,
        screening_result_id,
        expected_generation,
    )
    existing = (
        db.query(AnalysisJob)
        .filter(
            AnalysisJob.tenant_id == tenant_id,
            AnalysisJob.input_hash == input_hash,
            AnalysisJob.status.in_(("queued", "processing", "retrying", "completed")),
        )
        .first()
    )
    if existing is not None:
        return False

    row = (
        db.query(ScreeningResult)
        .filter(
            ScreeningResult.id == screening_result_id,
            ScreeningResult.tenant_id == tenant_id,
            ScreeningResult.analysis_generation == expected_generation,
        )
        .first()
    )
    digest = hashlib.sha256(input_hash.encode("utf-8")).hexdigest()
    job = AnalysisJob(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        candidate_id=getattr(row, "candidate_id", None),
        user_id=getattr(row, "user_id", None),
        job_type=job_type,
        resume_hash=digest,
        jd_hash=hashlib.sha256(f"{digest}:jd".encode("utf-8")).hexdigest(),
        input_hash=input_hash,
        status="queued",
        priority=priority,
        max_retries=2 if job_type != "llm_narrative" else 3,
        job_config={
            "screening_result_id": screening_result_id,
            "expected_generation": expected_generation,
            "llm_context": llm_context or {},
            "python_result": python_result or {},
            "screening_decision_id": screening_decision_id,
        },
    )
    db.add(job)
    db.commit()
    log.info(
        "Enqueued durable %s enrichment job id=%s screening_result_id=%s generation=%s",
        job_type,
        job.id,
        screening_result_id,
        expected_generation,
    )
    return True


async def complete_enrichment_job(job, db, *, expected_worker_id: str) -> bool:
    """Run one durable enrichment job under the queue worker lease."""
    from datetime import datetime, timezone

    from app.backend.models.db_models import AnalysisJob

    locked = (
        db.query(AnalysisJob)
        .filter(AnalysisJob.id == job.id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    lease = locked.leased_until
    if lease is not None and lease.tzinfo is None:
        lease = lease.replace(tzinfo=timezone.utc)
    if (
        locked.status != "processing"
        or locked.worker_id != expected_worker_id
        or (lease is not None and lease < datetime.now(timezone.utc))
    ):
        db.rollback()
        log.warning("lease_lost before enrichment job commit job_id=%s", job.id)
        return False

    cfg = locked.job_config or {}
    screening_result_id = int(cfg["screening_result_id"])
    expected_generation = int(cfg["expected_generation"])
    tenant_id = locked.tenant_id
    locked.processing_stage = "enriching"
    locked.progress_percent = 40
    db.commit()

    if locked.job_type == "llm_narrative":
        from app.backend.services.hybrid_pipeline import _background_llm_narrative

        await _background_llm_narrative(
            screening_result_id=screening_result_id,
            tenant_id=tenant_id,
            llm_context=cfg.get("llm_context") or {},
            python_result=cfg.get("python_result") or {},
            expected_analysis_generation=expected_generation,
            screening_decision_id=cfg.get("screening_decision_id"),
        )
    elif locked.job_type == "interview_kit":
        await background_interview_kit(
            screening_result_id,
            tenant_id,
            cfg.get("llm_context") or {},
            cfg.get("python_result") or {},
            expected_generation=expected_generation,
        )
    elif locked.job_type == "voice_strategy":
        await background_voice_strategy(
            screening_result_id,
            tenant_id,
            expected_generation=expected_generation,
        )
    else:
        raise ValueError(f"Unsupported enrichment job type: {locked.job_type}")

    locked = (
        db.query(AnalysisJob)
        .filter(AnalysisJob.id == job.id)
        .with_for_update()
        .populate_existing()
        .one()
    )
    if locked.status != "processing" or locked.worker_id != expected_worker_id:
        db.rollback()
        log.warning("lease_lost after enrichment job body job_id=%s", job.id)
        return False
    locked.status = "completed"
    locked.completed_at = datetime.now(timezone.utc)
    locked.progress_percent = 100
    locked.processing_stage = "complete"
    db.commit()
    return True


def schedule_post_narrative_enrichment(
    screening_result_id: int,
    tenant_id: int,
    llm_context: Dict[str, Any],
    python_result: Dict[str, Any],
    *,
    expected_generation: int,
    narrative_status: str,
    narrative_payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Chain background interview kit + voice strategy after narrative is persisted.

    Interview kit always runs as a separate LLM task — never copied from narrative fallback.
    """
    recommendation = (
        python_result.get("final_recommendation")
        or llm_context.get("scores", {}).get("final_recommendation")
        or ""
    )

    if not _update_screening_fields(
        screening_result_id,
        tenant_id,
        expected_generation=expected_generation,
        interview_kit_status="pending",
    ):
        return
    from app.backend.db.database import SessionLocal

    enqueue_db = SessionLocal()
    try:
        enqueue_enrichment_job(
            enqueue_db,
            job_type="interview_kit",
            screening_result_id=screening_result_id,
            tenant_id=tenant_id,
            expected_generation=expected_generation,
            llm_context=llm_context,
            python_result=python_result,
        )
    finally:
        enqueue_db.close()
    log.info(
        "Enqueued independent interview kit job for screening_result_id=%s (narrative_status=%s)",
        screening_result_id,
        narrative_status,
    )

    if recommendation in ("Shortlist", "Consider") and _prebuild_voice_strategy_enabled():
        _update_screening_fields(
            screening_result_id,
            tenant_id,
            expected_generation=expected_generation,
            voice_strategy_status="pending",
        )
        voice_db = SessionLocal()
        try:
            enqueue_enrichment_job(
                voice_db,
                job_type="voice_strategy",
                screening_result_id=screening_result_id,
                tenant_id=tenant_id,
                expected_generation=expected_generation,
            )
        finally:
            voice_db.close()
    else:
        _update_screening_fields(
            screening_result_id,
            tenant_id,
            expected_generation=expected_generation,
            voice_strategy_status="skipped",
        )


def load_cached_voice_strategy(
    db,
    screening_result_id: int | None,
    tenant_id: int,
    strategy_config: dict[str, Any],
) -> dict[str, Any] | None:
    """Return pre-built voice strategy if config hash matches."""
    if not screening_result_id:
        return None

    from app.backend.models.db_models import ScreeningResult

    row = db.query(ScreeningResult).filter(
        ScreeningResult.id == screening_result_id,
        ScreeningResult.tenant_id == tenant_id,
    ).first()
    if not row or row.voice_strategy_status not in ("ready", "fallback"):
        return None
    if not row.voice_strategy_json:
        return None

    expected_hash = voice_strategy_config_hash(strategy_config)
    if row.voice_strategy_config_hash and row.voice_strategy_config_hash != expected_hash:
        return None

    try:
        return json.loads(row.voice_strategy_json)
    except json.JSONDecodeError:
        return None
