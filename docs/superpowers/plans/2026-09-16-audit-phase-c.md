# Audit Remediation Phase C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close AUD-015–022 from `docs/superpowers/specs/2026-09-16-audit-phase-c-design.md`: ScreeningCommand + fingerprint, real worker concurrency, heartbeat/lease, one stale-recovery owner, DLQ artifact replay, idempotency body binding, ATS secret encryption, DNS-pinned fetches, stream abort at max_bytes, and capability reads via `get_tenant_plan`.

**Architecture:** A versioned `ScreeningCommand` is the enqueue/complete payload. Dedup uses SHA-256 fingerprint v1 stored in `AnalysisJob.input_hash`. The worker claims up to `QUEUE_MAX_CONCURRENT` jobs with an `asyncio.Semaphore`, each with its own DB session and a 30s heartbeat. Only APScheduler recovers stale jobs via conditional UPDATE. DLQ copies `artifact_id`. Idempotency keys include a request fingerprint; mismatch returns 409. ATS secrets go through AES-256-GCM with a versioned env keyring. `safe_request` connects to resolved public IPs and streams until `max_bytes`. Feature/limit checks use `get_tenant_plan`.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic (head `072_tenant_desired_plan`), asyncio, pytest, existing `db` / `client` fixtures.

## Global Constraints

- Work on current `main`. No feature branch or PR unless the user asks.
- Test locally. **Do not git commit or push unless the user explicitly asks.** Skip every “Commit” step until then.
- Do not weaken CSRF, JWT, MFA, ClamAV, CORS, rate limits, AUD-004 tenant predicates, or entitlement.
- TDD: failing test first, then minimal code.
- Queue and sync share AUD-005 quota. Re-run `app/backend/tests/test_audit_queue_quota.py` after queue changes.
- No new public HTTP routes. No Redis queue. No ATS allowlist/replay store. No cloud KMS SDK.
- `QUEUE_MAX_CONCURRENT` default 10, clamp 1–32. Heartbeat 30s, lease 10 minutes.
- Same key + different body → 409, no route execution.
- Never log integration secrets. Decrypt ATS secrets only at outbound/HMAC use.
- Capability reads use `get_tenant_plan`, not `tenant.plan`.

## File map

- Create: `app/backend/services/screening_command.py`
- Create: `app/backend/services/integration_secrets.py`
- Create: `app/backend/tests/test_audit_phase_c_screening.py`
- Create: `app/backend/tests/test_audit_phase_c_secrets_entitlement.py`
- Create: `alembic/versions/073_phase_c_queue.py`
- Modify: `app/backend/services/queue_manager.py`
- Modify: `app/backend/services/queue_analysis_service.py`
- Modify: `app/backend/services/scheduler.py`
- Modify: `app/backend/routes/queue_api.py`
- Modify: `app/backend/models/db_models.py` (`DeadLetterJob.artifact_id`, `IdempotencyKey`)
- Modify: `app/backend/middleware/idempotency.py`
- Modify: `app/backend/services/url_safety.py`
- Modify: `app/backend/routes/ats.py`, `app/backend/services/ats_connector.py`, `app/backend/models/schemas.py` (`ATSConnectionOut` configured flags)
- Modify: `app/backend/routes/analyze.py`, `app/backend/routes/subscription.py`
- Modify: `app/backend/tests/test_audit_phase_b_outbound.py`
- Modify: `app/backend/tests/test_production_hardening.py` (DLQ + idempotency lookups)
- Modify: `app/backend/tests/conftest.py` only if SQLite queue DDL is missing a column a test needs

---

### Task 1: ScreeningCommand + fingerprint v1

**Files:**
- Create: `app/backend/services/screening_command.py`
- Create: `app/backend/tests/test_audit_phase_c_screening.py`

**Interfaces:**
- Produces:
  - `@dataclass class ScreeningCommand` with fields: `schema_version: str`, `tenant_id: int`, `user_id: int`, `resume_hash: str`, `jd_hash: str`, `algorithm_version: str`, `candidate_id: int | None = None`, `artifact_id: str | None = None`, `requisition_id: int | None = None`, `role_template_id: int | None = None`, `criteria_version: int | None = None`, `scoring_weights: dict | None = None`, `skill_overrides: dict | None = None`
  - `class CrossTenantRequisitionError(ValueError)`
  - `class ArtifactUnavailable(ValueError)`
  - `def normalize_config(value: dict | None) -> dict | None`
  - `def analysis_fingerprint(cmd: ScreeningCommand) -> str`
  - `def command_to_dict(cmd: ScreeningCommand) -> dict`
  - `def command_from_dict(data: dict) -> ScreeningCommand`

- [ ] **Step 1: Write failing tests**

```python
# app/backend/tests/test_audit_phase_c_screening.py
from app.backend.services.screening_command import ScreeningCommand, analysis_fingerprint

def _cmd(**kwargs):
    base = dict(
        schema_version="v1",
        tenant_id=1,
        user_id=9,
        resume_hash="r" * 64,
        jd_hash="j" * 64,
        algorithm_version="1.0",
    )
    base.update(kwargs)
    return ScreeningCommand(**base)

def test_same_inputs_same_fingerprint():
    a = analysis_fingerprint(_cmd(scoring_weights={"a": 1, "b": 2}))
    b = analysis_fingerprint(_cmd(scoring_weights={"b": 2, "a": 1}, user_id=99, candidate_id=3))
    assert a == b
    assert len(a) == 64

def test_weights_change_fingerprint():
    assert analysis_fingerprint(_cmd(scoring_weights={"a": 1})) != analysis_fingerprint(
        _cmd(scoring_weights={"a": 2})
    )

def test_criteria_version_change_fingerprint():
    assert analysis_fingerprint(_cmd(requisition_id=5, criteria_version=1)) != analysis_fingerprint(
        _cmd(requisition_id=5, criteria_version=2)
    )

def test_skill_override_change_fingerprint():
    assert analysis_fingerprint(_cmd(skill_overrides={"python": True})) != analysis_fingerprint(
        _cmd(skill_overrides={"python": False})
    )

def test_filename_priority_excluded():
    # user_id / candidate_id / artifact_id must not affect hash
    assert analysis_fingerprint(_cmd(artifact_id="aaa")) == analysis_fingerprint(_cmd(artifact_id="bbb"))
```

- [ ] **Step 2: Run** `python -m pytest app/backend/tests/test_audit_phase_c_screening.py::test_same_inputs_same_fingerprint -q --tb=short`  
  Expected: FAIL (module missing)

- [ ] **Step 3: Implement** `screening_command.py`

```python
from __future__ import annotations
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Optional

ALGORITHM_VERSION_DEFAULT = "1.0"

class CrossTenantRequisitionError(ValueError):
    pass

class ArtifactUnavailable(ValueError):
    pass

@dataclass
class ScreeningCommand:
    schema_version: str
    tenant_id: int
    user_id: int
    resume_hash: str
    jd_hash: str
    algorithm_version: str
    candidate_id: Optional[int] = None
    artifact_id: Optional[str] = None
    requisition_id: Optional[int] = None
    role_template_id: Optional[int] = None
    criteria_version: Optional[int] = None
    scoring_weights: Optional[dict] = None
    skill_overrides: Optional[dict] = None

def normalize_config(value: dict | None) -> dict | None:
    if not value:
        return None
    def _norm(v: Any) -> Any:
        if isinstance(v, dict):
            return {str(k): _norm(v[k]) for k in sorted(v, key=lambda x: str(x))}
        if isinstance(v, list):
            return [_norm(i) for i in v]
        if isinstance(v, bool):
            return v
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        if isinstance(v, float):
            return format(v, ".10g")
        return v
    return _norm(value)

def analysis_fingerprint(cmd: ScreeningCommand) -> str:
    payload = {
        "schema_version": cmd.schema_version,
        "tenant_id": cmd.tenant_id,
        "resume_hash": cmd.resume_hash,
        "jd_hash": cmd.jd_hash,
        "requisition_id": cmd.requisition_id,
        "criteria_version": cmd.criteria_version,
        "role_template_id": cmd.role_template_id,
        "scoring_weights": normalize_config(cmd.scoring_weights),
        "skill_overrides": normalize_config(cmd.skill_overrides),
        "algorithm_version": cmd.algorithm_version,
    }
    canonical = "v1:" + json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def command_to_dict(cmd: ScreeningCommand) -> dict:
    return asdict(cmd)

def command_from_dict(data: dict) -> ScreeningCommand:
    return ScreeningCommand(
        schema_version=data.get("schema_version") or "v1",
        tenant_id=int(data["tenant_id"]),
        user_id=int(data["user_id"]),
        resume_hash=data["resume_hash"],
        jd_hash=data["jd_hash"],
        algorithm_version=data.get("algorithm_version") or ALGORITHM_VERSION_DEFAULT,
        candidate_id=data.get("candidate_id"),
        artifact_id=str(data["artifact_id"]) if data.get("artifact_id") else None,
        requisition_id=data.get("requisition_id"),
        role_template_id=data.get("role_template_id"),
        criteria_version=data.get("criteria_version"),
        scoring_weights=data.get("scoring_weights"),
        skill_overrides=data.get("skill_overrides"),
    )
```

- [ ] **Step 4: Run** `python -m pytest app/backend/tests/test_audit_phase_c_screening.py -q --tb=short`  
  Expected: PASS for fingerprint tests

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 2: Enqueue with fingerprint, persist command, reject cross-tenant requisition

**Files:**
- Modify: `app/backend/services/queue_manager.py` (`compute_hash` / `enqueue_job`)
- Modify: `app/backend/services/queue_analysis_service.py` (`prepare_file_for_queue`)
- Modify: `app/backend/routes/queue_api.py` (pass `requisition_id` into prepare/enqueue)
- Modify: `app/backend/tests/test_audit_phase_c_screening.py`

**Interfaces:**
- Consumes: `ScreeningCommand`, `analysis_fingerprint`, `command_to_dict`, `CrossTenantRequisitionError`
- Produces: `enqueue_job(..., requisition_id: int | None = None, scoring_weights=None, skill_overrides=None, role_template_id=None)` stores `job_config["command"]` and sets `input_hash = analysis_fingerprint(cmd)`. `prepare_file_for_queue(..., requisition_id: int | None = None)`.

- [ ] **Step 1: Write failing tests** (use `db`, `seed_subscription_plans`)

```python
import pytest
from app.backend.models.db_models import AnalysisJob, Requisition, Tenant
from app.backend.services.queue_manager import get_queue_manager
from app.backend.services.screening_command import CrossTenantRequisitionError, analysis_fingerprint, command_from_dict

@pytest.mark.asyncio
async def test_enqueue_persists_command_and_fingerprint(db, seed_subscription_plans):
    tenant = Tenant(name="C1", slug="c1-enq")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    req = Requisition(tenant_id=tenant.id, title="Eng", status="open", current_criteria_version=3, created_by=1)
    db.add(req)
    db.commit()
    db.refresh(req)
    mgr = get_queue_manager()
    job_id = await mgr.enqueue_job(
        tenant_id=tenant.id,
        resume_text="Alice engineer python",
        resume_filename="a.pdf",
        jd_text="Need python",
        user_id=1,
        requisition_id=req.id,
        scoring_weights={"skills": 0.5},
        skill_overrides={"python": True},
        role_template_id=None,
    )
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
    cmd = command_from_dict(job.job_config["command"])
    assert cmd.requisition_id == req.id
    assert cmd.criteria_version == 3
    assert job.input_hash == analysis_fingerprint(cmd)

@pytest.mark.asyncio
async def test_weights_change_creates_new_job(db, seed_subscription_plans):
    tenant = Tenant(name="C2", slug="c2-enq")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    mgr = get_queue_manager()
    a = await mgr.enqueue_job(tenant_id=tenant.id, resume_text="R", resume_filename="a.pdf", jd_text="J", user_id=1, scoring_weights={"w": 1})
    b = await mgr.enqueue_job(tenant_id=tenant.id, resume_text="R", resume_filename="a.pdf", jd_text="J", user_id=1, scoring_weights={"w": 2})
    assert a != b

@pytest.mark.asyncio
async def test_same_config_dedupes(db, seed_subscription_plans):
    tenant = Tenant(name="C3", slug="c3-enq")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    mgr = get_queue_manager()
    a = await mgr.enqueue_job(tenant_id=tenant.id, resume_text="R", resume_filename="a.pdf", jd_text="J", user_id=1)
    b = await mgr.enqueue_job(tenant_id=tenant.id, resume_text="R", resume_filename="other.pdf", jd_text="J", user_id=99)
    assert a == b

@pytest.mark.asyncio
async def test_cross_tenant_requisition_rejected(db, seed_subscription_plans):
    t1 = Tenant(name="T1", slug="c-t1")
    t2 = Tenant(name="T2", slug="c-t2")
    db.add_all([t1, t2])
    db.commit()
    db.refresh(t1)
    db.refresh(t2)
    req = Requisition(tenant_id=t2.id, title="X", status="open", created_by=1)
    db.add(req)
    db.commit()
    db.refresh(req)
    mgr = get_queue_manager()
    with pytest.raises(CrossTenantRequisitionError):
        await mgr.enqueue_job(tenant_id=t1.id, resume_text="R", resume_filename="a.pdf", jd_text="J", user_id=1, requisition_id=req.id)
    assert db.query(AnalysisJob).filter(AnalysisJob.tenant_id == t1.id).count() == 0
```

- [ ] **Step 2: Run those tests** — Expected: FAIL (`enqueue_job` unexpected kwargs / old hash)

- [ ] **Step 3: Implement**

In `enqueue_job`, after hashing resume/JD:

1. If `requisition_id` is set, load `Requisition` with `id` + `tenant_id`. Missing → `raise CrossTenantRequisitionError("Requisition not found")`. Use `req.current_criteria_version` and `req.legacy_role_template_id` if `role_template_id` is None.
2. Build `ScreeningCommand` (`schema_version="v1"`, `algorithm_version` default `"1.0"`).
3. `input_hash = analysis_fingerprint(cmd)`.
4. Existing dedup query unchanged except it now matches the new hash.
5. After quota reserve, `job_config["command"] = command_to_dict(cmd)` (keep `quota_reserved`).
6. After artifact flush, set `cmd.artifact_id = str(artifact.id)` and rewrite `job_config["command"]`.

`prepare_file_for_queue`: add `requisition_id` and pass it plus weights/overrides/template into `enqueue_job`.

`queue_api.submit_analysis_job` and `submit_analysis_file` / batch equivalent: pass `requisition_id`. Map `CrossTenantRequisitionError` → HTTP 400.

Keep `_check_and_increment_usage` as today (AUD-005).

- [ ] **Step 4: Run Task 2 tests +** `python -m pytest app/backend/tests/test_audit_queue_quota.py -q --tb=short`  
  Expected: PASS

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 3: Queue completion writes requisition + RequisitionCandidate

**Files:**
- Modify: `app/backend/services/queue_analysis_service.py` (`complete_queue_job`)
- Modify: `app/backend/tests/test_audit_phase_c_screening.py`

**Interfaces:**
- Consumes: `command_from_dict`, `_upsert_screening_result(..., requisition_id=)`, `_link_to_requisition`
- Produces: `complete_queue_job` (the queue `execute_screening` path) sets `ScreeningResult.requisition_id` and `RequisitionCandidate.screening_result_id`. Second completion for the same `job_id` does not insert a second `AnalysisResult`. Do not rewrite `analyze.py`; sync already links.

- [ ] **Step 1: Write failing tests** that enqueue (or insert job+artifact+command), monkeypatch `_process_single_resume` to a tiny dict `{fit_score: 70, final_recommendation: "Hire"}`, then `await complete_queue_job(job.id, db)`. Assert `ScreeningResult.requisition_id` and `RequisitionCandidate.screening_result_id`. Add a second call: `AnalysisResult` count for that `job_id` stays 1.

Follow existing `_get_or_create_candidate` / unique constraints; reuse patterns from `test_requisitions.py::test_analyze_with_requisition_id_links_pipeline` for tenant+req+user setup (`auth_client` not required if calling the service directly).

- [ ] **Step 2: Run** — Expected: FAIL (`requisition_id` None on result)

- [ ] **Step 3: Implement** `complete_queue_job`: read `job.job_config["command"]` via `command_from_dict` when present; pass `requisition_id=cmd.requisition_id`, `role_template_id=cmd.role_template_id or template_id`, weights/overrides from command into `_process_single_resume` / `_upsert_screening_result`. After upsert, if `cmd.requisition_id` and `job.user_id`: `_link_to_requisition(...)`. If `AnalysisResult` already exists for `job.id`, skip insert and return True.

- [ ] **Step 4: Run Task 3 tests** — Expected: PASS

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 4: Bounded concurrency + heartbeat (AUD-018, AUD-019)

**Files:**
- Modify: `app/backend/services/queue_manager.py` (`__init__`, `update_heartbeat`, `process_job`, `worker_loop`)
- Modify: `app/backend/tests/test_audit_phase_c_screening.py`

**Interfaces:**
- Produces: `self.max_concurrent_jobs = max(1, min(32, int(os.getenv("QUEUE_MAX_CONCURRENT", "10"))))`. `update_heartbeat` also sets `leased_until = now + timedelta(seconds=self.stale_job_timeout_seconds)`. `process_job` starts/stops a heartbeat task. `worker_loop` uses `asyncio.Semaphore(self.max_concurrent_jobs)` and a per-task `SessionLocal()`.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_two_jobs_overlap_with_concurrency_2(monkeypatch, db, seed_subscription_plans):
    import asyncio, os
    from app.backend.services.queue_manager import QueueManager
    os.environ["QUEUE_MAX_CONCURRENT"] = "2"
    started = []
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_complete(job_id, session):
        started.append(job_id)
        if len(started) >= 2:
            entered.set()
        await asyncio.wait_for(release.wait(), timeout=5)
        return True

    monkeypatch.setattr("app.backend.services.queue_analysis_service.complete_queue_job", slow_complete)
    mgr = QueueManager()
    # enqueue two jobs with distinct fingerprints, claim+process concurrently via worker_loop
    # assert entered.wait() succeeds (overlap), then release.set()
    # assert max(len(started)) semantics: never 3 in-flight if a third job exists
```

Also:

```python
@pytest.mark.asyncio
async def test_heartbeat_renews_lease_during_process(monkeypatch, db, seed_subscription_plans):
    # complete_queue_job sleeps > heartbeat interval (use QUEUE_HEARTBEAT_INTERVAL=0.05 in env)
    # after process, worker_heartbeat and leased_until are newer than claim time
```

```python
def test_heartbeat_task_stops_after_process(monkeypatch, ...):
    # after process_job returns, no leftover heartbeat task (mgr._heartbeat_tasks empty / cancelled)
```

Keep tests deterministic: patch `complete_queue_job`, do not run real scoring.

- [ ] **Step 2: Run** — Expected: FAIL (serial worker, heartbeat does not bump `leased_until`)

- [ ] **Step 3: Implement**

Clamp concurrency in `__init__`.

`update_heartbeat`: write both `worker_heartbeat` and `leased_until`.

`process_job`:

```python
stop = asyncio.Event()
async def _beat():
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_interval_seconds)
            break
        except asyncio.TimeoutError:
            hb_db = SessionLocal()
            try:
                await self.update_heartbeat(job.id, hb_db)
            finally:
                hb_db.close()
beat_task = asyncio.create_task(_beat())
try:
    ... existing process ...
finally:
    stop.set()
    beat_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await beat_task
```

`worker_loop`: remove stale recovery (Task 5 also requires this — do it here so concurrency work does not double-recover). Semaphore + `in_flight` set of tasks. Each task: `db = SessionLocal(); try: job=await get_next_job(db); if job: await process_job(job, db) finally: db.close()`. Poll when no job. On shutdown wait for `in_flight` with `asyncio.wait(..., timeout=...)`.

SQLite `with_for_update(skip_locked=True)` may no-op; tests still prove overlap by launching two `process_job` tasks after two claims, or by stubbing `get_next_job`. If SKIP LOCKED is unreliable in SQLite, the overlap test may call `process_job` twice concurrently on two pre-claimed jobs — that still proves sessions are not shared and semaphore can be unit-tested with a wrapper. Prefer: unit-test semaphore with a small helper `async def _run_claimed(job)` used by worker_loop.

- [ ] **Step 4: Run Task 4 tests** — Expected: PASS

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 5: Single stale-recovery owner (AUD-020)

**Files:**
- Modify: `app/backend/services/queue_manager.py` (`recover_stale_jobs`; confirm `worker_loop` has no recovery)
- Modify: `app/backend/services/scheduler.py` (`recover_stale_jobs` must use `get_queue_manager()`)
- Modify: `app/backend/tests/test_audit_phase_c_screening.py`
- Confirm: `test_audit_queue_quota.py::test_stale_permanent_failure_releases_reserved_quota` still passes (`QueueManager().recover_stale_jobs` remains valid on an instance)

**Interfaces:**
- Produces: `recover_stale_jobs` uses `UPDATE ... WHERE status='processing' AND (heartbeat < threshold OR leased_until < now)` then increments retry **only if** `rowcount == 1` per id (or SELECT FOR UPDATE then mutate). Heartbeating jobs are not selected.

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_concurrent_recovery_increments_retry_once(db, seed_subscription_plans):
    # one processing job, heartbeat 2h ago, retry_count=0
    # await asyncio.gather(mgr.recover_stale_jobs(db), mgr.recover_stale_jobs(db2))
    # assert retry_count == 1 and status == "retrying"

def test_worker_loop_source_has_no_recover_call():
    import inspect
    from app.backend.services.queue_manager import QueueManager
    src = inspect.getsource(QueueManager.worker_loop)
    assert "recover_stale_jobs" not in src

def test_scheduler_uses_singleton():
    import inspect
    from app.backend.services import scheduler
    src = inspect.getsource(scheduler.recover_stale_jobs)
    assert "get_queue_manager" in src
    assert "QueueManager()" not in src

@pytest.mark.asyncio
async def test_fresh_heartbeat_not_recovered(db, seed_subscription_plans):
    # processing job heartbeat=now, leased_until=now+10m → still processing, retry_count unchanged
```

- [ ] **Step 2: Run** — Expected: FAIL (scheduler still `QueueManager()`, worker_loop still recovers, double increment possible)

- [ ] **Step 3: Implement** conditional update; scheduler:

```python
def recover_stale_jobs():
    from app.backend.services.queue_manager import get_queue_manager
    db = SessionLocal()
    try:
        asyncio.run(get_queue_manager().recover_stale_jobs(db))
    ...
```

For concurrent test on SQLite, two `SessionLocal()` sessions; use `begin`/`with_for_update` if available, else `UPDATE ... WHERE retry_count = :old` so the second update matches 0 rows.

- [ ] **Step 4: Run Task 5 tests +** `python -m pytest app/backend/tests/test_audit_queue_quota.py -q --tb=short`  
  Expected: PASS

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 6: DLQ artifact_id replay (AUD-021)

**Files:**
- Create: `alembic/versions/073_phase_c_queue.py` (one revision for DLQ `artifact_id` and idempotency fingerprint; Task 7 fills the idempotency half of `upgrade()`, do not add 074)
- Modify: `app/backend/models/db_models.py` (`DeadLetterJob.artifact_id`)
- Modify: `app/backend/services/queue_manager.py` (`move_to_dead_letter`, `retry_dead_letter_job`, `purge_expired_artifacts`)
- Modify: `app/backend/tests/test_audit_phase_c_screening.py`
- Modify: `app/backend/tests/test_production_hardening.py` if DLQ constructor needs `artifact_id`

**Interfaces:**
- Produces: `DeadLetterJob.artifact_id`. `retry_dead_letter_job` calls `enqueue_job` / fingerprint with stored command + artifact; missing artifact → `ArtifactUnavailable`, status stays `pending`. `purge_expired_artifacts(db)` deletes expired artifacts **not** referenced by pending DLQ or live jobs.

- [ ] **Step 1: Write failing tests** — fail job via `move_to_dead_letter` with a real `AnalysisArtifact`; assert DLQ `artifact_id`. `retry_dead_letter_job` → new job has same `artifact_id` and command. Delete artifact, retry → `ArtifactUnavailable`, DLQ `pending`. Insert expired artifact referenced by pending DLQ; `purge_expired_artifacts` must keep it; unreferenced expired artifact is deleted.

- [ ] **Step 2: Run** — Expected: FAIL (no `artifact_id`)

- [ ] **Step 3: Implement model + 073 migration** (idempotent `add_column` if missing). `move_to_dead_letter` copies `job.artifact_id`. `retry_dead_letter_job`: load artifact by id+tenant; on miss raise `ArtifactUnavailable`; else `enqueue_job` with resume/jd from artifact and command fields; set `reprocessed` only after successful insert in the same `db.commit()`. Map UUID types for SQLite tests.

Alembic 073 skeleton:

```python
"""DLQ artifact_id + idempotency fingerprint (AUD-021/022)

Revision ID: 073_phase_c_queue
Revises: 072_tenant_desired_plan
"""
revision = "073_phase_c_queue"
down_revision = "072_tenant_desired_plan"

def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "dead_letter_jobs" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("dead_letter_jobs")}
        if "artifact_id" not in cols:
            op.add_column("dead_letter_jobs", sa.Column("artifact_id", sa.Uuid(as_uuid=True), nullable=True))
    # Task 7 continues this upgrade() for idempotency_keys
```

If Task 7 is in the same PR (it is), implement the full 073 in Task 6 including a no-op comment for idempotency, then Task 7 fills it — **do not create 074**. One head only.

- [ ] **Step 4: Run Task 6 tests** — Expected: PASS

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 7: Idempotency request fingerprint (AUD-022)

**Files:**
- Modify: `alembic/versions/073_phase_c_queue.py` (idempotency table)
- Modify: `app/backend/models/db_models.py` (`IdempotencyKey`)
- Modify: `app/backend/middleware/idempotency.py`
- Modify: `app/backend/tests/test_audit_phase_c_screening.py`
- Modify: `app/backend/tests/test_production_hardening.py` (`TestIdempotencyTenantScope` — `_lookup` gains fingerprint argument; pass the stored hash)

**Interfaces:**
- Produces: `def request_fingerprint(method: str, path: str, query: str, content_type: str, body: bytes) -> str`. `_lookup(key, tenant_id, endpoint, fingerprint)`. Same key + different fingerprint → `JSONResponse(409, {"detail": "Idempotency key reused with different request"})` without `call_next`. Composite unique `(key, tenant_id, endpoint)`. Migration **deletes all `idempotency_keys` rows** then adds `request_fingerprint String(64) NOT NULL` and unique constraint; drop old PK-on-key-only.

- [ ] **Step 1: Write failing tests** using FastAPI `client` with `X-Idempotency-Key` against a simple mutating test route if one exists; otherwise unit-test middleware helpers plus a tiny `POST` that is already authenticated (e.g. a cheap JSON POST). Required cases:
  - same key + same JSON → second response has `X-Idempotent-Replay: true` and identical body
  - same key + different JSON → 409, handler not invoked (counter)
  - same key different tenant → independent (existing test, updated signature)
  - same key different path → independent
  - expired row does not replay

Patch `_tenant_from_request` if needed to avoid JWT in unit tests of `_lookup`/`_store`.

- [ ] **Step 2: Run** — Expected: FAIL (replay instead of 409)

- [ ] **Step 3: Implement**

```python
async def dispatch(...):
    ...
    body = await request.body()
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    request = Request(request.scope, receive)
    fp = request_fingerprint(request.method, request.url.path, str(request.query_params), request.headers.get("content-type") or "", body)
    stored = _lookup(key, tenant_id, endpoint, fp)
    if stored == "conflict":
        return JSONResponse({"detail": "Idempotency key reused with different request"}, status_code=409)
    if stored is not None:
        status, resp_body = stored
        return JSONResponse(content=resp_body, status_code=status, headers={"X-Idempotent-Replay": "true"})
    ...
```

`_lookup`: if row exists for key+tenant+endpoint and not expired: if `row.request_fingerprint != fp` return `"conflict"`; else return status/body.

Canonical JSON: `json.dumps(json.loads(body), sort_keys=True, separators=(",", ":"))` when content-type starts with `application/json` and parse succeeds; else raw body bytes in the hash.

IdempotencyKey:

```python
key = Column(String(128), primary_key=True)
tenant_id = Column(Integer, primary_key=True)
endpoint = Column(String(200), primary_key=True)
request_fingerprint = Column(String(64), nullable=False)
```

SQLite tests: `create_all` recreate — add `request_fingerprint` to any raw DDL if present.

- [ ] **Step 4: Run Task 7 tests +** `python -m pytest app/backend/tests/test_production_hardening.py::TestIdempotencyTenantScope -q --tb=short`  
  Expected: PASS

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 8: ATS integration secrets (AUD-015)

**Files:**
- Create: `app/backend/services/integration_secrets.py`
- Create: `app/backend/tests/test_audit_phase_c_secrets_entitlement.py`
- Modify: `app/backend/routes/ats.py`, `app/backend/services/ats_connector.py`
- Modify: `app/backend/models/schemas.py` (`ATSConnectionOut`: `api_key_configured: bool`, `webhook_secret_configured: bool`)
- Modify: `app/backend/tests/conftest.py` — `os.environ.setdefault("INTEGRATION_MASTER_KEY", "0" * 64)` (32-byte hex) so tests encrypt

**Interfaces:**
- Produces:
  - `PREFIX = "enc:"`
  - `def encrypt_secret(plaintext: str | None) -> str | None`
  - `def decrypt_secret(stored: str | None) -> str | None`
  - `def secret_configured(stored: str | None) -> bool`
- `encrypt_secret` uses AES-256-GCM, format `enc:v1:<base64url(nonce||ct||tag)>`. Key from `INTEGRATION_MASTER_KEY` (64 hex chars or 32-byte urlsafe). Optional `INTEGRATION_MASTER_KEY_PREVIOUS` as `v0`.
- Missing key outside TESTING → `RuntimeError` on encrypt (fail closed). TESTING uses the conftest default.
- `decrypt_secret`: if stored is nonempty and not `enc:v`, return plaintext as-is (lazy migrate caller encrypts). If prefix present, pick versioned key. Fail without putting secret in the exception message.

- [ ] **Step 1: Write failing tests**

```python
# app/backend/tests/test_audit_phase_c_secrets_entitlement.py
import os
os.environ.setdefault("INTEGRATION_MASTER_KEY", "ab" * 32)

from app.backend.services.integration_secrets import decrypt_secret, encrypt_secret, secret_configured

def test_roundtrip_not_plaintext():
    ct = encrypt_secret("ghp_secret_value")
    assert ct != "ghp_secret_value"
    assert ct.startswith("enc:v1:")
    assert decrypt_secret(ct) == "ghp_secret_value"

def test_none_passthrough():
    assert encrypt_secret(None) is None
    assert decrypt_secret(None) is None
    assert secret_configured(None) is False

def test_wrong_key_does_not_include_secret(monkeypatch):
    ct = encrypt_secret("super-secret")
    monkeypatch.setenv("INTEGRATION_MASTER_KEY", "cd" * 32)
    # force reload or pass key version mismatch
    import app.backend.services.integration_secrets as sec
    monkeypatch.setattr(sec, "_current_keys", lambda: {1: bytes.fromhex("cd" * 32)})
    with pytest.raises(Exception) as ei:
        decrypt_secret(ct)
    assert "super-secret" not in str(ei.value)
```

Plus an ATS create test: POST connection with `api_key="plain-key"`; DB `api_key` starts with `enc:v`; GET JSON has `api_key_configured True` and no `"plain-key"`. Adapter path: set connection, call the function that builds headers; Authorization uses decrypted value (mock HTTP).

- [ ] **Step 2: Run** — Expected: FAIL (module missing)

- [ ] **Step 3: Implement encrypt/decrypt; wrap ATS create/update/reset writes; `_serialize_connection` adds configured flags and never dumps secrets; `ats_connector` decrypts `api_key` / `api_secret` / `webhook_secret` only when sending or verifying HMAC. After a successful `decrypt_secret` of legacy plaintext, write `encrypt_secret` back onto the ORM field and commit if the caller has a session (helper `decrypt_and_upgrade(conn, field_name, db)`).

- [ ] **Step 4: Run Task 8 tests + existing ATS tenant-boundary tests** — Expected: PASS (inbound HMAC tests must still set a secret; connector decrypts it)

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 9: DNS IP pinning

**Files:**
- Modify: `app/backend/services/url_safety.py`
- Modify: `app/backend/tests/test_audit_phase_b_outbound.py`

**Interfaces:**
- Produces: `def resolve_public_ips(host: str) -> list[str]` (unique, all `_is_public_ip`). `def pinned_request_url(url: str, ip: str) -> str` (replace hostname with IP, keep port/path). Each `safe_request` hop: resolve once, set `headers["Host"]` to original hostname if missing, `client.request(url=pinned, headers=..., extensions={"sni_hostname": original_host}` or httpx TLS equivalent). Try allowed IPs in order; no second `getaddrinfo` on failure of an IP except to use the already-resolved list.

- [ ] **Step 1: Write failing test**

```python
def test_connects_to_resolved_ip_with_original_host_header():
    seen = {}
    def request_impl(**kwargs):
        seen.update(kwargs)
        return httpx.Response(200, request=httpx.Request("GET", kwargs["url"]), content=b"ok")
    with patch("app.backend.services.url_safety.socket.getaddrinfo", return_value=[
        (None, None, None, None, ("93.184.216.34", 0)),
    ]), patch("app.backend.services.url_safety.httpx.Client", return_value=_sync_client(request_impl)):
        safe_request("GET", "https://example.com/a")
    from urllib.parse import urlparse
    assert urlparse(seen["url"]).hostname == "93.184.216.34"
    host = {k.lower(): v for k, v in (seen.get("headers") or {}).items()}
    assert host.get("host") == "example.com"
```

- [ ] **Step 2: Run** — Expected: FAIL (request URL still `example.com`)

- [ ] **Step 3: Implement pinning in both sync and async helpers. Preserve existing redirect/SSRF tests (they mock `client.request`; update mocks if they assert URL == original hostname).

- [ ] **Step 4: Run** `python -m pytest app/backend/tests/test_audit_phase_b_outbound.py -q --tb=short` — Expected: PASS

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 10: Stream + abort at max_bytes

**Files:**
- Modify: `app/backend/services/url_safety.py` (`_read_limited`, `_next_hop`, `safe_request`, `safe_request_async`)
- Modify: `app/backend/services/video_downloader.py` if it still uses `response.content` after `safe_request_async`
- Modify: `app/backend/tests/test_audit_phase_b_outbound.py`

**Interfaces:**
- Produces: `def _read_limited(response: httpx.Response, max_bytes: int) -> bytes` — iterate `iter_bytes(chunk_size=65536)`; if `len(buf) + len(chunk) > max_bytes`: `response.close()`; raise `UnsafeURLError("Response body exceeds maximum size")`. Do not assign `response.content` before this. Build the returned `Response` with the buffered bytes (`httpx.Response(status, headers=..., content=buf, request=response.request)`).

- [ ] **Step 1: Write failing test** — a mock response whose `.content` is huge if accessed, but `iter_bytes` yields 3 chunks of 400k; `max_bytes=500_000` must raise and must not concatenate all chunks. Optionally assert `.content` was never read (property that raises).

- [ ] **Step 2: Run** — Expected: FAIL (current code uses `len(response.content)`)

- [ ] **Step 3: Replace `_next_hop` size check with `_read_limited`. Use `client.stream(...)` / `async with client.stream` then read. Redirect hops also limited.

- [ ] **Step 4: Run** `python -m pytest app/backend/tests/test_audit_phase_b_outbound.py app/backend/tests/test_video_downloader.py -q --tb=short` — Expected: PASS

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 11: Effective-plan resolver for capability reads

**Files:**
- Modify: `app/backend/routes/analyze.py` (three `_get_plan_limits(tenant.plan)` sites)
- Modify: `app/backend/routes/subscription.py` (`get_my_subscription` current plan/limits)
- Modify: `app/backend/tests/test_audit_phase_c_secrets_entitlement.py` (and/or `test_audit_p0_entitlements.py`)

**Interfaces:**
- Consumes: `get_tenant_plan(db, tenant.id)`
- Produces: batch analyze and subscription GET limits from the resolver. Unpaid tenant with paid `plan_id` → starter/free limits.

- [ ] **Step 1: Write failing test** — tenant `plan_id` = growth, `subscription_status` not trialing/active/past_due; call the same helper analyze uses or GET `/api/subscription`; assert limits match starter (`get_tenant_plan`), not growth. If GET `/api/subscription` currently exposes growth limits, that is the bug.

- [ ] **Step 2: Run** — Expected: FAIL if subscription/analyze still use `tenant.plan`

- [ ] **Step 3: Replace capability reads:

```python
from app.backend.services.plan_entitlement_service import get_tenant_plan
plan = get_tenant_plan(db, tenant.id)
limits = _get_plan_limits(plan) if plan else ...
```

Do **not** change webhook `apply_verified_paid_plan`, admin change-plan, or audit of stored `tenant.plan.name` on mutate.

Grep `tenant.plan` in `app/backend`; remaining uses must be assignment/display of stored plan, not limits/features.

- [ ] **Step 4: Run** `python -m pytest app/backend/tests/test_audit_p0_entitlements.py app/backend/tests/test_audit_phase_c_secrets_entitlement.py -q --tb=short` — Expected: PASS

- [ ] **Step 5: Commit** — skip unless asked.

---

### Task 12: Local gate (no GitHub until asked)

- [ ] **Step 1: Run**

```
python -m pytest app/backend/tests/test_audit_phase_c_screening.py app/backend/tests/test_audit_phase_c_secrets_entitlement.py app/backend/tests/test_audit_queue_quota.py app/backend/tests/test_audit_phase_b_outbound.py app/backend/tests/test_audit_p0_entitlements.py app/backend/tests/test_production_hardening.py::TestDeadLetterQueue app/backend/tests/test_production_hardening.py::TestIdempotencyTenantScope -q --tb=short
```

Also: `python -m alembic heads` must show a **single** head `073_phase_c_queue`.

Grep `worker_loop` for `recover_stale_jobs` — must be absent. Grep `scheduler.recover_stale_jobs` for `get_queue_manager`. Grep `ats_connector.py` for `connection.api_key` used without `decrypt_secret` — outbound must decrypt.

- [ ] **Step 2: Fix regressions in Phase C files only** (plus quota/hardening/outbound/entitlement tests you touched).

- [ ] **Step 3: Stop. Report results. Wait for the user to say commit/push.**

---

## Spec coverage

| Spec item | Task |
|---|---|
| ScreeningCommand v1 fields + persist | 1–2 |
| Fingerprint v1 / dedup | 1–2 |
| Cross-tenant requisition rejected before enqueue | 2 |
| complete_queue_job requisition + RequisitionCandidate + idempotent AnalysisResult | 3 |
| Semaphore 1–32, own session | 4 |
| Heartbeat 30s / lease 10m / finally stop | 4 |
| APScheduler-only recovery, row lock, quota release | 5 |
| DLQ artifact_id, retry, purge skip | 6 |
| Idempotency fingerprint, 409, composite key, delete old rows | 7 |
| AUD-015 AES-GCM + migrate ATS secrets | 8 |
| DNS pin to resolved public IPs | 9 |
| Stream abort at max_bytes | 10 |
| get_tenant_plan for capability | 11 |
| Local pytest + single alembic head | 12 |
| No new public APIs / no cloud KMS / main only | Global constraints |
