"""Honor X-Idempotency-Key on mutating requests using PostgreSQL leases."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode

from jose import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.backend.services.request_idempotency import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    IdempotencyKeyInvalidError,
    IdempotencyUnavailableError,
    acquire_idempotency,
    complete_idempotency,
    fail_idempotency,
)
from app.backend.services.upload_limits import is_multipart_upload_path

log = logging.getLogger(__name__)

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_TTL_HOURS = 24


def canonicalize_query(query: str) -> str:
    raw = (query or "").lstrip("?")
    if not raw:
        return ""
    pairs = parse_qsl(raw, keep_blank_values=True)
    pairs.sort(key=lambda item: (item[0], item[1]))
    return urlencode(pairs, doseq=True)


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
            canonicalize_query(query).encode("utf-8"),
            (content_type or "").encode("utf-8"),
            hashed_body,
        ]
    )
    return hashlib.sha256(material).hexdigest()


def _tenant_from_request(request: Request) -> str:
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
        if "stream" in request.url.path or is_multipart_upload_path(request.url.path):
            return await call_next(request)
        key = request.headers.get("X-Idempotency-Key", "").strip()
        if not key:
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
        from app.backend.db.database import SessionLocal

        db = SessionLocal()
        try:
            try:
                lease = acquire_idempotency(
                    db,
                    tenant_id=int(tenant_id) if str(tenant_id).isdigit() else 0,
                    endpoint=endpoint,
                    key=key,
                    fingerprint=fp,
                )
                db.commit()
            except IdempotencyKeyInvalidError as exc:
                db.rollback()
                return JSONResponse({"detail": str(exc)}, status_code=400)
            except IdempotencyConflictError:
                db.rollback()
                return JSONResponse(
                    {"detail": "Idempotency key reused with different request"},
                    status_code=409,
                )
            except IdempotencyInProgressError:
                db.rollback()
                return JSONResponse({"detail": "Request already in progress"}, status_code=409)
            except Exception as exc:
                db.rollback()
                log.error("idempotency acquire failed: %s", type(exc).__name__)
                return JSONResponse({"detail": "Idempotency store unavailable"}, status_code=503)
        finally:
            db.close()

        if lease.replay:
            return JSONResponse(
                content=lease.response_body or {},
                status_code=lease.response_status or 200,
                headers={"X-Idempotent-Replay": "true"},
            )

        response = await call_next(request)
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
        store = SessionLocal()
        try:
            if 200 <= response.status_code < 300:
                complete_idempotency(store, lease, status=response.status_code, body=payload)
            else:
                fail_idempotency(store, lease, body=payload)
            store.commit()
        except Exception:
            store.rollback()
            return JSONResponse({"detail": "Idempotency store unavailable"}, status_code=503)
        finally:
            store.close()
        return response


def _lookup(key: str, tenant_id: str, endpoint: str, fingerprint: str):
    from app.backend.db.database import SessionLocal

    db = SessionLocal()
    try:
        lease = acquire_idempotency(
            db,
            tenant_id=int(tenant_id) if str(tenant_id).isdigit() else 0,
            endpoint=endpoint,
            key=key,
            fingerprint=fingerprint,
        )
        db.commit()
        if lease.replay:
            return lease.response_status, lease.response_body or {}
        fail_idempotency(db, lease)
        db.commit()
        return None
    except IdempotencyConflictError:
        return "conflict"
    except Exception:
        raise IdempotencyUnavailableError("lookup failed")
    finally:
        db.close()


def _store(key: str, tenant_id: str, endpoint: str, fingerprint: str, status: int, body) -> None:
    from app.backend.db.database import SessionLocal
    from app.backend.models.db_models import IdempotencyKey

    db = SessionLocal()
    try:
        row = (
            db.query(IdempotencyKey)
            .filter_by(
                key=key,
                tenant_id=int(tenant_id) if str(tenant_id).isdigit() else 0,
                endpoint=endpoint[:200],
            )
            .one_or_none()
        )
        if row is None:
            raise IdempotencyUnavailableError("missing idempotency row")
        row.state = "completed"
        row.request_fingerprint = fingerprint
        row.response_status = status
        row.response_body = body if isinstance(body, (dict, list)) else {"ok": True}
        row.expires_at = datetime.now(timezone.utc) + timedelta(hours=_TTL_HOURS)
        db.commit()
    finally:
        db.close()
