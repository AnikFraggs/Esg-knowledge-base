"""Model run and evaluation tracking.

Persists benchmark metrics (recall@k, extraction accuracy) and fine-tuning history.
"""
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from esg import settings
from esg.models import ModelRun


def record_eval(session: Session, metrics: Dict[str, Any], model_name: Optional[str] = None) -> ModelRun:
    """Record an evaluation benchmark run into the model_runs table."""
    model = model_name or getattr(settings, "OLLAMA_MODEL", "qwen3:8b")
    run = ModelRun(
        model=model,
        base_model=getattr(settings, "LLM_MODEL_MAIN", "qwen3:4b-instruct-2507"),
        role="eval",
        dataset_hash="",
        pairs=metrics.get("n", 0),
        config={"k": metrics.get("k", 5), "tolerance": metrics.get("value_tolerance", 0.02)},
        status="completed",
        metrics=metrics,
        started_at=datetime.utcnow(),
        ended_at=datetime.utcnow(),
    )
    session.add(run)
    session.flush()
    return run


def record_training_run(session: Session, role: str, model_name: str, pairs: int, config: Dict[str, Any]) -> ModelRun:
    """Record the initiation of a local SFT / embedder training run."""
    run = ModelRun(
        model=model_name,
        role=role,
        pairs=pairs,
        config=config,
        status="running",
        started_at=datetime.utcnow(),
    )
    session.add(run)
    session.flush()
    return run
