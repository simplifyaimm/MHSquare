import logging
import time

from celery import chain
from celery.signals import worker_ready
from sqlalchemy.exc import OperationalError

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


# after a Docker restart the worker can start before Postgres accepts connections: retry instead of losing the task
DB_RETRY = dict(autoretry_for=(OperationalError,), retry_backoff=5, retry_backoff_max=60, max_retries=8)


@celery_app.task(name="gen.take", acks_late=True, soft_time_limit=3600, time_limit=3660, **DB_RETRY)
def gen_take(take_id: str) -> None:
    """Hard cap of 1 h per take (renderer-slot waits + LLM + up to 3 renders); a hung take fails, never hangs."""
    generation.run_take(take_id)
    preview_warm.delay(take_id)  # its preview clip, off the take's critical path


@celery_app.task(name="preview.warm", acks_late=False)
def preview_warm(take_id: str) -> None:
    try:
        preview.warm_proxy(take_id)
    except Exception:  # noqa: BLE001 — an optimisation only: the preview build encodes what is missing
        log.exception("preview warm-up failed for take %s", take_id)


@worker_ready.connect
def _recover_on_boot(sender=None, **_) -> None:
    if not str(getattr(sender, "hostname", "")).startswith(
        "celery@"
    ):  # the generation worker, not the ML one
        return
    for attempt in range(12):  # Postgres may still be starting
        try:
            from app.services import shorts

            n, m = (
                generation.recover_interrupted(),
                preview.recover_interrupted() + shorts.recover_interrupted(),
            )
            break
        except OperationalError:
            time.sleep(5 * (attempt + 1))
    else:
        log.error("could not reach the database to recover interrupted work")
        return
    if n or m:
        log.warning("marked %d interrupted takes and %d interrupted builds/Shorts as failed", n, m)


@celery_app.task(name="preview.build", **DB_RETRY)
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


@celery_app.task(name="plan.templatize")
def plan_templatize(project_id: str, redo: bool = False) -> None:
    from app.services.planner import templatize

    templatize.run(project_id, redo)


def _register_templatize() -> None:
    from app.services.planner import templatize

    templatize.register_dispatch(lambda pid, redo=False: plan_templatize.delay(pid, redo))


_register_templatize()


# ---------------------------------------------------------------- covers
@celery_app.task(name="media.cutout", acks_late=False)  # ML worker; no redelivery loop if it OOMs
def media_cutout(brand_asset_id: str) -> None:
    from app.services import covers

    covers.cutout(brand_asset_id)


@celery_app.task(name="covers.generate")
def covers_generate(
    project_id: str,
    formats: list[str],
    headshot_id: str | None,
    n: int,
    note: str = "",
    engine: str = "template",
) -> None:
    from app.services import covers

    covers.generate(project_id, formats, headshot_id, n, note, engine)


# ---------------------------------------------------------------- shorts / reels from the long video
@celery_app.task(name="shorts.plan", **DB_RETRY)
def shorts_plan(project_id: str) -> None:
    from app.services import shorts

    shorts.plan(project_id)


@celery_app.task(name="shorts.render", **DB_RETRY)
def shorts_render(short_id: str) -> None:
    from app.services import shorts

    shorts.render(short_id)


def _register_shorts() -> None:
    from app.services import shorts

    shorts.register_dispatch("plan", lambda pid: shorts_plan.delay(pid))


_register_shorts()
