from celery import Celery

from app.core.config import get_settings

_s = get_settings()

celery_app = Celery("mhsquare", broker=_s.redis_url, backend=_s.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(
    task_acks_late=True,  # a crashed worker's task is redelivered
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    # ML worker (transcriber container): WhisperX + background removal
    task_routes={"ingest.transcribe": {"queue": "transcribe"}, "media.cutout": {"queue": "transcribe"}},
    broker_transport_options={"visibility_timeout": 60 * 60 * 4},  # long transcriptions
)
