"""Covered-topics registry: warn about likely repeats before generating (spec v2.1 §4).

Semantic similarity via LiteLLM embeddings; falls back to lexical similarity if embeddings fail.
"""

import logging
import math
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import client as llm
from app.models.style import CoveredTopic

log = logging.getLogger(__name__)
EMBED_THRESHOLD = 0.78
LEXICAL_THRESHOLD = 0.5
_STOP = {"the", "a", "an", "of", "for", "to", "in", "on", "and", "with", "how", "what", "why", "is", "vs", "tips",
         "using", "use", "your", "you", "guide", "intro", "introduction", "explained", "series", "episode", "short"}  # fmt: skip


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9@#+.]+", s.lower()) if t not in _STOP and len(t) > 1}


def lexical(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def ensure_embeddings(db: Session, topics: list[CoveredTopic]) -> bool:
    missing = [t for t in topics if not t.embedding]
    if not missing:
        return True
    try:
        for i in range(0, len(missing), 50):
            batch = missing[i : i + 50]
            vecs, _ = llm.embed([t.title for t in batch])
            for t, v in zip(batch, vecs, strict=True):
                t.embedding = v
        db.commit()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("topic embeddings unavailable, using lexical match: %s", e)
        db.rollback()
        return False


def check(db: Session, title: str, exclude_project_id=None) -> dict:
    title = title.strip()
    topics = [t for t in db.scalars(select(CoveredTopic)) if t.source_project_id is None
              or t.source_project_id != exclude_project_id]  # fmt: skip
    matches: list[dict] = []
    method = "lexical"
    if title and topics and ensure_embeddings(db, topics):
        try:
            (q,), _ = llm.embed([title])
            method = "embedding"
            for t in topics:
                s = cosine(q, t.embedding or [])
                if s >= EMBED_THRESHOLD or lexical(title, t.title) >= 0.8:
                    matches.append(_m(t, max(s, lexical(title, t.title))))
        except Exception as e:  # noqa: BLE001
            log.warning("query embedding failed: %s", e)
            method = "lexical"
    if method == "lexical":
        for t in topics:
            s = lexical(title, t.title)
            if s >= LEXICAL_THRESHOLD:
                matches.append(_m(t, s))
    matches.sort(key=lambda m: -m["score"])
    return {"title": title, "method": method, "matches": matches[:5], "likely_repeat": bool(matches)}


def _m(t: CoveredTopic, score: float) -> dict:
    return {"id": str(t.id), "title": t.title, "status": t.status, "pillar": t.pillar, "content_type": t.content_type,
            "score": round(score, 3)}  # fmt: skip
