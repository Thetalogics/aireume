from datetime import datetime, timedelta, timezone

from app.backend.models.db_models import WorkerHeartbeat
from app.backend.services.worker_heartbeat import ROLE, touch_worker_heartbeat, worker_heartbeat_fresh
from app.backend.worker_healthcheck import main as worker_health_main


class _HealthyDb:
    def __init__(self, inner):
        self._inner = inner

    def execute(self, *args, **kwargs):
        return self._inner.execute(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self._inner.get(*args, **kwargs)

    def query(self, *args, **kwargs):
        return self._inner.query(*args, **kwargs)

    def close(self):
        return None


class _DeadDb:
    def execute(self, *args, **kwargs):
        raise RuntimeError("database unavailable")

    def close(self):
        return None


def test_w1_recent_heartbeat_healthy(db, monkeypatch):
    touch_worker_heartbeat(db, detail="ok")
    assert worker_heartbeat_fresh(db) is True
    monkeypatch.setattr("app.backend.worker_healthcheck.SessionLocal", lambda: _HealthyDb(db))
    monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    assert worker_health_main() == 0


def test_w2_stale_heartbeat_unhealthy(db, monkeypatch):
    db.merge(WorkerHeartbeat(role=ROLE, updated_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
    db.commit()
    assert worker_heartbeat_fresh(db) is False
    monkeypatch.setattr("app.backend.worker_healthcheck.SessionLocal", lambda: _HealthyDb(db))
    monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    assert worker_health_main() == 1


def test_w3_db_unavailable_unhealthy(monkeypatch):
    monkeypatch.setattr("app.backend.worker_healthcheck.SessionLocal", lambda: _DeadDb())
    monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    assert worker_health_main() == 1


def test_w4_redis_required_unavailable_unhealthy(db, monkeypatch):
    touch_worker_heartbeat(db, detail="ok")
    monkeypatch.setattr("app.backend.worker_healthcheck.SessionLocal", lambda: _HealthyDb(db))
    monkeypatch.setenv("REDIS_REQUIRED", "1")
    monkeypatch.setattr(
        "app.backend.services.shared_cache.redis_is_healthy",
        lambda: False,
    )
    assert worker_health_main() == 1
