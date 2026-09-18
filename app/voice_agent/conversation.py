"""
Unified Conversation Engine for ARIA Voice Agent.

Replaces both ConversationEngine (screening) and RecruiterConversation
with a single state machine that handles all interview depth modes:

  • Quick  — 3-5 min, preset skill questions, pass/fail, no follow-ups
  • Standard — 10-15 min, LLM-strategy questions, 3-dimension scoring, 1 follow-up
  • Deep   — 20-30 min, full strategy, 5-dimension + fitment, 2 follow-ups
"""

import json
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger("voice_agent.conversation")


# ─── Enums ────────────────────────────────────────────────────────────────────

class InterviewDepth(Enum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class InterviewState(Enum):
    GREETING = "greeting"
    CONSENT = "consent"
    WARMUP = "warmup"
    QUESTIONS = "questions"
    FOLLOW_UP = "follow_up"
    WRAP_UP = "wrap_up"
    ENDED = "ended"


# ─── Context ──────────────────────────────────────────────────────────────────

@dataclass
class InterviewContext:
    """Per-call context for the unified conversation state machine."""
    session_id: str
    depth: InterviewDepth
    candidate_name: str
    company_name: str = "the company"
    jd_title: str = ""
    jd_must_have_skills: List[str] = field(default_factory=list)
    strategy: Optional[Dict] = None          # Pre-generated for standard/deep
    interview_config: Optional[Dict] = None

    # Tenant / personalisation (carried over from ScreeningContext)
    tenant_id: int = 0
    candidate_id: int = 0
    phone_number: str = ""
    result_generation: int = 1
    bot_name: str = "ARIA Assistant"
    greeting_style: str = "professional"
    consent_script: Optional[str] = None

    # Runtime state
    current_state: InterviewState = InterviewState.GREETING
    transcript: List[Dict] = field(default_factory=list)
    questions_asked: List[Dict] = field(default_factory=list)
    current_dimension_idx: int = 0
    current_question_idx: int = 0
    follow_up_count: int = 0
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    consent_recorded: bool = False
    started_at: Optional[float] = None
    wrap_up_reached: bool = False

    # ── Derived budgets ──

    @property
    def time_budget(self) -> int:
        """Max call duration in seconds."""
        return {"quick": 300, "standard": 900, "deep": 1800}[self.depth.value]

    @property
    def max_questions(self) -> int:
        return {"quick": 5, "standard": 12, "deep": 20}[self.depth.value]

    @property
    def max_follow_ups_per_question(self) -> int:
        return {"quick": 0, "standard": 1, "deep": 2}[self.depth.value]

    @property
    def has_warmup(self) -> bool:
        return self.depth in (InterviewDepth.STANDARD, InterviewDepth.DEEP)

    @property
    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        return time.time() - self.started_at


# ─── Unified Conversation Engine ──────────────────────────────────────────────

class UnifiedConversation:
    """
    Single conversation engine that handles all interview depths.

    Quick:    Linear question flow from JD skills, no follow-ups, single-pass.
    Standard: LLM-generated strategy questions, 1 follow-up per weak answer,
              3-dimension scoring (technical → behavioral → cultural).
    Deep:     Full strategy with dimension rotation, 2 follow-ups,
              5-dimension + fitment scoring.
    """

    # ── Transition phrases (from ConversationEngine) ──
    _TRANSITIONS = [
        "Great, thanks for sharing. ",
        "That's helpful context. ",
        "Interesting — thanks. ",
        "Got it. ",
        "Makes sense. ",
        "Appreciate that. ",
        "Thanks for explaining. ",
    ]

    # ── Edge-case markers (from ConversationEngine) ──
    _SILENCE_MARKERS = {"[silence]", "[no speech detected]", ""}
    _DONT_KNOW_MARKERS = ["i don't know", "i'm not sure", "can't think of", "no idea"]
    _RESCHEDULE_MARKERS = ["not a good time", "can we reschedule", "call back later", "busy right now"]
    _SALARY_MARKERS = ["salary", "pay", "compensation", "benefits", "how much"]
    _AI_DETECT_MARKERS = ["are you a robot", "are you ai", "is this automated", "am i talking to a bot"]
    _NEGATIVE_MARKERS = ["no", "not a good time", "busy", "can't", "cannot", "later", "call back"]
    _CONSENT_POSITIVE = ["yes", "yeah", "sure", "okay", "ok", "consent", "agree"]
    _CONSENT_NEGATIVE = ["no", "don't", "refuse", "decline"]
    _VAGUE_PHRASES = ["i think", "maybe", "i'm not sure", "kind of", "sort of"]

    def __init__(self, context: InterviewContext, llm_client, speech_client):
        self.ctx = context
        self.llm = llm_client
        self.speech = speech_client
        self.history: List[Dict] = []  # Chat history for LLM context
        self._questions: List[Dict] = []
        self._dimensions: List[Dict] = []
        self._load_questions()

    # ──────────────────────────────────────────────────────────────────────────
    # Question loading
    # ──────────────────────────────────────────────────────────────────────────

    def _load_questions(self):
        """Populate question list and dimension map based on depth."""
        if self.ctx.depth == InterviewDepth.QUICK:
            # Generate simple skill-based questions
            skills = self.ctx.jd_must_have_skills or []
            self._questions = [
                {
                    "question": f"Tell me about your experience with {skill}.",
                    "category": "technical",
                    "dimension": "technical",
                }
                for skill in skills[: self.ctx.max_questions]
            ]
            # Fallback if no skills provided
            if not self._questions:
                self._questions = [
                    {"question": "Tell me about your background and current role.", "category": "general", "dimension": "general"},
                    {"question": "What interests you about this position?", "category": "general", "dimension": "general"},
                    {"question": "Describe a challenging project you worked on recently.", "category": "general", "dimension": "general"},
                ]

        elif self.ctx.strategy:
            # Use pre-generated strategy (standard / deep)
            self._dimensions = self.ctx.strategy.get("dimensions", [])
            # Flatten questions across dimensions for quick access
            for dim in self._dimensions:
                for q in dim.get("questions", []):
                    self._questions.append({
                        "question": q.get("text", ""),
                        "category": dim.get("category", "general"),
                        "dimension": dim.get("name", "general"),
                        "context": q.get("context", ""),
                    })

        if not self._questions:
            # Ultimate fallback
            self._questions = [
                {"question": "Tell me about your background.", "category": "general", "dimension": "general"},
            ]

    # ──────────────────────────────────────────────────────────────────────────
    # Greeting & consent
    # ──────────────────────────────────────────────────────────────────────────

    async def get_greeting(self) -> str:
        """Generate greeting based on depth and personalisation."""
        name = self.ctx.candidate_name.split()[0] if self.ctx.candidate_name else "there"
        style_map = {
            "friendly": "warm, friendly, and conversational",
            "casual": "relaxed and casual but still professional",
            "professional": "polished, professional, and courteous",
        }
        _ = style_map.get(self.ctx.greeting_style, style_map["professional"])

        if self.ctx.depth == InterviewDepth.QUICK:
            return (
                f"Hi, is this {name}? This is {self.ctx.bot_name} calling from "
                f"{self.ctx.company_name} about the {self.ctx.jd_title or 'open'} position. "
                f"Do you have a few minutes for a quick screening call?"
            )
        else:
            duration = "10 to 15" if self.ctx.depth == InterviewDepth.STANDARD else "20 to 30"
            return (
                f"Hello {name}, this is ARIA, an AI interview assistant from "
                f"{self.ctx.company_name}. I'd like to conduct a {duration}-minute structured "
                f"interview for the {self.ctx.jd_title} role. Is now a good time?"
            )

    def _get_consent_prompt(self) -> str:
        if self.ctx.consent_script:
            return self.ctx.consent_script
        return (
            "Before we begin, I need to let you know that this call is being recorded "
            "for hiring evaluation purposes. Your responses will be used to assess your "
            "fit for the position. Do you consent to proceed with this recorded screening?"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Edge-case handling (ported from ConversationEngine.handle_edge_case)
    # ──────────────────────────────────────────────────────────────────────────

    def _detect_edge_case(self, text: str) -> Optional[str]:
        """
        Detect and handle edge cases in candidate speech.
        Returns a response string if an edge case was handled, else None.
        """
        stripped = text.strip()
        lower = stripped.lower()

        # Silence / no response
        if not stripped or lower in self._SILENCE_MARKERS:
            return "I didn't quite catch that. Could you repeat what you said?"

        # Candidate doesn't know / unsure
        if any(p in lower for p in self._DONT_KNOW_MARKERS):
            return "That's okay — let me rephrase. Can you think of any example, even from a different context?"

        # Candidate asks to reschedule
        if any(p in lower for p in self._RESCHEDULE_MARKERS):
            return (
                "No problem at all! I'll let the recruiting team know. "
                "They'll reach out to schedule a better time. Thanks for your time today!"
            )

        # Candidate asks about compensation
        if any(p in lower for p in self._SALARY_MARKERS):
            return (
                "Great question! The recruiter will be able to discuss compensation details "
                "during the next stage. For now, let's continue with the interview."
            )

        # Candidate asks if this is AI / robot
        if any(p in lower for p in self._AI_DETECT_MARKERS):
            return "Ha! I appreciate the question. Let's keep going — I'd love to hear more about your experience."

        # Very short / unclear response (single word that isn't yes/no)
        words = stripped.split()
        if len(words) <= 1 and lower not in {"yes", "no", "yeah", "nope", "sure", "okay", "ok"}:
            return "Could you elaborate a bit more on that?"

        return None  # No edge case

    def _is_negative(self, text: str) -> bool:
        """Detect negative / declining responses."""
        lower = text.lower().strip()
        return any(marker in lower for marker in self._NEGATIVE_MARKERS)

    def _is_consent_positive(self, text: str) -> bool:
        lower = text.lower().strip()
        return any(w in lower for w in self._CONSENT_POSITIVE)

    def _is_consent_negative(self, text: str) -> bool:
        lower = text.lower().strip()
        return any(w in lower for w in self._CONSENT_NEGATIVE)

    # ──────────────────────────────────────────────────────────────────────────
    # Main response handler
    # ──────────────────────────────────────────────────────────────────────────

    async def handle_response(self, candidate_text: str) -> Optional[str]:
        """
        Process candidate response and return next bot utterance.
        Returns None when interview should end.
        """
        self.ctx.transcript.append({
            "speaker": "candidate",
            "text": candidate_text,
            "timestamp": self.ctx.elapsed,
            "state": self.ctx.current_state.value,
        })

        if self.ctx.current_state == InterviewState.GREETING:
            return await self._handle_greeting_response(candidate_text)

        elif self.ctx.current_state == InterviewState.CONSENT:
            return await self._handle_consent_response(candidate_text)

        elif self.ctx.current_state == InterviewState.WARMUP:
            return await self._handle_warmup_response(candidate_text)

        elif self.ctx.current_state in (InterviewState.QUESTIONS, InterviewState.FOLLOW_UP):
            return await self._handle_question_response(candidate_text)

        elif self.ctx.current_state == InterviewState.WRAP_UP:
            return await self._handle_wrapup_response(candidate_text)

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # State handlers
    # ──────────────────────────────────────────────────────────────────────────

    async def _handle_greeting_response(self, text: str) -> str:
        if self._is_negative(text):
            self.ctx.current_state = InterviewState.ENDED
            return "No problem at all. We'll reach out at a better time. Thank you!"
        self.ctx.current_state = InterviewState.CONSENT
        return self._get_consent_prompt()

    async def _handle_consent_response(self, text: str) -> str:
        if self._is_consent_positive(text):
            self.ctx.consent_recorded = True
            if self.ctx.has_warmup:
                self.ctx.current_state = InterviewState.WARMUP
                return self._get_warmup_question()
            else:
                self.ctx.current_state = InterviewState.QUESTIONS
                return await self._get_next_question()
        elif self._is_consent_negative(text):
            self.ctx.consent_recorded = False
            self.ctx.current_state = InterviewState.ENDED
            return "I understand. Unfortunately, I cannot proceed without recording consent. Thank you for your time. Goodbye."
        else:
            return "I'm sorry, I didn't quite catch that. Do you consent to this call being recorded? Please say yes or no."

    async def _handle_warmup_response(self, text: str) -> str:
        """Process warmup answer and transition to dimension questions."""
        self.ctx.questions_asked.append({
            "dimension": "warmup",
            "category": "warmup",
            "question": self._get_warmup_question(),
            "response": text,
            "sequence": 0,
        })
        self.ctx.current_dimension_idx = 0
        self.ctx.current_question_idx = 0
        self.ctx.current_state = InterviewState.QUESTIONS
        return await self._get_next_question()

    async def _handle_question_response(self, text: str) -> Optional[str]:
        """Core question-handling logic: assess, follow-up, or advance."""
        # Edge-case detection (silence, "I don't know", salary, AI detection)
        edge = self._detect_edge_case(text)
        if edge:
            # If it's a reschedule request, end the call
            lower = text.strip().lower()
            if any(p in lower for p in self._RESCHEDULE_MARKERS):
                self.ctx.current_state = InterviewState.ENDED
                return edge

            # For other edge cases, respond and stay in current state
            self.history.append({"role": "user", "content": text})
            self.history.append({"role": "assistant", "content": edge})
            return edge

        # Record the answer against the current question
        if self.ctx.questions_asked:
            self.ctx.questions_asked[-1]["response"] = text
            self.ctx.questions_asked[-1]["response_duration"] = len(text.split()) / 2.5
        else:
            # First answer without a tracked question (shouldn't happen, but guard)
            self.ctx.questions_asked.append({
                "dimension": "general",
                "category": "general",
                "question": "(untracked)",
                "response": text,
                "sequence": 1,
            })

        # Check time budget (85% threshold → wrap-up)
        if self.ctx.started_at and self.ctx.elapsed > self.ctx.time_budget * 0.85:
            self.ctx.current_state = InterviewState.WRAP_UP
            return self._get_wrapup_message()

        # Follow-up logic (standard / deep only)
        if self.ctx.depth != InterviewDepth.QUICK and self.ctx.follow_up_count < self.ctx.max_follow_ups_per_question:
            if await self._should_follow_up(text):
                follow_up = await self._generate_follow_up(text)
                if follow_up:
                    self.ctx.follow_up_count += 1
                    self.ctx.current_state = InterviewState.FOLLOW_UP
                    self.ctx.questions_asked.append({
                        "dimension": self.ctx.questions_asked[-2].get("dimension") if len(self.ctx.questions_asked) >= 2 else "general",
                        "category": self.ctx.questions_asked[-2].get("category") if len(self.ctx.questions_asked) >= 2 else "general",
                        "question": follow_up,
                        "context": "follow_up",
                        "is_follow_up": True,
                        "sequence": len(self.ctx.questions_asked) + 1,
                    })
                    return follow_up

        # Reset follow-up counter and advance to next question
        self.ctx.follow_up_count = 0
        self.ctx.current_state = InterviewState.QUESTIONS

        # Advance question pointer
        self.ctx.current_question_idx += 1

        # Check if current dimension is exhausted → move to next
        if self.ctx.depth != InterviewDepth.QUICK and self._dimensions:
            if self.ctx.current_dimension_idx < len(self._dimensions):
                dim = self._dimensions[self.ctx.current_dimension_idx]
                dim_questions = dim.get("questions", [])
                if self.ctx.current_question_idx >= len(dim_questions):
                    # Move to next dimension
                    self.ctx.current_dimension_idx += 1
                    self.ctx.current_question_idx = 0

        # Check if all questions asked
        total_asked = len([q for q in self.ctx.questions_asked if not q.get("is_follow_up") and q.get("dimension") != "warmup"])
        if total_asked >= len(self._questions) or total_asked >= self.ctx.max_questions:
            self.ctx.current_state = InterviewState.WRAP_UP
            return self._get_wrapup_message()

        # Check if all dimensions exhausted (standard/deep)
        if self._dimensions and self.ctx.current_dimension_idx >= len(self._dimensions):
            self.ctx.current_state = InterviewState.WRAP_UP
            return self._get_wrapup_message()

        return await self._get_next_question()

    async def _handle_wrapup_response(self, text: str) -> str:
        """Handle the candidate's final question / comment during wrap-up."""
        # Record any final question from the candidate
        if text.strip():
            self.ctx.transcript.append({
                "speaker": "bot",
                "text": "Thank you for your time today. We'll be in touch with next steps. Have a great day!",
                "timestamp": self.ctx.elapsed,
                "state": "wrap_up",
            })
        self.ctx.current_state = InterviewState.ENDED
        self.ctx.wrap_up_reached = True
        return "Thank you for your time today. We'll be in touch with next steps. Have a great day!"

    # ──────────────────────────────────────────────────────────────────────────
    # Question retrieval
    # ──────────────────────────────────────────────────────────────────────────

    async def _get_next_question(self) -> str:
        """Get the next question based on depth and progress."""
        transition = random.choice(self._TRANSITIONS) if self.ctx.questions_asked else ""

        if self.ctx.depth == InterviewDepth.QUICK:
            # Linear: just pick the next question from the flat list
            idx = len([q for q in self.ctx.questions_asked if not q.get("is_follow_up")])
            if idx < len(self._questions):
                q = self._questions[idx]
                question_text = q.get("question", "")
            else:
                self.ctx.current_state = InterviewState.WRAP_UP
                return self._get_wrapup_message()
        else:
            # Dimension-based: pick from current dimension
            question_text = self._get_dimension_question()
            if question_text is None:
                self.ctx.current_state = InterviewState.WRAP_UP
                return self._get_wrapup_message()

        # Track the question we're about to ask
        self.ctx.questions_asked.append({
            "dimension": self._current_dimension_name(),
            "category": self._current_category(),
            "question": question_text,
            "sequence": len(self.ctx.questions_asked) + 1,
        })

        # For dimension transitions, add a prefix
        prefix = ""
        if self.ctx.current_question_idx == 0 and self.ctx.current_dimension_idx > 0 and self._dimensions:
            category = self._current_category()
            prefix = f"Let's move on to discuss {category}. "

        return transition + prefix + question_text

    def _get_dimension_question(self) -> Optional[str]:
        """Get next question from current dimension (standard/deep)."""
        if not self._dimensions:
            # No dimensions — fall back to flat list
            idx = self.ctx.current_question_idx
            if idx < len(self._questions):
                return self._questions[idx].get("question", "")
            return None

        if self.ctx.current_dimension_idx >= len(self._dimensions):
            return None

        dim = self._dimensions[self.ctx.current_dimension_idx]
        questions = dim.get("questions", [])

        if self.ctx.current_question_idx >= len(questions):
            return None

        q = questions[self.ctx.current_question_idx]
        return q.get("text", "")

    def _current_dimension_name(self) -> str:
        if self._dimensions and self.ctx.current_dimension_idx < len(self._dimensions):
            return self._dimensions[self.ctx.current_dimension_idx].get("name", "general")
        return "general"

    def _current_category(self) -> str:
        if self._dimensions and self.ctx.current_dimension_idx < len(self._dimensions):
            return self._dimensions[self.ctx.current_dimension_idx].get("category", "general")
        return "general"

    def _get_warmup_question(self) -> str:
        """Easy opening question to build rapport."""
        if self.ctx.strategy and self.ctx.strategy.get("warmup_question"):
            return self.ctx.strategy["warmup_question"]
        return (
            "Great, let's start with something easy. "
            "Could you briefly tell me about your current role and what interests you about this position?"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Follow-up logic (from RecruiterConversation)
    # ──────────────────────────────────────────────────────────────────────────

    async def _should_follow_up(self, response: str) -> bool:
        """Determine if a follow-up is needed based on answer quality."""
        if self.ctx.follow_up_count >= self.ctx.max_follow_ups_per_question:
            return False

        word_count = len(response.split())

        # Very short answer → follow up
        if word_count < 15:
            return True

        # Vague / hedging language in a short-ish answer
        lower = response.lower()
        if any(phrase in lower for phrase in self._VAGUE_PHRASES) and word_count < 40:
            return True

        return False

    async def _generate_follow_up(self, response: str) -> Optional[str]:
        """Generate a contextual follow-up question."""
        last_question = ""
        if self.ctx.questions_asked:
            last_question = self.ctx.questions_asked[-1].get("question", "")

        # Try LLM for dynamic follow-up (standard & deep)
        if self.llm and self.ctx.depth in (InterviewDepth.STANDARD, InterviewDepth.DEEP):
            try:
                prompt = (
                    f"You are a recruiter conducting a {self.ctx.depth.value} interview "
                    f"for a {self.ctx.jd_title} role.\n"
                    f"The candidate was asked: '{last_question}'\n"
                    f"They responded: '{response}'\n"
                    f"Generate ONE brief follow-up question to get more detail. "
                    f"Keep it conversational and under 30 words."
                )
                follow_up = await self.llm.generate(prompt)
                if follow_up and len(follow_up) < 200:
                    return follow_up.strip()
            except Exception as e:
                logger.warning("LLM follow-up generation failed: %s", e)

        # Fallback follow-ups
        word_count = len(response.split())
        if word_count < 15:
            return "Could you elaborate a bit more on that? Perhaps share a specific example?"
        return "That's interesting. Can you walk me through a specific situation where you applied that?"

    # ──────────────────────────────────────────────────────────────────────────
    # Wrap-up
    # ──────────────────────────────────────────────────────────────────────────

    def _get_wrapup_message(self) -> str:
        name = self.ctx.candidate_name.split()[0] if self.ctx.candidate_name else ""
        name_clause = f", {name}" if name else ""
        return (
            f"We've covered a lot of ground today{name_clause}. "
            f"Thank you for sharing your experiences and insights. "
            f"Is there anything else you'd like to add or any questions about the role?"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Completion & results
    # ──────────────────────────────────────────────────────────────────────────

    def is_complete(self) -> bool:
        return self.ctx.current_state == InterviewState.ENDED

    def get_result(self) -> Dict:
        """Package the interview result for backend callback."""
        return {
            "session_id": self.ctx.session_id,
            "duration_seconds": int(self.ctx.elapsed),
            "questions_asked": len(self.ctx.questions_asked),
            "consent_recorded": self.ctx.consent_recorded,
            "transcript": self.ctx.transcript,
            "questions_responses": self.ctx.questions_asked,
            "completion_reason": "natural" if self.ctx.wrap_up_reached else "time_limit",
            "depth": self.ctx.depth.value,
            "dimension_scores": self.ctx.dimension_scores,
        }

    def get_transcript(self) -> List[Dict]:
        return self.ctx.transcript

    def get_questions_responses(self) -> List[Dict]:
        return self.ctx.questions_asked

    # ──────────────────────────────────────────────────────────────────────────
    # LLM system prompt (used for full LLM-driven responses in free-form mode)
    # ──────────────────────────────────────────────────────────────────────────

    def build_system_prompt(self) -> str:
        """Build a full system prompt for LLM-driven conversation (optional mode)."""
        style_map = {
            "friendly": "warm, friendly, and conversational — like a helpful colleague",
            "casual": "relaxed and casual but still professional",
            "professional": "polished, professional, and courteous",
        }
        personality = style_map.get(self.ctx.greeting_style, style_map["professional"])

        prompt = (
            f"You are {self.ctx.bot_name}, a recruiter conducting a "
            f"{self.ctx.depth.value} interview for the position of "
            f"{self.ctx.jd_title or 'the open role'}. "
            f"You are speaking with {self.ctx.candidate_name}.\n\n"
            f"## Personality\n"
            f"- Be {personality}\n"
            f"- Use natural speech patterns: brief acknowledgments ('Uh-huh', 'Got it', 'That makes sense')\n"
            f"- Vary your transitions — don't repeat the same phrase\n"
            f"- Mirror the candidate's energy: if they're nervous, be extra warm\n\n"
            f"## Rules\n"
            f"- Ask ONE question at a time, then WAIT for the response\n"
            f"- Keep your responses to 1-3 sentences (this is a phone call, not an email)\n"
            f"- If the candidate gives a weak answer, gently probe: 'Can you tell me more about that?'\n"
            f"- If the candidate gives a strong answer, acknowledge it and move on\n"
            f"- Never mention AI, algorithms, or automated systems — you are a real recruiter\n"
            f"- If the candidate asks about salary/benefits, say you'll have the recruiter follow up\n"
            f"- Stay within the {self.ctx.time_budget // 60}-minute time budget\n"
        )
        return prompt
