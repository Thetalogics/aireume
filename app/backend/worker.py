"""Dedicated background worker process (queue + schedulers).

Run with: python -m app.backend.worker
API containers should set RUN_BACKGROUND_WORKERS=0.
"""
import asyncio
import logging
import os

os.environ.setdefault("RUN_BACKGROUND_WORKERS", "1")

from app.backend.main import app  # noqa: F401  — loads env validation
from app.backend.services.queue_manager import start_queue_worker, stop_queue_worker
from app.backend.services.scheduler import start_scheduler, stop_scheduler
from app.backend.services.voice_call_scheduler import start_voice_scheduler, stop_voice_scheduler

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("aria.worker")


async def main() -> None:
    log.info("Starting dedicated ARIA worker process")
    os.environ.setdefault("ARIA_PROCESS_ROLE", "worker")
    await start_queue_worker()
    start_scheduler()
    start_voice_scheduler()
    try:
        while True:
            from app.backend.db.database import SessionLocal
            from app.backend.services.worker_heartbeat import touch_worker_heartbeat

            db = SessionLocal()
            try:
                touch_worker_heartbeat(db, detail="loop")
            finally:
                db.close()
            await asyncio.sleep(15)
    finally:
        await stop_queue_worker()
        stop_scheduler()
        stop_voice_scheduler()


if __name__ == "__main__":
    asyncio.run(main())
