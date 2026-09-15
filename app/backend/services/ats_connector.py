"""
ATS Connector Service — push candidate status updates to external ATS systems.

Supports Greenhouse, Lever, Workday, and generic webhook-based ATS integrations.
Each provider has a specific adapter that translates internal status changes
into the external ATS's API format.
"""
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backend.models.db_models import (
    ATSConnection,
    ATSSyncLog,
    Candidate,
    ScreeningResult,
)

logger = logging.getLogger("aria.ats")


class ATSConnector:
    """Generic ATS push/pull connector with provider-specific adapters."""

    def __init__(self, db: Session):
        self.db = db

    async def push_candidate_status(
        self,
        connection: ATSConnection,
        candidate_id: int,
        screening_result_id: Optional[int] = None,
        external_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> dict[str, Any]:
        """Push a candidate status update to the external ATS.

        Returns: {"success": bool, "external_id": str|None, "error": str|None}
        """
        candidate = self.db.execute(
            select(Candidate).where(
                Candidate.id == candidate_id,
                Candidate.tenant_id == connection.tenant_id,
            )
        ).scalar_one_or_none()
        if not candidate:
            return {
                "success": False,
                "external_id": None,
                "error": "Candidate not found",
                "http_status": 404,
            }

        screening = None
        if screening_result_id:
            screening = self.db.execute(
                select(ScreeningResult).where(
                    ScreeningResult.id == screening_result_id,
                    ScreeningResult.tenant_id == connection.tenant_id,
                    ScreeningResult.candidate_id == candidate.id,
                )
            ).scalar_one_or_none()
            if screening is None:
                return {
                    "success": False,
                    "external_id": None,
                    "error": "Screening result not found",
                    "http_status": 404,
                }

        internal_status = status or (screening.status if screening else "pending")

        # Map internal status to external ATS status via connection mapping
        mapping = self._load_json(connection.status_mapping_json, default={})
        external_status = mapping.get(internal_status, internal_status)

        adapter = self._get_adapter(connection.provider)
        payload = adapter.build_push_payload(
            candidate=candidate,
            screening=screening,
            external_id=external_id,
            external_status=external_status,
            internal_status=internal_status,
        )

        url = adapter.get_endpoint(connection, external_id)
        headers = adapter.get_headers(connection)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                success = 200 <= resp.status_code < 300
                response_body = resp.text[:2000]

                self._log_sync(
                    connection=connection,
                    direction="push",
                    entity_type="candidate_status",
                    entity_id=external_id,
                    candidate_id=candidate_id,
                    screening_result_id=screening_result_id,
                    payload=payload,
                    response_status=resp.status_code,
                    response_body=response_body,
                    success=success,
                )

                if success:
                    connection.last_sync_at = datetime.now(timezone.utc)
                    connection.last_sync_status = "success"
                    connection.last_error = None
                else:
                    connection.last_sync_status = "failed"
                    connection.last_error = f"HTTP {resp.status_code}: {response_body[:200]}"

                self.db.commit()

                return {
                    "success": success,
                    "external_id": external_id,
                    "error": None if success else f"HTTP {resp.status_code}",
                }

        except Exception as e:
            logger.error("ATS push failed for connection %s: %s", connection.id, e)
            self._log_sync(
                connection=connection,
                direction="push",
                entity_type="candidate_status",
                entity_id=external_id,
                candidate_id=candidate_id,
                screening_result_id=screening_result_id,
                payload=payload,
                response_status=None,
                response_body=str(e)[:2000],
                success=False,
                error_message=str(e),
            )
            connection.last_sync_status = "failed"
            connection.last_error = str(e)[:500]
            self.db.commit()

            return {"success": False, "external_id": external_id, "error": str(e)}

    async def pull_candidate_status(
        self,
        connection: ATSConnection,
        external_id: str,
    ) -> dict[str, Any]:
        """Pull candidate status from external ATS.

        Returns: {"success": bool, "status": str|None, "error": str|None}
        """
        adapter = self._get_adapter(connection.provider)
        url = adapter.get_pull_endpoint(connection, external_id)
        headers = adapter.get_headers(connection)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, headers=headers)
                success = 200 <= resp.status_code < 300
                response_body = resp.text[:2000]

                external_status = None
                if success:
                    external_status = adapter.parse_pull_status(resp.json())

                self._log_sync(
                    connection=connection,
                    direction="pull",
                    entity_type="candidate_status",
                    entity_id=external_id,
                    payload=None,
                    response_status=resp.status_code,
                    response_body=response_body,
                    success=success,
                )

                connection.last_sync_at = datetime.now(timezone.utc)
                connection.last_sync_status = "success" if success else "failed"
                self.db.commit()

                return {
                    "success": success,
                    "status": external_status,
                    "error": None if success else f"HTTP {resp.status_code}",
                }

        except Exception as e:
            logger.error("ATS pull failed for connection %s: %s", connection.id, e)
            self._log_sync(
                connection=connection,
                direction="pull",
                entity_type="candidate_status",
                entity_id=external_id,
                response_status=None,
                response_body=str(e)[:2000],
                success=False,
                error_message=str(e),
            )
            connection.last_sync_status = "failed"
            connection.last_error = str(e)[:500]
            self.db.commit()

            return {"success": False, "status": None, "error": str(e)}

    async def sync_requisitions(
        self,
        connection: ATSConnection,
        tenant_id: int,
    ) -> dict[str, Any]:
        """Pull open requisitions from ATS — creates/updates local Requisition rows."""
        from app.backend.models.db_models import Requisition
        from app.backend.services.requisition_service import create_requisition

        adapter = self._get_adapter(connection.provider)
        if not hasattr(adapter, "fetch_open_requisitions"):
            return {
                "success": True,
                "synced": 0,
                "message": f"Provider {connection.provider} has no requisition sync adapter yet",
            }

        try:
            openings = await adapter.fetch_open_requisitions(connection)
            synced = 0
            for opening in openings or []:
                ext_id = str(opening.get("id") or opening.get("external_id") or "")
                if not ext_id:
                    continue
                existing = (
                    self.db.query(Requisition)
                    .filter(
                        Requisition.tenant_id == tenant_id,
                        Requisition.external_ats_id == ext_id,
                        Requisition.ats_provider == connection.provider,
                    )
                    .first()
                )
                title = opening.get("title") or opening.get("name") or f"ATS Opening {ext_id}"
                jd = opening.get("jd_text") or opening.get("content") or title
                if existing:
                    existing.title = title
                    existing.jd_text = jd
                    existing.status = "sourcing"
                else:
                    create_requisition(
                        self.db,
                        tenant_id=tenant_id,
                        created_by=None,
                        title=title,
                        jd_text=jd,
                        status="sourcing",
                    )
                    req = (
                        self.db.query(Requisition)
                        .filter(Requisition.tenant_id == tenant_id)
                        .order_by(Requisition.id.desc())
                        .first()
                    )
                    if req:
                        req.external_ats_id = ext_id
                        req.ats_provider = connection.provider
                synced += 1

            self._log_sync(
                connection=connection,
                direction="pull",
                entity_type="requisitions",
                payload={"count": synced},
                success=True,
            )
            connection.last_sync_at = datetime.now(timezone.utc)
            connection.last_sync_status = "success"
            self.db.commit()
            return {"success": True, "synced": synced}
        except Exception as e:
            logger.error("ATS requisition sync failed: %s", e)
            self._log_sync(
                connection=connection,
                direction="pull",
                entity_type="requisitions",
                success=False,
                error_message=str(e),
            )
            self.db.commit()
            return {"success": False, "error": str(e)}

    def verify_inbound_webhook(
        self,
        connection: ATSConnection,
        signature: str,
        body: bytes,
    ) -> bool:
        """Verify HMAC signature of inbound ATS webhook."""
        if not connection.webhook_secret:
            return True  # No secret configured, allow
        expected = hmac.new(
            connection.webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    def _get_adapter(self, provider: str) -> "BaseATSAdapter":
        adapters = {
            "greenhouse": GreenhouseAdapter(),
            "lever": LeverAdapter(),
            "workday": WorkdayAdapter(),
            "generic": GenericAdapter(),
        }
        return adapters.get(provider, GenericAdapter())

    def _log_sync(
        self,
        connection: ATSConnection,
        direction: str,
        entity_type: str,
        entity_id: Optional[str] = None,
        candidate_id: Optional[int] = None,
        screening_result_id: Optional[int] = None,
        payload: Optional[dict] = None,
        response_status: Optional[int] = None,
        response_body: Optional[str] = None,
        success: bool = False,
        error_message: Optional[str] = None,
    ) -> None:
        self.db.add(ATSSyncLog(
            connection_id=connection.id,
            tenant_id=connection.tenant_id,
            direction=direction,
            entity_type=entity_type,
            entity_id=entity_id,
            candidate_id=candidate_id,
            screening_result_id=screening_result_id,
            payload=json.dumps(payload, default=str) if payload else None,
            response_status=response_status,
            response_body=response_body,
            success=success,
            error_message=error_message,
        ))

    @staticmethod
    def _load_json(raw: Optional[str], default: Any = None) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return default


# ─── Provider Adapters ────────────────────────────────────────────────────────

class BaseATSAdapter:
    """Base class for ATS provider adapters."""

    def build_push_payload(
        self,
        candidate: Candidate,
        screening: Optional[ScreeningResult],
        external_id: Optional[str],
        external_status: str,
        internal_status: str,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def get_endpoint(self, connection: ATSConnection, external_id: Optional[str]) -> str:
        raise NotImplementedError

    def get_pull_endpoint(self, connection: ATSConnection, external_id: str) -> str:
        raise NotImplementedError

    def get_headers(self, connection: ATSConnection) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def parse_pull_status(self, data: dict) -> Optional[str]:
        return None

    def get_requisitions_endpoint(self, connection: ATSConnection) -> str:
        raise NotImplementedError

    def parse_requisitions_list(self, data: Any) -> list[dict[str, Any]]:
        return []

    async def fetch_open_requisitions(self, connection: ATSConnection) -> list[dict[str, Any]]:
        url = self.get_requisitions_endpoint(connection)
        headers = self.get_headers(connection)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code >= 400:
                logger.warning("ATS requisitions fetch failed HTTP %s for %s", resp.status_code, connection.provider)
                return []
            try:
                payload = resp.json()
            except Exception:
                return []
            return self.parse_requisitions_list(payload)


class GreenhouseAdapter(BaseATSAdapter):
    """Greenhouse Harvest API adapter."""

    def build_push_payload(self, candidate, screening, external_id, external_status, internal_status):
        return {
            "application_id": external_id,
            "status": external_status,
            "custom_fields": {
                "aria_fit_score": screening.deterministic_score if screening else None,
                "aria_recommendation": screening.recommendation if screening else None,
            },
        }

    def get_endpoint(self, connection, external_id):
        base = connection.base_url or "https://harvest.greenhouse.io"
        if external_id:
            return f"{base}/v1/applications/{external_id}"
        return f"{base}/v1/applications"

    def get_pull_endpoint(self, connection, external_id):
        base = connection.base_url or "https://harvest.greenhouse.io"
        return f"{base}/v1/applications/{external_id}"

    def get_headers(self, connection):
        import base64
        auth = base64.b64encode(f"{connection.api_key}:".encode()).decode()
        return {
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth}",
        }

    def parse_pull_status(self, data):
        return data.get("status")

    def get_requisitions_endpoint(self, connection):
        base = connection.base_url or "https://harvest.greenhouse.io"
        return f"{base}/v1/jobs?status=open"

    def parse_requisitions_list(self, data):
        jobs = data if isinstance(data, list) else data.get("jobs") or data.get("data") or []
        out = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            out.append({
                "id": str(job.get("id") or job.get("job_id") or ""),
                "title": job.get("name") or job.get("title"),
                "jd_text": job.get("notes") or job.get("content") or job.get("name") or "",
            })
        return out


class LeverAdapter(BaseATSAdapter):
    """Lever API adapter."""

    def build_push_payload(self, candidate, screening, external_id, external_status, internal_status):
        return {
            "opportunityId": external_id,
            "stage": external_status,
            "tags": ["aria-synced"],
            "customFields": {
                "ariaFitScore": screening.deterministic_score if screening else None,
            },
        }

    def get_endpoint(self, connection, external_id):
        base = connection.base_url or "https://api.lever.co/v1"
        if external_id:
            return f"{base}/opportunities/{external_id}"
        return f"{base}/opportunities"

    def get_pull_endpoint(self, connection, external_id):
        base = connection.base_url or "https://api.lever.co/v1"
        return f"{base}/opportunities/{external_id}"

    def get_headers(self, connection):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {connection.api_key}",
        }

    def parse_pull_status(self, data):
        return data.get("stage")

    def get_requisitions_endpoint(self, connection):
        base = connection.base_url or "https://api.lever.co/v1"
        return f"{base}/postings?state=published"

    def parse_requisitions_list(self, data):
        postings = data if isinstance(data, list) else data.get("data") or []
        out = []
        for p in postings:
            if not isinstance(p, dict):
                continue
            out.append({
                "id": str(p.get("id") or ""),
                "title": p.get("text") or p.get("title"),
                "jd_text": p.get("descriptionPlain") or p.get("description") or p.get("text") or "",
            })
        return out


class WorkdayAdapter(BaseATSAdapter):
    """Workday REST API adapter."""

    def build_push_payload(self, candidate, screening, external_id, external_status, internal_status):
        return {
            "Application_Reference_ID": external_id,
            "Status_Code": external_status,
            "Custom_Data": {
                "ARIA_Fit_Score": screening.deterministic_score if screening else None,
                "ARIA_Recommendation": screening.recommendation if screening else None,
            },
        }

    def get_endpoint(self, connection, external_id):
        base = connection.base_url or ""
        if not base:
            raise ValueError("Workday adapter requires base_url to be configured")
        if external_id:
            return f"{base}/ccx/api/applications/{external_id}"
        return f"{base}/ccx/api/applications"

    def get_pull_endpoint(self, connection, external_id):
        base = connection.base_url or ""
        if not base:
            raise ValueError("Workday adapter requires base_url to be configured")
        return f"{base}/ccx/api/applications/{external_id}"

    def get_headers(self, connection):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {connection.api_key}",
        }

    def parse_pull_status(self, data):
        return data.get("Status_Code")

    def get_requisitions_endpoint(self, connection):
        base = connection.base_url or ""
        if not base:
            raise ValueError("Workday adapter requires base_url to be configured")
        return f"{base}/ccx/api/job_requisitions?status=open"

    def parse_requisitions_list(self, data):
        rows = data if isinstance(data, list) else data.get("Report_Entry") or data.get("data") or []
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append({
                "id": str(row.get("Job_Requisition_ID") or row.get("id") or ""),
                "title": row.get("Job_Title") or row.get("title"),
                "jd_text": row.get("Job_Description") or row.get("jd_text") or row.get("Job_Title") or "",
            })
        return out


class GenericAdapter(BaseATSAdapter):
    """Generic webhook-based adapter for any ATS that accepts JSON POST."""

    def build_push_payload(self, candidate, screening, external_id, external_status, internal_status):
        return {
            "external_id": external_id,
            "status": external_status,
            "internal_status": internal_status,
            "candidate": {
                "name": candidate.name,
                "email": candidate.email,
                "phone": candidate.phone,
            },
            "screening": {
                "fit_score": screening.deterministic_score if screening else None,
                "recommendation": screening.recommendation if screening else None,
                "status": screening.status if screening else None,
            } if screening else None,
        }

    def get_endpoint(self, connection, external_id):
        base = connection.base_url or connection.webhook_url or ""
        if not base:
            raise ValueError("Generic adapter requires base_url or webhook_url")
        return base

    def get_pull_endpoint(self, connection, external_id):
        base = connection.base_url or ""
        if not base:
            raise ValueError("Generic adapter requires base_url for pull")
        return f"{base}?external_id={external_id}"

    def get_headers(self, connection):
        headers = {"Content-Type": "application/json"}
        if connection.api_key:
            headers["Authorization"] = f"Bearer {connection.api_key}"
        if connection.webhook_secret:
            headers["X-Webhook-Secret"] = connection.webhook_secret
        return headers

    def parse_pull_status(self, data):
        return data.get("status")

    def get_requisitions_endpoint(self, connection):
        base = connection.base_url or connection.webhook_url or ""
        if not base:
            raise ValueError("Generic adapter requires base_url or webhook_url")
        return base.rstrip("/") + "/requisitions"

    def parse_requisitions_list(self, data):
        rows = data if isinstance(data, list) else data.get("requisitions") or data.get("openings") or data.get("data") or []
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            out.append({
                "id": str(row.get("id") or row.get("external_id") or ""),
                "title": row.get("title") or row.get("name"),
                "jd_text": row.get("jd_text") or row.get("content") or row.get("description") or row.get("title") or "",
            })
        return out
