"""Patch optional/WIP modules only when they are already imported.

Global fixtures must not force-import packages that CI does not install
(for example langgraph / agent_pipeline).
"""
from __future__ import annotations

import sys
from typing import Any


def patch_loaded_module_attr(
    monkeypatch,
    module_name: str,
    attr: str,
    value: Any,
    *,
    raising: bool = True,
) -> bool:
    """Set ``attr`` on ``module_name`` only if that module is already in sys.modules.

    Does not import the module. Returns True when the attribute was patched.
    """
    module = sys.modules.get(module_name)
    if module is None:
        return False
    monkeypatch.setattr(module, attr, value, raising=raising)
    return True
