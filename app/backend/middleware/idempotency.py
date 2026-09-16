"""Honor X-Idempotency-Key on mutating requests."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

from jose import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_TTL_HOURS = 24


def request_fingerprint(method: str, path: str, query: str, content_type: str, body: bytes) -> str:
    raw = body or b""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    ct = (content_type or "").split(";")[0].strip().lower()
    hashed_body = raw
    if ct.startswith("application/json"):
        try:
            canonical = json.dumps(json.loads(raw.decode("utf-8")), sort_keys=True, separators=(",", ":"))
            hashed_body = canonical.encode("utf-8")
        except Exception:
            hashed_body = raw
    material = b"\0".join(
        [
            (method or "").encode("utf-8"),
            (path or "").encode("utf-8"),
            (query or "").encode("utf-8"),
            (content_type or "").encode("utf-8"),
            hashed_body,
        ]
    )
    return hashlib.sha256(material).hexdigest()


def _tenant_from_request(request: Request) -> str:
    """Bind idempotency to the authenticated tenant, never to spoofable headers."""
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = request.cookies.get("access_token")
    if not token:
        return "0"
    try:
        from app.backend.middleware.auth import SECRET_KEY, ALGORITHM
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        tenant_id = payload.get("tenant_id")
        if tenant_id is not None:
            return str(tenant_id)
    except Exception:
        pass
    return "0"


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in _MUTATING:
            return await call_next(request)
        if "stream" in request.url.path:
            return await call_next(request)
        key = request.headers.get("X-Idempotency-Key", "").strip()
        if not key:
            return await call_next(request)
        if os.getenv("TESTING", "").lower() in ("1", "true") and not key:
            return await call_next(request)

        tenant_id = _tenant_from_request(request)
        endpoint = f"{request.method}:{request.url.path}"
        body = await request.body()

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(request.scope, receive)
        fp = request_fingerprint(
            request.method,
            request.url.path,
            str(request.query_params),
            request.headers.get("content-type") or "",
            body,
        )
        stored = _lookup(key, tenant_id, endpoint, fp)
        if stored == "conflict":
            return JSONResponse(
                {"detail": "Idempotency key reused with different request"},
                status_code=409,
            )
        if stored is not None:
            status, resp_body = stored
            return JSONResponse(
                content=resp_body,
                status_code=status,
                headers={"X-Idempotent-Replay": "true"},
            )

        response = await call_next(request)
        if 200 <= response.status_code < 300:
            body_bytes = getattr(response, "body", b"")
            if not body_bytes and hasattr(response, "body_iterator"):
                chunks = []
                async for chunk in response.body_iterator:
                    chunks.append(chunk)
                body_bytes = b"".join(chunks)
                response = Response(
                    content=body_bytes,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )
            try:
                payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
            except Exception:
                payload = {"raw": hashlib.sha256(body_bytes).hexdigest()}
            _store(key, tenant_id, endpoint, fp, response.status_code, payload)
        return response


def _lookup(key: str, tenant_id: str, endpoint: str, fingerprint: str):
    try:
        from app.backend.db.database import SessionLocal
        from app.backend.models.db_models import IdempotencyKey

        db = SessionLocal()
        try:
            row = (
                db.query(IdempotencyKey)
                .filter(
                    IdempotencyKey.key == key[:128],
                    IdempotencyKey.endpoint == endpoint[:200],
                    IdempotencyKey.tenant_id == (int(tenant_id) if str(tenant_id).isdigit() else 0),
                )
                .first()
            )
            if not row:
                return None
            if row.expires_at and row.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
                return None
            if row.request_fingerprint != fingerprint:
                return "conflict"
            return row.response_status, row.response_body or {}
        finally:
            db.close()
    except Exception:
        return None


def _store(key: str, tenant_id: str, endpoint: str, fingerprint: str, status: int, body) -> None:
    try:
        from app.backend.db.database import SessionLocal
        from app.backend.models.db_models import IdempotencyKey

        db = SessionLocal()
        try:
            db.merge(
                IdempotencyKey(
                    key=key[:128],
                    tenant_id=int(tenant_id) if str(tenant_id).isdigit() else 0,
                    endpoint=endpoint[:200],
                    request_fingerprint=fingerprint,
                    response_status=status,
                    response_body=body if isinstance(body, (dict, list)) else {"ok": True},
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=_TTL_HOURS),
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception:
        pass
