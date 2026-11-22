"""Media served through short-lived signed URLs with HTTP Range support (video scrubbing)."""

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.media_urls import verify
from app.models import Asset
from app.services.storage import get_storage

router = APIRouter(tags=["media"])

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Single byte range → (start, end) inclusive. None = whole file. Raises ValueError if unsatisfiable."""
    if not header:
        return None
    m = _RANGE.match(header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        raise ValueError("bad range")
    if m.group(1):
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
    else:
        suffix = int(m.group(2))
        start, end = max(0, size - suffix), size - 1
    end = min(end, size - 1)
    if start > end or start >= size:
        raise ValueError("unsatisfiable")
    return start, end


@router.get("/media/{asset_id}")
def get_media(
    asset_id: uuid.UUID,
    request: Request,
    exp: int = Query(...),
    sig: str = Query(..., max_length=64),
    dl: str | None = Query(None, max_length=120, pattern=r"^[\w .()-]+$"),
    db: Session = Depends(get_db),
):
    if not verify(str(asset_id), exp, sig):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or expired media link")
    a = db.get(Asset, asset_id)
    storage = get_storage()
    if a is None or not storage.exists(a.storage_key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    size = storage.size(a.storage_key)
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
        "Content-Disposition": f'attachment; filename="{dl}"' if dl else "inline",
        "X-Content-Type-Options": "nosniff",
    }
    try:
        rng = parse_range(request.headers.get("range"), size)
    except ValueError:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
    if rng is None:
        return StreamingResponse(
            storage.iter_range(a.storage_key, 0, size - 1),
            media_type=a.mime,
            headers={**headers, "Content-Length": str(size)},
        )
    start, end = rng
    return StreamingResponse(
        storage.iter_range(a.storage_key, start, end),
        status_code=206,
        media_type=a.mime,
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        },
    )


@router.get("/media/brand/{asset_id}/{variant}")
def get_brand_media(
    asset_id: uuid.UUID,
    variant: str,
    exp: int = Query(...),
    sig: str = Query(..., max_length=64),
    db: Session = Depends(get_db),
):
    from fastapi.responses import FileResponse

    from app.core.media_urls import verify_brand
    from app.models.style import BrandAsset

    if variant not in ("original", "cutout") or not verify_brand(str(asset_id), variant, exp, sig):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid or expired media link")
    a = db.get(BrandAsset, asset_id)
    key = (a.storage_key if variant == "original" else a.cutout_key) if a else None
    storage = get_storage()
    if not key or not storage.exists(key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return FileResponse(storage.path(key), media_type=a.mime if variant == "original" else "image/png",
                        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})  # fmt: skip
