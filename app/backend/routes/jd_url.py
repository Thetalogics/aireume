"""
JD URL extraction endpoint — paste a job URL and get the JD text back.
"""
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from app.backend.middleware.auth import get_current_user
from app.backend.models.db_models import User
from app.backend.models.schemas import JdUrlRequest, JdUrlResponse
from app.backend.services.jd_scraper import scrape_jd
from app.backend.services.url_safety import validate_public_url, UnsafeURLError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jd", tags=["jd"])


@router.post("/extract-url", response_model=JdUrlResponse)
async def extract_jd_from_url(
    body: JdUrlRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        safe_url = validate_public_url(body.url)
    except UnsafeURLError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        body.url = safe_url
        jd_text = await scrape_jd(body.url)
    except HTTPException:
        raise
    except UnsafeURLError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (ValueError, TypeError, KeyError) as e:
        logger.warning(
            "JD extraction failed for URL %s: %s", body.url, e,
            extra={"error_code": "VALIDATION_ERROR"},
        )
        raise HTTPException(status_code=422, detail="Could not extract the job description from that URL.") from e
    except (OSError, httpx.HTTPError, ImportError) as e:
        logger.error(
            "JD extraction failed for URL %s: %s", body.url, e,
            extra={"error_code": "IO_ERROR"},
        )
        raise HTTPException(status_code=422, detail="Could not extract the job description from that URL.") from e
    except Exception as e:
        logger.warning(
            "JD extraction failed for URL %s: %s", body.url, e,
            extra={"error_code": "UPSTREAM_ERROR"},
        )
        raise HTTPException(status_code=422, detail="Could not extract the job description from that URL.") from e

    return JdUrlResponse(jd_text=jd_text, source_url=body.url)
