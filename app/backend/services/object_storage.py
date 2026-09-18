"""
Object Storage Service — abstracts S3/MinIO for resume file storage.

When configured (S3_ENDPOINT or S3_BUCKET env vars set), resume files are
stored in object storage and only the storage key is kept in the DB.
When not configured, falls back to DB BYTEA storage (backward compatible).

Environment variables:
  S3_ENDPOINT       — S3-compatible endpoint (e.g., http://localhost:9000 for MinIO)
  S3_ACCESS_KEY     — Access key
  S3_SECRET_KEY     — Secret key
  S3_BUCKET         — Bucket name (default: aria-resumes)
  S3_REGION         — Region (default: us-east-1)
  S3_USE_PATH_STYLE — Use path-style addressing (default: true for MinIO)
"""
import io
import logging
import os
from typing import Optional

logger = logging.getLogger("aria.storage")


def _is_configured() -> bool:
    """Check if S3/MinIO object storage is configured."""
    return bool(os.getenv("S3_ENDPOINT") and os.getenv("S3_BUCKET"))


def _get_client():
    """Create and return an S3 client. Returns None if not configured."""
    if not _is_configured():
        return None

    try:
        import boto3
        from botocore.config import Config
        from app.backend.services.reliability.timeouts import (
            OBJECT_STORAGE_CONNECT,
            OBJECT_STORAGE_READ,
        )

        endpoint = os.getenv("S3_ENDPOINT")
        access_key = os.getenv("S3_ACCESS_KEY", "")
        secret_key = os.getenv("S3_SECRET_KEY", "")
        region = os.getenv("S3_REGION", "us-east-1")
        use_path_style = os.getenv("S3_USE_PATH_STYLE", "true").lower() == "true"

        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(
                connect_timeout=OBJECT_STORAGE_CONNECT,
                read_timeout=OBJECT_STORAGE_READ,
                retries={"max_attempts": 1},
                s3={"addressing_style": "path" if use_path_style else "auto"},
            ),
        )
    except ImportError:
        logger.info("boto3 not installed, object storage unavailable")
        return None
    except Exception as e:
        logger.warning("Failed to create S3 client: %s", e)
        return None


def _get_bucket() -> str:
    return os.getenv("S3_BUCKET", "aria-resumes")


class ObjectStorageService:
    """Service for storing/retrieving files in S3-compatible object storage."""

    @staticmethod
    def is_available() -> bool:
        """Check if object storage is available and configured."""
        return _get_client() is not None

    @staticmethod
    def upload(key: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
        """Upload bytes to object storage. Returns True on success."""
        client = _get_client()
        if client is None:
            return False

        try:
            client.put_object(
                Bucket=_get_bucket(),
                Key=key,
                Body=io.BytesIO(data),
                ContentType=content_type,
            )
            return True
        except Exception as e:
            logger.error("S3 upload failed for key %s: %s", key, e)
            return False

    @staticmethod
    def download(key: str) -> Optional[bytes]:
        """Download bytes from object storage. Returns None on failure."""
        client = _get_client()
        if client is None:
            return None

        try:
            response = client.get_object(Bucket=_get_bucket(), Key=key)
            return response["Body"].read()
        except Exception as e:
            logger.error("S3 download failed for key %s: %s", key, e)
            return None

    @staticmethod
    def delete(key: str) -> bool:
        """Delete an object from storage. Returns True on success."""
        client = _get_client()
        if client is None:
            return False

        try:
            client.delete_object(Bucket=_get_bucket(), Key=key)
            return True
        except Exception as e:
            logger.warning("S3 delete failed for key %s: %s", key, e)
            return False

    @staticmethod
    def build_key(tenant_id: int, candidate_id: int, filename: str, suffix: str = "") -> str:
        """Build a standardized storage key for a resume file."""
        safe_name = os.path.basename(filename or "resume")
        key = f"tenant/{tenant_id}/candidate/{candidate_id}/{safe_name}"
        if suffix:
            key += f"_{suffix}"
        return key
