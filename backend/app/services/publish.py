"""YouTube details: title options, description (hook, what you'll learn, chapters, hashtags), tags and a pinned
comment, from one small model call.

The model writes the words; the code owns everything YouTube is strict about. Chapter timestamps come from the
real shot timeline (to the millisecond), never from the model, and are repaired to YouTube's rules: the first at
0:00, at least 3, each at least 10 s long. Title, description and tag lengths are enforced here as well. The
creator picks a title and can edit every field; edits are kept as the creator's.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import client as llm
from app.models import Asset, Project, Shot
from app.models.style import StyleProfile
from app.services import routing, style
from app.services.planner import extract_json

TITLE_MAX = 100  # YouTube's hard limit
TITLE_SHOWN = 70  # what search and suggestions show before cutting the title off
DESCRIPTION_MAX = 5000
TAGS_MAX = 500  # total characters, YouTube counts each tag plus a separator
CHAPTER_MIN_S = 10
CHAPTERS_MIN = 3


class PublishError(ValueError):
    pass


def _clip(s: object, limit: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= limit else s[: limit - 1].rstrip(" ,;:-") + "…"


class Chapter(BaseModel):
    shot: int = Field(ge=0)
    title: str

    @field_validator("title", mode="before")
    @classmethod
    def _t(cls, v: object) -> str:
        return _clip(v, 50)


class Pack(BaseModel):
    titles: list[str] = Field(min_length=1)
    hook: str
    learn: list[str] = Field(default_factory=list)
    chapters: list[Chapter] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    hashtags: list[str] = Field(default_factory=list)
    pinned_comment: str = ""

    @field_validator("titles", mode="before")
    @classmethod
    def _titles(cls, v: object) -> list[str]:
        out = [_clip(t, TITLE_MAX) for t in (v or []) if str(t or "").strip()]
        return list(dict.fromkeys(out))[:6]

    @field_validator("hook", mode="before")
    @classmethod
    def _hook(cls, v: object) -> str:
        return _clip(v, 300)

    @field_validator("pinned_comment", mode="before")
    @classmethod
    def _pin(cls, v: object) -> str:
        return _clip(v, 500)

    @field_validator("learn", mode="before")
    @classmethod
    def _learn(cls, v: object) -> list[str]:
        return [_clip(x, 200) for x in (v or []) if str(x or "").strip()][:6]

    @field_validator("tags", mode="before")
    @classmethod
    def _tags(cls, v: object) -> list[str]:
        tags = [_clip(str(t).replace(",", " ").strip("# "), 60) for t in (v or []) if str(t or "").strip()]
        return fit_tags(list(dict.fromkeys(tags)))

    @field_validator("hashtags", mode="before")
    @classmethod
    def _hashtags(cls, v: object) -> list[str]:
        out = []
        for t in v or []:
            word = "".join(ch for ch in str(t) if ch.isalnum())
            if word:
                out.append("#" + word)
        return list(dict.fromkeys(out))[:5]


def fit_tags(tags: list[str]) -> list[str]:
    """Keep tags (most important first) while the total stays under YouTube's 500-character limit."""
    out, total = [], 0
    for t in tags:
        cost = (
            len(t) + (2 if " " in t else 0) + (1 if out else 0)
        )  # multi-word tags are quoted; comma between
        if total + cost > TAGS_MAX - 20:  # margin: YouTube's own counting differs slightly
            continue
        out.append(t)
        total += cost
    return out


SYSTEM = """You write the YouTube details for one video of the channel "{brand}" — “{tagline}”.

Voice: plain, accurate, warm teacher. No hype, no clickbait, no invented numbers or promises, no ALL CAPS (except
acronyms like RAG, API), no emojis in titles. English only.

Write:
- titles: 5 options. Each at most 70 characters (aim for 45–65). The main search keyword within the first 40
  characters. A specific, honest promise of what the viewer gets. Make them different from each other (question,
  outcome, contrast, "how X works"...). The cover already says "{cover}" — the title should complement it, not
  repeat it word for word.
- hook: 1–2 sentences (max 220 characters) for the top of the description — the part shown before "more". Include
  the main keyword. Say who it is for and what they will be able to do.
- learn: 3–5 short bullets, each at most 90 characters: what the viewer will learn (concrete, from the script).
- chapters: {chapters_rule}
- tags: 12–20 search phrases, most important first: the exact topic, close variants, the broader field,
  technologies named in the script. Lowercase, no "#".
- hashtags: 3 (the topic, the field, the audience), no spaces.
- pinned_comment: one friendly question that invites viewers to share their own situation (max 300 characters).

Reply with ONLY JSON:
{{"titles": ["..."], "hook": "...", "learn": ["..."], "chapters": [{{"shot": 0, "title": "..."}}],
 "tags": ["..."], "hashtags": ["..."], "pinned_comment": "..."}}"""

CHAPTERS_LONG = (
    "5–10 chapters at the real topic changes. Each starts at a shot from the timeline (give its shot number); the "
    "first starts at shot 0. Titles at most 40 characters, descriptive (what is explained there), not clickbait. "
    "Each chapter at least 30 seconds."
)
CHAPTERS_SHORT = "[] (a Short has no chapters)."


def fmt_ts(ms: int, long_video: bool) -> str:
    s = int(ms // 1000)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if long_video else f"{m}:{sec:02d}"


def chapters(picked: list[Chapter], shots: list[Shot]) -> list[dict]:
    """Model-chosen chapter starts → YouTube-valid chapters at the shots' real start times."""
    start = {s.index: s.start_ms for s in shots}
    if not shots:
        return []
    end_ms = max(s.end_ms for s in shots)
    rows = sorted({(start[c.shot], c.title) for c in picked if c.shot in start}, key=lambda r: r[0])
    out: list[dict] = []
    for ms, title in rows:
        if not out:
            out.append({"ms": 0, "title": title})  # YouTube requires the first chapter at 0:00
        elif ms - out[-1]["ms"] >= CHAPTER_MIN_S * 1000:
            out.append({"ms": ms, "title": title})
    while len(out) > 1 and end_ms - out[-1]["ms"] < CHAPTER_MIN_S * 1000:
        out.pop()  # the last chapter must also last 10 s
    if len(out) < CHAPTERS_MIN:
        return []  # YouTube ignores fewer than 3 chapters
    long_video = end_ms >= 3600 * 1000
    return [{**c, "at": fmt_ts(c["ms"], long_video)} for c in out]


def compose_description(pack: Pack, chs: list[dict], brand: dict, short: bool) -> str:
    parts = [pack.hook]
    if pack.learn:
        parts.append("In this video:\n" + "\n".join(f"• {x}" for x in pack.learn))
    if chs:
        parts.append("Chapters:\n" + "\n".join(f"{c['at']} {c['title']}" for c in chs))
    sign = " — ".join(x for x in (brand.get("name"), brand.get("tagline")) if x)
    if sign:
        parts.append(sign + (f"\n{brand['handle']}" if brand.get("handle") else ""))
    tags = list(pack.hashtags)
    if short and "#shorts" not in {t.lower() for t in tags}:
        tags.append("#Shorts")
    if tags:
        parts.append(" ".join(tags))
    text = "\n\n".join(p for p in parts if p.strip())
    return text[:DESCRIPTION_MAX]


def timeline(shots: list[Shot]) -> str:
    """Compact shot list for the model: number, time, what is said (the chapter boundaries are chosen here)."""
    rows = []
    for s in shots:
        said = " ".join((s.transcript_text or "").split())[:140]
        rows.append(f"{s.index} [{fmt_ts(s.start_ms, False)}] {said}")
    return "\n".join(rows)


def _cover_headline(db: Session, p: Project, fmt: str) -> str:
    rows = db.scalars(select(Asset).where(Asset.project_id == p.id, Asset.kind == "cover"))
    chosen = [a for a in rows if (a.meta or {}).get("format") == fmt and (a.meta or {}).get("chosen")]
    return (chosen[0].meta or {}).get("headline", "") if chosen else ""


def generate(db: Session, p: Project, profile: StyleProfile, script: str) -> dict:
    if not script.strip():
        raise PublishError("Upload the script first")
    short = style.is_short(p.content_type)
    shots = list(db.scalars(select(Shot).where(Shot.project_id == p.id).order_by(Shot.index)))
    brand = profile.brand or {}
    cover = _cover_headline(db, p, "short" if short else "youtube") or "(no cover chosen yet)"
    system = SYSTEM.format(brand=brand.get("name", ""), tagline=brand.get("tagline", ""), cover=cover,
                           chapters_rule=CHAPTERS_SHORT if short or not shots else CHAPTERS_LONG)  # fmt: skip
    pillar = ((brand.get("pillars") or {}).get(p.pillar or "", {}) or {}).get("label", p.pillar or "—")
    from app.services.direction import brief

    user = (f"Format: {'YouTube Short' if short else 'long-form YouTube video'} · pillar: {pillar} · audience: "
            f"{p.audience_level} · topic: {p.topic or p.name}\n{brief(p.direction)}\n\n## Script\n{script.strip()[:20000]}")  # fmt: skip
    if shots and not short:
        user += f"\n\n## Timeline (shot number [start] narration)\n{timeline(shots)}"
    cfg = routing.load()
    text, res = llm.chat(cfg.planner.model, [{"role": "user", "content": user}], system=system,
                         fallbacks=cfg.planner.fallbacks, max_tokens=6000, temperature=0.5)  # fmt: skip
    try:
        pack = Pack.model_validate(extract_json(text))
    except (ValueError, KeyError, ValidationError) as ex:
        raise PublishError(f"could not read the AI's answer: {str(ex)[:300]}") from ex
    chs = [] if short else chapters(pack.chapters, shots)
    return {
        "titles": pack.titles,
        "title": pack.titles[0],
        "hook": pack.hook,
        "learn": pack.learn,
        "chapters": chs,
        "description": compose_description(pack, chs, brand, short),
        "tags": pack.tags,
        "hashtags": pack.hashtags,
        "pinned_comment": pack.pinned_comment,
        "cover_headline": "" if cover.startswith("(") else cover,
        "source": "ai",
        "model": res.alias,
        "cost_usd": round(res.cost_usd, 4),
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


EDITABLE = {"title", "description", "tags", "pinned_comment"}


def edit(current: dict | None, changes: dict) -> dict:
    """Creator edits: the chosen/typed title, the description text, tags and the pinned comment."""
    if not current:
        raise PublishError("Generate the YouTube details first")
    out = dict(current)
    if "title" in changes:
        t = " ".join(str(changes["title"] or "").split())
        if not t:
            raise PublishError("The title can't be empty")
        if len(t) > TITLE_MAX:
            raise PublishError(f"YouTube titles are at most {TITLE_MAX} characters (this one has {len(t)})")
        out["title"] = t
    if "description" in changes:
        d = str(changes["description"] or "").strip()
        if len(d) > DESCRIPTION_MAX:
            raise PublishError(f"YouTube descriptions are at most {DESCRIPTION_MAX} characters")
        if "<" in d or ">" in d:
            raise PublishError("YouTube doesn't allow < or > in descriptions")
        out["description"] = d
    if "tags" in changes:
        raw = changes["tags"]
        items = raw.split(",") if isinstance(raw, str) else list(raw or [])
        tags = [" ".join(str(t).split()).strip("# ") for t in items if str(t).strip()]
        kept = fit_tags(list(dict.fromkeys(tags)))
        if len(kept) < len(tags):
            raise PublishError(f"Tags are limited to about {TAGS_MAX} characters in total — remove a few")
        out["tags"] = kept
    if "pinned_comment" in changes:
        out["pinned_comment"] = _clip(changes["pinned_comment"], 500)
    out["source"] = "edited"
    out["edited_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    return out
