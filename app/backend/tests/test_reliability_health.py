"""Liveness vs readiness."""


def test_liveness_ok_when_db_down(client, monkeypatch):
    from app.backend import main as mainmod
    from sqlalchemy.exc import OperationalError

    monkeypatch.setattr(
        mainmod,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(OperationalError("down", {}, None)),
    )
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["live"] is True


def test_readiness_503_when_db_down(client, monkeypatch):
    from app.backend import main as mainmod
    from sqlalchemy.exc import OperationalError

    monkeypatch.setattr(
        mainmod,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(OperationalError("down", {}, None)),
    )
    resp = client.get("/ready")
    assert resp.status_code == 503
    assert resp.json()["status"] == "not_ready"


def test_readiness_exemptions_are_exact():
    from app.backend.middleware.csrf import CSRFMiddleware
    from app.backend.middleware.rate_limit import RateLimitMiddleware

    csrf = CSRFMiddleware(lambda scope, receive, send: None)
    rate_limit = RateLimitMiddleware(lambda scope, receive, send: None)
    assert csrf._is_exempt("/ready") is True
    assert csrf._is_exempt("/ready/admin") is False
    assert rate_limit._is_whitelisted("/ready") is True
    assert rate_limit._is_whitelisted("/ready/admin") is False
