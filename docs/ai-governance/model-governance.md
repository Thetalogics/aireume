# Model Governance

Supported providers for narrative/enrichment: Gemini, Ollama, OpenRouter, plus local structured-JSON fallbacks.

Decisions record requested and actual provider/model, fallback usage, prompt template id/version, and structured schema version. Prompt bodies, API keys, and chain-of-thought are not stored.

If deterministic scoring succeeds and narrative fails, the decision remains valid with `narrative_status=failed`.

LLM output is schema-validated before persistence. Invalid output is a narrative failure, not a partial authoritative write.
