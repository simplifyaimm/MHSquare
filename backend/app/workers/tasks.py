from app.workers.celery_app import celery_app


@celery_app.task(name="mhsquare.ping")
def ping() -> str:
    return "pong"
