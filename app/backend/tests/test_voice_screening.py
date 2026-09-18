"""
Tests for voice screening API routes:
  GET    /api/voice/settings
  PUT    /api/voice/settings
  POST   /api/voice/schedule
  GET    /api/voice/sessions
  GET    /api/voice/sessions/{id}
"""
import sys
import types
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# ── Ensure voice_call_scheduler is importable even without apscheduler ────────
# Prefer the real package when installed. Checking sys.modules alone would
# inject a non-package stub during collection and break later imports of
# apscheduler.triggers (e.g. services.scheduler).
try:
    import apscheduler  # noqa: F401
except ImportError:
    _fake_ap = types.ModuleType("apscheduler")
    _fake_ap.__path__ = []
    _fake_schedulers = types.ModuleType("apscheduler.schedulers")
    _fake_schedulers.__path__ = []
    _fake_bg = types.ModuleType("apscheduler.schedulers.background")
    _fake_bg.BackgroundScheduler = MagicMock
    _fake_triggers = types.ModuleType("apscheduler.triggers")
    _fake_triggers.__path__ = []
    _fake_interval = types.ModuleType("apscheduler.triggers.interval")
    _fake_interval.IntervalTrigger = MagicMock
    _fake_ap.schedulers = _fake_schedulers
    _fake_schedulers.background = _fake_bg
    _fake_ap.triggers = _fake_triggers
    _fake_triggers.interval = _fake_interval
    sys.modules["apscheduler"] = _fake_ap
    sys.modules["apscheduler.schedulers"] = _fake_schedulers
    sys.modules["apscheduler.schedulers.background"] = _fake_bg
    sys.modules["apscheduler.triggers"] = _fake_triggers
    sys.modules["apscheduler.triggers.interval"] = _fake_interval

from app.backend.models.db_models import (
    Candidate, RoleTemplate, VoiceTenantConfig, VoiceScreeningSession, VoiceTranscriptEntry,
)


@pytest.fixture
def auth_client(auth_client_with_enterprise_plan):
    """Voice routes require ai_interviews (Enterprise plan)."""
    return auth_client_with_enterprise_plan


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _create_candidate(db, tenant_id, name="Voice Candidate", email="voice@example.com"):
    c = Candidate(tenant_id=tenant_id, name=name, email=email)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _create_jd(db, tenant_id, name="Voice JD"):
    jd = RoleTemplate(tenant_id=tenant_id, name=name, jd_text="Python engineer role")
    db.add(jd)
    db.commit()
    db.refresh(jd)
    return jd


def _get_tenant_id(auth_client, db):
    """Extract tenant_id from the authenticated user behind auth_client."""
    me = auth_client.get("/api/auth/me")
    assert me.status_code == 200
    return me.json()["user"]["tenant_id"]


# ─── GET /api/voice/settings ──────────────────────────────────────────────────

class TestGetVoiceSettings:

    def test_requires_auth(self, client):
        """Unauthenticated request should return 401."""
        resp = client.get("/api/voice/settings")
        assert resp.status_code == 401

    def test_auto_creates_default_config(self, auth_client, db):
        """First GET should auto-create a default VoiceTenantConfig."""
        tenant_id = _get_tenant_id(auth_client, db)
        # Ensure no config exists yet
        existing = db.query(VoiceTenantConfig).filter(VoiceTenantConfig.tenant_id == tenant_id).first()
        assert existing is None

        resp = auth_client.get("/api/voice/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bot_name"] == "ARIA Assistant"
        assert data["bot_voice_gender"] == "female"
        assert data["greeting_style"] == "professional"
        assert data["call_duration_min"] == 5
        assert data["call_duration_max"] == 7
        assert data["max_retries"] == 3
        assert data["timezone"] == "UTC"
        assert data["follow_up_aggressiveness"] == "medium"
        assert data["assessment_detail_level"] == "full"

    def test_returns_existing_config(self, auth_client, db):
        """Subsequent GETs should return the same config without creating duplicates."""
        tenant_id = _get_tenant_id(auth_client, db)

        resp1 = auth_client.get("/api/voice/settings")
        assert resp1.status_code == 200
        config_id = resp1.json()["id"]

        resp2 = auth_client.get("/api/voice/settings")
        assert resp2.status_code == 200
        assert resp2.json()["id"] == config_id

        # Only one config should exist
        count = db.query(VoiceTenantConfig).filter(VoiceTenantConfig.tenant_id == tenant_id).count()
        assert count == 1


# ─── PUT /api/voice/settings ──────────────────────────────────────────────────

class TestUpdateVoiceSettings:

    def test_requires_auth(self, client):
        """Unauthenticated request should return 401."""
        resp = client.put("/api/voice/settings", json={"bot_name": "Test"})
        assert resp.status_code == 401

    def test_creates_config_if_missing(self, auth_client, db):
        """PUT should auto-create config if none exists."""
        tenant_id = _get_tenant_id(auth_client, db)
        assert db.query(VoiceTenantConfig).filter(VoiceTenantConfig.tenant_id == tenant_id).first() is None

        resp = auth_client.put("/api/voice/settings", json={"bot_name": "Recruiter Bot"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["bot_name"] == "Recruiter Bot"

    def test_partial_update(self, auth_client, db):
        """Should update only provided fields, leaving others at defaults."""
        # Create default config first
        auth_client.get("/api/voice/settings")

        resp = auth_client.put("/api/voice/settings", json={
            "bot_name": "Sarah",
            "greeting_style": "friendly",
            "call_duration_max": 10,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["bot_name"] == "Sarah"
        assert data["greeting_style"] == "friendly"
        assert data["call_duration_max"] == 10
        # Untouched fields keep defaults
        assert data["bot_voice_gender"] == "female"
        assert data["call_duration_min"] == 5
        assert data["max_retries"] == 3

    def test_update_phone_and_scheduling(self, auth_client, db):
        """Should update phone number, business hours, and timezone."""
        auth_client.get("/api/voice/settings")

        resp = auth_client.put("/api/voice/settings", json={
            "outbound_phone_number": "+14155551234",
            "business_hours_start": "08:00",
            "business_hours_end": "20:00",
            "timezone": "America/New_York",
            "allowed_days": [1, 2, 3, 4, 5, 6],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["outbound_phone_number"] == "+14155551234"
        assert data["business_hours_start"] == "08:00"
        assert data["business_hours_end"] == "20:00"
        assert data["timezone"] == "America/New_York"
        assert data["allowed_days"] == [1, 2, 3, 4, 5, 6]

    def test_update_consent_script(self, auth_client, db):
        """Should allow setting a custom consent script."""
        auth_client.get("/api/voice/settings")

        custom_script = "This call is recorded. Do you consent?"
        resp = auth_client.put("/api/voice/settings", json={"consent_script": custom_script})
        assert resp.status_code == 200
        assert resp.json()["consent_script"] == custom_script

    def test_empty_body_no_changes(self, auth_client, db):
        """Empty JSON body should not change any fields."""
        auth_client.get("/api/voice/settings")

        resp = auth_client.put("/api/voice/settings", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["bot_name"] == "ARIA Assistant"
        assert data["greeting_style"] == "professional"


# ─── POST /api/voice/schedule ─────────────────────────────────────────────────

class TestScheduleVoiceCall:

    def test_requires_auth(self, client):
        """Unauthenticated request should return 401."""
        resp = client.post("/api/voice/schedule", json={
            "candidate_id": 1, "jd_id": 1,
            "phone_number": "+14155551234",
            "scheduled_at": "2025-06-01T10:00:00Z",
        })
        assert resp.status_code == 401

    @patch("app.backend.services.voice_call_scheduler.schedule_voice_call")
    def test_schedule_success(self, mock_scheduler, auth_client, db):
        """Should create a voice screening session."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)
        jd = _create_jd(db, tenant_id)

        scheduled_at = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        resp = auth_client.post("/api/voice/schedule", json={
            "candidate_id": candidate.id,
            "jd_id": jd.id,
            "phone_number": "+14155551234",
            "scheduled_at": scheduled_at,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "scheduled"
        assert data["phone_number"] == "+14155551234"
        assert "session_id" in data

        # Verify in DB
        session = db.query(VoiceScreeningSession).filter(VoiceScreeningSession.id == data["session_id"]).first()
        assert session is not None
        assert session.tenant_id == tenant_id
        assert session.candidate_id == candidate.id
        assert session.jd_id == jd.id
        assert session.direction == "outbound"
        assert session.status == "scheduled"

    def test_schedule_candidate_not_found(self, auth_client, db):
        """Should return 404 if candidate doesn't exist."""
        _create_jd(db, _get_tenant_id(auth_client, db))

        resp = auth_client.post("/api/voice/schedule", json={
            "candidate_id": 99999,
            "jd_id": 1,
            "phone_number": "+14155551234",
            "scheduled_at": "2025-06-01T10:00:00Z",
        })
        assert resp.status_code == 404

    def test_schedule_candidate_other_tenant(self, auth_client, db):
        """Should return 404 if candidate belongs to a different tenant."""
        from app.backend.models.db_models import Tenant
        other_tenant = Tenant(name="Other Corp", slug="othercorp")
        db.add(other_tenant)
        db.commit()
        db.refresh(other_tenant)

        other_candidate = Candidate(tenant_id=other_tenant.id, name="Other", email="other@example.com")
        db.add(other_candidate)
        db.commit()

        resp = auth_client.post("/api/voice/schedule", json={
            "candidate_id": other_candidate.id,
            "jd_id": None,
            "phone_number": "+14155551234",
            "scheduled_at": "2025-06-01T10:00:00Z",
        })
        assert resp.status_code == 404

    @patch("app.backend.services.voice_call_scheduler.schedule_voice_call")
    def test_schedule_without_jd(self, mock_scheduler, auth_client, db):
        """Should allow scheduling without a JD (jd_id=None)."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)

        resp = auth_client.post("/api/voice/schedule", json={
            "candidate_id": candidate.id,
            "jd_id": None,
            "phone_number": "+14155551234",
            "scheduled_at": None,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "scheduled"


# ─── GET /api/voice/sessions ──────────────────────────────────────────────────

class TestListVoiceSessions:

    def test_requires_auth(self, client):
        """Unauthenticated request should return 401."""
        resp = client.get("/api/voice/sessions")
        assert resp.status_code == 401

    def test_empty_list(self, auth_client):
        """Should return empty list when no sessions exist."""
        resp = auth_client.get("/api/voice/sessions")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_sessions(self, auth_client, db):
        """Should return sessions for the tenant."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)

        # Create 2 sessions
        for i in range(2):
            s = VoiceScreeningSession(
                tenant_id=tenant_id,
                candidate_id=candidate.id,
                phone_number="+14155551234",
                direction="outbound",
                status="scheduled",
            )
            db.add(s)
        db.commit()

        resp = auth_client.get("/api/voice/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    def test_filter_by_status(self, auth_client, db):
        """Should filter sessions by status."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)

        s1 = VoiceScreeningSession(
            tenant_id=tenant_id, candidate_id=candidate.id,
            phone_number="+14155551234", status="scheduled",
        )
        s2 = VoiceScreeningSession(
            tenant_id=tenant_id, candidate_id=candidate.id,
            phone_number="+14155551235", status="completed",
        )
        db.add_all([s1, s2])
        db.commit()

        resp = auth_client.get("/api/voice/sessions?status=scheduled")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "scheduled"

    def test_filter_by_candidate(self, auth_client, db):
        """Should filter sessions by candidate_id."""
        tenant_id = _get_tenant_id(auth_client, db)
        c1 = _create_candidate(db, tenant_id, name="C1", email="c1@test.com")
        c2 = _create_candidate(db, tenant_id, name="C2", email="c2@test.com")

        db.add(VoiceScreeningSession(
            tenant_id=tenant_id, candidate_id=c1.id,
            phone_number="+14155551234", status="scheduled",
        ))
        db.add(VoiceScreeningSession(
            tenant_id=tenant_id, candidate_id=c2.id,
            phone_number="+14155551235", status="scheduled",
        ))
        db.commit()

        resp = auth_client.get(f"/api/voice/sessions?candidate_id={c1.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

    def test_pagination(self, auth_client, db):
        """Should respect limit and offset."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)

        for i in range(5):
            db.add(VoiceScreeningSession(
                tenant_id=tenant_id, candidate_id=candidate.id,
                phone_number=f"+1415555123{i}", status="scheduled",
            ))
        db.commit()

        resp = auth_client.get("/api/voice/sessions?limit=2&offset=0")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

        resp2 = auth_client.get("/api/voice/sessions?limit=2&offset=2")
        assert resp2.status_code == 200
        assert len(resp2.json()) == 2


# ─── GET /api/voice/sessions/{id} ─────────────────────────────────────────────

class TestGetVoiceSession:

    def test_requires_auth(self, client):
        """Unauthenticated request should return 401."""
        resp = client.get("/api/voice/sessions/1")
        assert resp.status_code == 401

    def test_session_not_found(self, auth_client):
        """Should return 404 for non-existent session."""
        resp = auth_client.get("/api/voice/sessions/99999")
        assert resp.status_code == 404

    def test_session_detail(self, auth_client, db):
        """Should return session detail with transcript entries."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)

        session = VoiceScreeningSession(
            tenant_id=tenant_id, candidate_id=candidate.id,
            phone_number="+14155551234", status="completed",
            duration_seconds=320, consent_recorded=True,
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        # Add transcript entries
        now = datetime.now(timezone.utc)
        entries = [
            VoiceTranscriptEntry(
                session_id=session.id, speaker="bot",
                text="Hi, this is ARIA Assistant. Am I speaking with Voice Candidate?",
                timestamp=now,
            ),
            VoiceTranscriptEntry(
                session_id=session.id, speaker="candidate",
                text="Yes, this is me.",
                timestamp=now + timedelta(seconds=5),
            ),
        ]
        db.add_all(entries)
        db.commit()

        resp = auth_client.get(f"/api/voice/sessions/{session.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["duration_seconds"] == 320
        assert data["consent_recorded"] is True
        assert "transcript" in data
        assert len(data["transcript"]) == 2
        assert data["transcript"][0]["speaker"] == "bot"
        assert data["transcript"][1]["speaker"] == "candidate"

    def test_session_other_tenant_returns_404(self, auth_client, db):
        """Should return 404 for session belonging to a different tenant."""
        from app.backend.models.db_models import Tenant
        other_tenant = Tenant(name="Other Corp 2", slug="othercorp2")
        db.add(other_tenant)
        db.commit()
        db.refresh(other_tenant)

        other_candidate = Candidate(tenant_id=other_tenant.id, name="Other", email="other2@test.com")
        db.add(other_candidate)
        db.commit()

        other_session = VoiceScreeningSession(
            tenant_id=other_tenant.id, candidate_id=other_candidate.id,
            phone_number="+14155559999", status="scheduled",
        )
        db.add(other_session)
        db.commit()

        resp = auth_client.get(f"/api/voice/sessions/{other_session.id}")
        assert resp.status_code == 404

    def test_empty_transcript(self, auth_client, db):
        """Should return empty transcript list when no entries exist."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)

        session = VoiceScreeningSession(
            tenant_id=tenant_id, candidate_id=candidate.id,
            phone_number="+14155551234", status="scheduled",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        resp = auth_client.get(f"/api/voice/sessions/{session.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["transcript"] == []


# ─── Internal Endpoints (secret-guarded, called by voice-agent) ───────────────

# In the test environment INTERNAL_SERVICE_SECRET resolves to this value
# (see app/backend/middleware/auth.py).
_INTERNAL_HEADERS = {"X-Internal-Secret": "test-internal-service-secret"}


class TestInternalConfig:

    def test_internal_config_returns_config(self, client, db):
        """Should return tenant config without auth."""
        from app.backend.models.db_models import Tenant
        tenant = Tenant(name="InternalTest", slug="internaltest")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        config = VoiceTenantConfig(tenant_id=tenant.id, bot_name="TestBot")
        db.add(config)
        db.commit()

        resp = client.get(f"/api/voice/internal/config/{tenant.id}", headers=_INTERNAL_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["bot_name"] == "TestBot"

    def test_internal_config_requires_secret(self, client):
        """Missing internal secret must be rejected with 403."""
        resp = client.get("/api/voice/internal/config/1")
        assert resp.status_code == 403

    def test_internal_config_missing(self, client, db):
        """Should return empty dict for unknown tenant."""
        resp = client.get("/api/voice/internal/config/99999", headers=_INTERNAL_HEADERS)
        assert resp.status_code == 200
        assert resp.json() == {}


class TestInternalCandidate:

    def test_internal_candidate_found(self, client, db):
        """Should return candidate info without auth."""
        from app.backend.models.db_models import Tenant
        tenant = Tenant(name="CandTest", slug="candtest")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        candidate = _create_candidate(db, tenant.id, email="cand@test.com")

        resp = client.get(
            f"/api/voice/internal/candidate/{tenant.id}/{candidate.id}",
            headers=_INTERNAL_HEADERS,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Voice Candidate"

    def test_internal_candidate_not_found(self, client, db):
        """Should return 404 for unknown candidate."""
        from app.backend.models.db_models import Tenant
        tenant = Tenant(name="CandMiss", slug="candmiss")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        resp = client.get(
            f"/api/voice/internal/candidate/{tenant.id}/99999",
            headers=_INTERNAL_HEADERS,
        )
        assert resp.status_code == 404


# ─── PATCH /api/voice/sessions/{id} ───────────────────────────────────────────

class TestUpdateVoiceSession:

    def test_patch_session_status(self, auth_client, db):
        """Should update session status."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)

        session = VoiceScreeningSession(
            tenant_id=tenant_id, candidate_id=candidate.id,
            phone_number="+14155551234", status="scheduled",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        resp = auth_client.patch(f"/api/voice/sessions/{session.id}", json={
            "expected_generation": 1,
            "status": "in_progress",
            "call_sid": "test-sid-123",
        }, headers=_INTERNAL_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

        # Verify DB
        db.refresh(session)
        assert session.status == "in_progress"
        assert session.call_sid == "test-sid-123"

    def test_patch_session_not_found(self, auth_client):
        """Should return 404 for unknown session."""
        resp = auth_client.patch("/api/voice/sessions/99999", json={
            "expected_generation": 1,
            "status": "completed",
        },
                                 headers=_INTERNAL_HEADERS)
        assert resp.status_code == 404

    def test_patch_ignores_disallowed_fields(self, auth_client, db):
        """Should not update fields outside the allowed set."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)

        session = VoiceScreeningSession(
            tenant_id=tenant_id, candidate_id=candidate.id,
            phone_number="+14155551234", status="scheduled",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        resp = auth_client.patch(f"/api/voice/sessions/{session.id}", json={
            "expected_generation": 1,
            "tenant_id": 999,  # Not allowed
            "phone_number": "+99999999999",  # Not allowed
        }, headers=_INTERNAL_HEADERS)
        assert resp.status_code == 200

        # Verify disallowed fields were NOT changed
        db.refresh(session)
        assert session.tenant_id == tenant_id
        assert session.phone_number == "+14155551234"

    def test_patch_rejects_stale_generation(self, auth_client, db):
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)
        session = VoiceScreeningSession(
            tenant_id=tenant_id,
            candidate_id=candidate.id,
            phone_number="+14155551235",
            status="scheduled",
            result_generation=2,
        )
        db.add(session)
        db.commit()

        resp = auth_client.patch(
            f"/api/voice/sessions/{session.id}",
            json={"expected_generation": 1, "status": "completed"},
            headers=_INTERNAL_HEADERS,
        )
        assert resp.status_code == 409
        db.refresh(session)
        assert session.status == "scheduled"


# ─── Voice Screening Service Tests ────────────────────────────────────────────

class TestBuildConversationContext:

    def test_build_context_with_jd(self, auth_client, db):
        """Should assemble full context from session + config + JD."""
        from app.backend.services.voice_screening_service import build_conversation_context
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)
        jd = _create_jd(db, tenant_id)

        # Create config
        auth_client.put("/api/voice/settings", json={"bot_name": "TestBot"})

        # Create session
        session = VoiceScreeningSession(
            tenant_id=tenant_id, candidate_id=candidate.id,
            jd_id=jd.id, phone_number="+14155551234", status="scheduled",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        ctx = build_conversation_context(db, session.id)
        assert ctx is not None
        assert ctx["bot_name"] == "TestBot"
        assert ctx["candidate_name"] == "Voice Candidate"
        assert ctx["jd_title"] == "Voice JD"
        assert ctx["phone_number"] == "+14155551234"

    def test_build_context_without_jd(self, auth_client, db):
        """Should handle sessions without a JD."""
        from app.backend.services.voice_screening_service import build_conversation_context
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)

        session = VoiceScreeningSession(
            tenant_id=tenant_id, candidate_id=candidate.id,
            phone_number="+14155551234", status="scheduled",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        ctx = build_conversation_context(db, session.id)
        assert ctx is not None
        assert ctx["jd_text"] == ""
        assert ctx["must_have_skills"] == []

    def test_build_context_not_found(self, db):
        """Should return empty dict for non-existent session."""
        from app.backend.services.voice_screening_service import build_conversation_context
        ctx = build_conversation_context(db, 99999)
        assert ctx == {}

    def test_build_context_extracts_skills_from_jd(self, auth_client, db):
        """Should extract must-have skills from JD's required_skills_override."""
        from app.backend.services.voice_screening_service import build_conversation_context
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)

        jd = RoleTemplate(
            tenant_id=tenant_id, name="Skills JD",
            jd_text="Python role",
            required_skills_override='["python", "docker", "aws"]',
        )
        db.add(jd)
        db.commit()
        db.refresh(jd)

        session = VoiceScreeningSession(
            tenant_id=tenant_id, candidate_id=candidate.id,
            jd_id=jd.id, phone_number="+14155551234", status="scheduled",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        ctx = build_conversation_context(db, session.id)
        assert ctx["must_have_skills"] == ["python", "docker", "aws"]


# ─── Voice Call Scheduler Tests ───────────────────────────────────────────────

class TestBusinessHours:

    def test_within_business_hours(self):
        """Should return True for a time within business hours."""
        from app.backend.services.voice_call_scheduler import is_within_business_hours
        from datetime import timezone, datetime

        config = VoiceTenantConfig(
            tenant_id=1, timezone="UTC",
            business_hours_start="09:00", business_hours_end="18:00",
            allowed_days=[1, 2, 3, 4, 5],
        )

        # Wednesday 10:00 UTC
        dt = datetime(2025, 6, 18, 10, 0, tzinfo=timezone.utc)
        assert is_within_business_hours(dt, config) is True

    def test_outside_business_hours(self):
        """Should return False for a time outside business hours."""
        from app.backend.services.voice_call_scheduler import is_within_business_hours
        from datetime import timezone, datetime

        config = VoiceTenantConfig(
            tenant_id=1, timezone="UTC",
            business_hours_start="09:00", business_hours_end="18:00",
            allowed_days=[1, 2, 3, 4, 5],
        )

        # Wednesday 20:00 UTC
        dt = datetime(2025, 6, 18, 20, 0, tzinfo=timezone.utc)
        assert is_within_business_hours(dt, config) is False

    def test_weekend_not_allowed(self):
        """Should return False for a weekend day."""
        from app.backend.services.voice_call_scheduler import is_within_business_hours
        from datetime import timezone, datetime

        config = VoiceTenantConfig(
            tenant_id=1, timezone="UTC",
            business_hours_start="09:00", business_hours_end="18:00",
            allowed_days=[1, 2, 3, 4, 5],
        )

        # Saturday 10:00 UTC
        dt = datetime(2025, 6, 21, 10, 0, tzinfo=timezone.utc)
        assert is_within_business_hours(dt, config) is False

    def test_adjust_to_business_hours(self):
        """Should move outside-hours time to next business slot."""
        from app.backend.services.voice_call_scheduler import adjust_to_business_hours
        from datetime import timezone, datetime

        config = VoiceTenantConfig(
            tenant_id=1, timezone="UTC",
            business_hours_start="09:00", business_hours_end="18:00",
            allowed_days=[1, 2, 3, 4, 5],
        )

        # Saturday 10:00 → should move to Monday 09:00
        dt = datetime(2025, 6, 21, 10, 0, tzinfo=timezone.utc)
        adjusted = adjust_to_business_hours(dt, config)
        assert adjusted.hour == 9
        assert adjusted.minute == 0
        # Monday = isoweekday 1
        assert adjusted.isoweekday() == 1


class TestFallbackAssessment:

    def test_fallback_assessment_structure(self):
        """Fallback assessment should have all required keys."""
        from app.backend.services.voice_screening_service import _fallback_assessment
        result = _fallback_assessment("John Doe")
        assert result["overall_recommendation"] == "maybe"
        assert result["overall_score"] == 50
        assert "John Doe" in result["summary"]
        assert isinstance(result["skill_assessments"], list)
        assert isinstance(result["risk_flags"], list)
        assert len(result["risk_flags"]) > 0


def _create_voice_session(db, tenant_id, candidate_id, status="scheduled"):
    """Helper to create a voice screening session for testing."""
    s = VoiceScreeningSession(
        tenant_id=tenant_id,
        candidate_id=candidate_id,
        phone_number="+14155551234",
        direction="outbound",
        status=status,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ─── POST /api/voice/sessions/{id}/cancel ─────────────────────────────────────

class TestCancelVoiceSession:

    def test_requires_auth(self, client, db):
        """Unauthenticated cancel should return 401."""
        resp = client.post("/api/voice/sessions/1/cancel")
        assert resp.status_code == 401

    def test_cancel_scheduled_session(self, auth_client, db):
        """Cancelling a scheduled session should set status to 'cancelled'."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)
        session = _create_voice_session(db, tenant_id, candidate.id, status="scheduled")
        session.assessment_json = '{"old":true}'
        session.transcript_json = '{"old":true}'
        session.duration_seconds = 12
        db.commit()

        resp = auth_client.post(f"/api/voice/sessions/{session.id}/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"
        assert data["session_id"] == session.id

    def test_cancel_completed_session_fails(self, auth_client, db):
        """Cannot cancel a completed session."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)
        session = _create_voice_session(db, tenant_id, candidate.id, status="completed")

        resp = auth_client.post(f"/api/voice/sessions/{session.id}/cancel")
        assert resp.status_code == 400

    def test_cancel_not_found(self, auth_client, db):
        """Cancelling a non-existent session should return 404."""
        resp = auth_client.post("/api/voice/sessions/9999/cancel")
        assert resp.status_code == 404

    def test_cancel_other_tenant_session(self, auth_client, db):
        """Cannot cancel a session from another tenant."""
        # Create session with tenant_id=999 (different from auth_client's tenant)
        candidate = _create_candidate(db, 999)
        session = _create_voice_session(db, 999, candidate.id, status="scheduled")

        resp = auth_client.post(f"/api/voice/sessions/{session.id}/cancel")
        assert resp.status_code == 404


# ─── POST /api/voice/sessions/{id}/reschedule ─────────────────────────────────

class TestRescheduleVoiceSession:

    def test_requires_auth(self, client, db):
        """Unauthenticated reschedule should return 401."""
        resp = client.post("/api/voice/sessions/1/reschedule", json={
            "candidate_id": 1, "phone_number": "+14155551234",
        })
        assert resp.status_code == 401

    def test_reschedule_scheduled_session(self, auth_client, db):
        """Rescheduling a scheduled session should update time and keep status scheduled."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)
        session = _create_voice_session(db, tenant_id, candidate.id, status="scheduled")

        new_time = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        resp = auth_client.post(f"/api/voice/sessions/{session.id}/reschedule", json={
            "candidate_id": candidate.id,
            "phone_number": "+14155559999",
            "scheduled_at": new_time,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "scheduled"
        assert data["message"] == "Call rescheduled successfully"
        db.refresh(session)
        assert session.result_generation == 2
        assert session.assessment_json is None
        assert session.transcript_json is None
        assert session.duration_seconds is None

    def test_reschedule_failed_session(self, auth_client, db):
        """Can reschedule a failed session."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)
        session = _create_voice_session(db, tenant_id, candidate.id, status="failed")

        resp = auth_client.post(f"/api/voice/sessions/{session.id}/reschedule", json={
            "candidate_id": candidate.id,
            "phone_number": "+14155551234",
        })
        assert resp.status_code == 200

    def test_reschedule_completed_fails(self, auth_client, db):
        """Cannot reschedule a completed session."""
        tenant_id = _get_tenant_id(auth_client, db)
        candidate = _create_candidate(db, tenant_id)
        session = _create_voice_session(db, tenant_id, candidate.id, status="completed")

        resp = auth_client.post(f"/api/voice/sessions/{session.id}/reschedule", json={
            "candidate_id": candidate.id,
            "phone_number": "+14155551234",
        })
        assert resp.status_code == 400

    def test_reschedule_not_found(self, auth_client, db):
        """Rescheduling a non-existent session should return 404."""
        resp = auth_client.post("/api/voice/sessions/9999/reschedule", json={
            "candidate_id": 1,
            "phone_number": "+14155551234",
        })
        assert resp.status_code == 404

