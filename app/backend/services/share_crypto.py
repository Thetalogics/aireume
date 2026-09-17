"""Share-link token and passcode hashing (AUD-037)."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1


def new_share_token() -> str:
    return secrets.token_urlsafe(32)


def hash_share_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_passcode(passcode: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        passcode.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return f"scrypt${salt.hex()}${derived.hex()}"


def verify_passcode(passcode: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    if stored_hash.startswith("scrypt$"):
        try:
            _, salt_hex, digest_hex = stored_hash.split("$", 2)
            derived = hashlib.scrypt(
                passcode.encode("utf-8"),
                salt=bytes.fromhex(salt_hex),
                n=_SCRYPT_N,
                r=_SCRYPT_R,
                p=_SCRYPT_P,
                dklen=32,
            )
            return hmac.compare_digest(derived.hex(), digest_hex)
        except Exception:
            return False
    if stored_hash.startswith("$2"):
        try:
            import bcrypt
            return bcrypt.checkpw(passcode.encode("utf-8"), stored_hash.encode("utf-8"))
        except Exception:
            return False
    digest = hashlib.sha256(passcode.encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, stored_hash)
