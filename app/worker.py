from celery import Celery

from app.config import get_settings
from app.db import SessionLocal
from app.services import ingest_evidence, inspect_artifact, run_fill_plan_job
from app.storage import get_storage

settings = get_settings()
celery_app = Celery("formfiller", broker=settings.redis_url, backend=settings.redis_url)


@celery_app.task(name="inspect_artifact", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def inspect_artifact_task(run_id: str) -> None:
    with SessionLocal() as session:
        inspect_artifact(session, get_storage(), run_id)


@celery_app.task(name="ingest_evidence", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def ingest_evidence_task(run_id: str) -> None:
    with SessionLocal() as session:
        ingest_evidence(session, get_storage(), run_id)


@celery_app.task(name="fill_plan", autoretry_for=(), max_retries=0)
def fill_plan_task(run_id: str) -> None:
    """Execute a queued model-backed plan; service persists failure state itself."""
    with SessionLocal() as session:
        run_fill_plan_job(session, run_id)
