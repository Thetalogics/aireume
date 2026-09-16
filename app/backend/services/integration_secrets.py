"""AES-256-GCM encryption for ATS/integration secrets (AUD-015)."""
from __future__ import annotations

import base64
import logging
import os
import re
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger("aria.integration_secrets")

PREFIX = "enc:"
_HEX_KEY = re.compile(r"^[0-9a-fA-F]{64}$")
_NONCE_LEN = 12


class IntegrationSecretError(RuntimeError):
    """Decrypt/encrypt failure without embedding secret material."""


def _is_testing() -> bool:
    return os.getenv("TESTING", "").lower() in {"1", "true", "yes"}


def _parse_key(raw: str) -> bytes:
    value = (raw or "").strip()
    if _HEX_KEY.match(value):
        return bytes.fromhex(value)
    pad = "=" * ((4 - len(value) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(value + pad)
    except Exception as extra:
        raise IntegrationSecretError("Invalid INTEGRATION_MASTER_KEY encoding") from extra
    if len(decoded) != 32:
        raise IntegrationSecretError("INTEGRATION_MASTER_KEY must be 32 bytes")
    return decoded


def _current_keys() -> dict[int, bytes]:
    keys: dict[int, bytes] = {}
    current = os.getenv("INTEGRATION_MASTER_KEY")
    if not current and _is_testing():
        current = "0" * 64
    previous = os.getenv("INTEGRATION_MASTER_KEY_PREVIOUS")
    if current:
        keys[1] = _parse_key(current)
    if previous:
        keys[0] = _parse_key(previous)
    return keys


def secret_configured(stored: Optional[str]) -> bool:
    return bool(stored)


def encrypt_secret(plaintext: Optional[str]) -> Optional[str]:
    if plaintext is None or plaintext == "":
        return None
    if plaintext.startswith("enc:v"):
        return plaintext
    keys = _current_keys()
    key = keys.get(1)
    if key is None:
        raise RuntimeError("INTEGRATION_MASTER_KEY is required to encrypt integration secrets")
    nonce = os.urandom(_NONCE_LEN)
    packed = nonce + AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    token = base64.urlsafe_b64encode(packed).decode("ascii").rstrip("=")
    return f"enc:v1:{token}"


def decrypt_secret(stored: Optional[str]) -> Optional[str]:
    if stored is None or stored == "":
        return stored if stored is None else None
    if not stored.startswith("enc:v"):
        return stored
    parts = stored.split(":", 2)
    if len(parts) != 3 or not parts[1].startswith("v"):
        raise IntegrationSecretError("Malformed integration secret ciphertext")
    try:
        version = int(parts[1][1:])
    except ValueError as extra:
        raise IntegrationSecretError("Malformed integration secret version") from extra
    keys = _current_keys()
    if not keys:
        raise IntegrationSecretError("No key available for ciphertext version")
    payload = parts[2]
    pad = "=" * ((4 - len(payload) % 4) % 4)
    try:
        packed = base64.urlsafe_b64decode(payload + pad)
    except Exception as extra:
        raise IntegrationSecretError("Malformed integration secret encoding") from extra
    if len(packed) < _NONCE_LEN + 16:
        raise IntegrationSecretError("Malformed integration secret ciphertext")
    nonce, ct = packed[:_NONCE_LEN], packed[_NONCE_LEN:]
    ordered: list[bytes] = []
    version_key = keys.get(version)
    if version_key is not None:
        ordered.append(version_key)
    for other in keys.values():
        if other not in ordered:
            ordered.append(other)
    last_exc: Exception | None = None
    for key in ordered:
        try:
            return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")
        except Exception as extra:
            last_exc = extra
            continue
    raise IntegrationSecretError("Failed to decrypt integration secret") from last_exc


def decrypt_and_upgrade(conn, field_name: str, db=None) -> Optional[str]:
    stored = getattr(conn, field_name, None)
    plain = decrypt_secret(stored)
    if stored and not str(stored).startswith("enc:v") and plain:
        try:
            setattr(conn, field_name, encrypt_secret(plain))
            if db is not None:
                db.commit()
        except Exception:
            logger.warning("Lazy re-encrypt of ATS secret field failed")
    return plain
