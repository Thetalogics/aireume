"""Webhook dispatch service with retry logic and HMAC signing."""
import hashlib
import hmac
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.backend.models.db_models import Webhook, WebhookDelivery
from app.backend.services.url_safety import UnsafeURLError, safe_request, validate_public_url

log = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_DELAYS = [1, 5, 30]  # seconds
MAX_FAILURE_COUNT = 10  # auto-disable after this many consecutive failures


def _sign_payload(payload_str: str, secret: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload."""
    return hmac.new(secret.encode(), payload_str.encode(), hashlib.sha256).hexdigest()


def _send_webhook(url: str, payload: dict, secret: str) -> tuple[int, str, bool]:
    """Send HTTP POST to webhook URL. Returns (status_code, body, success)."""
    payload_str = json.dumps(payload, default=str)
    payload_bytes = payload_str.encode()
    signature = _sign_payload(payload_str, secret)

    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Signature": f"sha256={signature}",
        "User-Agent": "ARIA-Webhook/1.0",
    }

    try:
        response = safe_request(
            "POST",
            url,
            require_https=True,
            timeout=10.0,
            content=payload_bytes,
            headers=headers,
        )
        success = 200 <= response.status_code < 300
        return response.status_code, response.text[:1000], success
    except UnsafeURLError as e:
        log.warning("Webhook delivery rejected: %s", str(e)[:1000])
        return 0, str(e)[:1000], False
    except Exception as e:
        log.warning("Webhook delivery failed: %s", str(e)[:1000])
        return 0, str(e)[:1000], False


def dispatch_event(db: Session, tenant_id: int, event: str, payload: dict):
    """Dispatch a webhook event to all matching active webhooks for a tenant.

    This runs synchronously; callers should invoke it from a background thread
    so it does not block the main request.
    """
    webhooks = (
        db.query(Webhook)
        .filter(Webhook.tenant_id == tenant_id, Webhook.is_active == True)
        .all()
    )

    for webhook in webhooks:
        # Check if this webhook subscribes to this event
        try:
            events = json.loads(webhook.events) if webhook.events else []
        except (json.JSONDecodeError, TypeError):
            events = []

        if event not in events and "*" not in events:
            continue

        # Attempt delivery with retries
        full_payload = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tenant_id": tenant_id,
            "data": payload,
        }

        success = False
        status_code = 0
        response_body = ""

        for attempt in range(1, MAX_RETRIES + 1):
            status_code, response_body, success = _send_webhook(
                webhook.url, full_payload, webhook.secret
            )

            delivery = WebhookDelivery(
                webhook_id=webhook.id,
                event=event,
                payload=json.dumps(full_payload, default=str),
                response_status=status_code,
                response_body=response_body,
                success=success,
                attempt=attempt,
            )
            db.add(delivery)

            if success:
                webhook.failure_count = 0
                webhook.last_triggered_at = datetime.now(timezone.utc)
                break
            else:
                webhook.failure_count += 1
                webhook.last_failure_at = datetime.now(timezone.utc)

                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAYS[attempt - 1])

        try:
            from app.backend.services.metrics import WEBHOOK_DELIVERY_TOTAL
            WEBHOOK_DELIVERY_TOTAL.labels(outcome="success" if success else "failure").inc()
        except Exception:
            pass

        # Auto-disable after too many failures
        if webhook.failure_count >= MAX_FAILURE_COUNT:
            webhook.is_active = False
            log.warning("Webhook %d auto-disabled after %d failures", webhook.id, webhook.failure_count)
            # Admin notification
            try:
                from app.backend.services.notification_service import create_admin_notification
                create_admin_notification(
                    db=db,
                    type="webhook_failure",
                    severity="warning",
                    title="Webhook auto-disabled",
                    message=(
                        f"Webhook id={webhook.id} (tenant_id={webhook.tenant_id}) was "
                        f"automatically disabled after {webhook.failure_count} consecutive failures."
                    ),
                    tenant_id=webhook.tenant_id,
                )
            except Exception:
                log.exception("Failed to create admin notification for webhook auto-disable")

        db.commit()


_webhook_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="webhook")


def dispatch_event_background(db_session_factory, tenant_id: int, event: str, payload: dict):
    """Fire-and-forget webhook dispatch using a thread pool.

    Use this from synchronous route handlers. The submitted task creates its own
    DB session via the provided factory.
    """
    _webhook_executor.submit(_dispatch_in_thread, db_session_factory, tenant_id, event, payload)


def _dispatch_in_thread(db_session_factory, tenant_id, event, payload):
    """Execute webhook delivery in a background thread with its own DB session."""
    if db_session_factory is None:
        from app.backend.db import database
        db_session_factory = database.SessionLocal
    db = db_session_factory()
    try:
        dispatch_event(db, tenant_id, event, payload)
    except Exception as e:
        log.error("Webhook dispatch failed for tenant %s, event %s: %s", tenant_id, event, e)
    finally:
        db.close()


def validate_webhook_url(url: str) -> tuple[bool, str]:
    """Validate webhook URL is safe for delivery."""
    try:
        validate_public_url(url, require_https=True)
    except UnsafeURLError as e:
        return False, str(e)
    return True, ""
