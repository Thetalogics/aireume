# Product claims vs current software

Do not treat marketing or wiki language as certified fact. Current code supports:

| Phrase to avoid | Accurate substitute |
|---|---|
| GDPR/FCRA/SOC 2/HIPAA/ISO compliant | Supports related **workflows** / logging; no independent certification is claimed here |
| Fine-tuned / AI fine-tuning | Prompt/model **personalization** via labeled examples |
| Bias-free / unbiased / 100% accurate | Explainable scores with known limits; adverse-impact **metrics** when sample size allows |
| Fully automated / guaranteed | Human review remains required for hiring decisions |
| Enterprise ready / production ready | See `PRODUCTION_READINESS.md` for what is verified vs environment-dependent |
| Zero retention | Retention is configurable; object-storage delete can require retries |
| Real-time (as a guarantee) | Queue + polling; not a hard real-time system |

Repo copy should follow this table. Wiki under `.qoder/repowiki` is historical (`CLAIMS_NOTICE.md`).
