import logging

from celery import chain
from celery.signals import worker_ready

from app.services import generation, planner
from app.services.assembly import preview
from app.services.ingest import pipeline, tts
from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="mhsquare.ping")
def ping() -> str:
    return "pong"


# ---------------------------------------------------------------- ingest
@celery_app.task(name="ingest.start")
def ingest_start(project_id: str) -> None:
    pipeline.step_cleanup(project_id)


@celery_app.task(name="ingest.transcribe")  # routed to the "transcribe" queue (transcriber container)
def ingest_transcribe(project_id: str, stage: str) -> None:
    pipeline.step_transcribe(project_id, stage)


@celery_app.task(name="ingest.apply_cuts")
def ingest_apply_cuts(project_id: str) -> None:
    pipeline.step_apply_cuts(project_id)


@celery_app.task(name="ingest.tts")
def ingest_tts(project_id: str, voice: str) -> None:
    tts.run_tts(project_id, voice)


# ---------------------------------------------------------------- planning / generation / preview
@celery_app.task(name="plan.run")
def plan_run(project_id: str) -> None:
    planner.run_planning(project_id)


@celery_app.task(name="gen.take", acks_late=True, soft_time_limit=1200, time_limit=1260)
def gen_take(take_id: str) -> None:
    """Hard cap of ~20 min per take (LLM + up to 3 renders); a hung take fails instead of blocking a slot."""
    generation.run_take(take_id)


@worker_ready.connect
def _recover_on_boot(sender=None, **_) -> None:
    if str(getattr(sender, "hostname", "")).startswith("celery@"):  # the generation worker, not the ML one
        n = generation.recover_interrupted()
        if n:
            log.warning("marked %d interrupted takes as failed", n)


@celery_app.task(name="preview.build")
def preview_build(project_id: str, quality: str = "proxy") -> None:
    preview.build_preview(project_id, quality)


def dispatch_takes(chains: list[list[str]]) -> None:
    """Independent chains run in parallel; takes inside a chain (dependent shots) run in order."""
    for ids in chains:
        if len(ids) == 1:
            gen_take.delay(ids[0])
        elif ids:
            chain(*(gen_take.si(t) for t in ids)).apply_async()


pipeline.register_dispatch("start", lambda pid: ingest_start.delay(pid))
pipeline.register_dispatch("transcribe", lambda pid, stage: ingest_transcribe.delay(pid, stage))
pipeline.register_dispatch("apply_cuts", lambda pid: ingest_apply_cuts.delay(pid))
planner.register_dispatch("plan", lambda pid: plan_run.delay(pid))


# ---------------------------------------------------------------- covers
@celery_app.task(name="media.cutout", acks_late=False)  # ML worker; no redelivery loop if it OOMs
def media_cutout(brand_asset_id: str) -> None:
    from app.services import covers

    covers.cutout(brand_asset_id)


@celery_app.task(name="covers.generate")
def covers_generate(
    project_id: str, formats: list[str], headshot_id: str | None, n: int, note: str = ""
) -> None:
    from app.services import covers

    covers.generate(project_id, formats, headshot_id, n, note)
