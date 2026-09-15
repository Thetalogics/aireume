"""Manual / enterprise-invoicing payment provider.

This provider works entirely offline — no external API keys or SDKs
required.  It is the default when no payment provider is configured,
and is suitable for enterprise customers who pay via invoice / PO.

The manual provider accepts internal webhook-style events triggered by
admins (e.g. payment.approved, payment.rejected).  Signature validation
is done via a shared API key passed in the X-Signature header.
"""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any

from sqlalchemy.orm import Session

from app.backend.models.db_models import Tenant
from app.backend.services.billing.base import PaymentProvider


class ManualProvider(PaymentProvider):
    """Offline / manual-invoicing payment provider.

    All operations are recorded in the local database.  No external
    dependencies are required.
    """

    # Shared secret for internal webhook signature validation.
    # In production, this comes from platform_configs via the factory.
    def __init__(self, db: Session = None, webhook_secret: str = ""):
        self._db = db
        self._webhook_secret = webhook_secret

    @property
    def provider_name(self) -> str:
        return "manual"

    def create_checkout_session(
        self,
        tenant_id: int,
        plan: str,
        success_url: str,
        cancel_url: str,
        stripe_customer_id: str = "",
        extra_metadata: Dict[str, Any] | None = None,
        price_id: str | None = None,
    ) -> Dict[str, Any]:
        reference_id = f"manual_{uuid.uuid4().hex[:12]}"
        return {
            "reference_id": reference_id,
            "provider": self.provider_name,
            "plan": plan,
            "tenant_id": tenant_id,
            "message": "Manual checkout created. Subscription will be activated by an administrator.",
        }

    def cancel_subscription(
        self,
        tenant_id: int,
        subscription_id: str,
    ) -> Dict[str, Any]:
        result = {
            "subscription_id": subscription_id,
            "status": "cancelled",
            "provider": self.provider_name,
        }

        if self._db is not None:
            tenant = self._db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant:
                tenant.subscription_status = "cancelled"
                self._db.commit()

        return result

    def get_subscription_status(
        self,
        tenant_id: int,
        subscription_id: str,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "subscription_id": subscription_id,
            "provider": self.provider_name,
        }

        if self._db is not None:
            tenant = self._db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant:
                result["status"] = tenant.subscription_status
                result["current_period_end"] = (
                    tenant.current_period_end.isoformat()
                    if tenant.current_period_end
                    else None
                )
                return result

        result["status"] = "unknown"
        result["current_period_end"] = None
        return result

    def prorate_plan_change(
        self,
        tenant_id: int,
        subscription_id: str,
        old_plan_price: int,
        new_plan_price: int,
        proration_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Record proration details for manual invoicing.

        No external API calls are made. The proration is logged in the
        tenant's metadata for later manual invoicing.
        """
        result = {
            "tenant_id": tenant_id,
            "subscription_id": subscription_id,
            "provider": self.provider_name,
            "status": "pending_manual_invoice",
            "proration": proration_data,
        }

        if self._db is not None:
            tenant = self._db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant:
                # Store proration info in tenant metadata for manual invoicing
                import json
                from app.backend.services.metadata_utils import safe_parse_metadata
                metadata = safe_parse_metadata(tenant.metadata_json)

                pending_prorations = metadata.get("pending_prorations", [])
                pending_prorations.append({
                    "subscription_id": subscription_id,
                    "old_plan_price_cents": old_plan_price,
                    "new_plan_price_cents": new_plan_price,
                    "proration_data": proration_data,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                })
                metadata["pending_prorations"] = pending_prorations
                tenant.metadata_json = json.dumps(metadata)
                self._db.commit()

        return result

    def handle_webhook_event(
        self,
        payload: bytes,
        signature: str,
    ) -> Dict[str, Any]:
        """Process a manual webhook event (typically from admin actions).

        Validates the HMAC-SHA256 signature using the shared secret.
        If no secret is configured (e.g. in dev mode), validation is skipped.
        """
        # Decode payload
        payload_str = payload.decode("utf-8") if isinstance(payload, bytes) else payload

        # Validate signature if a webhook secret is configured
        if self._webhook_secret:
            expected_sig = hmac.new(
                self._webhook_secret.encode(),
                payload_str.encode(),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(signature, expected_sig):
                raise ValueError("Invalid webhook signature for manual provider")

        event = json.loads(payload_str)
        event_type = event.get("event", "unknown")
        event_data = event.get("payload", event.get("data", {}))

        return {
            "event_type": event_type,
            "data": event_data,
            "provider": self.provider_name,
        }
