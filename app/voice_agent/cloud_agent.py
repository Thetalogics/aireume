"""
ARIA Voice Screening — LiveKit Cloud Agent (Option B).

Pipeline (matches LiveKit Cloud dashboard):
  STT: Deepgram Nova-2
  TTS: Cartesia Sonic
  Turn detection: LiveKit Inference VAD + built-in turn handling

Screening logic stays in ARIA orchestrators (kit-driven or dynamic).
The cloud LLM is bypassed for kit mode via a custom ``llm_node``.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure `app.*` imports work in LiveKit job subprocesses (Docker CMD runs a file path).
_APP_ROOT = Path(__file__).resolve().parents[2]
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

import asyncio
import json
import logging
import os
import re
import time
import traceback
from typing import Any, AsyncIterable

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, cli, inference

from app.voice_agent.voice_flow_log import log_step, metadata_summary

try:
    from livekit.agents.voice import room_io

    RoomInputOptions = room_io.RoomInputOptions
except ImportError:
    RoomInputOptions = None  # type: ignore

try:
    from livekit.plugins import noise_cancellation
except ImportError:
    noise_cancellation = None  # type: ignore

load_dotenv()

logger = logging.getLogger("aria.cloud_agent")

AGENT_NAME = os.getenv("LIVEKIT_AGENT_NAME", "ARIA")
STT_MODEL = os.getenv("LIVEKIT_STT_MODEL", "deepgram/nova-2")
STT_LANGUAGE = os.getenv("LIVEKIT_STT_LANGUAGE", "en")
# sonic-1 / gemma-3-27b-it are not on LiveKit Inference (404 at session.start).
TTS_MODEL = os.getenv("LIVEKIT_TTS_MODEL", "cartesia/sonic-3")
TTS_VOICE = (
    os.getenv("LIVEKIT_TTS_VOICE", "").strip()
    or "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc"
)


def get_livekit_llm_model() -> str:
    from app.backend.services.llm_service import require_env

    return require_env("LIVEKIT_LLM_MODEL", purpose="LiveKit cloud agent LLM slot")


ARIA_BACKEND_URL = os.getenv("ARIA_BACKEND_URL", "http://backend:8000")
INTERNAL_SERVICE_SECRET = os.getenv("INTERNAL_SERVICE_SECRET", "dev-internal-service-secret")
INTERNAL_HEADERS = {"X-Internal-Secret": INTERNAL_SERVICE_SECRET}
CLOSE_PAUSE_S = float(os.getenv("ARIA_CALL_CLOSE_PAUSE_S", "1.5"))
CONSOLE_SIGNOFF = (
    "This concludes your screening session. You can close this window now. Goodbye!"
)

# Module-level server + entrypoint so job subprocesses can pickle the handler.
server = AgentServer()


def _extract_last_user_message(chat_ctx) -> str:
    items = list(getattr(chat_ctx, "items", []) or [])
    for item in reversed(items):
        role = getattr(item, "role", None)
        if role == "user":
            text = getattr(item, "text_content", None) or getattr(item, "content", "")
            if isinstance(text, list):
                text = " ".join(str(part) for part in text)
            return str(text or "").strip()
    return ""


async def _notify_backend_complete(
    session_id: str,
    result: dict,
    backend_url: str,
    expected_generation: int,
) -> None:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{backend_url}/api/interviews/internal/complete",
                json={
                    "session_id": session_id,
                    "expected_generation": expected_generation,
                    "event_id": f"voice-{session_id}-{expected_generation}",
                    "result": result,
                },
                headers=INTERNAL_HEADERS,
            )
            resp.raise_for_status()
            log_step(logger, session_id, "backend_complete_ok")
    except Exception as e:
        log_step(
            logger,
            session_id,
            "backend_complete_failed",
            level=logging.ERROR,
            error=str(e),
        )


async def _update_session(
    session_id: int,
    updates: dict,
    backend_url: str,
    expected_generation: int,
) -> None:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(
                f"{backend_url}/api/voice/sessions/{session_id}",
                json={"expected_generation": expected_generation, **updates},
                headers=INTERNAL_HEADERS,
            )
            log_step(
                logger,
                session_id,
                "backend_session_patch",
                status=resp.status_code,
                updates=",".join(sorted(updates.keys())),
            )
    except Exception as e:
        log_step(
            logger,
            session_id,
            "backend_session_patch_failed",
            level=logging.WARNING,
            error=str(e),
            updates=",".join(sorted(updates.keys())),
        )


async def _wait_for_speech_idle(session: AgentSession, timeout_s: float = 45.0) -> None:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        handle = session.current_speech
        if handle is None:
            return
        await handle
        await asyncio.sleep(0.05)


async def _await_session_closed(session: AgentSession) -> None:
    close_future: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    @session.once("close")
    def _on_close(_ev) -> None:
        if not close_future.done():
            close_future.set_result(None)

    session.shutdown(drain=True)
    await close_future


async def _graceful_call_close(
    *,
    ctx: JobContext,
    session: AgentSession,
    orchestrator,
    console_mode: bool,
    participant_identity: str,
    session_id: int | str,
) -> None:
    closing_msg = None
    if hasattr(orchestrator, "force_closing_message"):
        closing_msg = orchestrator.force_closing_message()

    if closing_msg:
        log_step(logger, session_id, "agent_closing_message_send", chars=len(closing_msg))
        handle = session.say(closing_msg, allow_interruptions=False)
        await handle
        log_step(logger, session_id, "agent_closing_message_sent")
    else:
        await _wait_for_speech_idle(session)

    if console_mode:
        log_step(logger, session_id, "agent_console_signoff_send")
        handle = session.say(CONSOLE_SIGNOFF, allow_interruptions=False)
        await handle
        log_step(logger, session_id, "agent_console_signoff_sent")

    await asyncio.sleep(CLOSE_PAUSE_S)
    log_step(logger, session_id, "agent_call_close_pause_done", pause_s=CLOSE_PAUSE_S)

    if not console_mode and participant_identity:
        try:
            from livekit.api import RoomParticipantIdentity

            await ctx.api.room.remove_participant(
                RoomParticipantIdentity(
                    room=ctx.room.name,
                    identity=participant_identity,
                )
            )
            log_step(logger, session_id, "agent_sip_hangup_ok", identity=participant_identity)
        except Exception as hangup_err:
            log_step(
                logger,
                session_id,
                "agent_sip_hangup_failed",
                level=logging.WARNING,
                error=str(hangup_err),
            )

    try:
        await ctx.delete_room()
        log_step(logger, session_id, "agent_room_deleted", room=ctx.room.name)
    except Exception as room_err:
        log_step(
            logger,
            session_id,
            "agent_room_delete_failed",
            level=logging.WARNING,
            error=str(room_err),
        )

    await _await_session_closed(session)
    log_step(logger, session_id, "agent_session_closed")


def _build_orchestrator(metadata: dict[str, Any]):
    from app.voice_agent.kit_orchestrator import KitDrivenOrchestrator
    from app.voice_agent.orchestrator import InterviewOrchestrator, OrchestratorContext

    interview_config = metadata.get("interview_config") or {}
    depth = metadata.get("depth") or "quick"
    duration_minutes = interview_config.get("duration_minutes")
    if duration_minutes and isinstance(duration_minutes, (int, float)) and duration_minutes > 0:
        duration_s = int(duration_minutes * 60)
    else:
        duration_s = {"quick": 300, "standard": 900, "deep": 1200}.get(depth, 1200)

    tenant_config = metadata.get("tenant_config") or {}
    orch_ctx = OrchestratorContext(
        session_id=str(metadata["session_id"]),
        candidate_name=metadata.get("candidate_name") or "there",
        company_name=tenant_config.get("company_name", "the company"),
        jd_title=metadata.get("jd_title") or "",
        bot_name=tenant_config.get("bot_name", "ARIA"),
        tenant_id=metadata.get("tenant_id"),
        candidate_id=metadata.get("candidate_id"),
        phone_number=metadata.get("phone_number"),
        result_generation=int(metadata["result_generation"]),
        candidate_context=metadata.get("candidate_context") or {},
        role_context={
            "title": metadata.get("jd_title") or "",
            "required_skills": metadata.get("jd_must_have_skills") or [],
        },
        screening_result=metadata.get("interview_strategy") or {},
        total_duration_s=duration_s,
        consent_script=tenant_config.get("consent_script"),
        use_custom_interview_opening=bool(tenant_config.get("use_custom_interview_opening")),
        interview_opening_script=tenant_config.get("interview_opening_script"),
    )

    kit_questions = []
    interview_kit = metadata.get("interview_kit") or {}
    if isinstance(interview_kit, dict):
        kit_questions = interview_kit.get("questions") or []

    if kit_questions:
        log_step(
            logger,
            orch_ctx.session_id,
            "orchestrator_kit_ready",
            mode="kit",
            questions=len(kit_questions),
        )
        thread_transitions = interview_kit.get("thread_transitions") or {}
        return KitDrivenOrchestrator(
            orch_ctx, kit_questions, thread_transitions=thread_transitions
        ), orch_ctx, "kit"

    log_step(
        logger,
        orch_ctx.session_id,
        "orchestrator_dynamic_fallback",
        mode="dynamic",
        level=logging.WARNING,
    )
    return InterviewOrchestrator(orch_ctx, None, None), orch_ctx, "dynamic"


class ScreeningAgent(Agent):
    """ARIA screening agent — orchestrator drives replies, cloud handles STT/TTS."""

    def __init__(self, orchestrator, orch_ctx, mode: str, **kwargs):
        super().__init__(**kwargs)
        self.orchestrator = orchestrator
        self.orch_ctx = orch_ctx
        self.mode = mode

    async def on_enter(self) -> None:
        # Greeting is sent after the SIP participant answers (see entrypoint).
        return

    async def llm_node(self, chat_ctx, tools, model_settings) -> AsyncIterable[str]:
        user_text = _extract_last_user_message(chat_ctx)
        if not user_text:
            return

        bot_text = await self.orchestrator.handle_candidate_response(user_text)
        if bot_text:
            yield bot_text
        if self.orchestrator.is_complete():
            logger.info("Interview complete session=%s", self.orch_ctx.session_id)


@server.rtc_session(agent_name=AGENT_NAME)
async def aria_rtc_entrypoint(ctx: JobContext) -> None:
    session_id: int | str | None = None
    backend_url = ARIA_BACKEND_URL
    try:
        raw = ctx.job.metadata or "{}"
        log_step(
            logger,
            None,
            "agent_job_received",
            room=ctx.room.name,
            job_id=getattr(ctx.job, "id", ""),
            raw_metadata_bytes=len(raw.encode("utf-8")) if isinstance(raw, str) else 0,
        )
        await ctx.connect()
        metadata = json.loads(raw) if isinstance(raw, str) else (raw or {})
        session_id = int(metadata.get("session_id") or 0)
        participant_identity = metadata.get("participant_identity") or f"candidate-{session_id}"
        phone_number = metadata.get("phone_number") or ""
        sip_trunk_id = metadata.get("sip_trunk_id") or ""
        sip_phone = re.sub(r"[^\d+]", "", phone_number)
        # Browser console tests never include phone_number — do not infer SIP from agent env.
        console_mode = bool(metadata.get("console_mode")) or not sip_phone
        log_step(
            logger,
            session_id,
            "agent_room_connected",
            room=ctx.room.name,
            trunk=sip_trunk_id,
            phone=phone_number,
            participant_identity=participant_identity,
            console_mode=console_mode,
            **metadata_summary(metadata),
        )

        orchestrator, orch_ctx, mode = _build_orchestrator(metadata)
        backend_url = metadata.get("aria_backend_url") or ARIA_BACKEND_URL
        log_step(
            logger,
            session_id,
            "agent_orchestrator_ready",
            mode=mode,
            backend_url=backend_url,
        )

        session = AgentSession(
            stt=inference.STT(STT_MODEL, language=STT_LANGUAGE),
            tts=inference.TTS(TTS_MODEL, voice=TTS_VOICE),
            vad=inference.VAD(),
            llm=inference.LLM(get_livekit_llm_model()),
            min_endpointing_delay=0.4,
            max_endpointing_delay=1.2,
        )

        agent = ScreeningAgent(
            orchestrator=orchestrator,
            orch_ctx=orch_ctx,
            mode=mode,
            instructions=(
                "You are ARIA, a professional phone screener for hiring. "
                "Keep responses concise and conversational for phone calls."
            ),
        )

        start_kwargs: dict[str, Any] = {"agent": agent, "room": ctx.room}
        if RoomInputOptions and noise_cancellation:
            start_kwargs["room_input_options"] = RoomInputOptions(
                noise_cancellation=noise_cancellation.BVCTelephony(),
            )

        log_step(logger, session_id, "agent_session_starting", console_mode=console_mode)
        session_started = asyncio.create_task(session.start(**start_kwargs))

        if console_mode:
            log_step(
                logger,
                session_id,
                "agent_console_mode",
                detail="Skipping SIP dial; waiting for browser participant",
            )
        else:
            try:
                from livekit.api import CreateSIPParticipantRequest

                if not sip_phone or not sip_trunk_id:
                    raise ValueError(
                        f"Missing phone_number or sip_trunk_id for session {session_id}"
                    )

                log_step(
                    logger,
                    session_id,
                    "agent_sip_dial_start",
                    phone=sip_phone,
                    trunk=sip_trunk_id,
                    participant_identity=participant_identity,
                    room=ctx.room.name,
                )
                dial_started = time.perf_counter()
                await ctx.api.sip.create_sip_participant(
                    CreateSIPParticipantRequest(
                        sip_trunk_id=sip_trunk_id,
                        sip_call_to=sip_phone,
                        room_name=ctx.room.name,
                        participant_identity=participant_identity,
                        participant_name=metadata.get("candidate_name") or "Candidate",
                        hide_phone_number=False,
                        wait_until_answered=True,
                    )
                )
                log_step(
                    logger,
                    session_id,
                    "agent_sip_dial_answered",
                    elapsed_ms=int((time.perf_counter() - dial_started) * 1000),
                )
            except Exception as dial_err:
                log_step(
                    logger,
                    session_id,
                    "agent_sip_dial_failed",
                    level=logging.ERROR,
                    error=str(dial_err),
                )
                await _update_session(
                    session_id,
                    {"status": "failed", "error_log": f"SIP dial failed: {dial_err}"},
                    backend_url,
                    orch_ctx.result_generation,
                )
                session_started.cancel()
                return

        try:
            await session_started
        except Exception as session_err:
            log_step(
                logger,
                session_id,
                "agent_session_start_failed",
                level=logging.ERROR,
                error=str(session_err),
                stt=STT_MODEL,
                tts=TTS_MODEL,
                llm=get_livekit_llm_model(),
                traceback=traceback.format_exc(),
            )
            raise
        log_step(logger, session_id, "agent_session_started")
        if console_mode:
            participant = await ctx.wait_for_participant()
        else:
            participant = await ctx.wait_for_participant(identity=participant_identity)
        log_step(
            logger,
            session_id,
            "agent_participant_active",
            identity=participant.identity,
            participant_kind=getattr(participant, "kind", ""),
        )
        await _update_session(
            session_id,
            {"status": "in_progress"},
            backend_url,
            orch_ctx.result_generation,
        )

        greeting = await orchestrator.start()
        orch_ctx.transcript.append(
            {
                "speaker": "bot",
                "text": greeting,
                "timestamp": 0.0,
                "stage": "introduction",
            }
        )
        log_step(logger, session_id, "agent_greeting_send", chars=len(greeting))
        await session.say(greeting, allow_interruptions=True)
        log_step(logger, session_id, "agent_greeting_sent")

        try:
            while not orchestrator.is_complete():
                if orch_ctx.time_remaining < 10:
                    log_step(logger, session_id, "agent_time_budget_reached")
                    break
                await asyncio.sleep(1)
        finally:
            await _graceful_call_close(
                ctx=ctx,
                session=session,
                orchestrator=orchestrator,
                console_mode=console_mode,
                participant_identity=participant_identity,
                session_id=session_id,
            )
            result = orchestrator.get_result()
            result["voice_pipeline"] = "livekit_cloud"
            result["stt_model"] = STT_MODEL
            result["tts_model"] = TTS_MODEL
            await _notify_backend_complete(
                orch_ctx.session_id,
                result,
                backend_url,
                orch_ctx.result_generation,
            )
            await _update_session(
                session_id,
                {
                    "status": "completed",
                    "duration_seconds": int(orch_ctx.elapsed),
                },
                backend_url,
                orch_ctx.result_generation,
            )
            log_step(
                logger,
                session_id,
                "agent_session_complete",
                duration_s=int(orch_ctx.elapsed),
                answers=len(orch_ctx.questions_responses),
            )
    except Exception as fatal_err:
        log_step(
            logger,
            session_id,
            "agent_job_failed",
            level=logging.ERROR,
            error=str(fatal_err),
            traceback=traceback.format_exc(),
        )
        if session_id:
            try:
                await _update_session(
                    int(session_id),
                    {"status": "failed", "error_log": str(fatal_err)},
                    backend_url,
                    int(metadata["result_generation"]),
                )
            except Exception:
                pass
        raise


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    log_step(
        logger,
        None,
        "agent_worker_starting",
        agent_name=AGENT_NAME,
        pythonpath=os.getenv("PYTHONPATH", ""),
        cwd=os.getcwd(),
    )
    log_step(
        logger,
        None,
        "agent_worker_ready",
        agent_name=AGENT_NAME,
        pythonpath=os.getenv("PYTHONPATH", ""),
        stt=STT_MODEL,
        tts=TTS_MODEL,
        tts_voice=TTS_VOICE,
        llm=get_livekit_llm_model(),
    )
    cli.run_app(server)


if __name__ == "__main__":
    main()
