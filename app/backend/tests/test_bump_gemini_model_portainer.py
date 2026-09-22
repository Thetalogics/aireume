"""Unit tests for scripts/bump_gemini_model_portainer.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "bump_gemini_model_portainer.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("bump_gemini_model_portainer", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_smoke_thinking_matches_runtime_helper():
    from app.backend.services.llm_service import _gemini_thinking_config

    mod = _load_mod()
    for model in ("gemini-3.5-flash", "gemini-3.7-flash", "gemini-3.8-flash"):
        assert mod.production_thinking_config(model) == _gemini_thinking_config(model, True)


def test_smoke_sends_the_app_thinking_config(monkeypatch):
    mod = _load_mod()
    posted: dict = {}

    def fake_http(method, url, *, headers=None, body=None):
        posted["body"] = body
        return {"candidates": []}

    monkeypatch.setattr(mod, "_http_json", fake_http)
    mod.smoke_test_model("gk", "gemini-3.8-flash")
    thinking = posted["body"]["generationConfig"]["thinkingConfig"]
    assert thinking == {"thinkingLevel": "low"}


def test_gemini_api_key_prefers_process_env_over_portainer_stack():
    mod = _load_mod()
    key = mod.gemini_api_key_from_sources(
        process_env={"GEMINI_API_KEY": "from-github"},
        stack_env={"GEMINI_API_KEY": "from-portainer"},
    )
    assert key == "from-github"


def test_gemini_api_key_falls_back_to_portainer_stack_env():
    mod = _load_mod()
    key = mod.gemini_api_key_from_sources(
        process_env={},
        stack_env={"GEMINI_API_KEY": "from-portainer"},
    )
    assert key == "from-portainer"


def test_gemini_api_key_missing_from_both_sources_raises():
    mod = _load_mod()
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        mod.gemini_api_key_from_sources(process_env={}, stack_env={})


def test_dry_run_reads_key_from_portainer_and_never_puts(monkeypatch, capsys):
    mod = _load_mod()
    calls: list[tuple[str, str]] = []

    def fake_http(method, url, *, headers=None, body=None):
        calls.append((method, url))
        if "api/stacks/" in url and method == "GET":
            return {"Env": [{"name": "GEMINI_API_KEY", "value": "gk-from-stack"}]}
        if url.startswith("https://generativelanguage.googleapis.com") and method == "GET":
            return {
                "models": [
                    {"name": "models/gemini-2.0-flash"},
                    {"name": "models/gemini-2.5-flash"},
                ]
            }
        if "generateContent" in url and method == "POST":
            return {"candidates": []}
        raise AssertionError(f"unexpected {method} {url}")

    monkeypatch.setattr(mod, "_http_json", fake_http)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("PORTAINER_URL", "https://portainer.example.com")
    monkeypatch.setenv("PORTAINER_API_TOKEN", "token")
    monkeypatch.setenv("PORTAINER_STACK_ID", "12")
    monkeypatch.setenv("PORTAINER_ENDPOINT_ID", "1")
    monkeypatch.setattr(sys, "argv", ["bump_gemini_model_portainer.py", "--dry-run"])

    assert mod.main() == 0
    out = capsys.readouterr().out
    assert "Portainer update skipped" in out
    assert "gk-from-stack" not in out
    assert "Would set:" in out
    assert not any(method == "PUT" for method, _url in calls)
    assert any(method == "GET" and "api/stacks/" in url for method, url in calls)
