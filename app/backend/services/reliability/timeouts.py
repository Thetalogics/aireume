"""Explicit provider timeout budgets (seconds). Callers should pass these to httpx."""
from __future__ import annotations

import os


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


GEMINI_CONNECT = _f("GEMINI_CONNECT_TIMEOUT", 5)
GEMINI_READ = _f("GEMINI_READ_TIMEOUT", 60)
OLLAMA_CONNECT = _f("OLLAMA_CONNECT_TIMEOUT", 5)
OLLAMA_READ = _f("OLLAMA_READ_TIMEOUT", 120)
OPENROUTER_CONNECT = _f("OPENROUTER_CONNECT_TIMEOUT", 5)
OPENROUTER_READ = _f("OPENROUTER_READ_TIMEOUT", 60)
STRIPE_CONNECT = _f("STRIPE_CONNECT_TIMEOUT", 5)
STRIPE_READ = _f("STRIPE_READ_TIMEOUT", 30)
RAZORPAY_CONNECT = _f("RAZORPAY_CONNECT_TIMEOUT", 5)
RAZORPAY_READ = _f("RAZORPAY_READ_TIMEOUT", 30)
URL_FETCH_CONNECT = _f("URL_FETCH_CONNECT_TIMEOUT", 3)
URL_FETCH_READ = _f("URL_FETCH_READ_TIMEOUT", 10)
EMAIL_CONNECT = _f("EMAIL_CONNECT_TIMEOUT", 5)
EMAIL_READ = _f("EMAIL_READ_TIMEOUT", 20)
LIVEKIT_CONNECT = _f("LIVEKIT_CONNECT_TIMEOUT", 5)
LIVEKIT_READ = _f("LIVEKIT_READ_TIMEOUT", 15)
OBJECT_STORAGE_CONNECT = _f("OBJECT_STORAGE_CONNECT_TIMEOUT", 5)
OBJECT_STORAGE_READ = _f("OBJECT_STORAGE_READ_TIMEOUT", 30)


def httpx_timeout(connect: float, read: float):
    import httpx

    return httpx.Timeout(connect=connect, read=read, write=read, pool=connect)
