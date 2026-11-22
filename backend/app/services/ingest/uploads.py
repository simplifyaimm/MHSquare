"""Upload validation: size limits, extension allow-list, magic-byte sniffing, ffprobe for media.

Files are stored under random keys with an extension derived from the sniffed type (never the
user's filename) and are never executed.
"""

import os
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Asset, AssetKind, Project
from app.services.ingest import media
from app.services.storage import get_storage

MB = 1024 * 1024
LIMITS = {
    AssetKind.script: 2 * MB,
    AssetKind.audio: 4 * 1024 * MB,
    AssetKind.facecam: 40 * 1024 * MB,
    AssetKind.character_ref: 25 * MB,
}
EXTENSIONS = {
    AssetKind.script: {"md", "txt"},
    AssetKind.audio: {"wav", "m4a", "mp3"},
    AssetKind.facecam: {"mp4", "mov", "webm", "mkv"},
    AssetKind.character_ref: {"png", "jpg", "jpeg", "webp"},
}
MIMES = {
    AssetKind.audio: {"audio/wav", "audio/mpeg", "audio/mp4", "video/mp4"},
    AssetKind.facecam: {"video/mp4", "video/quicktime", "video/webm"},
    AssetKind.character_ref: {"image/png", "image/jpeg", "image/webp"},
}
STORED_EXT = {
    "audio/wav": "wav", "audio/mpeg": "mp3", "audio/mp4": "m4a", "video/mp4": "mp4",
    "video/quicktime": "mov", "video/webm": "webm", "image/png": "png", "image/jpeg": "jpg",
    "image/webp": "webp", "text/markdown": "md", "text/plain": "txt",
}  # fmt: skip
SINGLE_KINDS = {AssetKind.script, AssetKind.audio, AssetKind.facecam}
MAX_CHARACTER_REFS = 3
CHUNK_MAX = 16 * MB


class UploadRejected(ValueError):
    pass


def check_declared(db: Session, project: Project, kind: str, filename: str, size: int) -> None:
    if kind not in LIMITS:
        raise UploadRejected(f"Unknown upload kind {kind!r}")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in EXTENSIONS[AssetKind(kind)]:
        raise UploadRejected(
            f"{kind}: allowed file types are {', '.join(sorted(EXTENSIONS[AssetKind(kind)]))}"
        )
    if size <= 0 or size > LIMITS[AssetKind(kind)]:
        raise UploadRejected(f"{kind}: file must be at most {LIMITS[AssetKind(kind)] // MB} MB")
    if kind == AssetKind.character_ref:
        n = db.scalar(
            select(func.count()).where(Asset.project_id == project.id, Asset.kind == AssetKind.character_ref)
        )
        if n >= MAX_CHARACTER_REFS:
            raise UploadRejected(f"At most {MAX_CHARACTER_REFS} character reference images")


def tmp_path(upload_id: uuid.UUID) -> Path:
    d = get_storage().root / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{upload_id.hex}.part"


def finalize(db: Session, project: Project, kind: str, src: Path, original_name: str) -> Asset:
    """Validate the received file's actual content and turn it into an Asset."""
    if src.stat().st_size > LIMITS[AssetKind(kind)]:
        raise UploadRejected("file too large")
    with src.open("rb") as f:
        head = f.read(64)

    meta: dict = {}
    if kind == AssetKind.script:
        raw = src.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise UploadRejected("Script must be UTF-8 text") from e
        if "\x00" in text:
            raise UploadRejected("Script must be plain text")
        mime = "text/markdown" if original_name.lower().endswith(".md") else "text/plain"
        meta = {"chars": len(text), "words": len(text.split())}
    else:
        mime = media.sniff(head) or ""
        if mime not in MIMES[AssetKind(kind)]:
            raise UploadRejected(f"{kind}: file content is not an accepted type")
        if kind in (AssetKind.audio, AssetKind.facecam):
            try:
                meta = media.media_meta(src)
            except media.MediaError as e:
                raise UploadRejected(f"{kind}: could not read media ({e})") from e
            if kind == AssetKind.audio and not meta.get("has_audio"):
                raise UploadRejected("Narration file has no audio stream")
            if kind == AssetKind.facecam and not meta.get("has_video"):
                raise UploadRejected("Face-cam file has no video stream")
            if meta.get("duration_ms", 0) <= 0:
                raise UploadRejected(f"{kind}: zero-length media")
            if kind == AssetKind.audio:
                mime = {"video/mp4": "audio/mp4"}.get(mime, mime)

    storage = get_storage()
    if kind in SINGLE_KINDS:
        for old in db.scalars(select(Asset).where(Asset.project_id == project.id, Asset.kind == kind)).all():
            storage.delete(old.storage_key)
            db.delete(old)
    key = storage.new_key(project.id, STORED_EXT[mime])
    size, digest = storage.put_file(key, src)
    asset = Asset(
        project_id=project.id,
        kind=kind,
        storage_key=key,
        sha256=digest,
        size=size,
        mime=mime,
        original_name=os.path.basename(original_name)[:255],
        meta=meta,
    )
    db.add(asset)
    db.commit()
    return asset
