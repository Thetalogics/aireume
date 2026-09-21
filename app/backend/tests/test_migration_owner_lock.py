import threading

from sqlalchemy import create_engine, text

from app.backend.services.migration_owner import MIGRATION_LOCK_KEY
from app.backend.tests.reliability_env import require_postgres


def test_f4_advisory_lock_blocks_second_owner():
    url = require_postgres()
    engine = create_engine(url)
    held = threading.Event()
    release = threading.Event()
    results = []

    def hold():
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            locked = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY}).scalar()
            results.append(("first", bool(locked)))
            held.set()
            release.wait(5)
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})

    def race():
        held.wait(5)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            locked = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY}).scalar()
            results.append(("second", bool(locked)))
            if locked:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
            release.set()

    t1 = threading.Thread(target=hold)
    t2 = threading.Thread(target=race)
    t1.start()
    t2.start()
    t1.join(8)
    t2.join(8)
    mapping = dict(results)
    assert mapping.get("first") is True
    assert mapping.get("second") is False
    engine.dispose()
