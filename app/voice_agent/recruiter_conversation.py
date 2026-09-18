"""
DEPRECATED: This module is superseded by conversation.py (UnifiedConversation).
Kept for reference during transition period. Will be removed in next release.

AI Recruiter Conversation Module

Advanced interview state machine that conducts multi-dimensional 
candidate interviews via voice, with dynamic question adaptation.
"""

import logging
import json
import time
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict

logger = logging.getLogger("aria.recruiter_conversation")


class RecruiterState(Enum):
    GREETING = "greeting"
    CONSENT = "consent"
    WARMUP = "warmup"
    TECHNICAL = "technical"
    BEHAVIORAL = "behavioral"
    CULTURAL = "cultural"
    WRAP_UP = "wrap_up"
    ANALYSIS = "analysis"
    ENDED = "ended"


@dataclass
class RecruiterContext:
    """Context for an AI Recruiter interview session."""
    session_id: str
    tenant_id: int
    candidate_id: int
    candidate_name: str
    phone_number: str
    jd_title: str
    interview_strategy: dict  # Pre-generated strategy with questions
    interview_config: dict    # Duration, focus areas
    result_generation: int = 1
    
    # Runtime state
    current_state: RecruiterState = RecruiterState.GREETING
    transcript: List[dict] = field(default_factory=list)
    questions_asked: List[dict] = field(default_factory=list)
    current_dimension_idx: int = 0
    current_question_idx: int = 0
    follow_up_count: int = 0
    max_follow_ups_per_question: int = 2
    consent_recorded: bool = False
    start_time: float = 0.0
    target_duration_seconds: int = 900  # 15 min default
    wrap_up_reached: bool = False
    
    # Evaluation tracking
    dimension_scores: Dict[str, list] = field(default_factory=dict)


class RecruiterConversation:
    """
    Advanced conversation orchestrator for AI Recruiter interviews.
    
    Differences from basic VoiceScreening:
    - Dynamic question flow (adapts based on answers)
    - Multi-dimensional assessment (technical, behavioral, cultural)
    - Follow-up question generation
    - Time management (stays within target duration)
    - Real-time answer quality detection
    """
    
    def __init__(self, context: RecruiterContext, speech_client, llm_client=None):
        self.context = context
        self.speech = speech_client
        self.llm = llm_client  # For dynamic follow-up generation
        self.state_handlers = {
            RecruiterState.GREETING: self._handle_greeting,
            RecruiterState.CONSENT: self._handle_consent,
            RecruiterState.WARMUP: self._handle_warmup,
            RecruiterState.TECHNICAL: self._handle_dimension,
            RecruiterState.BEHAVIORAL: self._handle_dimension,
            RecruiterState.CULTURAL: self._handle_dimension,
            RecruiterState.WRAP_UP: self._handle_wrap_up,
            RecruiterState.ANALYSIS: self._handle_analysis,
        }
    
    async def start(self):
        """Begin the interview - called after call connects."""
        self.context.start_time = time.time()
        return await self._transition_to(RecruiterState.GREETING)
    
    async def handle_candidate_speech(self, text: str) -> Optional[str]:
        """
        Process candidate's speech and determine next action.
        Returns bot's response text (to be synthesized to speech).
        """
        # Record in transcript
        self.context.transcript.append({
            "speaker": "candidate",
            "text": text,
            "timestamp": time.time() - self.context.start_time,
            "state": self.context.current_state.value
        })
        
        # Route to current state handler
        handler = self.state_handlers.get(self.context.current_state)
        if handler:
            return await handler(text)
        return None
    
    async def _transition_to(self, new_state: RecruiterState) -> str:
        """Transition to a new state and return the opening message."""
        self.context.current_state = new_state
        logger.info(f"[{self.context.session_id}] State transition -> {new_state.value}")
        
        if new_state == RecruiterState.GREETING:
            return self._get_greeting()
        elif new_state == RecruiterState.CONSENT:
            return self._get_consent_prompt()
        elif new_state == RecruiterState.WARMUP:
            return self._get_warmup_question()
        elif new_state in (RecruiterState.TECHNICAL, RecruiterState.BEHAVIORAL, RecruiterState.CULTURAL):
            return self._get_next_dimension_question()
        elif new_state == RecruiterState.WRAP_UP:
            self.context.wrap_up_reached = True
            return self._get_wrap_up()
        elif new_state == RecruiterState.ANALYSIS:
            return None  # Silent - post-call processing
        return ""
    
    def _get_greeting(self) -> str:
        """Generate personalized greeting."""
        name = self.context.candidate_name.split()[0] if self.context.candidate_name else "there"
        return (
            f"Hello {name}, this is ARIA, the AI recruitment assistant. "
            f"Thank you for taking the time to speak with me today regarding the "
            f"{self.context.jd_title} position. "
            f"This conversation will take about {self.context.target_duration_seconds // 60} minutes. "
            f"Before we begin, I need to let you know that this call will be recorded for assessment purposes."
        )
    
    def _get_consent_prompt(self) -> str:
        return "Do you consent to this call being recorded? You can say yes or no."
    
    def _get_warmup_question(self) -> str:
        """Easy opening question to build rapport."""
        warmup = self.context.interview_strategy.get("warmup_question")
        if warmup:
            return warmup
        return (
            f"Great, let's start with something easy. "
            f"Could you briefly tell me about your current role and what interests you about this position?"
        )
    
    def _get_next_dimension_question(self) -> str:
        """Get next question from the strategy for current dimension."""
        strategy = self.context.interview_strategy
        dimensions = strategy.get("dimensions", [])
        
        if self.context.current_dimension_idx >= len(dimensions):
            return None  # No more dimensions
        
        dimension = dimensions[self.context.current_dimension_idx]
        questions = dimension.get("questions", [])
        
        if self.context.current_question_idx >= len(questions):
            return None  # No more questions in this dimension
        
        question = questions[self.context.current_question_idx]
        self.context.questions_asked.append({
            "dimension": dimension.get("name"),
            "category": dimension.get("category"),
            "question": question.get("text"),
            "context": question.get("context"),
            "sequence": len(self.context.questions_asked) + 1
        })
        
        # Add transition phrase if first question in new dimension
        prefix = ""
        if self.context.current_question_idx == 0 and self.context.current_dimension_idx > 0:
            category = dimension.get("category", "next area")
            prefix = f"Let's move on to discuss {category}. "
        
        return prefix + question.get("text", "")
    
    async def _handle_greeting(self, text: str) -> str:
        """After greeting, move to consent."""
        return await self._transition_to(RecruiterState.CONSENT)
    
    async def _handle_consent(self, text: str) -> str:
        """Process consent response."""
        text_lower = text.lower().strip()
        if any(w in text_lower for w in ["yes", "yeah", "sure", "okay", "ok", "consent", "agree"]):
            self.context.consent_recorded = True
            return await self._transition_to(RecruiterState.WARMUP)
        elif any(w in text_lower for w in ["no", "don't", "refuse", "decline"]):
            self.context.consent_recorded = False
            return "I understand. Unfortunately, I cannot proceed without recording consent. Thank you for your time. Goodbye."
        else:
            return "I'm sorry, I didn't quite catch that. Do you consent to this call being recorded? Please say yes or no."
    
    async def _handle_warmup(self, text: str) -> str:
        """Process warmup response and transition to first dimension."""
        # Record warmup response
        self.context.questions_asked.append({
            "dimension": "warmup",
            "category": "warmup", 
            "question": self._get_warmup_question(),
            "response": text,
            "sequence": 0
        })
        
        # Transition to first assessment dimension
        self.context.current_dimension_idx = 0
        self.context.current_question_idx = 0
        return await self._transition_to(RecruiterState.TECHNICAL)
    
    async def _handle_dimension(self, text: str) -> str:
        """Handle response during dimension assessment (technical/behavioral/cultural)."""
        # Record the response
        if self.context.questions_asked:
            self.context.questions_asked[-1]["response"] = text
            self.context.questions_asked[-1]["response_duration"] = len(text.split()) / 2.5  # Approx seconds
        
        # Check if we should ask a follow-up
        if await self._should_follow_up(text):
            follow_up = await self._generate_follow_up(text)
            if follow_up:
                self.context.follow_up_count += 1
                self.context.questions_asked.append({
                    "dimension": self.context.questions_asked[-1].get("dimension"),
                    "category": self.context.questions_asked[-1].get("category"),
                    "question": follow_up,
                    "context": "follow_up",
                    "is_follow_up": True,
                    "sequence": len(self.context.questions_asked) + 1
                })
                return follow_up
        
        # Reset follow-up counter and move to next question
        self.context.follow_up_count = 0
        self.context.current_question_idx += 1
        
        # Check time - if running long, wrap up
        elapsed = time.time() - self.context.start_time
        if elapsed > self.context.target_duration_seconds * 0.85:
            return await self._transition_to(RecruiterState.WRAP_UP)
        
        # Check if more questions in current dimension
        strategy = self.context.interview_strategy
        dimensions = strategy.get("dimensions", [])
        
        if self.context.current_dimension_idx < len(dimensions):
            dimension = dimensions[self.context.current_dimension_idx]
            questions = dimension.get("questions", [])
            
            if self.context.current_question_idx < len(questions):
                return self._get_next_dimension_question()
        
        # Move to next dimension
        self.context.current_dimension_idx += 1
        self.context.current_question_idx = 0
        
        # Determine next state based on dimension index
        dimension_states = [RecruiterState.TECHNICAL, RecruiterState.BEHAVIORAL, RecruiterState.CULTURAL]
        if self.context.current_dimension_idx < len(dimensions):
            next_state = dimension_states[min(self.context.current_dimension_idx, len(dimension_states) - 1)]
            return await self._transition_to(next_state)
        
        # All dimensions complete
        return await self._transition_to(RecruiterState.WRAP_UP)
    
    async def _should_follow_up(self, response: str) -> bool:
        """Determine if a follow-up is needed based on answer quality."""
        if self.context.follow_up_count >= self.context.max_follow_ups_per_question:
            return False
        
        # Simple heuristics (can be enhanced with LLM)
        word_count = len(response.split())
        if word_count < 15:  # Very short answer
            return True
        
        # Check for vague indicators
        vague_phrases = ["i think", "maybe", "i'm not sure", "kind of", "sort of"]
        if any(phrase in response.lower() for phrase in vague_phrases) and word_count < 40:
            return True
        
        return False
    
    async def _generate_follow_up(self, response: str) -> Optional[str]:
        """Generate a contextual follow-up question."""
        if not self.context.questions_asked:
            return None
        
        last_question = self.context.questions_asked[-1].get("question", "")
        
        # Try LLM for dynamic follow-up
        if self.llm:
            try:
                prompt = (
                    f"The candidate was asked: '{last_question}'\n"
                    f"They responded: '{response}'\n"
                    f"Generate ONE brief follow-up question to get more detail. "
                    f"Keep it conversational and under 30 words."
                )
                follow_up = await self.llm.generate(prompt)
                if follow_up and len(follow_up) < 200:
                    return follow_up.strip()
            except Exception as e:
                logger.warning(f"LLM follow-up generation failed: {e}")
        
        # Fallback follow-ups
        word_count = len(response.split())
        if word_count < 15:
            return "Could you elaborate a bit more on that? Perhaps share a specific example?"
        return "That's interesting. Can you walk me through a specific situation where you applied that?"
    
    async def _handle_wrap_up(self, text: str) -> str:
        """Handle wrap-up phase - acknowledge and end the interview."""
        self.context.current_state = RecruiterState.ANALYSIS
        return (
            "Thank you so much for your time today. You've given me excellent insights into your "
            "background and experience. Our team will review this conversation along with your "
            "application materials and get back to you soon. Have a great day!"
        )
    
    async def _handle_analysis(self, text: str) -> Optional[str]:
        """Post-call - no more interaction."""
        return None
    
    def _get_wrap_up(self) -> str:
        """Wrap up message."""
        name = self.context.candidate_name.split()[0] if self.context.candidate_name else ""
        return (
            f"We've covered a lot of ground today{', ' + name if name else ''}. "
            f"Thank you for sharing your experiences and insights. "
            f"Is there anything else you'd like to add or any questions about the role?"
        )
    
    def get_transcript(self) -> list:
        """Return the full conversation transcript."""
        return self.context.transcript
    
    def get_questions_responses(self) -> list:
        """Return structured Q&A pairs for evaluation."""
        return self.context.questions_asked
    
    def is_complete(self) -> bool:
        """Check if interview has ended."""
        return self.context.current_state in (RecruiterState.ENDED, RecruiterState.ANALYSIS)
    
    async def end_call(self) -> dict:
        """Finalize the interview and return summary."""
        self.context.current_state = RecruiterState.ENDED
        elapsed = time.time() - self.context.start_time
        
        return {
            "session_id": self.context.session_id,
            "duration_seconds": int(elapsed),
            "questions_asked": len(self.context.questions_asked),
            "consent_recorded": self.context.consent_recorded,
            "transcript": self.context.transcript,
            "questions_responses": self.context.questions_asked,
            "completion_reason": "natural" if self.context.wrap_up_reached else "time_limit"
        }
