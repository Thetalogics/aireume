# Historical secret baseline (gitleaks)

Full-history gitleaks (`secret-scan-history`) uses `.gitleaksignore` with **exact
fingerprints** only. New leaks still fail (`--exit-code 1`). Commit-range
`secret-scan` is unchanged and does not use this baseline as a reason to
weaken range scanning.

These findings predate commit `5c33aa65be7dea7e60ac7143c639c8bd4de9bee4`.

| Fingerprint | Original commit | Path | Rule | Classification | Credential type | Rotation / revocation | Why baselined |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `87c5223ec34a08074c0b10db22d3ff1436782f3d:.env.example:generic-api-key:23` | `87c5223ec34a08074c0b10db22d3ff1436782f3d` | `.env.example` | generic-api-key | **Real historical credential** (non-placeholder `JWT_SECRET_KEY` assignment in git history; value not logged here) | JWT HMAC signing secret (`JWT_SECRET_KEY`) | **OPS ACTION REQUIRED — not rotated/revoked in this session.** Cursor cannot rotate deployed JWT secrets. Rotate `JWT_SECRET_KEY` in every environment that ever used the historic example value, invalidate sessions/refresh tokens, then mark verified here. Checked 2026-09-21: unusable-old-credential **not verified**. | Fingerprint-only baseline; immutable git history. |
| `87c5223ec34a08074c0b10db22d3ff1436782f3d:.env.example:generic-api-key:43` | `87c5223ec34a08074c0b10db22d3ff1436782f3d` | `.env.example` | generic-api-key | **Real historical credential** (non-placeholder `OLLAMA_API_KEY` assignment in git history; value not logged here) | Ollama Cloud API key (`OLLAMA_API_KEY`) | **OPS ACTION REQUIRED — not rotated/revoked in this session.** Revoke the historic key in the Ollama console and issue a new key. Checked 2026-09-21: unusable-old-credential **not verified**. | Fingerprint-only baseline; immutable git history. |
| `ad5f3f5e9784ed17aaa589cd2729fc5695153cd5:PRODUCTION_DEPLOYMENT_GUIDE.md:generic-api-key:75` | `ad5f3f5e9784ed17aaa589cd2729fc5695153cd5` | `PRODUCTION_DEPLOYMENT_GUIDE.md` | generic-api-key | **Real historical credential** (non-placeholder JWT example in the old deployment guide; value not logged here) | JWT HMAC signing secret (`JWT_SECRET_KEY`) | **OPS ACTION REQUIRED — not rotated/revoked in this session.** Same JWT rotation as the `.env.example` finding if that documented value was ever deployed. Checked 2026-09-21: unusable-old-credential **not verified**. | Fingerprint-only baseline; immutable git history. |
| `5874d139dfd407ac1d13304f6665ad2ebf751be9:app/backend/tests/test_billing.py:stripe-access-token:163` | `5874d139dfd407ac1d13304f6665ad2ebf751be9` | `app/backend/tests/test_billing.py` | stripe-access-token | **Synthetic test fixture** | Stripe test token shape | N/A — not a live Stripe secret | Historical commit still matches `sk_test_` detector; current allowlist is path-based for working tree only. |
| `0731f7960c235f5c34eb0449c13e5b5d270043e8:DEPLOYMENT_GUIDE.md:curl-auth-header:80` | `0731f7960c235f5c34eb0449c13e5b5d270043e8` | `DEPLOYMENT_GUIDE.md` | curl-auth-header | **Placeholder documentation** | Bearer token in curl example | N/A | Docs sample Authorization header in old commit. |
| `0731f7960c235f5c34eb0449c13e5b5d270043e8:DEPLOYMENT_GUIDE.md:curl-auth-header:88` | `0731f7960c235f5c34eb0449c13e5b5d270043e8` | `DEPLOYMENT_GUIDE.md` | curl-auth-header | **Placeholder documentation** | Bearer token in curl example | N/A | Docs sample Authorization header in old commit. |

Do not add fingerprints here without classifying the finding and, for real
credentials, stating rotation status honestly.

## OPS ACTION REQUIRED (2026-09-21)

Cursor cannot rotate or revoke provider-side credentials. `.gitleaksignore` is
not a substitute for rotation.

| Credential | Historic locations (no values) | Action | Verified unusable? |
| --- | --- | --- | --- |
| `JWT_SECRET_KEY` (HMAC) | `.env.example` @ `87c5223` L23 and `PRODUCTION_DEPLOYMENT_GUIDE.md` @ `ad5f3f5` L75 are the **same historic value** | Rotate in every environment that ever used it; invalidate sessions/refresh tokens | No |
| `OLLAMA_API_KEY` | `.env.example` @ `87c5223` L43 | Revoke in Ollama Cloud console; provision a new key | No |

After ops verifies, update the rotation column above with date and evidence (console screenshot / “old key rejected”), still without writing the secret.
