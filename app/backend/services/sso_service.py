"""SAML 2.0 SSO: python3-saml verification, pinned IdP cert, Redis request binding."""
import base64
import logging
import os
import uuid
import zlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from sqlalchemy.orm import Session

from app.backend.models.db_models import SSOConfig, User, Tenant

logger = logging.getLogger(__name__)

# ─── Allowed SSO roles ────────────────────────────────────────────────────────

ALLOWED_SSO_ROLES = {"viewer", "recruiter", "admin", "hiring_manager", "ta_lead"}

# ─── SAML Namespaces ──────────────────────────────────────────────────────────

SAML_PROTOCOL_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
SAML_ASSERTION_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
XMLDSIG_NS = "http://www.w3.org/2000/09/xmldsig#"

_NS_MAP = {
    "saml2p": SAML_PROTOCOL_NS,
    "saml2": SAML_ASSERTION_NS,
    "ds": XMLDSIG_NS,
}


def _ns_tag(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


# ─── Certificate helpers ──────────────────────────────────────────────────────

def _parse_x509_cert(pem_str: str) -> x509.Certificate:
    """Parse a PEM or bare-base64 X.509 certificate string."""
    pem = pem_str.strip()
    if not pem.startswith("-----BEGIN"):
        pem = f"-----BEGIN CERTIFICATE-----\n{pem}\n-----END CERTIFICATE-----"
    return x509.load_pem_x509_certificate(pem.encode())


SAML_REQUEST_TTL_SECONDS = 600


def is_sso_trust_ready(sso_config: SSOConfig) -> bool:
    cert = (getattr(sso_config, "idp_certificate", None) or "").strip()
    sso_url = (getattr(sso_config, "idp_sso_url", None) or "").strip()
    return bool(cert and sso_url)


def _saml_request_key(request_id: str) -> str:
    return f"saml:authn:{request_id}"


def _saml_state_requires_redis() -> bool:
    env = os.getenv("APP_ENV", os.getenv("ENVIRONMENT", "")).lower()
    return env in ("production", "prod")


def _saml_redis_or_raise() -> None:
    from app.backend.services.shared_cache import redis_is_healthy

    if _saml_state_requires_redis() and not redis_is_healthy():
        raise ValueError("SSO is disabled: request state store is unavailable")


def persist_saml_authn_request(request_id: str, tenant_id: int) -> None:
    from app.backend.services.shared_cache import cache_set

    _saml_redis_or_raise()
    cache_set(
        _saml_request_key(request_id),
        {"tenant_id": int(tenant_id)},
        SAML_REQUEST_TTL_SECONDS,
        require_redis=_saml_state_requires_redis(),
    )


def peek_saml_authn_request(request_id: str, tenant_id: int) -> bool:
    from app.backend.services.shared_cache import cache_get

    _saml_redis_or_raise()
    stored = cache_get(_saml_request_key(request_id), require_redis=_saml_state_requires_redis())
    if not stored or not isinstance(stored, dict):
        return False
    return int(stored.get("tenant_id") or 0) == int(tenant_id)


def consume_saml_authn_request(request_id: str, tenant_id: int) -> bool:
    from app.backend.services.shared_cache import cache_consume_if_tenant

    _saml_redis_or_raise()
    require_redis = _saml_state_requires_redis()
    key = _saml_request_key(request_id)
    stored = cache_consume_if_tenant(key, tenant_id, require_redis=require_redis)
    if stored is None or stored is False:
        return False
    if not isinstance(stored, dict):
        return False
    return int(stored.get("tenant_id") or 0) == int(tenant_id)


def _idp_cert_body(pem_str: str) -> str:
    return (
        pem_str.replace("-----BEGIN CERTIFICATE-----", "")
        .replace("-----END CERTIFICATE-----", "")
        .replace("\r", "")
        .replace("\n", "")
        .replace(" ", "")
        .strip()
    )


def _onelogin_settings(sso_config: SSOConfig) -> dict:
    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": sso_config.sp_entity_id or "",
            "assertionConsumerService": {
                "url": sso_config.sp_acs_url or "",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            "x509cert": "",
            "privateKey": "",
        },
        "idp": {
            "entityId": sso_config.idp_entity_id or "",
            "singleSignOnService": {
                "url": sso_config.idp_sso_url or "",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": _idp_cert_body(sso_config.idp_certificate or ""),
        },
        "security": {
            "wantAssertionsSigned": True,
            "wantMessagesSigned": False,
            "wantAttributeStatement": False,
            "rejectUnsolicitedResponsesWithInResponseTo": True,
        },
    }


def _http_request_for_acs(sso_config: SSOConfig, saml_response_b64: str) -> dict:
    parsed = urlparse(sso_config.sp_acs_url or "http://localhost/api/sso/callback")
    return {
        "https": "on" if parsed.scheme == "https" else "off",
        "http_host": parsed.netloc or "localhost",
        "script_name": parsed.path or "/",
        "get_data": {},
        "post_data": {"SAMLResponse": saml_response_b64},
    }


def authenticate_saml_response_with_onelogin(
    saml_response_b64: str,
    sso_config: SSOConfig,
    request_id: str,
) -> None:
    """Cryptographic SAML verification pinned to the tenant IdP certificate."""
    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth
    except ImportError as exc:
        raise ValueError("SAML Response signature verification failed") from exc

    if not request_id:
        raise ValueError("SAML InResponseTo is invalid or expired")
    try:
        auth = OneLogin_Saml2_Auth(
            _http_request_for_acs(sso_config, saml_response_b64),
            old_settings=_onelogin_settings(sso_config),
        )
        auth.process_response(request_id=request_id)
    except ValueError:
        raise
    except Exception as exc:
        logger.warning("SAML verification failed: %s", type(exc).__name__)
        raise ValueError("SAML Response signature verification failed") from exc
    errors = auth.get_errors()
    if errors or not auth.is_authenticated():
        logger.warning(
            "SAML verification failed: %s %s",
            errors or "not authenticated",
            auth.get_last_error_reason(),
        )
        raise ValueError("SAML Response signature verification failed")


# ─── SAML Request / Response helpers ──────────────────────────────────────────

def _build_authn_request(
    sp_entity_id: str,
    acs_url: str,
    request_id: str,
) -> bytes:
    """Build a minimal SAML 2.0 AuthnRequest XML document."""
    issue_instant = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<samlp:AuthnRequest
    xmlns:samlp="{SAML_PROTOCOL_NS}"
    xmlns:saml="{SAML_ASSERTION_NS}"
    ID="{request_id}"
    Version="2.0"
    IssueInstant="{issue_instant}"
    Destination=""
    ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
    AssertionConsumerServiceURL="{acs_url}">
    <saml:Issuer>{sp_entity_id}</saml:Issuer>
    <samlp:NameIDPolicy Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress" AllowCreate="true"/>
</samlp:AuthnRequest>'''
    return xml.encode("utf-8")


def _extract_assertion_attributes(assertion_elem: ET.Element) -> dict:
    """Extract attribute statement values from a SAML Assertion."""
    attrs = {}
    multi_value_attrs: dict[str, list[str]] = {}
    attr_statement = assertion_elem.find(f".//{_ns_tag(SAML_ASSERTION_NS, 'AttributeStatement')}")
    if attr_statement is not None:
        for attr in attr_statement.findall(_ns_tag(SAML_ASSERTION_NS, "Attribute")):
            name = attr.get("Name", "").strip()
            values = [
                v.text.strip()
                for v in attr.findall(_ns_tag(SAML_ASSERTION_NS, "AttributeValue"))
                if v.text
            ]
            if not values:
                continue
            lower_name = name.lower()
            if "emailaddress" in lower_name or lower_name == "email":
                attrs["email"] = values[0]
            elif "givenname" in lower_name or lower_name == "first_name":
                attrs["first_name"] = values[0]
            elif "surname" in lower_name or lower_name == "last_name":
                attrs["last_name"] = values[0]
            elif "displayname" in lower_name or lower_name == "name":
                attrs["name"] = values[0]
            elif any(k in lower_name for k in ("memberof", "group", "role")):
                multi_value_attrs.setdefault(name, []).extend(values)
            else:
                attrs[name] = values[0]
                if len(values) > 1:
                    multi_value_attrs.setdefault(name, []).extend(values)
    if multi_value_attrs:
        attrs["_multi"] = multi_value_attrs
    return attrs


def _extract_idp_groups(attrs: dict, groups_attribute: str | None) -> list[str]:
    """Normalize IdP group claims from SAML attributes."""
    groups: list[str] = []
    multi = attrs.get("_multi") or {}
    attr_key = (groups_attribute or "groups").strip()

    for name, values in multi.items():
        short = name.split("/")[-1].split(":")[-1].lower()
        key = attr_key.lower()
        if key in short or short in {key, "memberof", "groups", "group", "roles"}:
            groups.extend(values)

    for candidate_key in (attr_key, "groups", "group", "memberOf", "roles"):
        val = attrs.get(candidate_key)
        if isinstance(val, str):
            groups.append(val)
        elif isinstance(val, list):
            groups.extend(val)

    # De-duplicate while preserving order
    seen = set()
    normalized = []
    for g in groups:
        g = (g or "").strip()
        if g and g not in seen:
            seen.add(g)
            normalized.append(g)
    return normalized


def resolve_sso_role(db: Session, tenant_id: int, sso_config: SSOConfig, groups: list[str]) -> str:
    """Pick highest-privilege mapped role from IdP groups, else default_role."""
    from app.backend.models.db_models import SSOGroupRoleMapping

    role_rank = {"viewer": 1, "hiring_manager": 2, "recruiter": 3, "admin": 4}
    best_role = sso_config.default_role or "viewer"
    if best_role not in ALLOWED_SSO_ROLES:
        best_role = "viewer"

    if not groups:
        return best_role

    mappings = (
        db.query(SSOGroupRoleMapping)
        .filter(SSOGroupRoleMapping.tenant_id == tenant_id)
        .all()
    )
    group_set = {g.lower() for g in groups}
    for mapping in mappings:
        if mapping.idp_group.lower() in group_set and mapping.role in ALLOWED_SSO_ROLES:
            if role_rank.get(mapping.role, 0) > role_rank.get(best_role, 0):
                best_role = mapping.role
    return best_role


def _extract_name_id(subject_elem: ET.Element) -> Optional[str]:
    """Extract NameID from Subject element."""
    name_id = subject_elem.find(_ns_tag(SAML_ASSERTION_NS, "NameID"))
    if name_id is not None and name_id.text:
        return name_id.text.strip()
    return None


# ─── Service Class ────────────────────────────────────────────────────────────

class SSOService:
    """Lightweight SAML 2.0 processor."""

    def generate_saml_request(self, sso_config: SSOConfig) -> tuple[str, str]:
        """
        Generate SAML AuthnRequest, persist request id for ACS binding,
        return (redirect_url, request_id).
        """
        if not is_sso_trust_ready(sso_config):
            raise ValueError("SSO is disabled: IdP certificate is not configured")
        request_id = f"ARIA{uuid.uuid4().hex[:24].upper()}"
        persist_saml_authn_request(request_id, sso_config.tenant_id)
        authn_xml = _build_authn_request(
            sp_entity_id=sso_config.sp_entity_id,
            acs_url=sso_config.sp_acs_url,
            request_id=request_id,
        )
        compressed = zlib.compress(authn_xml)[2:-4]
        saml_request_b64 = base64.b64encode(compressed).decode()

        sep = "&" if "?" in sso_config.idp_sso_url else "?"
        redirect_url = f"{sso_config.idp_sso_url}{sep}SAMLRequest={saml_request_b64}"
        return redirect_url, request_id

    def process_saml_response(
        self,
        saml_response_b64: str,
        sso_config: SSOConfig,
        verify_signature: bool = True,
    ) -> dict:
        """Validate SAML Response with python3-saml, request binding, and replay protection."""
        if verify_signature is False:
            raise ValueError("SAML Response signature verification failed")
        if not is_sso_trust_ready(sso_config):
            raise ValueError("SSO is disabled: IdP certificate is not configured")

        try:
            response_xml = base64.b64decode(saml_response_b64)
        except Exception:
            raise ValueError("Invalid SAMLResponse base64 encoding")

        try:
            root = ET.fromstring(response_xml)
        except ET.ParseError as exc:
            raise ValueError(f"Invalid SAML Response XML: {exc}")

        in_response_to = (root.get("InResponseTo") or "").strip()
        if not in_response_to:
            raise ValueError("SAML Response missing InResponseTo")
        if not peek_saml_authn_request(in_response_to, sso_config.tenant_id):
            raise ValueError("SAML InResponseTo is invalid or expired")

        authenticate_saml_response_with_onelogin(
            saml_response_b64, sso_config, request_id=in_response_to
        )
        if not consume_saml_authn_request(in_response_to, sso_config.tenant_id):
            raise ValueError("SAML InResponseTo is invalid or expired")

        # Check for error status
        status_elem = root.find(_ns_tag(SAML_PROTOCOL_NS, "Status"))
        if status_elem is not None:
            status_code = status_elem.find(f".//{_ns_tag(SAML_PROTOCOL_NS, 'StatusCode')}")
            if status_code is not None:
                code = status_code.get("Value", "")
                if "Success" not in code:
                    raise ValueError(f"SAML error status: {code}")

        issuer_elem = root.find(_ns_tag(SAML_ASSERTION_NS, "Issuer"))
        if issuer_elem is None:
            issuer_elem = root.find(f".//{_ns_tag(SAML_ASSERTION_NS, 'Issuer')}")
        issuer = (issuer_elem.text or "").strip() if issuer_elem is not None else ""
        if sso_config.idp_entity_id and issuer and issuer != sso_config.idp_entity_id:
            raise ValueError("SAML issuer mismatch")

        # Find Assertion
        assertion = root.find(_ns_tag(SAML_ASSERTION_NS, "Assertion"))
        if assertion is None:
            raise ValueError("No SAML Assertion found in response")

        assertion_id = (assertion.get("ID") or "").strip()
        if assertion_id:
            from app.backend.services.shared_cache import cache_get, cache_set

            replay_key = f"saml:assert:{sso_config.tenant_id}:{assertion_id}"
            require_redis = _saml_state_requires_redis()
            if cache_get(replay_key, require_redis=require_redis):
                raise ValueError("SAML assertion has already been used")
            cache_set(replay_key, 1, SAML_REQUEST_TTL_SECONDS, require_redis=require_redis)

        # Check Conditions (Audience & NotOnOrAfter)
        conditions = assertion.find(_ns_tag(SAML_ASSERTION_NS, "Conditions"))
        if conditions is not None:
            not_before = conditions.get("NotBefore")
            not_on_or_after = conditions.get("NotOnOrAfter")
            now = datetime.now(timezone.utc)

            if not_on_or_after:
                expiry = datetime.fromisoformat(not_on_or_after.replace("Z", "+00:00"))
                if now >= expiry:
                    raise ValueError("SAML Assertion has expired")

            if not_before:
                start = datetime.fromisoformat(not_before.replace("Z", "+00:00"))
                if now < start:
                    raise ValueError("SAML Assertion not yet valid")

            # Audience check
            audience_restriction = conditions.find(_ns_tag(SAML_ASSERTION_NS, "AudienceRestriction"))
            if audience_restriction is not None:
                audiences = [
                    a.text.strip()
                    for a in audience_restriction.findall(_ns_tag(SAML_ASSERTION_NS, "Audience"))
                    if a.text
                ]
                if sso_config.sp_entity_id and sso_config.sp_entity_id not in audiences:
                    raise ValueError("SAML Audience mismatch")

        # Extract NameID
        subject = assertion.find(_ns_tag(SAML_ASSERTION_NS, "Subject"))
        name_id = _extract_name_id(subject) if subject is not None else None
        if not name_id:
            raise ValueError("SAML Assertion missing NameID")

        # Extract attributes
        attrs = _extract_assertion_attributes(assertion)

        # Build result
        email = attrs.get("email", name_id)  # fallback to NameID if no email attr
        name = attrs.get("name")
        if not name:
            first = attrs.get("first_name", "")
            last = attrs.get("last_name", "")
            name = f"{first} {last}".strip() or None

        return {
            "email": email.lower().strip(),
            "name": name,
            "name_id": name_id,
            "first_name": attrs.get("first_name"),
            "last_name": attrs.get("last_name"),
            "groups": _extract_idp_groups(attrs, getattr(sso_config, "groups_attribute", None)),
        }

    def get_or_create_user(
        self,
        db: Session,
        tenant_id: int,
        sso_config: SSOConfig,
        user_attrs: dict,
    ) -> User:
        """
        Find existing user by email or auto-provision.

        For auto-provisioned users a cryptographically random unusable password
        is generated because the column is non-nullable.
        """
        email = user_attrs["email"]
        groups = user_attrs.get("groups") or []
        role = resolve_sso_role(db, tenant_id, sso_config, groups)
        user = db.query(User).filter(User.email == email, User.tenant_id == tenant_id).first()

        if user:
            # Only sync role when the IdP asserted group membership; otherwise
            # preserve manually assigned roles for pre-existing accounts.
            if groups and user.role != role:
                user.role = role
                db.commit()
                db.refresh(user)
            return user

        if not sso_config.auto_provision:
            raise ValueError("User not found and auto-provisioning is disabled")

        # Auto-provision user with random unusable password
        random_password = secrets.token_urlsafe(32)
        # Import hash function from auth module
        from app.backend.routes.auth import _hash_password

        user = User(
            tenant_id=tenant_id,
            email=email,
            hashed_password=_hash_password(random_password),
            role=role,
            is_active=True,
            email_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


# ─── Singleton instance ───────────────────────────────────────────────────────

sso_service = SSOService()
