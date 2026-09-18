"""
Voice Screening Service — Core orchestration layer.

Module-level functions (follows transcript_service.py pattern):
  - generate_screening_questions() — LLM generates Qs from JD skills
  - generate_post_call_assessment() — LLM generates structured assessment from transcript
  - evaluate_answer_quality() — LLM evaluates candidate answer in real-time
  - build_conversation_context() — Assembles tenant config + JD + candidate into context
  - process_completed_call() — Post-call pipeline: transcript → assessment → status update
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backend.models.db_models import (
    VoiceTenantConfig, VoiceScreeningSession, VoiceTranscriptEntry,
    Candidate, RoleTemplate,
)

logger = logging.getLogger(__name__)


# ─── Dynamic Question Generation ──────────────────────────────────────────────

async def generate_screening_questions(jd_text: str, must_have_skills: list, jd_title: str = "") -> list:
    """
    Generate 3-5 screening questions from JD must-have skills.

    Returns a list of question strings, one per key skill.
    """
    if not must_have_skills:
        return ["Tell me about your most challenging project in the last year."]

    skills_str = ", ".join(must_have_skills[:10])
    system_prompt = (
        "You are an expert technical recruiter conducting a phone screening. "
        "Generate concise, open-ended screening questions that probe a candidate's "
        "depth of knowledge. Each question should target a specific skill. "
        "Return ONLY a JSON array of strings, no other text.\n\n"
        "Rules:\n"
        "- 3-5 questions total\n"
        "- Each question should be specific to a skill, not generic\n"
        "- Use 'Tell me about...', 'Describe how...', 'Walk me through...' style\n"
        "- Avoid yes/no questions\n"
    )

    user_msg = (
        f"Role: {jd_title or 'the position'}\n"
        f"Must-have skills: {skills_str}\n"
        f"Job description excerpt: {jd_text[:1000]}\n\n"
        f"Generate screening questions."
    )

    try:
        from app.backend.services.app_llm_client import generate_app_llm, parse_json_from_llm
        import json as _json

        response_text = await generate_app_llm(
            f"{system_prompt}\n\n{user_msg}",
            max_output_tokens=512,
            temperature=0.7,
            timeout=120.0,
            json_mode=True,
            log_label="screening_questions",
        )
        if response_text:
            try:
                questions = _json.loads(response_text)
            except _json.JSONDecodeError:
                questions = parse_json_from_llm(response_text)
            if isinstance(questions, list) and all(isinstance(q, str) for q in questions):
                return questions[:5]
            if isinstance(questions, dict):
                for key in ("questions", "screening_questions"):
                    items = questions.get(key)
                    if isinstance(items, list) and all(isinstance(q, str) for q in items):
                        return items[:5]
            lines = [
                line.strip().lstrip("0123456789.-) ")
                for line in response_text.split("\n")
                if line.strip() and "?" in line
            ]
            if lines:
                return lines[:5]
    except Exception as e:
        logger.error("Question generation failed: %s", e, exc_info=True)

    return [f"Tell me about your experience with {s}." for s in must_have_skills[:3]]


# ─── Answer Quality Evaluation ────────────────────────────────────────────────

async def evaluate_answer_quality(question: str, answer: str, skill: str = "") -> dict:
    """
    Evaluate a candidate's answer quality in real-time.

    Returns:
        {
            "quality": "strong" | "adequate" | "weak",
            "score": 1-5,
            "should_follow_up": bool,
            "follow_up_prompt": str or None,
        }
    """
    system_prompt = (
        "You are a recruiter evaluating a candidate's phone screening answer. "
        "Assess the answer quality and decide if a follow-up is needed. "
        "Return ONLY a JSON object with these keys:\n"
        '{"quality": "strong|adequate|weak", "score": 1-5, '
        '"should_follow_up": true/false, "follow_up_prompt": "..." or null}'
    )

    user_msg = (
        f"Skill being assessed: {skill or 'general'}\n"
        f"Question: {question}\n"
        f"Candidate's answer: {answer}\n\n"
        f"Evaluate the answer quality."
    )

    try:
        from app.backend.services.app_llm_client import generate_app_json

        result = await generate_app_json(
            f"{system_prompt}\n\n{user_msg}",
            max_output_tokens=256,
            temperature=0.3,
            timeout=60.0,
            log_label="answer_quality",
        )
        if result:
            return {
                "quality": result.get("quality", "adequate"),
                "score": min(5, max(1, int(result.get("score", 3)))),
                "should_follow_up": result.get("should_follow_up", False),
                "follow_up_prompt": result.get("follow_up_prompt"),
            }
    except Exception as e:
        logger.error("Answer evaluation failed: %s", e, exc_info=True)

    return {"quality": "adequate", "score": 3, "should_follow_up": False, "follow_up_prompt": None}


# ─── Post-Call Assessment ─────────────────────────────────────────────────────

async def generate_post_call_assessment(
    transcript: list,
    jd_text: str,
    candidate_name: str,
    jd_title: str = "",
    must_have_skills: list = None,
    nice_to_have_skills: list = None,
    detail_level: str = "full",
    kit_qa: list | None = None,
) -> dict:
    """
    Generate a structured post-call assessment from the full transcript.

    Returns:
        {
            "overall_recommendation": "strong_yes|yes|maybe|no|strong_no",
            "overall_score": 1-100,
            "summary": "...",
            "skill_assessments": [{"skill": "...", "rating": 1-5, "evidence": "..."}],
            "communication_score": 1-5,
            "risk_flags": ["..."],
            "per_question_assessment": [{"question": "...", "answer_summary": "...", "rating": 1-5, "evidence": "..."}],
        }
    """
    # Format transcript for LLM
    transcript_text = "\n".join(
        f"{'Recruiter' if e.get('speaker') == 'bot' else 'Candidate'}: {e.get('text', '')}"
        for e in transcript
    )

    skills_str = ", ".join(must_have_skills[:10]) if must_have_skills else "general skills"
    nice_skills_str = ", ".join(nice_to_have_skills[:10]) if nice_to_have_skills else ""

    rubric_block = ""
    if kit_qa:
        rubric_lines = []
        for item in kit_qa[:12]:
            if not isinstance(item, dict):
                continue
            rubric_lines.append(f"Q: {item.get('question', '')}")
            rubric_lines.append(f"A: {(item.get('answer') or '')[:500]}")
            criteria = item.get("scoring_criteria") or {}
            if criteria:
                rubric_lines.append(
                    "Rubric: "
                    + "; ".join(f"{k}={v}" for k, v in criteria.items() if v)
                )
            listen = item.get("what_to_listen_for") or []
            if listen:
                rubric_lines.append("Listen for: " + "; ".join(listen[:4]))
            rubric_lines.append("")
        if rubric_lines:
            rubric_block = (
                "\n--- INTERVIEW KIT RUBRIC (score each Q&A as strong|adequate|weak) ---\n"
                + "\n".join(rubric_lines)
                + "--- END RUBRIC ---\n"
            )

    detail_instruction = (
        "Provide a FULL assessment with per-question breakdowns, skill-by-skill ratings, "
        "and detailed evidence quotes."
        if detail_level == "full"
        else "Provide a BRIEF assessment with just the overall recommendation, summary, and top 3 strengths/concerns."
    )

    system_prompt = (
        "You are a senior recruiter evaluating a phone screening call. "
        "Generate a structured assessment based on the conversation transcript. "
        "Return ONLY a JSON object with these keys:\n"
        "{\n"
        '  "overall_recommendation": "strong_yes|yes|maybe|no|strong_no",\n'
        '  "overall_score": 0-100,\n'
        '  "summary": "2-3 sentence summary",\n'
        '  "skill_assessments": [{"skill": "...", "rating": 1-5, "evidence": "quote from transcript"}],\n'
        '  "communication_score": 1-5,\n'
        '  "risk_flags": ["concern1", "concern2"],\n'
        '  "per_question_assessment": [{"question": "...", "answer_summary": "...", "rating": 1-5, "evidence": "...", "rubric_rating": "strong|adequate|weak"}]\n'
        "}\n\n"
        f"{detail_instruction}\n"
        "When a rubric is provided, map each answer to strong, adequate, or weak using scoring_criteria.\n"
        "Be objective and evidence-based. Quote specific parts of the transcript."
    )

    user_msg = (
        f"Candidate: {candidate_name}\n"
        f"Role: {jd_title or 'the position'}\n"
        f"Must-have skills: {skills_str}\n"
        + (f"Nice-to-have skills: {nice_skills_str}\n" if nice_skills_str else "")
        + f"Job description: {jd_text[:1500]}\n\n"
        + rubric_block
        + f"--- TRANSCRIPT ---\n{transcript_text[:5000]}\n--- END TRANSCRIPT ---"
    )

    try:
        from app.backend.services.app_llm_client import generate_app_llm, parse_json_from_llm

        response_text = await generate_app_llm(
            user_msg,
            system=system_prompt,
            max_output_tokens=2048,
            temperature=0.2,
            timeout=180.0,
            json_mode=True,
            log_label="post_call_assessment",
        )
        if not response_text:
            return _fallback_assessment(candidate_name)

        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            result = parse_json_from_llm(response_text) or _fallback_assessment(candidate_name)

        # Validate and normalize
        valid_recommendations = {"strong_yes", "yes", "maybe", "no", "strong_no"}
        if result.get("overall_recommendation") not in valid_recommendations:
            result["overall_recommendation"] = "maybe"

        result["overall_score"] = min(100, max(0, int(result.get("overall_score", 50))))
        result.setdefault("summary", "")
        result.setdefault("skill_assessments", [])
        result.setdefault("communication_score", 3)
        result.setdefault("risk_flags", [])
        result.setdefault("per_question_assessment", [])

        return result

    except Exception as e:
        logger.error("Post-call assessment generation failed: %s", e, exc_info=True)
        return _fallback_assessment(candidate_name)


def _fallback_assessment(candidate_name: str) -> dict:
    """Fallback assessment when LLM fails."""
    return {
        "overall_recommendation": "maybe",
        "overall_score": 50,
        "summary": f"Assessment generation failed. Manual review recommended for {candidate_name}.",
        "skill_assessments": [],
        "communication_score": 3,
        "risk_flags": ["Automated assessment unavailable — manual review required"],
        "per_question_assessment": [],
    }


# ─── Conversation Context Builder ─────────────────────────────────────────────

def build_conversation_context(db: Session, session_id: int) -> dict:
    """
    Assemble all context needed for a screening call from the DB.

    Returns dict with: tenant_config, candidate, jd, screening_questions
    """
    voice_session = db.execute(
        select(VoiceScreeningSession).where(VoiceScreeningSession.id == session_id)
    ).scalar_one_or_none()

    if voice_session is None:
        return {}

    # Tenant config
    config = db.execute(
        select(VoiceTenantConfig).where(VoiceTenantConfig.tenant_id == voice_session.tenant_id)
    ).scalar_one_or_none()

    # Candidate
    candidate = db.execute(
        select(Candidate).where(Candidate.id == voice_session.candidate_id)
    ).scalar_one_or_none()

    # JD (optional)
    jd = None
    if voice_session.jd_id:
        jd = db.execute(
            select(RoleTemplate).where(RoleTemplate.id == voice_session.jd_id)
        ).scalar_one_or_none()

    # Extract must-have and nice-to-have skills from JD
    must_have_skills = []
    nice_to_have_skills = []
    jd_text = ""
    jd_title = ""
    if jd:
        jd_text = jd.jd_text or ""
        jd_title = jd.name or ""
        for raw_override, target in (
            (jd.required_skills_override, must_have_skills),
            (jd.nice_to_have_skills_override, nice_to_have_skills),
        ):
            if not raw_override:
                continue
            try:
                skills_data = json.loads(raw_override)
                if isinstance(skills_data, list):
                    for s in skills_data:
                        if isinstance(s, str):
                            target.append(s)
                        elif isinstance(s, dict) and "skill" in s:
                            target.append(s["skill"])
            except (json.JSONDecodeError, TypeError):
                pass

    return {
        "session": voice_session,
        "tenant_config": config,
        "candidate": candidate,
        "jd": jd,
        "jd_text": jd_text,
        "jd_title": jd_title,
        "must_have_skills": must_have_skills,
        "nice_to_have_skills": nice_to_have_skills,
        "candidate_name": candidate.name if candidate else "Candidate",
        "phone_number": voice_session.phone_number,
        "bot_name": config.bot_name if config else "ARIA Assistant",
        "greeting_style": config.greeting_style if config else "professional",
        "call_duration_max": config.call_duration_max if config else 420,
        "consent_script": config.consent_script if config else None,
    }


# ─── Post-Call Pipeline ───────────────────────────────────────────────────────

async def process_completed_call(
    db: Session,
    session_id: int,
    *,
    expected_generation: int,
) -> bool:
    """
    Full post-call pipeline:
    1. Load transcript from DB
    2. Generate structured assessment
    3. Store assessment in session
    4. Update candidate status if auto_update_status is enabled
    """
    ctx = build_conversation_context(db, session_id)
    if not ctx:
        logger.error("Cannot process call — session %d not found", session_id)
        return

    voice_session = ctx["session"]
    if voice_session.result_generation != expected_generation:
        return False
    candidate = ctx["candidate"]
    config = ctx["tenant_config"]

    # Load transcript entries
    entries = db.execute(
        select(VoiceTranscriptEntry)
        .where(VoiceTranscriptEntry.session_id == session_id)
        .order_by(VoiceTranscriptEntry.timestamp.asc())
    ).scalars().all()

    transcript = [{"speaker": e.speaker, "text": e.text} for e in entries]

    kit_qa = []
    if voice_session.transcript_json:
        try:
            kit_meta = json.loads(voice_session.transcript_json)
            kit_qa = kit_meta.get("questions_responses") or []
        except json.JSONDecodeError:
            kit_qa = []

    if not transcript and not kit_qa:
        logger.warning("Session %d has no transcript or kit Q&A — skipping assessment", session_id)
        voice_session.status = "completed"
        voice_session.ended_at = voice_session.ended_at or datetime.now(timezone.utc)
        db.commit()
        return True

    # Generate assessment
    assessment = await generate_post_call_assessment(
        transcript=transcript,
        jd_text=ctx["jd_text"],
        candidate_name=ctx["candidate_name"],
        jd_title=ctx["jd_title"],
        must_have_skills=ctx["must_have_skills"],
        nice_to_have_skills=ctx.get("nice_to_have_skills", []),
        detail_level=config.assessment_detail_level if config else "full",
        kit_qa=kit_qa,
    )

    # Revalidate the immutable callback generation after the provider call.
    db.refresh(voice_session, with_for_update=True)
    if voice_session.result_generation != expected_generation:
        from app.backend.services.reliability.stale import discard_stale

        discard_stale(
            job_type="voice_assessment",
            job_id=f"voice-assessment-{session_id}-{expected_generation}",
            target_id=session_id,
            expected_version=expected_generation,
            current_version=voice_session.result_generation,
            tenant_id=voice_session.tenant_id,
        )
        db.rollback()
        return False

    # Store assessment
    voice_session.assessment_json = json.dumps(assessment, default=str)
    voice_session.status = "completed"
    voice_session.ended_at = datetime.now(timezone.utc)

    if entries:
        voice_session.duration_seconds = int(
            (entries[-1].timestamp - entries[0].timestamp).total_seconds()
        ) if len(entries) > 1 else 0

    # Persist consolidated outcome when screening result is linked
    try:
        from app.backend.models.db_models import ScreeningResult
        from app.backend.services.consolidated_recommendation import (
            compute_consolidated_for_result,
            persist_outcome_to_screening_result,
        )

        sr = None
        if voice_session.candidate_id and voice_session.jd_id:
            sr = db.execute(
                select(ScreeningResult)
                .where(
                    ScreeningResult.tenant_id == voice_session.tenant_id,
                    ScreeningResult.candidate_id == voice_session.candidate_id,
                    ScreeningResult.role_template_id == voice_session.jd_id,
                    ScreeningResult.is_active == True,
                )
                .order_by(ScreeningResult.timestamp.desc())
            ).scalar_one_or_none()
        if sr:
            try:
                analysis = json.loads(sr.analysis_result or "{}")
            except json.JSONDecodeError:
                analysis = {}
            outcome = compute_consolidated_for_result(
                db,
                sr,
                analysis_score=analysis.get("fit_score") or sr.deterministic_score,
                call_score=assessment.get("overall_score"),
                call_source="ai",
                call_recommendation=assessment.get("overall_recommendation"),
                evidence=[assessment.get("summary", "")],
            )
            persist_outcome_to_screening_result(sr, outcome, call_source="ai")
    except Exception as e:
        logger.warning("Consolidated outcome save failed for session %d: %s", session_id, e)

    db.commit()
    logger.info(
        "Post-call assessment complete for session %d: recommendation=%s score=%d",
        session_id,
        assessment.get("overall_recommendation"),
        assessment.get("overall_score", 0),
    )
    return True

    # Send notification to recruiter
    _notify_call_completed(db, voice_session, candidate, config, assessment)


def _notify_call_completed(db, voice_session, candidate, config, assessment):
    """Send in-app notification + email to recruiter after a completed call."""
    try:
        from app.backend.services.notification_service import create_admin_notification
        from app.backend.models.db_models import User

        score = assessment.get("overall_score", "N/A")
        recommendation = assessment.get("overall_recommendation", "N/A")

        # In-app notification for all tenant users
        create_admin_notification(
            db=db,
            type="voice_screening_completed",
            severity="info",
            title=f"Voice Screening Complete: {candidate.name if candidate else 'Candidate'}",
            message=(
                f"Screening call for {candidate.name if candidate else 'Candidate'} "
                f"(phone: {voice_session.phone_number}) completed.\n"
                f"Score: {score}/10 | Recommendation: {recommendation}\n"
                f"Duration: {voice_session.duration_seconds or 0}s"
            ),
            tenant_id=voice_session.tenant_id,
        )

        # Email notification to tenant users
        try:
            from app.backend.services.email_service import (
                email_service,
                get_tenant_email_service,
            )
            users = db.execute(
                select(User).where(User.tenant_id == voice_session.tenant_id)
            ).scalars().all()

            tenant_svc = get_tenant_email_service(db, voice_session.tenant_id)
            svc = tenant_svc if tenant_svc else email_service

            if svc and svc.is_configured:
                subject = f"Voice Screening Complete — {candidate.name if candidate else 'Candidate'}"
                body_html = (
                    f"<h3>Voice Screening Call Completed</h3>"
                    f"<p><strong>Candidate:</strong> {candidate.name if candidate else 'N/A'}</p>"
                    f"<p><strong>Phone:</strong> {voice_session.phone_number}</p>"
                    f"<p><strong>Score:</strong> {score}/10</p>"
                    f"<p><strong>Recommendation:</strong> {recommendation}</p>"
                    f"<p><strong>Duration:</strong> {voice_session.duration_seconds or 0}s</p>"
                    f"<p><a href='/voice-screening'>View full transcript and assessment</a></p>"
                )
                for user in users:
                    if user.email:
                        svc.send_email(user.email, subject, body_html)
        except Exception as e:
            logger.warning("Failed to send voice screening email: %s", e)

    except Exception as e:
        logger.warning("Failed to send voice screening notification: %s", e)


def _notify_call_failed(db, voice_session, candidate, config):
    """Send in-app notification when a call fails (all retries exhausted)."""
    try:
        from app.backend.services.notification_service import create_admin_notification

        create_admin_notification(
            db=db,
            type="voice_screening_failed",
            severity="warning",
            title=f"Voice Screening Failed: {candidate.name if candidate else 'Candidate'}",
            message=(
                f"All retry attempts exhausted for {candidate.name if candidate else 'Candidate'} "
                f"(phone: {voice_session.phone_number}).\n"
                f"Retries: {voice_session.retry_count}\n"
                f"This candidate may need manual follow-up."
            ),
            tenant_id=voice_session.tenant_id,
        )
    except Exception as e:
        logger.warning("Failed to send failed call notification: %s", e)


def _notify_escalation(db, voice_session, candidate, config):
    """Send notification when a call is escalated (all retries exhausted)."""
    try:
        from app.backend.services.notification_service import create_admin_notification

        create_admin_notification(
            db=db,
            type="voice_screening_escalated",
            severity="error",
            title=f"Voice Screening Escalated: {candidate.name if candidate else 'Candidate'}",
            message=(
                f"Screening for {candidate.name if candidate else 'Candidate'} "
                f"(phone: {voice_session.phone_number}) has been escalated.\n"
                f"All {voice_session.retry_count} retry attempts failed.\n"
                f"Manual follow-up required."
            ),
            tenant_id=voice_session.tenant_id,
        )
    except Exception as e:
        logger.warning("Failed to send escalation notification: %s", e)
