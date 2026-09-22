from celery import Celery

from app.config import get_settings
from app.db import SessionLocal
from app.services import inspect_artifact
from app.storage import get_storage

settings = get_settings()
celery_app = Celery("formfiller", broker=settings.redis_url, backend=settings.redis_url)


@celery_app.task(name="inspect_artifact", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def inspect_artifact_task(run_id: str) -> None:
    with SessionLocal() as session:
        inspect_artifact(session, get_storage(), run_id)

