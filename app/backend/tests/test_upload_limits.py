import inspect

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from app.backend.main import RequestSizeLimitMiddleware
from app.backend.middleware.idempotency import IdempotencyMiddleware
from app.backend.services.upload_limits import (
    BATCH_UPLOAD_MAX_BYTES,
    DEFAULT_REQUEST_MAX_BYTES,
    VIDEO_UPLOAD_MAX_BYTES,
    is_multipart_upload_path,
    limit_for_path,
)


def test_u_limits_are_internally_consistent():
    assert DEFAULT_REQUEST_MAX_BYTES == 25 * 1024 * 1024
    assert VIDEO_UPLOAD_MAX_BYTES == 200 * 1024 * 1024
    assert BATCH_UPLOAD_MAX_BYTES == 500 * 1024 * 1024
    assert limit_for_path("/api/video/upload") == VIDEO_UPLOAD_MAX_BYTES
    assert limit_for_path("/api/analyze/video") == VIDEO_UPLOAD_MAX_BYTES
    assert limit_for_path("/api/analyze/batch") == BATCH_UPLOAD_MAX_BYTES
    assert is_multipart_upload_path("/api/video/upload") is True
    assert is_multipart_upload_path("/api/auth/login") is False


def _video_app(monkeypatch, video_max=2 * 1024 * 1024):
    import app.backend.services.upload_limits as limits

    monkeypatch.setattr(limits, "VIDEO_UPLOAD_MAX_BYTES", video_max)
    reached = {"ok": False, "bytes": 0}

    app = FastAPI()

    @app.post("/api/video/upload")
    async def video(request: Request):
        reached["ok"] = True
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
        reached["bytes"] = total
        return JSONResponse({"ok": True, "bytes": total})

    @app.post("/api/auth/login")
    async def login(request: Request):
        body = await request.body()
        return JSONResponse({"ok": True, "bytes": len(body)})

    app.add_middleware(RequestSizeLimitMiddleware)
    return TestClient(app, raise_server_exceptions=False), reached


def _multipart(size: int, filename="clip.mp4"):
    return {"file": (filename, b"v" * size, "video/mp4")}


def test_u1_under_limit_multipart_reaches_endpoint(monkeypatch):
    client, reached = _video_app(monkeypatch)
    resp = client.post("/api/video/upload", files=_multipart(1500 * 1024))
    assert resp.status_code == 200
    assert reached["ok"] is True


def test_u2_over_limit_multipart_413(monkeypatch):
    client, reached = _video_app(monkeypatch)
    resp = client.post("/api/video/upload", files=_multipart(2500 * 1024))
    assert resp.status_code == 413
    assert reached["ok"] is False


def test_u3_chunked_no_content_length_over_limit_413(monkeypatch):
    import asyncio

    import app.backend.services.upload_limits as limits
    from starlette.applications import Starlette
    from starlette.responses import Response
    from starlette.routing import Route

    monkeypatch.setattr(limits, "VIDEO_UPLOAD_MAX_BYTES", 2 * 1024 * 1024)

    async def endpoint(request):
        return Response("ok")

    inner = Starlette(routes=[Route("/api/video/upload", endpoint, methods=["POST"])])
    app = RequestSizeLimitMiddleware(inner)

    async def receive():
        return {"type": "http.request", "body": b"x" * (3 * 1024 * 1024), "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/video/upload",
        "raw_path": b"/api/video/upload",
        "query_string": b"",
        "headers": [(b"content-type", b"multipart/form-data; boundary=x")],
        "client": ("test", 123),
        "server": ("test", 80),
    }

    async def call():
        messages = []

        async def _send(msg):
            messages.append(msg)

        await app(scope, receive, _send)
        return messages

    messages = asyncio.run(call())
    start = next(m for m in messages if m["type"] == "http.response.start")
    assert start["status"] == 413


def test_u4_json_request_still_bounded(monkeypatch):
    import app.backend.services.upload_limits as limits

    monkeypatch.setattr(limits, "DEFAULT_REQUEST_MAX_BYTES", 1024)
    client, _ = _video_app(monkeypatch)
    resp = client.post("/api/auth/login", content=b"{" + (b"a" * 2000) + b"}", headers={"content-type": "application/json"})
    assert resp.status_code == 413


def test_u5_clamav_runs_before_video_parser():
    from app.backend.routes import video as video_route

    src = inspect.getsource(video_route.analyze_video)
    assert src.index("scan_any_upload") < src.index("analyze_video_file")


def test_u6_idempotency_does_not_buffer_multipart_via_body():
    src = inspect.getsource(IdempotencyMiddleware.dispatch)
    assert "is_multipart_upload_path" in src
    multipart_idx = src.index("is_multipart_upload_path")
    body_idx = src.index("await request.body()")
    assert multipart_idx < body_idx
