"""
Tenant prompt personalization — labelled outcomes are stored as a Modelfile SYSTEM prompt.
This is not weight/adapter fine-tuning.
"""
import json
import logging
import os
import asyncio
import httpx
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.backend.db.database import get_db
from app.backend.middleware.auth import get_current_user, require_admin
from app.backend.middleware.rbac import require_recruiter_or_admin
from app.backend.models.db_models import ScreeningResult, TrainingExample, User
from app.backend.models.schemas import LabelRequest, TrainingStatusResponse

logger = logging.getLogger(__name__)

router  = APIRouter(prefix="/api/training", tags=["training"])
_status: dict = {}  # tenant_id → {trained, last_trained, model_name}

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")


def _training_enabled() -> bool:
    """
    Fine-tuning-style training is not implemented. Prompt personalization is disabled
    by default in production and must be enabled via TRAINING_ENABLED=true.
    """
    explicit = os.getenv("TRAINING_ENABLED")
    if explicit is not None:
        return explicit.strip().lower() in ("1", "true", "yes", "on")
    return os.getenv("ENVIRONMENT", "development").lower() != "production"


@router.post("/label")
def label_example(
    body: LabelRequest,
    current_user: User = Depends(require_recruiter_or_admin),
    db: Session = Depends(get_db)
):
    result = db.query(ScreeningResult).filter(
        ScreeningResult.id == body.screening_result_id,
        ScreeningResult.tenant_id == current_user.tenant_id
    ).first()
    if not result:
        raise HTTPException(status_code=404, detail="Screening result not found")

    existing = db.query(TrainingExample).filter(
        TrainingExample.screening_result_id == body.screening_result_id
    ).first()
    if existing:
        existing.outcome  = body.outcome
        existing.feedback = body.feedback
        db.commit()
        return {"updated": existing.id, "outcome": body.outcome}

    example = TrainingExample(
        tenant_id=current_user.tenant_id,
        screening_result_id=body.screening_result_id,
        outcome=body.outcome,
        feedback=body.feedback,
    )
    db.add(example)
    db.commit()
    db.refresh(example)

    # Also update the screening result status
    if body.outcome == "hired":
        result.status = "hired"
    elif body.outcome == "rejected":
        result.status = "rejected"
    db.commit()

    return {"created": example.id, "outcome": body.outcome}


@router.post("/train")
def start_training(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db)
):
    if not _training_enabled():
        raise HTTPException(
            status_code=403,
            detail="Prompt personalization is in beta and is currently disabled. Contact your administrator to enable it."
        )

    examples = (
        db.query(TrainingExample)
        .filter(TrainingExample.tenant_id == current_user.tenant_id)
        .all()
    )
    if len(examples) < 10:
        raise HTTPException(
            status_code=400,
            detail=f"Need at least 10 labeled examples to train. Current: {len(examples)}"
        )

    training_data = []
    for ex in examples:
        result   = ex.result
        analysis = json.loads(result.analysis_result) if result else {}
        training_data.append({
            "fit_score":  analysis.get("fit_score", 50),
            "outcome":    ex.outcome,
            "feedback":   ex.feedback or "",
        })

    tenant_id  = current_user.tenant_id
    model_name = f"aria-{tenant_id}"

    background_tasks.add_task(_train_model, tenant_id, model_name, training_data)
    return {"message": "Training started in background", "model_name": model_name}


async def _train_model(tenant_id: int, model_name: str, training_data: list):
    """Build a custom Ollama model from training examples."""
    _status[tenant_id] = {"trained": False, "model_name": model_name, "last_trained": None}

    examples_text = "\n".join([
        f"Score {d['fit_score']}/100 → {d['outcome'].upper()}"
        + (f" ({d['feedback']})" if d['feedback'] else "")
        for d in training_data[:50]
    ])

    from app.backend.services.llm_service import get_ollama_model

    modelfile = (
        f"FROM {get_ollama_model()}\n"
        f"SYSTEM You are ARIA, a specialized recruiter AI trained on {len(training_data)} real hiring outcomes. "
        f"You analyze candidate profiles and predict fit. "
        f"Training examples:\n{examples_text}\n"
        f"Always return valid JSON with keys: strengths, weaknesses, education_analysis, risk_signals, final_recommendation."
    )

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            await client.post(
                f"{OLLAMA_BASE_URL}/api/create",
                json={"name": model_name, "modelfile": modelfile}
            )
        _status[tenant_id] = {
            "trained":      True,
            "model_name":   model_name,
            "last_trained": datetime.now(timezone.utc),
        }
    except (httpx.HTTPError, OSError, ValueError, RuntimeError) as e:
        logger.exception(
            "Model training failed for tenant %s: %s", tenant_id, e,
            extra={"error_code": "LLM_ERROR"},
        )
        _status[tenant_id] = {
            "trained": False,
            "model_name": model_name,
            "error": str(e),
        }


@router.get("/status", response_model=TrainingStatusResponse)
def training_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    count = db.query(TrainingExample).filter(
        TrainingExample.tenant_id == current_user.tenant_id
    ).count()

    status = _status.get(current_user.tenant_id, {})
    return TrainingStatusResponse(
        labeled_count=count,
        trained=status.get("trained", False),
        model_name=status.get("model_name"),
        last_trained=status.get("last_trained"),
    )
