# app/backend/tests/test_audit_phase_c_secrets_entitlement.py
import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("INTEGRATION_MASTER_KEY", "ab" * 32)

from app.backend.models.db_models import ATSConnection
from app.backend.services.ats_connector import GenericAdapter
from app.backend.services.integration_secrets import decrypt_secret, encrypt_secret, secret_configured


def test_roundtrip_not_plaintext():
    ct = encrypt_secret("ghp_secret_value")
    assert ct != "ghp_secret_value"
    assert ct.startswith("enc:v1:")
    assert decrypt_secret(ct) == "ghp_secret_value"


def test_none_passthrough():
    assert encrypt_secret(None) is None
    assert decrypt_secret(None) is None
    assert secret_configured(None) is False


def test_wrong_key_does_not_include_secret(monkeypatch):
    ct = encrypt_secret("super-secret")
    monkeypatch.setenv("INTEGRATION_MASTER_KEY", "cd" * 32)
    import app.backend.services.integration_secrets as sec

    monkeypatch.setattr(sec, "_current_keys", lambda: {1: bytes.fromhex("cd" * 32)})
    with pytest.raises(Exception) as ei:
        decrypt_secret(ct)
    assert "super-secret" not in str(ei.value)


def test_decrypt_enc_v1_after_master_rotation(monkeypatch):
    key_a = "aa" * 32
    key_b = "bb" * 32
    monkeypatch.setenv("INTEGRATION_MASTER_KEY", key_a)
    monkeypatch.delenv("INTEGRATION_MASTER_KEY_PREVIOUS", raising=False)
    ct = encrypt_secret("rotate-me")
    assert ct.startswith("enc:v1:")
    monkeypatch.setenv("INTEGRATION_MASTER_KEY", key_b)
    monkeypatch.setenv("INTEGRATION_MASTER_KEY_PREVIOUS", key_a)
    assert decrypt_secret(ct) == "rotate-me"


def test_ats_create_encrypts_api_key_and_hides_from_json(auth_client, db, seed_subscription_plans):
    resp = auth_client.post(
        "/api/ats/connections",
        json={"provider": "generic", "label": "Sec ATS", "api_key": "plain-key"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["api_key_configured"] is True
    assert "plain-key" not in resp.text
    assert "api_key" not in body
    db.expire_all()
    conn = db.get(ATSConnection, body["id"])
    assert conn.api_key.startswith("enc:v")


def test_adapter_headers_use_decrypted_api_key():
    conn = MagicMock()
    conn.api_key = encrypt_secret("plain-key")
    conn.webhook_secret = None
    headers = GenericAdapter().get_headers(conn)
    assert headers["Authorization"] == "Bearer plain-key"
    assert "enc:v" not in headers["Authorization"]
