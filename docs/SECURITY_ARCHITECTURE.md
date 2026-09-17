# Security architecture

ARIA is a multi-tenant recruiting product. Paid capability comes only from **effective entitlement** (provider/trial), not from a tenant writing `plan_id`.

## Authentication

- JWT access + refresh cookies, CSRF double-submit, optional MFA.
- SAML uses python3-saml with the tenant IdP certificate only. AuthnRequest IDs live in Redis in production and are consumed atomically (`GETDEL`/Lua).
- OAuth identities are unique per provider user. Email membership is `(tenant_id, email)`. Ambiguous emails fail closed.

## Authorization

- Roles: platform admin, tenant admin, recruiter, hiring manager, viewer.
- HM access is assignment-scoped. Requisition users are tenant-validated.

## Tenant isolation

- Queries that load candidates, results, requisitions, and ATS objects filter `tenant_id`.
- ScreeningCommand binds requisition/candidate to the command tenant.

## Secrets

- Integration credentials go through `integration_secrets`.
- No IdP certs, tokens, or resume PII in logs.

## Outbound SSRF

- Shared `url_safety` with DNS pinning and private-network rejection for ATS, JD fetch, and webhooks.

## Queue

- Fingerprint includes result-changing config. Quota is reserved and released. Heartbeats, leases, DLQ unique `original_job_id`. Sync and queue persist through `execute_screening`.

## Billing trust

- Stripe checkout uses server-mapped `price_...` IDs. Plan change updates the subscription item price. Webhooks map purchased items to internal plans.

## PII

- Candidate delete attempts object-storage deletion. Anonymization clears candidate and screening text. Export completeness is still being expanded. External LLM calls should go through PII redaction; transcript analysis already does.
