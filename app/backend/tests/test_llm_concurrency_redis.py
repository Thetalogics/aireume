import multiprocessing as mp
import os
import tempfile
import time
from pathlib import Path

from app.backend.services.llm_concurrency import acquire_llm_permit, release_llm_permit
from app.backend.tests.reliability_env import require_redis


def _r6_worker(url, tenant, limit, hold_seconds, result_q):
    os.environ["REDIS_URL"] = url
    os.environ.pop("REDIS_REQUIRED", None)
    from app.backend.services import shared_cache

    shared_cache._redis = None
    shared_cache._redis_failed = False
    from app.backend.services.llm_concurrency import acquire_llm_permit as acquire

    token = acquire(tenant, limit, ttl_seconds=8)
    result_q.put(("got", bool(token), os.getpid()))
    if token:
        time.sleep(hold_seconds)
        from app.backend.services.llm_concurrency import release_llm_permit as release

        release(tenant, token)
        result_q.put(("released", True, os.getpid()))


def _r6_crash_holder(url, tenant, limit, marker_path):
    try:
        os.environ["REDIS_URL"] = url
        from app.backend.services import shared_cache

        shared_cache._redis = None
        shared_cache._redis_failed = False
        from app.backend.services.llm_concurrency import acquire_llm_permit as acquire

        token = acquire(tenant, limit, ttl_seconds=2)
        Path(marker_path).write_text("1" if token else "0", encoding="utf-8")
    except Exception as exc:
        Path(marker_path).write_text(f"err:{exc}", encoding="utf-8")
        return
    os._exit(1)


def test_r1_limit_two():
    require_redis()
    tenant = 91001
    first = acquire_llm_permit(tenant, 2)
    second = acquire_llm_permit(tenant, 2)
    third = acquire_llm_permit(tenant, 2)
    assert first and second
    assert third is None
    release_llm_permit(tenant, first)
    fourth = acquire_llm_permit(tenant, 2)
    assert fourth
    release_llm_permit(tenant, second)
    release_llm_permit(tenant, fourth)
    release_llm_permit(tenant, fourth)


def test_r5_required_redis_missing(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("REDIS_REQUIRED", "1")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    from app.backend.services import shared_cache

    shared_cache._redis = None
    shared_cache._redis_failed = False
    from app.backend.services.llm_concurrency import LlmConcurrencyUnavailable

    try:
        acquire_llm_permit(1, 2)
        assert False, "expected fail-closed"
    except LlmConcurrencyUnavailable:
        pass


def test_r6_multiprocess_limit_and_lease_recovery():
    url = require_redis()
    tenant = 92006
    limit = 2
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = [
        ctx.Process(target=_r6_worker, args=(url, tenant, limit, 1.2, q))
        for _ in range(4)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(8)
        assert proc.exitcode == 0
    got = []
    while not q.empty():
        event = q.get_nowait()
        if event[0] == "got":
            got.append(event[1])
    assert sum(1 for ok in got if ok) <= 2
    assert sum(1 for ok in got if ok) == 2

    marker = Path(tempfile.gettempdir()) / f"aria_r6_{os.getpid()}.txt"
    if marker.exists():
        marker.unlink()
    crash = ctx.Process(target=_r6_crash_holder, args=(url, tenant + 1, 1, str(marker)))
    crash.start()
    crash.join(8)
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "1"
    time.sleep(2.5)
    token = acquire_llm_permit(tenant + 1, 1, ttl_seconds=8)
    assert token
    release_llm_permit(tenant + 1, token)
