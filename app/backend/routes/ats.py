"""
ATS Connector routes — manage external ATS integrations and push/pull candidate status.

Endpoints:
  POST   /api/ats/connections                  — Create an ATS connection
  GET    /api/ats/connections                  — List ATS connections for tenant
  GET    /api/ats/connections/{id}             — Get connection detail
  PUT    /api/ats/connections/{id}             — Update connection
  DELETE /api/ats/connections/{id}             — Delete connection
  POST   /api/ats/connections/{id}/push        — Push candidate status to ATS
  POST   /api/ats/connections/{id}/pull        — Pull candidate status from ATS
  GET    /api/ats/connections/{id}/logs        — List sync logs for connection
  POST   /api/ats/webhook/{connection_id}      — Inbound webhook from external ATS
"""
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backend.db.database import get_db
from app.backend.middleware.auth import get_current_user
from app.backend.models.db_models import (
    ATSConnection,
    ATSSyncLog,
    Candidate,
    ScreeningResult,
    User,
)
from app.backend.models.schemas import (
    ATSConnectionCreate,
    ATSConnectionOut,
    ATSConnectionUpdate,
    ATSPushRequest,
    ATSSyncLogOut,
)
from app.backend.services.ats_connector import ATSConnector
from app.backend.services.integration_secrets import encrypt_secret, secret_configured
from app.backend.services.url_safety import UnsafeURLError, validate_public_url

_SECRET_FIELDS = ("api_key", "api_secret", "webhook_secret")


def _store_secret(value):
    if value is None:
        return None
    if isinstance(value, str) and value.startswith("enc:v"):
        return value
    return encrypt_secret(value)

logger = logging.getLogger("aria.ats")

router = APIRouter(prefix="/api/ats", tags=["ats"])


def _require_admin(current_user: User) -> None:
    if current_user.role not in {"admin", "recruiter"}:
        raise HTTPException(status_code=403, detail="Admin or recruiter access required")


def _validate_ats_urls(base_url, webhook_url):
    try:
        if base_url:
            validate_public_url(base_url)
        if webhook_url:
            validate_public_url(webhook_url)
    except UnsafeURLError as e:
        raise HTTPException(status_code=400, detail="ATS URL is not allowed") from e


def _serialize_connection(conn: ATSConnection) -> dict:
    data = ATSConnectionOut.model_validate(conn).model_dump()
    data["api_key_configured"] = secret_configured(conn.api_key)
    data["webhook_secret_configured"] = secret_configured(conn.webhook_secret)
    for field in _SECRET_FIELDS:
        data.pop(field, None)
    mapping = conn.status_mapping_json
    if mapping:
        try:
            data["status_mapping_json"] = json.loads(mapping)
        except (json.JSONDecodeError, TypeError):
            data["status_mapping_json"] = {}
    else:
        data["status_mapping_json"] = {}
    return data


# ─── Connection CRUD ──────────────────────────────────────────────────────────

@router.post("/connections", response_model=ATSConnectionOut, status_code=status.HTTP_201_CREATED)
def create_connection(
    body: ATSConnectionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new ATS connection for the tenant."""
    _require_admin(current_user)
    _validate_ats_urls(body.base_url, body.webhook_url)

    conn = ATSConnection(
        tenant_id=current_user.tenant_id,
        provider=body.provider,
        label=body.label,
        api_key=_store_secret(body.api_key),
        api_secret=_store_secret(body.api_secret),
        base_url=body.base_url,
        webhook_url=body.webhook_url,
        webhook_secret=_store_secret(body.webhook_secret),
        sync_direction=body.sync_direction,
        status_mapping_json=json.dumps(body.status_mapping_json) if body.status_mapping_json else None,
    )
    db.add(conn)
    db.commit()
    db.refresh(conn)
    return _serialize_connection(conn)


@router.get("/connections", response_model=List[ATSConnectionOut])
def list_connections(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all ATS connections for the tenant."""
    _require_admin(current_user)
    connections = db.execute(
        select(ATSConnection).where(ATSConnection.tenant_id == current_user.tenant_id)
    ).scalars().all()
    return [_serialize_connection(c) for c in connections]


@router.get("/connections/{connection_id}", response_model=ATSConnectionOut)
def get_connection(
    connection_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get details of a specific ATS connection."""
    _require_admin(current_user)
    conn = db.execute(
        select(ATSConnection).where(
            ATSConnection.id == connection_id,
            ATSConnection.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="ATS connection not found")
    return _serialize_connection(conn)


@router.put("/connections/{connection_id}", response_model=ATSConnectionOut)
def update_connection(
    connection_id: int,
    body: ATSConnectionUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update an ATS connection."""
    _require_admin(current_user)
    conn = db.execute(
        select(ATSConnection).where(
            ATSConnection.id == connection_id,
            ATSConnection.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="ATS connection not found")

    update_data = body.model_dump(exclude_unset=True)
    _validate_ats_urls(update_data.get("base_url"), update_data.get("webhook_url"))
    for field, value in update_data.items():
        if field == "status_mapping_json" and isinstance(value, dict):
            value = json.dumps(value)
        if field in _SECRET_FIELDS:
            value = _store_secret(value)
        setattr(conn, field, value)

    db.commit()
    db.refresh(conn)
    return _serialize_connection(conn)


@router.delete("/connections/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_connection(
    connection_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete an ATS connection."""
    _require_admin(current_user)
    conn = db.execute(
        select(ATSConnection).where(
            ATSConnection.id == connection_id,
            ATSConnection.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="ATS connection not found")
    db.delete(conn)
    db.commit()


# ─── Push / Pull ──────────────────────────────────────────────────────────────

@router.post("/connections/{connection_id}/push")
async def push_to_ats(
    connection_id: int,
    body: ATSPushRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Push a candidate's status to the external ATS."""
    _require_admin(current_user)

    conn = db.execute(
        select(ATSConnection).where(
            ATSConnection.id == connection_id,
            ATSConnection.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="ATS connection not found")
    if not conn.is_active:
        raise HTTPException(status_code=400, detail="ATS connection is not active")

    connector = ATSConnector(db)
    result = await connector.push_candidate_status(
        connection=conn,
        candidate_id=body.candidate_id,
        screening_result_id=body.screening_result_id,
        external_id=body.external_id,
        status=body.status,
    )

    if not result["success"]:
        status_code = int(result.get("http_status") or status.HTTP_502_BAD_GATEWAY)
        raise HTTPException(
            status_code=status_code,
            detail=f"ATS push failed: {result.get('error', 'unknown error')}",
        )

    return result


@router.post("/connections/{connection_id}/pull")
async def pull_from_ats(
    connection_id: int,
    body: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pull a candidate's status from the external ATS."""
    _require_admin(current_user)

    conn = db.execute(
        select(ATSConnection).where(
            ATSConnection.id == connection_id,
            ATSConnection.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="ATS connection not found")
    if not conn.is_active:
        raise HTTPException(status_code=400, detail="ATS connection is not active")

    external_id = body.get("external_id")
    if not external_id:
        raise HTTPException(status_code=400, detail="external_id required")

    connector = ATSConnector(db)
    result = await connector.pull_candidate_status(
        connection=conn,
        external_id=external_id,
    )

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"ATS pull failed: {result.get('error', 'unknown error')}",
        )

    return result


@router.post("/connections/{connection_id}/sync-requisitions")
async def sync_requisitions_from_ats(
    connection_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pull open requisitions from ATS into local Requisition records (hub sync)."""
    _require_admin(current_user)

    conn = db.execute(
        select(ATSConnection).where(
            ATSConnection.id == connection_id,
            ATSConnection.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="ATS connection not found")

    connector = ATSConnector(db)
    result = await connector.sync_requisitions(connection=conn, tenant_id=current_user.tenant_id)
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.get("error", "ATS requisition sync failed"),
        )
    return result


# ─── Sync Logs ────────────────────────────────────────────────────────────────

@router.get("/connections/{connection_id}/logs", response_model=List[ATSSyncLogOut])
def list_sync_logs(
    connection_id: int,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List sync logs for a connection."""
    _require_admin(current_user)

    conn = db.execute(
        select(ATSConnection).where(
            ATSConnection.id == connection_id,
            ATSConnection.tenant_id == current_user.tenant_id,
        )
    ).scalar_one_or_none()
    if not conn:
        raise HTTPException(status_code=404, detail="ATS connection not found")

    logs = db.execute(
        select(ATSSyncLog)
        .where(ATSSyncLog.connection_id == connection_id)
        .order_by(ATSSyncLog.created_at.desc())
        .limit(min(limit, 200))
    ).scalars().all()

    return [ATSSyncLogOut.model_validate(log) for log in logs]


# ─── Inbound Webhook ──────────────────────────────────────────────────────────

@router.post("/webhook/{connection_id}")
async def ats_inbound_webhook(
    connection_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Receive inbound webhook from external ATS.

    Not authenticated via JWT — uses HMAC signature verification with
    the connection's webhook_secret.
    """
    conn = db.execute(
        select(ATSConnection).where(
            ATSConnection.id == connection_id,
            ATSConnection.is_active == True,
        )
    ).scalar_one_or_none()

    if not conn:
        raise HTTPException(status_code=404, detail="ATS connection not found")

    body = await request.body()

    # Verify HMAC signature
    signature = request.headers.get("X-ATS-Signature", "")
    connector = ATSConnector(db)
    if not connector.verify_inbound_webhook(conn, signature, body):
        raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Log the inbound webhook
    db.add(ATSSyncLog(
        connection_id=connection_id,
        tenant_id=conn.tenant_id,
        direction="pull",
        entity_type=payload.get("entity_type", "unknown"),
        entity_id=payload.get("external_id"),
        candidate_id=None,
        screening_result_id=None,
        payload=json.dumps(payload, default=str),
        response_status=200,
        response_body="inbound webhook received",
        success=True,
    ))
    db.commit()

    # Process the webhook — update internal status if applicable
    external_status = payload.get("status")
    external_id = payload.get("external_id")
    candidate_id = payload.get("candidate_id")

    if external_status and candidate_id:
        mapping = connector._load_json(conn.status_mapping_json, default={})
        reverse_mapping = {v: k for k, v in mapping.items()}
        internal_status = reverse_mapping.get(external_status, external_status)

        screening = db.execute(
            select(ScreeningResult).where(
                ScreeningResult.candidate_id == candidate_id,
                ScreeningResult.tenant_id == conn.tenant_id,
            )
        ).scalar_one_or_none()

        if screening and screening.status != internal_status:
            screening.status = internal_status
            screening.status_updated_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(
                "ATS inbound webhook updated candidate %s status to '%s'",
                candidate_id, internal_status,
            )

    return {"status": "ok", "connection_id": connection_id}
