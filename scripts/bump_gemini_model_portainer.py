#!/usr/bin/env python3
"""
Option C: bump Gemini Flash model to stable_n_minus_1 and patch Portainer stack env.

Usage (CI or manual):
  export PORTAINER_URL=https://portainer.example.com
  export PORTAINER_API_TOKEN=...
  export PORTAINER_STACK_ID=123
  export PORTAINER_ENDPOINT_ID=1
  python scripts/bump_gemini_model_portainer.py [--dry-run]

GEMINI_API_KEY is optional in process env. If unset, it is read from the
Portainer stack Env. --dry-run GETs the stack and never PUTs.

Only updates Gemini primary slots (not Ollama/OpenRouter fallbacks):
  GEMINI_MODEL, GEMINI_KIT_MODEL, GEMINI_NARRATIVE_MODEL, GEMINI_MODEL_VOICE,
  LIVEKIT_LLM_MODEL
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

GEMINI_LIST_URL = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_VARS = (
    "GEMINI_MODEL",
    "GEMINI_KIT_MODEL",
    "GEMINI_NARRATIVE_MODEL",
    "GEMINI_MODEL_VOICE",
)
LIVEKIT_VAR = "LIVEKIT_LLM_MODEL"
FLASH_PATTERN = re.compile(r"gemini-[\d.]+-flash", re.IGNORECASE)


def _http_json(method: str, url: str, *, headers: dict | None = None, body: dict | None = None) -> dict:
    data = None
    req_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} {url}: {detail}") from exc


def list_gemini_flash_models(api_key: str) -> list[str]:
    payload = _http_json("GET", GEMINI_LIST_URL, headers={"x-goog-api-key": api_key})
    names: list[str] = []
    for item in payload.get("models") or []:
        name = str(item.get("name") or "")
        short = name.split("/")[-1] if name else ""
        if short and FLASH_PATTERN.search(short):
            names.append(short)
    return sorted(set(names))


def pick_stable_n_minus_1(flash_models: list[str]) -> str:
    """Pick the second-newest flash model by version sort (stable_n_minus_1)."""
    if not flash_models:
        raise RuntimeError("No Gemini Flash models returned from models.list")

    def sort_key(model: str) -> tuple:
        parts = model.lower().replace("gemini-", "").split("-")
        version = parts[0] if parts else "0"
        nums = [int(x) for x in re.findall(r"\d+", version)]
        while len(nums) < 3:
            nums.append(0)
        return tuple(nums[:3])

    ordered = sorted(flash_models, key=sort_key)
    if len(ordered) >= 2:
        return ordered[-2]
    return ordered[-1]


def smoke_test_model(api_key: str, model: str) -> None:
    url = f"{GEMINI_LIST_URL}/{model}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": 'Reply with JSON: {"ok": true}'}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 64,
            "responseMimeType": "application/json",
        },
    }
    _http_json("POST", url, headers={"x-goog-api-key": api_key}, body=body)


def env_map_from_stack(stack: dict) -> dict[str, str]:
    env = stack.get("Env") or []
    return {
        str(item.get("name")): str(item.get("value") or "")
        for item in env
        if item.get("name")
    }


def gemini_api_key_from_sources(*, process_env: dict[str, str], stack_env: dict[str, str]) -> str:
    """Resolve the Gemini key without requiring it in GitHub Actions secrets.

    Prefer an explicit process env (local/dev). Otherwise read GEMINI_API_KEY
    from the Portainer stack Env. Never log the key.
    """
    key = (process_env.get("GEMINI_API_KEY") or "").strip()
    if key:
        return key
    key = (stack_env.get("GEMINI_API_KEY") or "").strip()
    if key:
        return key
    raise RuntimeError("GEMINI_API_KEY is not set in the environment or Portainer stack Env")


def _portainer_creds() -> tuple[str, str, str, str] | None:
    url = os.getenv("PORTAINER_URL", "").strip()
    token = os.getenv("PORTAINER_API_TOKEN", "").strip()
    stack_id = os.getenv("PORTAINER_STACK_ID", "").strip()
    endpoint_id = os.getenv("PORTAINER_ENDPOINT_ID", "1").strip() or "1"
    if url and token and stack_id:
        return url, token, stack_id, endpoint_id
    return None


def portainer_get_stack(base_url: str, token: str, endpoint_id: int, stack_id: int) -> dict:
    url = f"{base_url.rstrip('/')}/api/stacks/{stack_id}?endpointId={endpoint_id}"
    return _http_json("GET", url, headers={"X-API-Key": token})


def portainer_update_stack_env(
    base_url: str,
    token: str,
    endpoint_id: int,
    stack_id: int,
    env_updates: dict[str, str],
    *,
    dry_run: bool,
) -> dict[str, str]:
    stack = portainer_get_stack(base_url, token, endpoint_id, stack_id)
    env = stack.get("Env") or []
    env_map = {item["name"]: item.get("value", "") for item in env if item.get("name")}

    for key, value in env_updates.items():
        env_map[key] = value

    if dry_run:
        return env_map

    url = f"{base_url.rstrip('/')}/api/stacks/{stack_id}?endpointId={endpoint_id}"
    body = {
        "env": [{"name": k, "value": v} for k, v in sorted(env_map.items())],
        "prune": False,
    }
    _http_json("PUT", url, headers={"X-API-Key": token}, body=body)
    return env_map


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump Gemini Flash model in Portainer (Option C)")
    parser.add_argument("--dry-run", action="store_true", help="Print chosen model and env diff only")
    args = parser.parse_args()

    creds = _portainer_creds()
    stack_env: dict[str, str] = {}
    if creds:
        portainer_url, portainer_token, stack_id, endpoint_id = creds
        stack = portainer_get_stack(portainer_url, portainer_token, int(endpoint_id), int(stack_id))
        stack_env = env_map_from_stack(stack)

    try:
        api_key = gemini_api_key_from_sources(process_env=dict(os.environ), stack_env=stack_env)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        if not creds:
            print(
                "Pass PORTAINER_URL, PORTAINER_API_TOKEN, and PORTAINER_STACK_ID "
                "to read GEMINI_API_KEY from the stack Env (no GitHub Gemini secret required).",
                file=sys.stderr,
            )
        return 1

    flash_models = list_gemini_flash_models(api_key)
    chosen = pick_stable_n_minus_1(flash_models)
    livekit_value = f"google/{chosen}"

    print(f"Available Flash models ({len(flash_models)}): {', '.join(flash_models)}")
    print(f"Selected stable_n_minus_1: {chosen}")
    print(f"LiveKit model: {livekit_value}")
    if creds and not (os.getenv("GEMINI_API_KEY") or "").strip():
        print("Gemini API key source: Portainer stack Env (value not logged)")

    smoke_test_model(api_key, chosen)
    print("Smoke test passed")

    env_updates = {name: chosen for name in GEMINI_VARS}
    env_updates[LIVEKIT_VAR] = livekit_value

    if args.dry_run or not creds:
        print("Portainer update skipped (dry-run or missing PORTAINER_* env)")
        print("Would set:")
        for k, v in env_updates.items():
            print(f"  {k}={v}")
        return 0

    portainer_url, portainer_token, stack_id, endpoint_id = creds
    portainer_update_stack_env(
        portainer_url,
        portainer_token,
        int(endpoint_id),
        int(stack_id),
        env_updates,
        dry_run=False,
    )
    print(f"Portainer stack {stack_id} updated. Redeploy stack to pick up new env.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
