"""GDPR erasure proofs against a real S3-compatible object store."""
from __future__ import annotations

import os
import uuid

import pytest
from botocore.exceptions import ClientError

from app.backend.models.db_models import Candidate, PendingObjectDeletion, Tenant
from app.backend.services import gdpr_service


def _require_object_storage():
    required = os.getenv("OBJECT_STORAGE_INTEGRATION_REQUIRED") == "1"
    endpoint = os.getenv("S3_ENDPOINT")
    bucket = os.getenv("S3_BUCKET")
    if not endpoint or not bucket:
        if required:
            pytest.fail(
                "OBJECT_STORAGE_INTEGRATION_REQUIRED=1 but S3_ENDPOINT or S3_BUCKET is missing",
                pytrace=False,
            )
        pytest.skip("real object-storage integration is not configured")

    from app.backend.services.object_storage import _get_client

    client = _get_client()
    if client is None:
        if required:
            pytest.fail("object-storage client could not be created", pytrace=False)
        pytest.skip("object-storage client could not be created")
    try:
        client.head_bucket(Bucket=bucket)
    except Exception as exc:
        if required:
            pytest.fail(f"configured object storage is unreachable: {exc}", pytrace=False)
        pytest.skip(f"configured object storage is unreachable: {exc}")
    return client, bucket


def _assert_object_missing(client, bucket: str, key: str) -> None:
    with pytest.raises(ClientError) as exc_info:
        client.head_object(Bucket=bucket, Key=key)
    assert exc_info.value.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}


def test_hard_delete_removes_real_object_and_candidate(db, seed_subscription_plans):
    client, bucket = _require_object_storage()
    suffix = uuid.uuid4().hex
    tenant = Tenant(name=f"Storage Delete {suffix}", slug=f"storage-delete-{suffix}")
    db.add(tenant)
    db.commit()
    candidate = Candidate(
        tenant_id=tenant.id,
        name="Storage Erasure",
        email=f"storage-delete-{suffix}@example.com",
        resume_file_key=f"gdpr/{suffix}/resume.pdf",
    )
    db.add(candidate)
    db.commit()
    candidate_id = candidate.id
    key = candidate.resume_file_key
    client.put_object(Bucket=bucket, Key=key, Body=b"sensitive resume")

    outcome = gdpr_service.hard_delete_candidate(db, candidate_id, tenant.id)

    assert outcome.get("error") is None
    assert outcome["object_storage_complete"] is True
    assert db.get(Candidate, candidate_id) is None
    _assert_object_missing(client, bucket, key)


def test_hard_delete_survives_storage_outage_and_finishes_on_retry(
    db, monkeypatch, seed_subscription_plans
):
    client, bucket = _require_object_storage()
    working_endpoint = os.environ["S3_ENDPOINT"]
    suffix = uuid.uuid4().hex
    tenant = Tenant(name=f"Storage Retry {suffix}", slug=f"storage-retry-{suffix}")
    db.add(tenant)
    db.commit()
    candidate = Candidate(
        tenant_id=tenant.id,
        name="Storage Retry",
        email=f"storage-retry-{suffix}@example.com",
        resume_file_key=f"gdpr/{suffix}/resume.pdf",
    )
    db.add(candidate)
    db.commit()
    candidate_id = candidate.id
    key = candidate.resume_file_key
    client.put_object(Bucket=bucket, Key=key, Body=b"sensitive resume")

    monkeypatch.setenv("S3_ENDPOINT", "http://127.0.0.1:1")
    outcome = gdpr_service.hard_delete_candidate(db, candidate_id, tenant.id)

    assert outcome["error"] == "object_storage_delete_incomplete"
    assert db.get(Candidate, candidate_id) is not None
    pending = db.query(PendingObjectDeletion).filter_by(
        tenant_id=tenant.id,
        storage_key=key,
    ).one()
    assert pending.status == "pending"
    assert pending.workflow_status == "object_pending"

    monkeypatch.setenv("S3_ENDPOINT", working_endpoint)
    pending.next_retry_at = None
    db.commit()
    summary = gdpr_service.process_pending_object_deletions(db)

    assert summary["deleted"] == 1
    assert summary["finalized"] == 1
    assert db.get(Candidate, candidate_id) is None
    db.refresh(pending)
    assert pending.status == "completed"
    assert pending.workflow_status == "completed"
    _assert_object_missing(client, bucket, key)
