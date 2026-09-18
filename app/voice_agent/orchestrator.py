"""
Interview Orchestrator — the core interview engine.

Replaces UnifiedConversation and RecruiterConversation with a real-time
orchestration loop that:
1. Manages 7 explicit interview stages
2. Uses the Question Planner for dynamic question generation
3. Uses the Answer Evaluator for real-time scoring
4. Tracks communication metrics throughout
5. Maintains full conversation memory
6. Adapts difficulty based on answer quality

Stages:
  1. Introduction     — verify identity, explain process, consent
  2. Resume Verification — compare answers against resume claims
  3. Technical        — adaptive technical questions
  4. Behavioral       — STAR-format questions
  5. Communication    — assess communication skills
  6. Motivation       — career goals, why this company
  7. Closing          — candidate questions, next steps
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from app.voice_agent.question_planner import QuestionPlanner
from app.voice_agent.turn_planner import TurnPlanner
from app.voice_agent.communication_tracker import CommunicationTracker
from app.voice_agent.speech_pipeline import pick_immediate_ack

logger = logging.getLogger("voice_agent.orchestrator")


class InterviewStage(Enum):
    INTRODUCTION = "introduction"
    RESUME_VERIFICATION = "resume_verification"
    TECHNICAL = "technical"
    BEHAVIORAL = "behavioral"
    COMMUNICATION = "communication"
    MOTIVATION = "motivation"
    CLOSING = "closing"
    ENDED = "ended"


STAGE_ORDER = [
    InterviewStage.INTRODUCTION,
    InterviewStage.RESUME_VERIFICATION,
    InterviewStage.TECHNICAL,
    InterviewStage.BEHAVIORAL,
    InterviewStage.COMMUNICATION,
    InterviewStage.MOTIVATION,
    InterviewStage.CLOSING,
]

# Time allocation per stage (seconds) for a 20-minute interview
DEFAULT_STAGE_DURATIONS = {
    InterviewStage.INTRODUCTION: 120,
    InterviewStage.RESUME_VERIFICATION: 180,
    InterviewStage.TECHNICAL: 360,
    InterviewStage.BEHAVIORAL: 240,
    InterviewStage.COMMUNICATION: 120,
    InterviewStage.MOTIVATION: 120,
    InterviewStage.CLOSING: 60,
}


@dataclass
class OrchestratorContext:
    """Context for the Interview Orchestrator."""
    session_id: str
    candidate_name: str = "there"
    company_name: str = "the company"
    jd_title: str = ""
    bot_name: str = "ARIA"
    tenant_id: int = 0
    candidate_id: int = 0
    phone_number: str = ""
    result_generation: int = 1

    # Candidate context (from backend)
    candidate_context: dict[str, Any] = field(default_factory=dict)
    role_context: dict[str, Any] = field(default_factory=dict)
    screening_result: dict[str, Any] = field(default_factory=dict)

    # Interview config
    total_duration_s: int = 1200  # 20 minutes default
    consent_script: Optional[str] = None
    use_custom_interview_opening: bool = False
    interview_opening_script: Optional[str] = None

    # Runtime state
    current_stage: InterviewStage = InterviewStage.INTRODUCTION
    started_at: Optional[float] = None
    consent_recorded: bool = False
    transcript: list[dict[str, Any]] = field(default_factory=list)
    questions_responses: list[dict[str, Any]] = field(default_factory=list)
    answer_scores: list[int] = field(default_factory=list)
    resume_inconsistencies: list[dict[str, str]] = field(default_factory=list)
    warm_phrases: list[str] = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return time.time() - self.started_at

    @property
    def time_remaining(self) -> int:
        return max(0, int(self.total_duration_s - self.elapsed))

    @property
    def stage_time_remaining(self) -> int:
        stage_duration = DEFAULT_STAGE_DURATIONS.get(self.current_stage, 120)
        # Calculate time spent in current stage
        if not self.questions_responses:
            return stage_duration
        stage_start = self.questions_responses[0].get("stage_start", self.started_at or time.time())
        if isinstance(stage_start, (int, float)):
            spent = time.time() - stage_start
        else:
            spent = 0
        return max(0, int(stage_duration - spent))


class InterviewOrchestrator:
    """
    Real-time interview orchestrator with adaptive questioning.

    Usage:
        orchestrator = InterviewOrchestrator(ctx, llm_client, speech_client)
        greeting = await orchestrator.start()
        # ... publish greeting via TTS ...
        # When candidate speaks:
        response = await orchestrator.handle_candidate_response(transcribed_text)
        # ... publish response via TTS ...
    """

    # Edge case markers
    _SILENCE_MARKERS = {"[silence]", "[no speech detected]", ""}
    _DONT_KNOW_MARKERS = ["i don't know", "i'm not sure", "can't think of", "no idea"]
    _RESCHEDULE_MARKERS = ["not a good time", "can we reschedule", "call back later", "busy right now"]
    _CONSENT_POSITIVE = {"yes", "yeah", "sure", "okay", "ok", "yep", "go ahead", "i consent"}
    _CONSENT_NEGATIVE = {"no", "nope", "don't", "not now", "i don't consent"}

    def __init__(self, ctx: OrchestratorContext, llm_client, speech_client):
        self.ctx = ctx
        self.llm = llm_client
        self.speech = speech_client
        self.turn_planner = TurnPlanner()
        self.planner = QuestionPlanner()
        self.comm_tracker = CommunicationTracker()

        # Conversation memory for LLM context
        self.history: list[dict[str, str]] = []

        # Stage tracking
        self._current_question: str = ""
        self._question_start_time: float = 0.0
        self._stage_question_count: int = 0
        self._waiting_for_answer: bool = False
        self._introduction_step: int = 0  # Track sub-steps in introduction

    async def start(self) -> str:
        """Begin the interview — returns the greeting text."""
        self.ctx.started_at = time.time()
        self.ctx.current_stage = InterviewStage.INTRODUCTION
        greeting = await self._get_introduction_message(step=0)
        self.history.append({"role": "assistant", "content": greeting})
        self.ctx.transcript.append({
            "speaker": "bot",
            "text": greeting,
            "timestamp": 0.0,
            "stage": "introduction",
        })
        return greeting

    def should_play_filler(self) -> bool:
        """Play a short ack while the LLM thinks on slow-path turns."""
        if self.ctx.current_stage == InterviewStage.INTRODUCTION and self._introduction_step < 3:
            return False
        if self.ctx.current_stage in (InterviewStage.CLOSING, InterviewStage.ENDED):
            return False
        return True

    def pick_filler(self) -> str:
        return pick_immediate_ack()

    async def handle_candidate_response(self, text: str) -> Optional[str]:
        """
        Process a candidate's response and return the bot's next utterance.
        Returns None when the interview should end.
        """
        text = text.strip()
        if not text or text.lower() in self._SILENCE_MARKERS:
            return "I didn't quite catch that. Could you repeat what you said?"

        # Record in transcript
        self.ctx.transcript.append({
            "speaker": "candidate",
            "text": text,
            "timestamp": self.ctx.elapsed,
            "stage": self.ctx.current_stage.value,
        })
        self.history.append({"role": "user", "content": text})

        # Handle based on current stage
        if self.ctx.current_stage == InterviewStage.INTRODUCTION:
            return await self._handle_introduction(text)
        elif self.ctx.current_stage == InterviewStage.CLOSING:
            return await self._handle_closing(text)
        elif self.ctx.current_stage == InterviewStage.ENDED:
            return None
        else:
            return await self._handle_question_response(text)

    async def _get_introduction_message(self, step: int) -> str:
        """Generate introduction stage messages."""
        name = self.ctx.candidate_name.split()[0] if self.ctx.candidate_name else "there"
        duration_min = self.ctx.total_duration_s // 60

        if step == 0:
            # Greeting + identity verification
            return (
                f"Hi, is this {name}? This is {self.ctx.bot_name} calling from "
                f"{self.ctx.company_name} about the {self.ctx.jd_title or 'open'} position. "
                f"Do you have a few minutes for a quick interview call?"
            )
        elif step == 1:
            # Process explanation + consent
            return (
                f"Great, thanks {name}. This will be a {duration_min}-minute structured interview. "
                f"I'll ask questions about your background, technical skills, and experience. "
                f"This call is being recorded for evaluation purposes. Do you consent to proceed?"
            )
        elif step == 2:
            # Mic check + start
            return (
                "Perfect. Before we begin, can you confirm you can hear me clearly? "
                "Just say yes if you can."
            )
        else:
            # Transition to next stage
            return "Excellent. Let's start by talking about your background."

    async def _handle_introduction(self, text: str) -> Optional[str]:
        """Handle the multi-step introduction stage."""
        lower = text.lower().strip()

        if self._introduction_step == 0:
            # Identity verification response
            if any(m in lower for m in self._RESCHEDULE_MARKERS):
                self.ctx.current_stage = InterviewStage.ENDED
                return "No problem at all. We'll reach out at a better time. Thank you!"
            # Move to consent
            self._introduction_step = 1
            msg = await self._get_introduction_message(step=1)
            self.history.append({"role": "assistant", "content": msg})
            self.ctx.transcript.append({
                "speaker": "bot", "text": msg,
                "timestamp": self.ctx.elapsed, "stage": "introduction",
            })
            return msg

        elif self._introduction_step == 1:
            # Consent response
            if any(w in lower for w in self._CONSENT_POSITIVE):
                self.ctx.consent_recorded = True
                self._introduction_step = 2
                msg = await self._get_introduction_message(step=2)
                self.history.append({"role": "assistant", "content": msg})
                self.ctx.transcript.append({
                    "speaker": "bot", "text": msg,
                    "timestamp": self.ctx.elapsed, "stage": "introduction",
                })
                return msg
            elif any(w in lower for w in self._CONSENT_NEGATIVE):
                self.ctx.current_stage = InterviewStage.ENDED
                return "I understand. Unfortunately, I cannot proceed without recording consent. Thank you for your time. Goodbye."
            else:
                return "I'm sorry, I didn't quite catch that. Do you consent to this call being recorded? Please say yes or no."

        elif self._introduction_step == 2:
            # Mic check response
            self._introduction_step = 3
            # Transition to resume verification
            return await self._advance_to_stage(InterviewStage.RESUME_VERIFICATION)

        return "Let's continue. " + await self._advance_to_stage(InterviewStage.RESUME_VERIFICATION)

    async def _handle_question_response(self, text: str) -> Optional[str]:
        """Handle a response to a question in technical/behavioral/etc stages."""
        # Record communication metrics
        self.comm_tracker.record_answer(self._current_question, text)

        full_context = {
            "candidate": self.ctx.candidate_context,
            "role": self.ctx.role_context,
            "screening": self.ctx.screening_result,
        }
        conversation_history = [
            {"question": qr["question"], "answer": qr["answer"]}
            for qr in self.ctx.questions_responses
        ]
        questions_asked = [qr["question"] for qr in self.ctx.questions_responses]

        turn = await self.turn_planner.plan_next_turn(
            question=self._current_question,
            answer=text,
            stage=self.ctx.current_stage.value,
            context=full_context,
            conversation_history=conversation_history,
            answer_scores=self.ctx.answer_scores,
            questions_asked=questions_asked,
            time_remaining_s=self.ctx.time_remaining,
        )

        self.ctx.questions_responses.append({
            "stage": self.ctx.current_stage.value,
            "question": self._current_question,
            "answer": text,
            "score": turn["score"],
            "reasoning": turn.get("reasoning", ""),
            "difficulty_adjustment": turn.get("difficulty_adjustment", "same"),
            "key_points": turn.get("key_points", []),
            "concerns": turn.get("concerns", []),
            "timestamp": self.ctx.elapsed,
        })
        self.ctx.answer_scores.append(turn["score"])

        if self.ctx.current_stage == InterviewStage.RESUME_VERIFICATION:
            self._check_resume_consistency(self._current_question, text)

        if self.ctx.time_remaining < 60:
            return await self._advance_to_stage(InterviewStage.CLOSING)

        self._stage_question_count += 1
        stage_max_questions = self._get_stage_max_questions()
        if self._stage_question_count >= stage_max_questions:
            return await self._advance_to_next_stage()

        response_text = turn["question"]

        self._current_question = turn["question"]
        self._question_start_time = time.time()
        self.comm_tracker.record_question_end()

        self.history.append({"role": "assistant", "content": response_text})
        self.ctx.transcript.append({
            "speaker": "bot", "text": response_text,
            "timestamp": self.ctx.elapsed, "stage": self.ctx.current_stage.value,
        })

        return response_text

    def _check_resume_consistency(self, question: str, answer: str) -> None:
        """Check if the candidate's answer is consistent with their resume."""
        work_exp = self.ctx.candidate_context.get("parsed_work_experience", [])
        answer_lower = answer.lower()

        for exp in work_exp:
            if isinstance(exp, dict):
                company = str(exp.get("company", "")).lower()
                role = str(exp.get("title", "")).lower()
                if company and company in question.lower():
                    # Check if the answer mentions the company or role
                    if company not in answer_lower and role not in answer_lower:
                        self.ctx.resume_inconsistencies.append({
                            "question": question,
                            "answer": answer[:200],
                            "concern": f"Candidate didn't mention {company} or {role} when asked about it.",
                        })
                        logger.info("Resume inconsistency detected: %s", company)

    def _get_stage_max_questions(self) -> int:
        """Maximum questions per stage based on stage type."""
        return {
            InterviewStage.RESUME_VERIFICATION: 3,
            InterviewStage.TECHNICAL: 5,
            InterviewStage.BEHAVIORAL: 3,
            InterviewStage.COMMUNICATION: 2,
            InterviewStage.MOTIVATION: 3,
        }.get(self.ctx.current_stage, 3)

    async def _advance_to_stage(self, stage: InterviewStage) -> str:
        """Advance to a specific stage and return the opening question."""
        self.ctx.current_stage = stage
        self._stage_question_count = 0

        full_context = {
            "candidate": self.ctx.candidate_context,
            "role": self.ctx.role_context,
            "screening": self.ctx.screening_result,
        }

        next_q = await self.planner.generate_question(
            stage=stage.value,
            context=full_context,
            conversation_history=[
                {"question": qr["question"], "answer": qr["answer"]}
                for qr in self.ctx.questions_responses
            ],
            answer_scores=self.ctx.answer_scores,
            difficulty_adjustment="same",
            questions_asked=[qr["question"] for qr in self.ctx.questions_responses],
            time_remaining_s=self.ctx.time_remaining,
        )

        # Add stage transition prefix
        stage_labels = {
            InterviewStage.RESUME_VERIFICATION: "Let's start by verifying your background. ",
            InterviewStage.TECHNICAL: "Now let's dive into some technical questions. ",
            InterviewStage.BEHAVIORAL: "I'd like to understand how you handle real-world situations. ",
            InterviewStage.COMMUNICATION: "Let me assess your communication skills. ",
            InterviewStage.MOTIVATION: "Let's talk about your career goals. ",
            InterviewStage.CLOSING: "We're coming to the end of our interview. ",
        }
        prefix = stage_labels.get(stage, "")
        response_text = prefix + next_q["question"]

        self._current_question = next_q["question"]
        self._question_start_time = time.time()
        self.comm_tracker.record_question_end()

        self.history.append({"role": "assistant", "content": response_text})
        self.ctx.transcript.append({
            "speaker": "bot", "text": response_text,
            "timestamp": self.ctx.elapsed, "stage": stage.value,
        })

        return response_text

    async def _advance_to_next_stage(self) -> str:
        """Advance to the next stage in order."""
        current_idx = STAGE_ORDER.index(self.ctx.current_stage) if self.ctx.current_stage in STAGE_ORDER else 0
        next_idx = current_idx + 1

        if next_idx >= len(STAGE_ORDER):
            return await self._advance_to_stage(InterviewStage.CLOSING)

        return await self._advance_to_stage(STAGE_ORDER[next_idx])

    async def _handle_closing(self, text: str) -> Optional[str]:
        """Handle the closing stage."""
        name = self.ctx.candidate_name.split()[0] if self.ctx.candidate_name else ""
        name_clause = f", {name}" if name else ""

        # Record any final candidate question
        if text.strip():
            self.ctx.questions_responses.append({
                "stage": "closing",
                "question": "Do you have any questions?",
                "answer": text,
                "score": 0,
                "timestamp": self.ctx.elapsed,
            })

        self.ctx.current_stage = InterviewStage.ENDED
        farewell = (
            f"Thank you for your time today{name_clause}. "
            f"We'll be in touch with next steps within a few days. "
            f"Have a great day!"
        )
        self.history.append({"role": "assistant", "content": farewell})
        self.ctx.transcript.append({
            "speaker": "bot", "text": farewell,
            "timestamp": self.ctx.elapsed, "stage": "closing",
        })
        return farewell

    def force_closing_message(self) -> Optional[str]:
        """Speak a farewell when the call ends abruptly (e.g. time budget)."""
        if self.ctx.current_stage == InterviewStage.ENDED:
            return None
        if self.ctx.current_stage == InterviewStage.CLOSING:
            self.ctx.current_stage = InterviewStage.ENDED
            return None

        name = self.ctx.candidate_name.split()[0] if self.ctx.candidate_name else ""
        name_clause = f", {name}" if name else ""
        farewell = (
            f"Thank you for your time today{name_clause}. "
            f"We'll be in touch with next steps within a few days. "
            f"Have a great day!"
        )
        self.ctx.transcript.append(
            {
                "speaker": "bot",
                "text": farewell,
                "timestamp": self.ctx.elapsed,
                "stage": "closing",
            }
        )
        self.ctx.current_stage = InterviewStage.ENDED
        return farewell

    def is_complete(self) -> bool:
        """Check if the interview is complete."""
        return self.ctx.current_stage == InterviewStage.ENDED

    def get_result(self) -> dict[str, Any]:
        """Package the interview result for backend callback."""
        agg = self.comm_tracker.get_aggregate_metrics()
        return {
            "session_id": self.ctx.session_id,
            "duration_seconds": int(self.ctx.elapsed),
            "consent_recorded": self.ctx.consent_recorded,
            "transcript": self.ctx.transcript,
            "questions_responses": self.ctx.questions_responses,
            "answer_scores": self.ctx.answer_scores,
            "avg_answer_score": sum(self.ctx.answer_scores) / len(self.ctx.answer_scores) if self.ctx.answer_scores else 0,
            "resume_inconsistencies": self.ctx.resume_inconsistencies,
            "communication_metrics": {
                "total_answers": agg.total_answers,
                "total_words": agg.total_words,
                "avg_speaking_speed_wpm": agg.avg_speaking_speed_wpm,
                "avg_answer_length": agg.avg_answer_length,
                "total_fillers": agg.total_fillers,
                "avg_silence_before_answer": agg.avg_silence_before_answer,
                "confidence_trend": agg.confidence_trend,
                "speaking_speed_trend": agg.speaking_speed_trend,
                "overall_confidence_score": agg.overall_confidence_score,
                "per_answer": self.comm_tracker.get_per_answer_metrics(),
            },
            "completion_reason": "natural" if self.ctx.current_stage == InterviewStage.ENDED else "time_limit",
            "stages_completed": [
                qr.get("stage") for qr in self.ctx.questions_responses
            ],
        }
