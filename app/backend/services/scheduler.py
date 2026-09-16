"""Background scheduler for periodic tasks (APScheduler)."""
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.backend.db.database import SessionLocal

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def process_dunning_retries():
    """Process all due dunning retries."""
    from app.backend.services.billing.dunning_service import dunning_service

    db = SessionLocal()
    try:
        result = dunning_service.process_due_retries(db)
        logger.info("Dunning retry processing complete: %s", result)
    except Exception as exc:
        logger.error("Dunning retry processing failed: %s", exc, exc_info=True)
    finally:
        db.close()


def recover_stale_jobs():
    """Single stale-job recovery path — delegates entirely to QueueManager."""
    import asyncio
    from app.backend.services.queue_manager import get_queue_manager

    db = SessionLocal()
    try:
        asyncio.run(get_queue_manager().recover_stale_jobs(db))
    except Exception as exc:
        logger.error("Stale job recovery failed: %s", exc, exc_info=True)
        db.rollback()
    finally:
        db.close()


def gdpr_cleanup_job():
    from app.backend.services.gdpr_service import cleanup_expired_data
    db = SessionLocal()
    try:
        result = cleanup_expired_data(db)
        logger.info("GDPR cleanup complete: %s", result)
        try:
            from app.backend.services.metrics import GDPR_PURGE_TOTAL
            GDPR_PURGE_TOTAL.inc()
        except Exception:
            pass
    except Exception as exc:
        logger.error("GDPR cleanup failed: %s", exc, exc_info=True)
    finally:
        db.close()


def expire_trials_job():
    """Mark expired self-serve trials as past_due."""
    from app.backend.services.trial_service import expire_trials
    db = SessionLocal()
    try:
        count = expire_trials(db)
        if count:
            logger.info("Expired %d trials", count)
    except Exception as exc:
        logger.error("Trial expiry job failed: %s", exc, exc_info=True)
    finally:
        db.close()


def process_scheduled_reports():
    """Deliver due scheduled analytics reports via email."""
    from app.backend.services.custom_report_service import process_due_scheduled_reports

    db = SessionLocal()
    try:
        count = process_due_scheduled_reports(db)
        if count:
            logger.info("Delivered %d scheduled analytics reports", count)
    except Exception as exc:
        logger.error("Scheduled report delivery failed: %s", exc, exc_info=True)
    finally:
        db.close()


def start_scheduler():
    """Start the background scheduler with all periodic jobs."""
    if scheduler.running:
        return

    scheduler.add_job(
        process_dunning_retries,
        trigger=IntervalTrigger(hours=1),
        id="dunning_retries",
        replace_existing=True,
        misfire_grace_time=300,  # 5-minute grace window for misfires
    )

    scheduler.add_job(
        recover_stale_jobs,
        trigger=IntervalTrigger(minutes=5),
        id="stale_job_recovery",
        replace_existing=True,
        misfire_grace_time=60,
    )

    scheduler.add_job(
        expire_trials_job,
        trigger=IntervalTrigger(hours=1),
        id="trial_expiry",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        process_scheduled_reports,
        trigger=IntervalTrigger(hours=1),
        id="scheduled_analytics_reports",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.add_job(
        gdpr_cleanup_job,
        trigger=IntervalTrigger(hours=6),
        id="gdpr_cleanup",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.start()
    logger.info(
        "Background scheduler started "
        "(dunning retries: every 1 h, stale job recovery: every 5 min, trial expiry: every 1 h, "
        "scheduled reports: every 1 h)"
    )


def stop_scheduler():
    """Gracefully stop the background scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Background scheduler stopped")
