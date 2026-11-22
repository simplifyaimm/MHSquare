"""Ingest orchestration. Order matters (spec §5.1):

cleanup → raw transcription → cut detection → [I approve cuts] → apply cuts → final transcription.

Every step persists project.status / ingest_step before work starts, so a crashed worker can be
resumed with `resume_ingest`. Steps are idempotent: derived assets of the same kind are replaced.
"""

import json
import logging
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import get_sessionmaker
from app.models import Asset, AssetKind, CutProposal, IngestStep, Project, ProjectStatus, Transcript
from app.services.ingest import cuts as cutlib
from app.services.ingest import media
from app.services.ingest.transcribe import get_transcriber
from app.services.storage import get_storage

log = logging.getLogger(__name__)

# Injected by app.workers.tasks so this module doesn't import Celery.
Dispatch = Callable[..., None]
_dispatch: dict[str, Dispatch] = {}


def register_dispatch(name: str, fn: Dispatch) -> None:
    _dispatch[name] = fn


class IngestError(RuntimeError):
    pass


# ---------------------------------------------------------------- helpers


def latest_asset(db: Session, project_id: uuid.UUID, kind: str, stage: str | None = None) -> Asset | None:
    q = (
        select(Asset)
        .where(Asset.project_id == project_id, Asset.kind == kind)
        .order_by(Asset.created_at.desc())
    )
    for a in db.scalars(q):
        if stage is None or (a.meta or {}).get("stage") == stage:
            return a
    return None


def replace_derived(
    db: Session, project: Project, kind: str, src: Path, ext: str, mime: str, meta: dict
) -> Asset:
    """Store `src` as the project's single asset of `kind` (and meta.stage), removing older ones."""
    storage = get_storage()
    for old in db.scalars(select(Asset).where(Asset.project_id == project.id, Asset.kind == kind)).all():
        if meta.get("stage") is None or (old.meta or {}).get("stage") == meta.get("stage"):
            storage.delete(old.storage_key)
            db.delete(old)
    key = storage.new_key(project.id, ext)
    size, digest = storage.put_file(key, src)
    asset = Asset(
        project_id=project.id, kind=kind, storage_key=key, sha256=digest, size=size, mime=mime, meta=meta
    )
    db.add(asset)
    db.flush()
    return asset


def _workdir() -> tempfile.TemporaryDirectory:
    root = get_storage().root / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)


def _set(db: Session, p: Project, status: str, step: str | None) -> None:
    p.status, p.ingest_step, p.error = status, step, None
    db.commit()


def _fail(project_id: uuid.UUID, err: Exception) -> None:
    log.exception("ingest failed for %s", project_id)
    with get_sessionmaker()() as db:
        p = db.get(Project, project_id)
        if p:
            p.status = ProjectStatus.failed
            p.error = f"{type(err).__name__}: {err}"[:4000]
            db.commit()


def _store_waveform(db: Session, p: Project, audio_path: Path, stage: str) -> None:
    peaks = media.waveform_peaks(audio_path)
    with _workdir() as wd:
        f = Path(wd) / "waveform.json"
        f.write_text(json.dumps(peaks))
        replace_derived(db, p, AssetKind.waveform, f, "json", "application/json", {"stage": stage})


# ---------------------------------------------------------------- validation / entry points


def check_ready_for_ingest(db: Session, p: Project) -> None:
    if not latest_asset(db, p.id, AssetKind.script):
        raise IngestError("Upload the script first")
    if not latest_asset(db, p.id, AssetKind.audio):
        raise IngestError("Upload the narration audio first")


def begin_ingest(db: Session, p: Project) -> None:
    """Called by the API. Moves the project to `ingesting` and queues the first step."""
    check_ready_for_ingest(db, p)
    _set(db, p, ProjectStatus.ingesting, IngestStep.cleanup)
    _dispatch["start"](str(p.id))


def resume_ingest(db: Session, p: Project) -> None:
    """Retry after a failure (or a crash) from the step that was running."""
    if p.ingest_step in (IngestStep.apply_cuts, IngestStep.transcribe_final):
        _set(db, p, ProjectStatus.finalizing, IngestStep.apply_cuts)
        _dispatch["apply_cuts"](str(p.id))
    else:
        begin_ingest(db, p)


def approve_cuts(db: Session, p: Project, enabled_ids: set[str]) -> CutProposal:
    proposal = db.scalar(
        select(CutProposal).where(CutProposal.project_id == p.id).order_by(CutProposal.created_at.desc())
    )
    if proposal is None or p.status != ProjectStatus.cuts_review:
        raise IngestError("No cut proposal awaiting review")
    unknown = enabled_ids - {r["id"] for r in proposal.ranges}
    if unknown:
        raise IngestError(f"Unknown cut ids: {sorted(unknown)}")
    proposal.ranges = [{**r, "enabled": r["id"] in enabled_ids} for r in proposal.ranges]
    proposal.approved = True
    proposal.approved_at = datetime.now(UTC)
    _set(db, p, ProjectStatus.finalizing, IngestStep.apply_cuts)
    _dispatch["apply_cuts"](str(p.id))
    return proposal


# ---------------------------------------------------------------- worker steps


def step_cleanup(project_id: str) -> None:
    pid = uuid.UUID(project_id)
    try:
        with get_sessionmaker()() as db:
            p = db.get(Project, pid)
            if p is None:
                return
            _set(db, p, ProjectStatus.ingesting, IngestStep.cleanup)
            audio = latest_asset(db, pid, AssetKind.audio)
            if audio is None:
                raise IngestError("no narration audio")
            storage = get_storage()
            with _workdir() as wd:
                out = Path(wd) / "clean.wav"
                media.get_cleaner("ffmpeg").clean(storage.path(audio.storage_key), out)
                meta = media.media_meta(out) | {"cleaner": "ffmpeg", "source_asset_id": str(audio.id)}
                clean = replace_derived(db, p, AssetKind.audio_clean, out, "wav", "audio/wav", meta)
            _store_waveform(db, p, storage.path(clean.storage_key), "clean")
            _set(db, p, ProjectStatus.ingesting, IngestStep.transcribe_raw)
        _dispatch["transcribe"](project_id, "raw")
    except Exception as e:  # noqa: BLE001
        _fail(pid, e)


def step_transcribe(project_id: str, stage: str) -> None:
    """Runs in the transcriber container. stage: 'raw' (cleaned, uncut) or 'final' (after cuts)."""
    pid = uuid.UUID(project_id)
    try:
        with get_sessionmaker()() as db:
            p = db.get(Project, pid)
            if p is None:
                return
            kind = AssetKind.audio_clean if stage == "raw" else AssetKind.audio_final
            src = latest_asset(db, pid, kind)
            if src is None:
                raise IngestError(f"missing {kind}")
            path = get_storage().path(src.storage_key)
            lang = (p.ingest_settings or {}).get("language")
            result = get_transcriber().transcribe(path, lang)
            version = 1 if stage == "raw" else 2
            db.execute(delete(Transcript).where(Transcript.project_id == pid, Transcript.version == version))
            db.add(
                Transcript(
                    project_id=pid,
                    version=version,
                    is_final=stage == "final",
                    language=result.language,
                    text=result.text,
                    words=result.words,
                    source_asset_id=src.id,
                )
            )
            db.commit()

            if stage == "raw":
                _set(db, p, ProjectStatus.ingesting, IngestStep.detect_cuts)
                s = p.ingest_settings or {}
                proposed = cutlib.detect_cuts(
                    result.words,
                    int(src.meta.get("duration_ms", 0)),
                    fillers=s.get("fillers", []),
                    silence_ms=int(s.get("silence_ms", 600)),
                    keep_pause_ms=int(s.get("keep_pause_ms", 150)),
                )
                db.execute(delete(CutProposal).where(CutProposal.project_id == pid))
                db.add(CutProposal(project_id=pid, ranges=[c.as_dict() for c in proposed]))
                _set(db, p, ProjectStatus.cuts_review, None)
            else:
                _store_waveform(db, p, path, "final")
                _set(db, p, ProjectStatus.ready, None)
    except Exception as e:  # noqa: BLE001
        _fail(pid, e)


def step_apply_cuts(project_id: str) -> None:
    pid = uuid.UUID(project_id)
    try:
        with get_sessionmaker()() as db:
            p = db.get(Project, pid)
            if p is None:
                return
            _set(db, p, ProjectStatus.finalizing, IngestStep.apply_cuts)
            proposal = db.scalar(
                select(CutProposal)
                .where(CutProposal.project_id == pid, CutProposal.approved.is_(True))
                .order_by(CutProposal.created_at.desc())
            )
            clean = latest_asset(db, pid, AssetKind.audio_clean)
            if proposal is None or clean is None:
                raise IngestError("cuts not approved or cleaned audio missing")
            storage = get_storage()
            duration = int(clean.meta.get("duration_ms", 0))
            keeps = cutlib.keep_segments(proposal.ranges, duration, p.fps)
            removed = duration - sum(e - s for s, e in keeps)
            with _workdir() as wd:
                out = Path(wd) / "final.wav"
                media.cut_audio(storage.path(clean.storage_key), out, keeps)
                meta = media.media_meta(out) | {"removed_ms": removed, "segments": len(keeps)}
                replace_derived(db, p, AssetKind.audio_final, out, "wav", "audio/wav", meta)

                facecam = latest_asset(db, pid, AssetKind.facecam)
                if facecam is not None:
                    vout = Path(wd) / "facecam.mp4"
                    media.cut_video(
                        storage.path(facecam.storage_key), vout, keeps, p.facecam_offset_ms, p.fps
                    )
                    vmeta = media.media_meta(vout) | {"offset_ms": p.facecam_offset_ms}
                    replace_derived(db, p, AssetKind.facecam_final, vout, "mp4", "video/mp4", vmeta)
            _set(db, p, ProjectStatus.finalizing, IngestStep.transcribe_final)
        _dispatch["transcribe"](project_id, "final")
    except Exception as e:  # noqa: BLE001
        _fail(pid, e)
