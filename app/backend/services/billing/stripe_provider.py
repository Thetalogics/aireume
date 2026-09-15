"""Stripe payment provider implementation."""
from typing import Dict, Any

from app.backend.services.billing.base import PaymentProvider

try:
    import stripe  # type: ignore
except ImportError:
    stripe = None


class StripeProvider(PaymentProvider):
    """Payment provider backed by Stripe.

    Requires the ``stripe`` Python package to be installed.  All methods
    raise a clear error when the dependency is missing.
    """

    def __init__(self, api_key: str = "", webhook_secret: str = ""):
        self._api_key = api_key
        self._webhook_secret = webhook_secret
        if stripe is not None and api_key:
            stripe.api_key = api_key

    def _require_stripe(self):
        if stripe is None:
            raise RuntimeError(
                "The 'stripe' package is not installed. "
                "Install it with: pip install stripe"
            )

    @property
    def provider_name(self) -> str:
        return "stripe"

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
        self._require_stripe()
        resolved_price = (price_id or "").strip()
        if not resolved_price.startswith("price_"):
            raise ValueError("Stripe checkout requires a server-mapped price_ id")
        metadata = {"tenant_id": str(tenant_id)}
        if extra_metadata:
            metadata.update({str(k): str(v) for k, v in extra_metadata.items() if v is not None})
        checkout_params: Dict[str, Any] = {
            "mode": "subscription",
            "payment_method_types": ["card"],
            "line_items": [{"price": resolved_price, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": metadata,
        }
        # Reuse an existing Stripe customer to avoid creating duplicates
        if stripe_customer_id:
            checkout_params["customer"] = stripe_customer_id
        else:
            # Always create a new customer so we can capture the customer ID
            checkout_params["customer_creation"] = "always"
        session = stripe.checkout.Session.create(**checkout_params)
        return {
            "session_id": session.id,
            "url": session.url,
            "provider": self.provider_name,
        }

    def cancel_subscription(
        self,
        tenant_id: int,
        subscription_id: str,
    ) -> Dict[str, Any]:
        self._require_stripe()
        sub = stripe.Subscription.modify(subscription_id, cancel_at_period_end=True)
        return {
            "subscription_id": sub.id,
            "status": sub.status,
            "cancel_at_period_end": sub.cancel_at_period_end,
            "provider": self.provider_name,
        }

    def get_subscription_status(
        self,
        tenant_id: int,
        subscription_id: str,
    ) -> Dict[str, Any]:
        self._require_stripe()
        sub = stripe.Subscription.retrieve(subscription_id)
        return {
            "subscription_id": sub.id,
            "status": sub.status,
            "current_period_end": sub.current_period_end,
            "provider": self.provider_name,
        }

    def prorate_plan_change(
        self,
        tenant_id: int,
        subscription_id: str,
        old_plan_price: int,
        new_plan_price: int,
        proration_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Apply a prorated plan change via Stripe's proration API.

        Uses ``proration_behavior='create_prorations'`` on the subscription
        so Stripe generates invoice items automatically.
        """
        self._require_stripe()
        if not subscription_id:
            return {
                "tenant_id": tenant_id,
                "provider": self.provider_name,
                "status": "skipped_no_subscription_id",
                "proration": proration_data,
            }

        try:
            sub = stripe.Subscription.modify(
                subscription_id,
                proration_behavior="create_prorations",
            )
            return {
                "tenant_id": tenant_id,
                "subscription_id": sub.id,
                "provider": self.provider_name,
                "status": sub.status,
                "proration_behavior": "create_prorations",
                "proration": proration_data,
            }
        except Exception as exc:
            return {
                "tenant_id": tenant_id,
                "subscription_id": subscription_id,
                "provider": self.provider_name,
                "status": "error",
                "error": str(exc),
                "proration": proration_data,
            }

    def handle_webhook_event(
        self,
        payload: bytes,
        signature: str,
    ) -> Dict[str, Any]:
        self._require_stripe()
        event = stripe.Webhook.construct_event(
            payload, signature, self._webhook_secret
        )
        return {
            "event_id": event.get("id"),
            "event_type": event["type"],
            "data": event["data"],
            "provider": self.provider_name,
        }
