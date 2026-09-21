"""Single source of truth for HTTP upload size limits (bytes)."""
from __future__ import annotations

import os

DEFAULT_REQUEST_MAX_BYTES = int(os.getenv("DEFAULT_REQUEST_MAX_BYTES", str(25 * 1024 * 1024)))
RESUME_UPLOAD_MAX_BYTES = int(os.getenv("RESUME_UPLOAD_MAX_BYTES", str(25 * 1024 * 1024)))
VIDEO_UPLOAD_MAX_BYTES = int(os.getenv("VIDEO_UPLOAD_MAX_BYTES", str(200 * 1024 * 1024)))
BATCH_UPLOAD_MAX_BYTES = int(os.getenv("BATCH_UPLOAD_MAX_BYTES", str(500 * 1024 * 1024)))
NGINX_CLIENT_MAX_BODY_BYTES = int(os.getenv("NGINX_CLIENT_MAX_BODY_BYTES", str(500 * 1024 * 1024)))

MULTIPART_PREFIXES = (
    "/api/analyze",
    "/api/upload/",
    "/api/video/upload",
    "/api/transcript/upload",
)


def limit_for_path(path: str) -> int:
    if path.startswith("/api/video/upload") or path.startswith("/api/analyze/video"):
        return VIDEO_UPLOAD_MAX_BYTES
    if path.startswith("/api/analyze/batch") or path.startswith("/api/upload/batch"):
        return BATCH_UPLOAD_MAX_BYTES
    if path.startswith("/api/upload/") or path.startswith("/api/analyze") or path.startswith("/api/transcript/upload"):
        return max(RESUME_UPLOAD_MAX_BYTES, DEFAULT_REQUEST_MAX_BYTES)
    return DEFAULT_REQUEST_MAX_BYTES


def is_multipart_upload_path(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in MULTIPART_PREFIXES)


def assert_nginx_agreement() -> None:
    if BATCH_UPLOAD_MAX_BYTES > NGINX_CLIENT_MAX_BODY_BYTES:
        raise ValueError("application batch limit exceeds nginx client_max_body_size")
