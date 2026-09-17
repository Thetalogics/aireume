"""
Shared test utilities that can be safely imported by test modules
without triggering conftest.py re-initialization.
"""
from app.backend.db import database


def access_token_from(resp) -> str:
    """Read access token from httpOnly cookie, then JSON body (API grant)."""
    token = resp.cookies.get("access_token")
    if token:
        return token
    data = resp.json() if resp.content else {}
    return (data or {}).get("access_token")


def _verify_user_via_api(email: str):
    """Mark a user's email as verified via the DB session.

    Uses ``database.SessionLocal`` which is set to ``TestingSessionLocal``
    by conftest.py at startup, so it always targets the in-memory test DB
    regardless of which module scope the caller lives in.
    """
    from app.backend.models.db_models import User
    db = database.SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.email_verified = True
            db.commit()
    finally:
        db.close()


def resolve_plan(db, *names: str):
    """Look up a subscription plan by name, supporting legacy aliases (e.g. pro → growth)."""
    from app.backend.models.db_models import SubscriptionPlan
    return db.query(SubscriptionPlan).filter(
        SubscriptionPlan.name.in_(names),
        SubscriptionPlan.is_active == True,
    ).first()


def assign_tenant_plan(db, *plan_names: str, slug=None, tenant_id=None, email=None):
    """Assign a subscription plan to a tenant (by slug, id, or user email)."""
    from app.backend.models.db_models import Tenant, User
    from app.backend.services.feature_flag_service import invalidate_cache

    tenant = None
    if tenant_id is not None:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    elif slug:
        tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
    elif email:
        user = db.query(User).filter(User.email == email).first()
        if user:
            tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()

    if tenant is None:
        raise ValueError("assign_tenant_plan: tenant not found")

    plan = resolve_plan(db, *plan_names)
    if plan is None:
        raise ValueError(f"assign_tenant_plan: no plan matching {plan_names!r}")

    tenant.plan_id = plan.id
    tenant.subscription_status = "active"
    db.commit()
    invalidate_cache(tenant.id)
    return tenant


def allow_ad_hoc_screening(db, *, tenant_id=None, slug=None, email=None):
    """Allow analyze without requisition_id (default product mode is requisition_required)."""
    from app.backend.models.db_models import Tenant, User
    from app.backend.services.requisition_service import get_or_create_tenant_settings

    tid = tenant_id
    if tid is None and slug:
        tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
        tid = tenant.id if tenant else None
    if tid is None and email:
        user = db.query(User).filter(User.email == email).first()
        tid = user.tenant_id if user else None
    if tid is None:
        raise ValueError("allow_ad_hoc_screening: tenant not found")

    settings = get_or_create_tenant_settings(db, tid)
    settings.screening_mode = "allow_ad_hoc"
    db.commit()
    return settings


class StatementCounter:
    """Count SQL statements on the test engine (AUD-043)."""

    def __init__(self, engine):
        self.engine = engine
        self.statements = []

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def __enter__(self):
        from sqlalchemy import event
        event.listen(self.engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc):
        from sqlalchemy import event
        event.remove(self.engine, "before_cursor_execute", self._on_execute)

    @property
    def dml(self):
        out = []
        for stmt in self.statements:
            token = stmt.lstrip().split(None, 1)[0].upper() if stmt.strip() else ""
            if token in {"INSERT", "UPDATE", "DELETE"}:
                out.append(stmt)
        return out

