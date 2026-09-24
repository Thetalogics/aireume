"""Staging and production refuse to boot when an LLM model they will call is unset."""

import pytest

from app.backend.main import _validate_environment


def _ready(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("OLLAMA_MODEL_BACKEND", "deepseek-v4.1-flash:cloud")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.7-flash")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "qwen/qwen3.8-27b:free")
    monkeypatch.setenv("TESTING", "false")


def test_staging_exits_when_ollama_model_missing(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.delenv("OLLAMA_MODEL_BACKEND", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    with pytest.raises(SystemExit):
        _validate_environment()


def test_staging_starts_without_gemini_or_openrouter(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _validate_environment()


def test_staging_starts_when_llm_models_are_set(monkeypatch):
    _ready(monkeypatch)
    _validate_environment()


def test_openrouter_model_not_required_without_key(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    _validate_environment()


def test_development_does_not_exit_when_models_missing(monkeypatch):
    _ready(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("OLLAMA_MODEL_BACKEND", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    _validate_environment()
