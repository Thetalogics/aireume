"""Recruiter orchestrator — main service for AI Recruiter interviews."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, available_timezones

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backend.models.db_models import (
    Candidate,
    RecruiterInterviewQuestion,
    RecruiterInterviewSession,
    RecruiterScorecard,
    ScreeningResult,
    VoiceScreeningSession,
    VoiceTranscriptEntry,
)
from app.backend.services.recruiter.context_engine import InterviewContextEngine
from app.backend.services.kit_strategy import load_kit_strategy_for_screening
from app.backend.services.recruiter.evaluation_agents import (
    TechnicalEvaluator,
    BehavioralEvaluator,
    CommunicationEvaluator,
    CulturalFitEvaluator,
)
from app.backend.services.recruiter.fitment_adjuster import FitmentAdjuster
from app.backend.services.recruiter.recommendation_agent import RecommendationAgent
from app.backend.services.recruiter.copilot_agent import CopilotAgent

logger = logging.getLogger("aria.recruiter")


class RecruiterOrchestrator:
    """Main orchestration service for AI Recruiter interviews."""

    def __init__(self, db: Session):
        self.db = db
        self.context_engine = InterviewContextEngine()

    async def initiate_interview(
        self,
        tenant_id: int,
        candidate_id: int,
        jd_id: int,
        screening_result_id: int | None,
        trigger_type: str,
        config: dict[str, Any] | None = None,
        created_by: int | None = None,
    ) -> str:
        """
        Creates a new recruiter interview session, generates a strategy,
        and schedules the voice call via existing voice infrastructure.
        """
        logger.info(
            "Initiating AI recruiter interview: tenant=%s candidate=%s jd=%s",
            tenant_id,
            candidate_id,
            jd_id,
        )

        if config is None:
            config = {}

        # Multi-tenancy: ensure candidate and JD belong to the tenant
        candidate = self.db.execute(
            select(Candidate).where(
                Candidate.id == candidate_id,
                Candidate.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()
        if candidate is None:
            raise ValueError(f"Candidate {candidate_id} not found for tenant {tenant_id}")

        if not candidate.phone:
            raise ValueError(f"Candidate {candidate_id} has no phone number")

        if screening_result_id is not None:
            sr = self.db.execute(
                select(ScreeningResult).where(
                    ScreeningResult.id == screening_result_id,
                    ScreeningResult.tenant_id == tenant_id,
                    ScreeningResult.candidate_id == candidate_id,
                )
            ).scalar_one_or_none()
            if sr is None:
                raise ValueError(
                    f"Screening result {screening_result_id} not found for this candidate"
                )

        # Duplicate check: prevent multiple active scheduled sessions for same candidate + JD
        active_statuses = {"pending_strategy", "scheduled", "in_progress"}
        existing = self.db.execute(
            select(RecruiterInterviewSession).where(
                RecruiterInterviewSession.tenant_id == tenant_id,
                RecruiterInterviewSession.candidate_id == candidate_id,
                RecruiterInterviewSession.jd_id == jd_id,
                RecruiterInterviewSession.status.in_(active_statuses),
            )
        ).scalars().first()
        if existing:
            raise ValueError(
                f"Active recruiter session already exists for this candidate and JD "
                f"(session_id={existing.id}, status={existing.status}). "
                f"Cancel it before creating a new one."
            )

        # Build context and generate strategy
        context = self.context_engine.build_context(
            self.db,
            candidate_id=candidate_id,
            screening_result_id=screening_result_id,
            jd_id=jd_id,
        )

        strategy_config = {
            "duration_minutes": config.get("duration_minutes", 20),
            "question_count": config.get("question_count", 12),
            "depth": config.get("depth", "standard"),
        }

        role_title = context.get("role", {}).get("title", "")
        candidate_name = context.get("candidate", {}).get("name", "there")

        strategy = load_kit_strategy_for_screening(
            self.db,
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            jd_id=jd_id,
            screening_result_id=screening_result_id,
            config=strategy_config,
            role_title=role_title,
            candidate_name=candidate_name,
        )
        logger.info(
            "Using interview-kit strategy for screening_result_id=%s depth=%s questions=%s",
            screening_result_id,
            strategy.get("depth"),
            strategy.get("kit_question_count"),
        )

        # Merge must-ask questions from project config and interview templates
        strategy = self._merge_must_ask_questions(strategy, config, jd_id)

        # Normalize scheduled_at to timezone-aware datetime using provided timezone
        tz_name = config.get("timezone")
        scheduled_at = self._parse_scheduled_at(
            config.get("scheduled_at"),
            timezone_hint=tz_name,
        )
        if scheduled_at is not None and scheduled_at < datetime.now(timezone.utc):
            raise ValueError("Scheduled time must be in the future")

        # Scheduling conflict detection — check for overlapping interviews
        if scheduled_at is not None:
            self._check_scheduling_conflict(tenant_id, candidate_id, scheduled_at, duration_minutes=20)

        phone_number = config.get("phone_number") or candidate.phone

        # Map configured duration to the DB-supported depth labels (quick/deep).
        depth_label = strategy_config.get("depth") or "standard"
        if depth_label == "quick" or (strategy_config.get("duration_minutes") or 20) <= 7:
            voice_depth = "quick"
        else:
            voice_depth = "deep"

        # Create the voice screening session using existing infrastructure
        voice_session = VoiceScreeningSession(
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            jd_id=jd_id,
            phone_number=phone_number,
            direction="outbound",
            status="scheduled",
            interview_depth=voice_depth,
            scheduled_at=scheduled_at,
        )
        self.db.add(voice_session)
        self.db.commit()
        self.db.refresh(voice_session)

        # Schedule the call
        from app.backend.services.voice_call_scheduler import schedule_voice_call

        schedule_voice_call(voice_session.id, scheduled_at)

        # Create the recruiter interview session
        interview_session = RecruiterInterviewSession(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            candidate_id=candidate_id,
            jd_id=jd_id,
            screening_result_id=screening_result_id,
            voice_session_id=voice_session.id,
            trigger_type=trigger_type,
            status="pending_strategy",
            interview_strategy_json=json.dumps(strategy, default=str),
            interview_config_json=json.dumps(config, default=str),
            created_by=created_by,
        )
        self.db.add(interview_session)

        # Persist generated questions
        for q in strategy.get("questions", []):
            self.db.add(
                RecruiterInterviewQuestion(
                    id=str(uuid.uuid4()),
                    session_id=interview_session.id,
                    sequence_number=q.get("sequence_number", 0),
                    category=q.get("category", "technical"),
                    question_text=q.get("question_text", ""),
                    question_context=q.get("question_context", ""),
                    is_follow_up=False,
                )
            )

        interview_session.status = "scheduled"
        self.db.commit()
        self.db.refresh(interview_session)

        logger.info(
            "Recruiter interview session created: %s (voice_session=%s)",
            interview_session.id,
            voice_session.id,
        )
        return interview_session.id

    async def on_interview_completed(
        self,
        session_id: str,
        *,
        expected_voice_generation: int | None = None,
    ) -> bool:
        """
        Post-interview processing pipeline:
        transcript -> evaluation agents -> fitment adjustment -> recommendation -> scorecard.
        """
        logger.info("Processing completed recruiter interview: %s", session_id)

        interview_session = self._get_session(session_id)
        if interview_session is None:
            logger.error("Recruiter interview session %s not found", session_id)
            return False

        # Ensure tenant-scoped access
        tenant_id = interview_session.tenant_id

        # Load transcript from voice session
        transcript: list[dict[str, Any]] = []
        voice_session = interview_session.voice_session
        if voice_session and expected_voice_generation is None:
            expected_voice_generation = voice_session.result_generation
        if voice_session:
            entries = self.db.execute(
                select(VoiceTranscriptEntry)
                .where(VoiceTranscriptEntry.session_id == voice_session.id)
                .order_by(VoiceTranscriptEntry.timestamp.asc())
            ).scalars().all()
            transcript = [
                {"speaker": e.speaker, "text": e.text, "timestamp": e.timestamp.isoformat() if e.timestamp else None, "question_id": e.question_id}
                for e in entries
            ]

        # ── Build question-response pairs ─────────────────────────────────────
        # Prefer responses stored by the internal callback endpoint (which
        # captures actual candidate answers). Fall back to deriving from
        # strategy + transcript when the callback hasn't stored responses.
        stored_questions = self.db.execute(
            select(RecruiterInterviewQuestion)
            .where(RecruiterInterviewQuestion.session_id == session_id)
            .order_by(RecruiterInterviewQuestion.sequence_number.asc())
        ).scalars().all()

        if any(q.candidate_response for q in stored_questions):
            # Use stored responses from the callback
            questions_responses = [
                {
                    "sequence_number": q.sequence_number,
                    "category": q.category,
                    "question": q.question_text,
                    "question_context": q.question_context or "",
                    "response": q.candidate_response or "",
                    "response_duration": q.response_duration_seconds,
                    "is_follow_up": q.is_follow_up,
                    "question_id": q.id,
                    "score": q.answer_score,
                }
                for q in stored_questions
            ]
        else:
            # Fall back to deriving from strategy + transcript
            strategy = self._load_json(interview_session.interview_strategy_json, {})
            questions_responses = self._pair_questions_responses(
                strategy.get("questions", []), transcript
            )

        # Rebuild context
        context = self.context_engine.build_context(
            self.db,
            candidate_id=interview_session.candidate_id,
            screening_result_id=interview_session.screening_result_id,
            jd_id=interview_session.jd_id,
        )

        # Run evaluators
        technical_eval = TechnicalEvaluator()
        behavioral_eval = BehavioralEvaluator()
        communication_eval = CommunicationEvaluator()
        cultural_eval = CulturalFitEvaluator()

        strategy = self._load_json(interview_session.interview_strategy_json, {})
        hypotheses = []
        screening = context.get("screening_result", {}) or {}
        analysis = screening.get("analysis_result") or {}
        if isinstance(analysis, dict):
            iq = analysis.get("interview_questions") or {}
            if isinstance(iq, dict):
                hypotheses = iq.get("hypotheses") or []

        jd_context = {
            "required_skills": context.get("role", {}).get("required_skills", []),
            "title": context.get("role", {}).get("title", ""),
            "hypotheses": hypotheses,
        }
        company_context = {
            "title": context.get("role", {}).get("title", ""),
            "jd_text": context.get("role", {}).get("jd_text", ""),
        }

        technical, behavioral, communication, cultural = await asyncio.gather(
            technical_eval.evaluate(questions_responses, jd_context),
            behavioral_eval.evaluate(questions_responses, company_context),
            communication_eval.evaluate(transcript, {}),
            cultural_eval.evaluate(questions_responses, company_context),
        )

        # Extract motivation and integrity scores from per-answer data
        motivation_score = self._extract_dimension_score(questions_responses, "motivation", cultural.get("score", 50))
        integrity_score = self._extract_dimension_score(questions_responses, "integrity", behavioral.get("score", 50))

        # Confidence score from communication metrics (if available from orchestrator)
        confidence_score = communication.get("score", 50)
        if any(qr.get("confidence_score") for qr in questions_responses):
            scores = [qr["confidence_score"] for qr in questions_responses if qr.get("confidence_score")]
            confidence_score = sum(scores) // len(scores) if scores else 50

        scorecard = {
            "technical": technical,
            "behavioral": behavioral,
            "communication": communication,
            "cultural_fit": cultural,
            "motivation": {"score": motivation_score, "evidence": ["Derived from motivation stage answers."]},
            "integrity": {"score": integrity_score, "evidence": ["Derived from resume verification and behavioral answers."]},
            "confidence": {"score": confidence_score, "evidence": ["Derived from communication metrics."]},
        }

        # Run Copilot agent for per-answer observations
        copilot = CopilotAgent()
        for qr in questions_responses:
            if qr.get("answer") and qr.get("score") is not None:
                try:
                    observation = await copilot.generate_observation(
                        question=qr.get("question", ""),
                        answer=qr.get("answer", ""),
                        stage=qr.get("stage", qr.get("category", "technical")),
                        answer_score=qr.get("score", 50),
                        context=context,
                    )
                    qr["copilot_observation"] = observation
                except Exception as e:
                    logger.warning("Copilot observation failed for Q: %s", e)

        # Fitment adjustment
        screening = context.get("screening_result", {}) or {}
        original_fitment = {
            "score": screening.get("fit_score", 50),
            "risk_signals": screening.get("risk_signals", []),
        }
        fitment_adjuster = FitmentAdjuster()
        adjusted_fitment = await fitment_adjuster.adjust(
            original_fitment, scorecard, questions_responses
        )

        # Final recommendation
        recommender = RecommendationAgent()
        recommendation = await recommender.recommend(scorecard, adjusted_fitment, context)

        if voice_session:
            self.db.refresh(voice_session, with_for_update=True)
            if voice_session.result_generation != expected_voice_generation:
                self.db.rollback()
                logger.info(
                    "Discarded stale recruiter scorecard session_id=%s expected_generation=%s current_generation=%s",
                    session_id,
                    expected_voice_generation,
                    voice_session.result_generation,
                )
                return False

        # Persist scorecard
        scorecard_record = RecruiterScorecard(
            id=str(uuid.uuid4()),
            session_id=interview_session.id,
            tenant_id=tenant_id,
            candidate_id=interview_session.candidate_id,
            technical_score=technical.get("score"),
            technical_evidence=json.dumps(technical, default=str),
            behavioral_score=behavioral.get("score"),
            behavioral_evidence=json.dumps(behavioral, default=str),
            communication_score=communication.get("score"),
            communication_evidence=json.dumps(communication, default=str),
            cultural_fit_score=cultural.get("score"),
            cultural_fit_evidence=json.dumps(cultural, default=str),
            risk_signals_validated=json.dumps(
                {"signals": adjusted_fitment.get("risks_validated", [])},
                default=str,
            ),
            gaps_explained=json.dumps(
                {"items": adjusted_fitment.get("gaps_explained", [])},
                default=str,
            ),
            original_fit_score=original_fitment.get("score"),
            adjusted_fit_score=adjusted_fitment.get("adjusted_score"),
            adjustment_reasoning=adjusted_fitment.get("reasoning"),
            overall_score=recommendation.get("overall_score"),
            confidence_level=recommendation.get("confidence_level"),
            recommendation=recommendation.get("recommendation"),
            recommendation_reasoning=recommendation.get("recommendation_reasoning"),
            executive_summary=recommendation.get("executive_summary"),
        )
        self.db.add(scorecard_record)

        # Update stored questions with copilot observations and scores
        for qr in questions_responses:
            if qr.get("copilot_observation") or qr.get("score") is not None:
                matching_db_q = next(
                    (q for q in stored_questions if q.question_text == qr.get("question", "")),
                    None,
                )
                if matching_db_q:
                    if qr.get("score") is not None:
                        matching_db_q.answer_score = qr["score"]
                    if qr.get("copilot_observation"):
                        matching_db_q.copilot_observation = json.dumps(qr["copilot_observation"], default=str)

        interview_session.status = "completed"
        interview_session.ended_at = datetime.now(timezone.utc)
        if transcript and voice_session and voice_session.started_at:
            try:
                interview_session.duration_seconds = int(
                    (datetime.now(timezone.utc) - voice_session.started_at).total_seconds()
                )
            except Exception:
                pass

        # Persist consolidated outcome on screening result
        if interview_session.screening_result_id:
            from app.backend.services.consolidated_recommendation import (
                average_scores,
                compute_consolidated_for_result,
                persist_outcome_to_screening_result,
            )

            sr = self.db.execute(
                select(ScreeningResult).where(
                    ScreeningResult.id == interview_session.screening_result_id,
                    ScreeningResult.tenant_id == interview_session.tenant_id,
                )
            ).scalar_one_or_none()
            if sr:
                analysis_score = (
                    original_fitment.get("score")
                    or screening.get("fit_score")
                    or sr.deterministic_score
                )
                live_scores = [
                    q.answer_score for q in stored_questions if q.answer_score is not None
                ]
                live_avg = average_scores(live_scores)
                call_score = (
                    recommendation.get("overall_score")
                    or live_avg
                    or adjusted_fitment.get("adjusted_score")
                )
                evidence_snippets: list[str] = []
                if recommendation.get("executive_summary"):
                    evidence_snippets.append(str(recommendation["executive_summary"])[:500])
                elif recommendation.get("recommendation_reasoning"):
                    evidence_snippets.append(str(recommendation["recommendation_reasoning"])[:500])
                outcome = compute_consolidated_for_result(
                    self.db,
                    sr,
                    analysis_score=analysis_score,
                    call_score=call_score,
                    call_source="ai",
                    call_recommendation=recommendation.get("recommendation"),
                    evidence=evidence_snippets,
                )
                persist_outcome_to_screening_result(sr, outcome, call_source="ai")

        self.db.commit()

        logger.info(
            "Recruiter interview completed: %s recommendation=%s score=%s",
            session_id,
            recommendation.get("recommendation"),
            recommendation.get("overall_score"),
        )
        return True

    def get_session_status(self, session_id: str) -> dict[str, Any]:
        """Returns current session status with progress info."""
        session = self._get_session(session_id)
        if session is None:
            return {"error": "Session not found", "session_id": session_id}

        scorecard = session.scorecard
        return {
            "session_id": session.id,
            "status": session.status,
            "tenant_id": session.tenant_id,
            "candidate_id": session.candidate_id,
            "jd_id": session.jd_id,
            "voice_session_id": session.voice_session_id,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "duration_seconds": session.duration_seconds,
            "scorecard": {
                "overall_score": scorecard.overall_score,
                "recommendation": scorecard.recommendation,
                "confidence_level": scorecard.confidence_level,
            } if scorecard else None,
        }

    async def cancel_interview(self, session_id: str) -> None:
        """Cancels a scheduled or in-progress interview."""
        session = self._get_session(session_id)
        if session is None:
            logger.error("Cannot cancel — session %s not found", session_id)
            return

        if session.status in ("completed", "cancelled"):
            logger.info("Session %s already %s", session_id, session.status)
            return

        session.status = "cancelled"
        session.ended_at = datetime.now(timezone.utc)

        # Cancel associated voice session and scheduler jobs
        if session.voice_session_id:
            from app.backend.services.voice_call_scheduler import cancel_pending_retries

            cancel_pending_retries(session.voice_session_id)
            voice_session = self.db.execute(
                select(VoiceScreeningSession).where(
                    VoiceScreeningSession.id == session.voice_session_id,
                    VoiceScreeningSession.tenant_id == session.tenant_id,
                )
            ).scalar_one_or_none()
            if voice_session and voice_session.status not in ("completed", "failed"):
                voice_session.status = "cancelled"
                voice_session.ended_at = datetime.now(timezone.utc)

        self.db.commit()
        logger.info("Recruiter interview cancelled: %s", session_id)

    async def retry_interview(self, session_id: str) -> str:
        """Retries a failed interview by creating a new session."""
        session = self._get_session(session_id)
        if session is None:
            raise ValueError(f"Session {session_id} not found")

        logger.info("Retrying recruiter interview: %s", session_id)

        config = self._load_json(session.interview_config_json, {})
        config["retried_from_session_id"] = session_id
        config["scheduled_at"] = config.get("scheduled_at") or datetime.now(timezone.utc).isoformat()

        new_session_id = await self.initiate_interview(
            tenant_id=session.tenant_id,
            candidate_id=session.candidate_id,
            jd_id=session.jd_id,
            screening_result_id=session.screening_result_id,
            trigger_type=f"retry:{session.trigger_type}",
            config=config,
            created_by=session.created_by,
        )

        logger.info("Retry session created: %s -> %s", session_id, new_session_id)
        return new_session_id

    # ─── Helpers ──────────────────────────────────────────────────────────────

    def _get_session(self, session_id: str) -> RecruiterInterviewSession | None:
        return self.db.execute(
            select(RecruiterInterviewSession).where(
                RecruiterInterviewSession.id == session_id
            )
        ).scalar_one_or_none()

    def _parse_scheduled_at(
        self, value: Any, timezone_hint: str | None = None
    ) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value
        else:
            try:
                dt = datetime.fromisoformat(value)
            except (ValueError, TypeError):
                return None

        # If no timezone info, interpret as the user's selected timezone (or UTC)
        if dt.tzinfo is None:
            tz = timezone.utc
            if timezone_hint and timezone_hint in available_timezones:
                tz = ZoneInfo(timezone_hint)
            dt = dt.replace(tzinfo=tz)

        return dt.astimezone(timezone.utc)

    def _load_json(self, raw: str | None, default: Any) -> Any:
        if not raw:
            return default
        try:
            parsed = json.loads(raw)
            return parsed if parsed is not None else default
        except (json.JSONDecodeError, TypeError):
            return default

    def _check_scheduling_conflict(
        self,
        tenant_id: int,
        candidate_id: int,
        scheduled_at: datetime,
        duration_minutes: int = 20,
    ) -> None:
        """Check for scheduling conflicts — raises ValueError if the candidate
        already has an interview scheduled within the time window.
        """
        from datetime import timedelta
        window_start = scheduled_at - timedelta(minutes=duration_minutes)
        window_end = scheduled_at + timedelta(minutes=duration_minutes)

        conflicting = self.db.execute(
            select(VoiceScreeningSession).where(
                VoiceScreeningSession.tenant_id == tenant_id,
                VoiceScreeningSession.candidate_id == candidate_id,
                VoiceScreeningSession.status.in_(["scheduled", "ringing", "in_progress"]),
                VoiceScreeningSession.scheduled_at.between(window_start, window_end),
            )
        ).scalars().all()

        if conflicting:
            conflict_ids = [str(s.id) for s in conflicting]
            raise ValueError(
                f"Scheduling conflict: candidate {candidate_id} already has "
                f"interview(s) {', '.join(conflict_ids)} scheduled within "
                f"the requested time window"
            )

    def _merge_must_ask_questions(
        self,
        strategy: dict[str, Any],
        config: dict[str, Any],
        jd_id: int | None,
    ) -> dict[str, Any]:
        """Merge must-ask questions from project config and interview templates into the strategy.

        Must-ask questions are prepended to the generated questions list so they
        are asked first. Each question is tagged with `must_ask: True`.
        """
        must_ask: list[dict[str, Any]] = []

        # 1. From config (direct must_ask_questions list)
        config_questions = config.get("must_ask_questions", [])
        for q in config_questions:
            if isinstance(q, dict) and q.get("question"):
                must_ask.append({
                    "question_text": q["question"],
                    "category": q.get("category", "technical"),
                    "question_context": q.get("rationale", ""),
                    "must_ask": True,
                    "sequence_number": len(must_ask) + 1,
                })

        # 2. From interview templates attached to the project
        project_id = config.get("project_id")
        if project_id:
            from app.backend.models.db_models import InterviewTemplate
            templates = self.db.execute(
                select(InterviewTemplate).where(
                    InterviewTemplate.project_id == project_id,
                    InterviewTemplate.is_active == True,
                )
            ).scalars().all()
            for tpl in templates:
                tpl_questions = self._load_json(tpl.questions_json, [])
                if isinstance(tpl_questions, list):
                    for q in tpl_questions:
                        if isinstance(q, dict) and q.get("question"):
                            must_ask.append({
                                "question_text": q["question"],
                                "category": q.get("category", "technical"),
                                "question_context": q.get("rationale", ""),
                                "must_ask": True,
                                "sequence_number": len(must_ask) + 1,
                            })

        if not must_ask:
            return strategy

        existing_questions = strategy.get("questions", [])
        # Renumber existing questions after must-ask ones
        offset = len(must_ask)
        for i, q in enumerate(existing_questions):
            if isinstance(q, dict):
                q["sequence_number"] = offset + i + 1

        strategy["questions"] = must_ask + existing_questions
        strategy["must_ask_count"] = len(must_ask)
        return strategy

    def _pair_questions_responses(
        self,
        questions: list[dict[str, Any]],
        transcript: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Best-effort pairing of strategy questions with candidate transcript turns.

        When transcript entries include a 'question_id' field (tagged by the
        voice agent), pairing is deterministic. Otherwise, falls back to
        sequential assignment of candidate turns to questions.
        """
        # Build a map of question_id -> response text from transcript
        id_to_responses: dict[str, list[str]] = {}
        candidate_turns: list[str] = []

        for t in transcript:
            if not isinstance(t, dict):
                continue
            if t.get("speaker") == "bot":
                continue
            text = t.get("text", "")
            qid = t.get("question_id")
            if qid:
                id_to_responses.setdefault(str(qid), []).append(text)
            candidate_turns.append(text)

        paired: list[dict[str, Any]] = []
        turn_idx = 0
        for q in questions:
            if not isinstance(q, dict):
                continue
            qid = q.get("question_id") or q.get("id")
            if qid and str(qid) in id_to_responses:
                response = " ".join(id_to_responses[str(qid)])
            else:
                response = candidate_turns[turn_idx] if turn_idx < len(candidate_turns) else ""
                if response:
                    turn_idx += 1
            paired.append({
                "sequence_number": q.get("sequence_number"),
                "category": q.get("category", "technical"),
                "question": q.get("question_text", ""),
                "response": response,
                "question_context": q.get("question_context", ""),
                "question_id": str(qid) if qid else None,
            })
        return paired

    def _extract_dimension_score(
        self,
        questions_responses: list[dict[str, Any]],
        dimension: str,
        fallback: int = 50,
    ) -> int:
        """Extract a dimension score from per-answer data (from the voice agent orchestrator)."""
        matching = [
            qr for qr in questions_responses
            if isinstance(qr, dict) and qr.get("stage") == dimension and qr.get("score") is not None
        ]
        if matching:
            scores = [qr["score"] for qr in matching]
            return sum(scores) // len(scores)
        # Also check category field for backward compat
        matching_cat = [
            qr for qr in questions_responses
            if isinstance(qr, dict) and qr.get("category") == dimension and qr.get("score") is not None
        ]
        if matching_cat:
            scores = [qr["score"] for qr in matching_cat]
            return sum(scores) // len(scores)
        return fallback
