from app.services.ingest import pipeline
from app.workers.celery_app import celery_app


@celery_app.task(name="mhsquare.ping")
def ping() -> str:
    return "pong"


@celery_app.task(name="ingest.start")
def ingest_start(project_id: str) -> None:
    pipeline.step_cleanup(project_id)


@celery_app.task(name="ingest.transcribe")  # routed to the "transcribe" queue (transcriber container)
def ingest_transcribe(project_id: str, stage: str) -> None:
    pipeline.step_transcribe(project_id, stage)


@celery_app.task(name="ingest.apply_cuts")
def ingest_apply_cuts(project_id: str) -> None:
    pipeline.step_apply_cuts(project_id)


pipeline.register_dispatch("start", lambda pid: ingest_start.delay(pid))
pipeline.register_dispatch("transcribe", lambda pid, stage: ingest_transcribe.delay(pid, stage))
pipeline.register_dispatch("apply_cuts", lambda pid: ingest_apply_cuts.delay(pid))
