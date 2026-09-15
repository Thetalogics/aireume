# app/backend/tests/test_audit_phase_b_outbound.py
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.backend.services.url_safety import (
    UnsafeURLError,
    _origin_changed,
    safe_request,
    safe_request_async,
    validate_public_url,
)

_PUBLIC_IP = "93.184.216.34"
_EXAMPLE = "https://example.com/a"


def _fake_getaddrinfo(host, *args, **kwargs):
    """Public A record for allowed hosts; keep literals like 10.0.0.1 private."""
    if host in {"10.0.0.1", "127.0.0.1", "::1"}:
        return [(None, None, None, None, (host, 0))]
    return [(None, None, None, None, (_PUBLIC_IP, 0))]


def _sync_client(request_impl):
    client = MagicMock()
    client.request.side_effect = request_impl
    client.__enter__.return_value = client
    client.__exit__.return_value = False
    return client


def _async_client(request_impl):
    client = MagicMock()
    client.request = AsyncMock(side_effect=request_impl)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _response(status, url, *, location=None, content=b"ok"):
    headers = {}
    if location is not None:
        headers["Location"] = location
    return httpx.Response(
        status,
        headers=headers,
        content=content,
        request=httpx.Request("GET", url),
    )


def test_rejects_userinfo():
    with pytest.raises(UnsafeURLError):
        validate_public_url("https://user:pass@example.com/path")


def test_require_https_rejects_http():
    with pytest.raises(UnsafeURLError):
        validate_public_url("http://example.com/hook", require_https=True)


def test_rejects_localhost_and_rfc1918():
    for url in (
        "http://localhost/x",
        "https://127.0.0.1/x",
        "https://10.0.0.1/x",
        "https://[::1]/x",
    ):
        with pytest.raises(UnsafeURLError):
            validate_public_url(url)


def test_rejects_hostname_resolving_private():
    with patch(
        "app.backend.services.url_safety.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("10.1.2.3", 0))],
    ):
        with pytest.raises(UnsafeURLError):
            validate_public_url("https://evil.example/x")


def test_allows_public_https():
    with patch(
        "app.backend.services.url_safety.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("93.184.216.34", 0))],
    ):
        assert validate_public_url("https://example.com/a") == "https://example.com/a"


def test_safe_request_public_url_200_once():
    requested = []

    def request(method, url, **kwargs):
        requested.append(url)
        return _response(200, url)

    client = _sync_client(request)
    with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        with patch("app.backend.services.url_safety.httpx.Client", return_value=client) as client_cls:
            resp = safe_request("GET", _EXAMPLE)

    assert resp.status_code == 200
    assert requested == [_EXAMPLE]
    client.request.assert_called_once()
    assert client_cls.call_args.kwargs.get("follow_redirects") is False


def test_safe_request_rejects_redirect_to_private_ip():
    requested = []

    def request(method, url, **kwargs):
        requested.append(url)
        return _response(302, url, location="https://10.0.0.1/")

    client = _sync_client(request)
    with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        with patch("app.backend.services.url_safety.httpx.Client", return_value=client):
            with pytest.raises(UnsafeURLError):
                safe_request("GET", _EXAMPLE)

    assert requested == [_EXAMPLE]
    assert all("10.0.0.1" not in u for u in requested)


def test_safe_request_rejects_redirect_to_file_scheme():
    requested = []

    def request(method, url, **kwargs):
        requested.append(url)
        return _response(302, url, location="file:///etc/passwd")

    client = _sync_client(request)
    with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        with patch("app.backend.services.url_safety.httpx.Client", return_value=client):
            with pytest.raises(UnsafeURLError):
                safe_request("GET", _EXAMPLE)

    assert requested == [_EXAMPLE]


def test_safe_request_rejects_fourth_redirect():
    hops = [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
        "https://example.com/d",
        "https://example.com/e",
    ]
    requested = []

    def request(method, url, **kwargs):
        requested.append(url)
        idx = hops.index(url)
        return _response(302, url, location=hops[idx + 1])

    client = _sync_client(request)
    with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        with patch("app.backend.services.url_safety.httpx.Client", return_value=client):
            with pytest.raises(UnsafeURLError):
                safe_request("GET", hops[0])

    assert requested == hops[:4]


def test_safe_request_rejects_missing_location():
    def request(method, url, **kwargs):
        return _response(302, url)

    client = _sync_client(request)
    with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        with patch("app.backend.services.url_safety.httpx.Client", return_value=client):
            with pytest.raises(UnsafeURLError):
                safe_request("GET", _EXAMPLE)
    client.request.assert_called_once()


def test_safe_request_rejects_redirect_loop():
    requested = []

    def request(method, url, **kwargs):
        requested.append(url)
        return _response(302, url, location=_EXAMPLE)

    client = _sync_client(request)
    with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        with patch("app.backend.services.url_safety.httpx.Client", return_value=client):
            with pytest.raises(UnsafeURLError):
                safe_request("GET", _EXAMPLE)

    assert requested == [_EXAMPLE]


def test_safe_request_rejects_oversized_body():
    def request(method, url, **kwargs):
        return _response(200, url, content=b"x" * 64)

    client = _sync_client(request)
    with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        with patch("app.backend.services.url_safety.httpx.Client", return_value=client):
            with pytest.raises(UnsafeURLError):
                safe_request("GET", _EXAMPLE, max_bytes=32)


@pytest.mark.asyncio
async def test_safe_request_async_public_url_200_once():
    requested = []

    async def request(method, url, **kwargs):
        requested.append(url)
        return _response(200, url)

    client = _async_client(request)
    with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        with patch("app.backend.services.url_safety.httpx.AsyncClient", return_value=client) as client_cls:
            resp = await safe_request_async("GET", _EXAMPLE)

    assert resp.status_code == 200
    assert requested == [_EXAMPLE]
    client.request.assert_awaited_once()
    assert client_cls.call_args.kwargs.get("follow_redirects") is False


@pytest.mark.asyncio
async def test_safe_request_async_rejects_redirect_to_private_ip():
    requested = []

    async def request(method, url, **kwargs):
        requested.append(url)
        return _response(302, url, location="https://10.0.0.1/")

    client = _async_client(request)
    with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        with patch("app.backend.services.url_safety.httpx.AsyncClient", return_value=client):
            with pytest.raises(UnsafeURLError):
                await safe_request_async("GET", _EXAMPLE)

    assert requested == [_EXAMPLE]
    assert all("10.0.0.1" not in u for u in requested)


def test_origin_changed_includes_scheme_default_ports():
    assert _origin_changed("https://example.com/a", "https://example.com:443/b") is False
    assert _origin_changed("https://example.com:443/a", "https://example.com/b") is False
    assert _origin_changed("http://example.com/a", "http://example.com:80/b") is False
    assert _origin_changed("https://example.com/a", "https://example.com:8443/b") is True
    assert _origin_changed("http://example.com/a", "http://example.com:8080/b") is True
    assert _origin_changed("http://example.com/a", "https://example.com/a") is True


def test_safe_request_post_302_does_not_follow_as_get():
    host_a = "https://public-a.example/hook"
    host_b = "https://public-b.example/hook"
    calls = []

    def request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        if url == host_a:
            return _response(302, url, location=host_b)
        return _response(200, url)

    client = _sync_client(request)
    with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        with patch("app.backend.services.url_safety.httpx.Client", return_value=client):
            with pytest.raises(UnsafeURLError):
                safe_request("POST", host_a, json={"event": "x"})

    assert [c["url"] for c in calls] == [host_a]
    assert calls[0]["method"].upper() == "POST"
    assert all(c["url"] != host_b for c in calls)
    assert not any(c["url"] == host_b and c["method"].upper() == "GET" for c in calls)


def test_safe_request_strips_auth_on_cross_host_307():
    host_a = "https://public-a.example/hook"
    host_b = "https://public-b.example/hook"
    calls = []

    def request(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        if url == host_a:
            return _response(307, url, location=host_b)
        return _response(200, url)

    client = _sync_client(request)
    with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        with patch("app.backend.services.url_safety.httpx.Client", return_value=client):
            resp = safe_request(
                "POST",
                host_a,
                headers={"Authorization": "Bearer secret", "X-Custom": "keep"},
                json={"event": "x"},
            )

    assert resp.status_code == 200
    assert len(calls) == 2
    first, second = calls
    assert first["url"] == host_a
    assert first["method"].upper() == "POST"
    assert first.get("headers", {}).get("Authorization") == "Bearer secret"
    assert second["url"] == host_b
    assert second["method"].upper() == "POST"
    second_headers = second.get("headers") or {}
    assert "Authorization" not in second_headers
    assert not any(k.lower() == "authorization" for k in second_headers)
    assert second.get("json") == {"event": "x"}
    assert second_headers.get("X-Custom") == "keep"


def test_validate_webhook_url_requires_https_and_public_dns():
    from app.backend.services.webhook_service import validate_webhook_url
    ok, _ = validate_webhook_url("http://example.com/h")
    assert ok is False
    with patch("app.backend.services.url_safety.socket.getaddrinfo", return_value=[
        (None, None, None, None, ("127.0.0.1", 0)),
    ]):
        ok, err = validate_webhook_url("https://internal.example/h")
        assert ok is False
        assert err


def test_send_webhook_does_not_connect_to_private_hostname():
    from app.backend.services.webhook_service import _send_webhook

    with patch(
        "app.backend.services.url_safety.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("127.0.0.1", 0))],
    ):
        with patch("httpx.Client") as httpx_client:
            with patch("app.backend.services.url_safety.httpx.Client") as safety_client:
                status, body, success = _send_webhook(
                    "https://evil.local/h", {"event": "x"}, "secret"
                )

    assert success is False
    assert status == 0
    assert body
    httpx_client.assert_not_called()
    safety_client.assert_not_called()


@pytest.mark.asyncio
async def test_scrape_jd_rejects_redirect_to_private_ip():
    from app.backend.services.jd_scraper import scrape_jd

    requested = []

    async def request(method, url, **kwargs):
        requested.append(url)
        return _response(302, url, location="https://127.0.0.1/")

    client = _async_client(request)
    with patch("app.backend.services.jd_scraper._BS4_AVAILABLE", True):
        with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
            with patch("app.backend.services.url_safety.httpx.AsyncClient", return_value=client):
                with pytest.raises(UnsafeURLError):
                    await scrape_jd(_EXAMPLE)

    assert requested == [_EXAMPLE]
    assert all("127.0.0.1" not in u for u in requested)


@pytest.mark.asyncio
async def test_scrape_jd_passes_eight_mib_max_bytes():
    from app.backend.services.jd_scraper import scrape_jd

    with patch("app.backend.services.jd_scraper._BS4_AVAILABLE", True):
        with patch(
            "app.backend.services.jd_scraper.safe_request_async",
            new_callable=AsyncMock,
            side_effect=UnsafeURLError("too large"),
        ) as mocked:
            with pytest.raises(UnsafeURLError):
                await scrape_jd(_EXAMPLE)

    mocked.assert_awaited_once()
    assert mocked.await_args.kwargs.get("max_bytes") == 8 * 1024 * 1024


def test_extract_jd_url_scrape_unsafe_returns_400(auth_client):
    with patch(
        "app.backend.routes.jd_url.validate_public_url",
        side_effect=lambda url: url,
    ), patch(
        "app.backend.routes.jd_url.scrape_jd",
        side_effect=UnsafeURLError("Redirect missing Location"),
    ):
        resp = auth_client.post("/api/jd/extract-url", json={"url": "https://example.com/job"})
    assert resp.status_code == 400
    assert "Redirect missing Location" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_resolve_zoom_url_rejects_redirect_to_private_ip():
    from app.backend.services.video_downloader import resolve_zoom_url

    requested = []

    async def request(method, url, **kwargs):
        requested.append(url)
        return _response(302, url, location="https://10.0.0.1/")

    client = _async_client(request)
    with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        with patch("app.backend.services.url_safety.httpx.AsyncClient", return_value=client):
            with pytest.raises(ValueError) as exc:
                await resolve_zoom_url(_EXAMPLE)

    assert str(exc.value) == "URL is not allowed"
    assert requested == [_EXAMPLE]
    assert all("10.0.0.1" not in u for u in requested)


@pytest.mark.asyncio
async def test_http_download_rejects_redirect_to_private_ip():
    from app.backend.services.video_downloader import _http_download

    requested = []

    async def request(method, url, **kwargs):
        requested.append(url)
        return _response(302, url, location="https://10.0.0.1/")

    client = _async_client(request)
    with patch("app.backend.services.url_safety.socket.getaddrinfo", side_effect=_fake_getaddrinfo):
        with patch("app.backend.services.url_safety.httpx.AsyncClient", return_value=client):
            with pytest.raises(ValueError) as exc:
                await _http_download(_EXAMPLE, "direct")

    assert str(exc.value) == "URL is not allowed"
    assert requested == [_EXAMPLE]
    assert all("10.0.0.1" not in u for u in requested)


def test_create_ats_connection_rejects_loopback_base_url(auth_client, seed_subscription_plans):
    resp = auth_client.post(
        "/api/ats/connections",
        json={
            "provider": "generic",
            "label": "Loopback ATS",
            "base_url": "http://127.0.0.1",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "ATS URL is not allowed"


def test_update_ats_connection_rejects_loopback_base_url(auth_client, db, seed_subscription_plans):
    created = auth_client.post(
        "/api/ats/connections",
        json={"provider": "generic", "label": "Safe ATS"},
    )
    assert created.status_code == 201
    conn_id = created.json()["id"]

    resp = auth_client.put(
        f"/api/ats/connections/{conn_id}",
        json={"base_url": "http://127.0.0.1"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "ATS URL is not allowed"


@pytest.mark.asyncio
async def test_push_candidate_status_does_not_connect_when_base_url_dns_is_private(
    db, seed_subscription_plans
):
    from app.backend.models.db_models import ATSConnection, Candidate, Tenant
    from app.backend.services.ats_connector import ATSConnector

    tenant = Tenant(name="ATS SSRF", slug="ats-ssrf")
    db.add(tenant)
    db.flush()
    cand = Candidate(tenant_id=tenant.id, name="Ok", email="ok@x.com")
    conn = ATSConnection(
        tenant_id=tenant.id,
        provider="generic",
        label="Evil ATS",
        base_url="https://evil.example/hook",
        is_active=True,
    )
    db.add_all([cand, conn])
    db.commit()
    db.refresh(cand)

    with patch(
        "app.backend.services.url_safety.socket.getaddrinfo",
        return_value=[(None, None, None, None, ("127.0.0.1", 0))],
    ):
        with patch("httpx.AsyncClient") as httpx_client:
            with patch("app.backend.services.url_safety.httpx.AsyncClient") as safety_client:
                result = await ATSConnector(db).push_candidate_status(conn, cand.id)

    assert result["success"] is False
    httpx_client.assert_not_called()
    safety_client.assert_not_called()


def _hmac_hex(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_verify_inbound_webhook_non_ascii_signature_returns_false():
    from app.backend.services.ats_connector import ATSConnector

    conn = MagicMock()
    conn.webhook_secret = "whsec_correct"
    body = b'{"status":"hired"}'
    connector = ATSConnector(MagicMock())
    assert connector.verify_inbound_webhook(conn, "café-not-ascii", body) is False
    assert connector.verify_inbound_webhook(conn, _hmac_hex("whsec_correct", body), body) is True


def _ats_webhook_fixture(db, *, secret="", tenant_slug="ats-wh"):
    from app.backend.models.db_models import ATSConnection, Candidate, ScreeningResult, Tenant

    tenant = Tenant(name="ATS WH", slug=tenant_slug)
    db.add(tenant)
    db.flush()
    cand = Candidate(tenant_id=tenant.id, name="Own", email=f"{tenant_slug}@x.com")
    db.add(cand)
    db.flush()
    screening = ScreeningResult(
        tenant_id=tenant.id,
        candidate_id=cand.id,
        resume_text="r",
        jd_text="jd",
        parsed_data="{}",
        analysis_result="{}",
        status="pending",
    )
    conn = ATSConnection(
        tenant_id=tenant.id,
        provider="generic",
        label="Inbound ATS",
        base_url="https://example.test",
        webhook_secret=secret,
        is_active=True,
    )
    db.add_all([screening, conn])
    db.commit()
    db.refresh(screening)
    db.refresh(conn)
    return conn, screening


def _post_ats_webhook(client, conn_id, payload: dict, signature: str | None):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-ATS-Signature"] = signature
    return client.post(f"/api/ats/webhook/{conn_id}", content=body, headers=headers), body


def test_inbound_ats_webhook_blank_secret_returns_403_without_writes(client, db):
    from app.backend.models.db_models import ATSSyncLog, ScreeningResult

    conn, screening = _ats_webhook_fixture(db, secret="")
    payload = {"status": "hired", "candidate_id": screening.candidate_id, "entity_type": "candidate_status"}
    resp, _ = _post_ats_webhook(client, conn.id, payload, "deadbeef")
    assert resp.status_code == 403
    db.expire_all()
    assert db.query(ATSSyncLog).filter(ATSSyncLog.connection_id == conn.id).count() == 0
    assert db.get(ScreeningResult, screening.id).status == "pending"


def test_inbound_ats_webhook_wrong_signature_returns_403_without_writes(client, db):
    from app.backend.models.db_models import ATSSyncLog, ScreeningResult

    secret = "whsec_correct"
    conn, screening = _ats_webhook_fixture(db, secret=secret, tenant_slug="ats-wh-bad")
    payload = {"status": "hired", "candidate_id": screening.candidate_id}
    body = json.dumps(payload).encode()
    wrong = _hmac_hex("whsec_other", body)
    resp, _ = _post_ats_webhook(client, conn.id, payload, wrong)
    assert resp.status_code == 403
    db.expire_all()
    assert db.query(ATSSyncLog).filter(ATSSyncLog.connection_id == conn.id).count() == 0
    assert db.get(ScreeningResult, screening.id).status == "pending"


def test_inbound_ats_webhook_valid_hmac_returns_200(client, db):
    from app.backend.models.db_models import ATSSyncLog, ScreeningResult

    secret = "whsec_correct"
    conn, screening = _ats_webhook_fixture(db, secret=secret, tenant_slug="ats-wh-ok")
    payload = {"status": "hired", "candidate_id": screening.candidate_id, "entity_type": "candidate_status"}
    body = json.dumps(payload).encode()
    resp, _ = _post_ats_webhook(client, conn.id, payload, _hmac_hex(secret, body))
    assert resp.status_code == 200
    db.expire_all()
    assert db.query(ATSSyncLog).filter(ATSSyncLog.connection_id == conn.id).count() == 1
    assert db.get(ScreeningResult, screening.id).status == "hired"

    prefixed = _post_ats_webhook(
        client, conn.id, payload, "sha256=" + _hmac_hex(secret, body)
    )[0]
    assert prefixed.status_code == 200


def test_inbound_ats_webhook_cross_tenant_candidate_does_not_update(client, db):
    from app.backend.models.db_models import Candidate, ScreeningResult, Tenant

    secret = "whsec_correct"
    conn, _own = _ats_webhook_fixture(db, secret=secret, tenant_slug="ats-wh-a")
    other = Tenant(name="Other ATS", slug="ats-wh-b")
    db.add(other)
    db.flush()
    foreign = Candidate(tenant_id=other.id, name="Foreign", email="foreign-wh@x.com")
    db.add(foreign)
    db.flush()
    foreign_sr = ScreeningResult(
        tenant_id=other.id,
        candidate_id=foreign.id,
        resume_text="r",
        jd_text="jd",
        parsed_data="{}",
        analysis_result="{}",
        status="pending",
    )
    db.add(foreign_sr)
    db.commit()
    db.refresh(foreign_sr)

    payload = {"status": "hired", "candidate_id": foreign.id}
    body = json.dumps(payload).encode()
    resp, _ = _post_ats_webhook(client, conn.id, payload, _hmac_hex(secret, body))
    assert resp.status_code == 200
    db.expire_all()
    assert db.get(ScreeningResult, foreign_sr.id).status == "pending"
