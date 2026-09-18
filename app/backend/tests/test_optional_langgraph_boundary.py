"""Optional-dependency patch helper must not force-import WIP modules."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path

import httpx
import pytest

from app.backend.tests.optional_deps import patch_loaded_module_attr

_WIP = "app.backend.services.wip.agent_pipeline"


def test_patch_helper_does_not_import_unloaded_module(monkeypatch):
    name = "app.backend.tests._definitely_not_imported_optional_mod"
    sys.modules.pop(name, None)
    assert name not in sys.modules
    patched = patch_loaded_module_attr(monkeypatch, name, "get_fast_llm", lambda: None)
    assert patched is False
    assert name not in sys.modules


def test_patch_helper_patches_already_loaded_module(monkeypatch):
    name = "app.backend.tests._loaded_optional_mod"
    mod = types.ModuleType(name)
    mod.flag = "before"
    sys.modules[name] = mod
    try:
        assert patch_loaded_module_attr(monkeypatch, name, "flag", "after") is True
        assert mod.flag == "after"
    finally:
        sys.modules.pop(name, None)


def test_conftest_does_not_dotted_import_agent_pipeline():
    src = Path(__file__).with_name("conftest.py").read_text(encoding="utf-8")
    assert '"app.backend.services.wip.agent_pipeline.get_fast_llm"' not in src
    assert '"app.backend.services.wip.agent_pipeline.get_reasoning_llm"' not in src
    assert "importlib.import_module" not in src


def test_unrelated_test_does_not_import_agent_pipeline(monkeypatch):
    """Auth/billing-style tests must not load the WIP pipeline via the autouse fixture."""
    if importlib.util.find_spec("langgraph") is not None:
        pytest.skip("langgraph is installed; agent_pipeline may already be loaded")
    assert _WIP not in sys.modules
    patched = patch_loaded_module_attr(
        monkeypatch,
        _WIP,
        "get_fast_llm",
        lambda *a, **k: None,
    )
    assert patched is False
    assert _WIP not in sys.modules


def test_agent_pipeline_skipped_when_langgraph_absent():
    if importlib.util.find_spec("langgraph") is not None:
        pytest.skip("langgraph is installed in this environment")
    src = Path(__file__).with_name("test_agent_pipeline.py").read_text(encoding="utf-8")
    assert "pytest.importorskip" in src
    assert '"langgraph"' in src
    root = Path(__file__).resolve().parents[3]
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.backend.services.wip.agent_pipeline",
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode != 0, combined
    assert "langgraph" in combined.lower()


@pytest.mark.asyncio
async def test_hermetic_http_blocks_gemini_ollama_openrouter():
    blocked = (
        "https://generativelanguage.googleapis.com/v1/models",
        "http://127.0.0.1:11434/api/generate",
        "https://openrouter.ai/api/v1/chat",
    )
    async with httpx.AsyncClient() as client:
        for url in blocked:
            with pytest.raises(httpx.ConnectError, match="hermetic tests blocked LLM host"):
                await client.request("POST", url)
