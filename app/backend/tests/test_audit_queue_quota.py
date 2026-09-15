"""AUD-005: queued analysis quota is reserved once and released on cancel."""
import uuid

from app.backend.models.db_models import AnalysisJob, Tenant
from app.backend.routes.analyze_helpers import release_job_analysis_quota


def test_cancel_releases_reserved_queue_quota(db, seed_subscription_plans):
    tenant = Tenant(name="QuotaRelease", slug="quota-release")
    tenant.analyses_count_this_month = 4
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    job = AnalysisJob(
        tenant_id=tenant.id,
        status="queued",
        job_type="resume_screening",
        resume_hash="a" * 64,
        jd_hash="b" * 64,
        input_hash=uuid.uuid4().hex,
        job_config={"quota_reserved": True},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    release_job_analysis_quota(db, job)
    db.commit()
    db.refresh(tenant)
    db.refresh(job)
    assert tenant.analyses_count_this_month == 3
    assert job.job_config.get("quota_released") is True
    release_job_analysis_quota(db, job)
    db.commit()
    db.refresh(tenant)
    assert tenant.analyses_count_this_month == 3
