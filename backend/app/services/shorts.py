"""Shorts / Reels from a long video.

1. plan(): one planner-model call reads the shot timeline and picks self-contained clips (a range of whole shots,
   so the narration never starts or ends mid-sentence), writes the on-screen hook, title, description and
   hashtags, and groups multi-step ideas into a series. Code validates everything: real shot ranges, 20–75 s,
   no overlaps, series part numbers, a daily posting order, YouTube/Instagram limits.
2. render(): a 1080×1920 vertical video per clip (the creator's chosen layout):

     0–240        dark top band: series badge + hook title (brand fonts)
     240–1760     the animation panel, 1080×1520. Template shots are RE-DRAWN natively for it (free, sharp) and
                  keep their content above the talking-head corner; text cards and 16:9 renders fill the area
                  above it. Word-by-word captions sit just above the talking head.
     x 64, y 1240 the talking head, 600×620, over the panel's bottom-left (running past its bottom edge):
                  the same moment of the cut face-cam; empty if there is none.

   Audio is the creator's real narration for the clip's time range.
"""

import hashlib
import logging
import re
import tempfile
import textwrap
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.db import get_sessionmaker
from app.llm import client as llm
from app.models import Asset, AssetKind, Project, ShortClip, Shot, Transcript
from app.services import routing, style
from app.services.generation import _ffmpeg
from app.services.generation import templates as T
from app.services.planner import extract_json
from app.services.storage import get_storage

log = logging.getLogger(__name__)

W, H = 1080, 1920
STAGE_W, STAGE_H, STAGE_Y = 1080, 1520, 240  # the animation panel
FACE_X, FACE_Y, FACE_W, FACE_H = 64, 1240, 600, 620  # the talking head, over the panel's bottom-left
CLEAR_H = FACE_Y - STAGE_Y - 120  # 880: scene content stays above this (captions + face below)
CAPTION_BOTTOM = FACE_Y - 16  # captions right above the talking head
MIN_MS, MAX_MS = 40_000, 120_000  # 70–110 s is the target; a complete interview answer may be ~45 s
STAGE_VERSION = "s4"  # bump when the template stage render changes: cached segments are then rebuilt
FITTED_VERSION = "f3"  # the same for text cards and fitted 16:9 scenes
# templates are authored on a slightly smaller CSS canvas and rendered at 1.11×: bigger text and cards on a phone
AUTHOR_W, AUTHOR_H = 972, 1368
AUTHOR_SCALE = STAGE_W / AUTHOR_W
AUTHOR_CLEAR_H = round(CLEAR_H / AUTHOR_SCALE)  # 792
_dispatch: dict = {}


def register_dispatch(name: str, fn) -> None:
    _dispatch[name] = fn


class PlanError(ValueError):
    pass


def _clip(v: object, n: int) -> str:
    s = " ".join(str(v or "").split())
    if len(s) <= n:
        return s
    cut = s[: n + 1].rsplit(" ", 1)[0]
    return (cut if len(cut) >= n // 2 else s[:n]).rstrip(" ,;:-")


def hashtags(v: object) -> list[str]:
    """#Word tags, deduplicated, at most 5 (#Shorts is added to the description separately)."""
    items = str(v).replace(",", " ").split() if isinstance(v, str) else list(v or [])
    out = []
    for t in items:
        word = "".join(ch for ch in str(t) if ch.isalnum())
        if word and word.lower() != "shorts":
            out.append("#" + word)
    return list(dict.fromkeys(out))[:5]


class Pick(BaseModel):
    src: int = Field(alias="from")
    to: int
    series: str | None = None
    day: int | None = None  # the model's posting order, if it gave one
    context_line: str = ""  # on-screen setup in the first seconds: who / what situation
    hook: str
    title: str
    description: str = ""
    hashtags: list[str] = Field(default_factory=list)
    pinned_comment: str = ""
    cover: dict = Field(
        default_factory=dict
    )  # the cover concept (headline, highlight, subline, points, visual)
    score: float = 5.0
    why: str = ""

    @field_validator("hook", mode="before")
    @classmethod
    def _hook(cls, v: object) -> str:
        return _clip(v, 44)

    @field_validator("context_line", mode="before")
    @classmethod
    def _context(cls, v: object) -> str:
        return _clip(v, 64)

    @field_validator("pinned_comment", mode="before")
    @classmethod
    def _pinned(cls, v: object) -> str:
        return _clip(v, 300)

    @field_validator("cover", mode="before")
    @classmethod
    def _cover(cls, v: object) -> dict:
        if not isinstance(v, dict):
            return {}
        return {"headline": _clip(v.get("headline"), 40), "highlight": _clip(v.get("highlight"), 30),
                "subline": _clip(v.get("subline"), 60), "visual": _clip(v.get("visual"), 160),
                "points": [_clip(x, 48) for x in (v.get("points") or []) if str(x or "").strip()][:4]}  # fmt: skip

    @field_validator("day", mode="before")
    @classmethod
    def _day(cls, v: object) -> int | None:
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, v: object) -> str:
        return _clip(v, 80)

    @field_validator("description", mode="before")
    @classmethod
    def _desc(cls, v: object) -> str:
        return _clip(v, 600)

    @field_validator("series", mode="before")
    @classmethod
    def _series(cls, v: object) -> str | None:
        s = _clip(v, 40)
        return s or None

    @field_validator("hashtags", mode="before")
    @classmethod
    def _tags(cls, v: object) -> list[str]:
        return hashtags(v)

    @field_validator("score", mode="before")
    @classmethod
    def _score(cls, v: object) -> float:
        try:
            return max(0.0, min(10.0, float(v)))
        except (TypeError, ValueError):
            return 5.0


PLAN_SYSTEM = """You are a senior tech-education creator and short-form strategist (YouTube Shorts + Instagram Reels)
cutting one long explainer video of the channel "{brand}" — “{tagline}” into Shorts. Reach and quality matter, not
the number of Shorts. You get the timeline: one line per shot: number [start, seconds] narration.

The narration is fixed: each Short is a continuous range of whole shots (from, to). You choose where it starts and
ends. On screen the tool adds a context line (first seconds), a hook in the top band, captions and an end card.

Pick the clips that work ON THEIR OWN:
- 70–110 seconds is the target; ~45–60 s only when the range is exactly one complete idea (e.g. one interview
  question and its answer). Let the content decide; never pad, never cut a concept in half.
- the FIRST SPOKEN LINE must work as an opening: a question, a problem, "Skill 1. RAG.", "Let's start with…". Never
  "so", "and", "as I said", "now", "here's the problem" with no problem stated, or a reference to earlier parts.
- it teaches ONE complete idea with its concrete example and ENDS on a complete takeaway sentence — never on a list
  item or a trailing comma.
- a viewer who never saw the long video understands it from second 0 (the context line names the hidden premise).
- clips must not overlap and must not reuse shots.
- use a SERIES only where the video already has a numbered skeleton (Skill 1–4, Month 1–3, World 1–2), so the
  "PART k/n" badge matches what is said. Same series name on every part; each part still teaches a complete idea.

For each clip write:
- context_line: ≤ 60 characters, the premise the narration assumes ("AI interview Q: chatbot over 50,000 policy
  docs", "Java dev Robin keeps hearing: 'Learn Python for AI'")
- hook: the on-screen title, ≤ 44 characters, plain and specific
- title: ≤ 70 characters, main keyword FIRST; series parts end with " | <series> k/n"
- description: 2–3 short sentences: what the viewer learns (the tool adds the full-video pointer)
- hashtags: 3–5 (topic, field, audience)
- pinned_comment: one question that invites replies (experience, stack, which part to go deeper on)
- cover: {{"headline": "2–6 words, the concrete thing", "highlight": "word(s) in the accent colour",
  "subline": "the developer payoff", "points": ["3–4 short points"], "visual": "one topic-specific diagram idea"}}
- day: posting order (1 = first); series parts on consecutive days; the strongest identity hook first
- score: 1–10 standalone strength; why: one short reason
Voice: no hype, no clickbait, no invented numbers, English only.

Reply with ONLY JSON: {{"shorts": [{{"from": 6, "to": 16, "series": null, "day": 1, "context_line": "...",
"hook": "...", "title": "...", "description": "...", "hashtags": ["#..."], "pinned_comment": "...",
"cover": {{"headline": "...", "highlight": "...", "subline": "...", "points": ["..."], "visual": "..."}},
"score": 8, "why": "..."}}]}}"""


def _ts(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def timeline(shots: list[Shot]) -> str:
    return "\n".join(f"{s.index} [{_ts(s.start_ms)}, {s.duration_ms / 1000:.0f}s] {' '.join((s.transcript_text or '').split())}"
                     for s in shots)  # fmt: skip


def normalise(picks: list[Pick], shots: list[Shot], taken: list[tuple[int, int]] | None = None,
              long_title: str = "") -> list[dict]:  # fmt: skip
    """Model picks → valid, non-overlapping clips with series parts and a daily posting order."""
    by = {s.index: s for s in shots}
    used = list(taken or [])  # shot ranges already rendered (kept across re-planning)
    accepted: list[tuple[Pick, int, int]] = []
    for pk in sorted(picks, key=lambda x: -x.score):
        a, b = sorted((pk.src, pk.to))
        if a not in by or b not in by:
            continue
        dur = by[b].end_ms - by[a].start_ms
        if not MIN_MS <= dur <= MAX_MS or any(a <= y and x <= b for x, y in used):
            continue
        used.append((a, b))
        accepted.append((pk, a, b))
    # a series needs ≥ 2 parts; number the parts by time
    groups: dict[str, list] = {}
    for item in accepted:
        if item[0].series:
            groups.setdefault(item[0].series.lower(), []).append(item)
    blocks: list[tuple[float, list]] = []
    for items in groups.values():
        if len(items) >= 2:
            items.sort(key=lambda x: x[1])
            blocks.append((max(i[0].score for i in items), items))
    in_series = {id(i) for _, items in blocks for i in items}
    blocks += [(i[0].score, [i]) for i in accepted if id(i) not in in_series]
    if all(i[0].day is not None for i in accepted):  # the strategist's posting order (series stay together)
        blocks.sort(key=lambda bl: min(i[0].day for i in bl[1]))
    else:
        blocks.sort(key=lambda bl: -bl[0])  # strongest first: day 1 decides whether the series takes off
    out, day = [], 0
    for _, items in blocks:
        n = len(items)
        for k, (pk, a, b) in enumerate(items, start=1):
            day += 1
            series = pk.series if n >= 2 else None
            title = pk.title
            if series and "part" not in title.lower() and not re.search(r"\b\d+\s*/\s*\d+\b", title):
                title = _clip(f"{title} (Part {k}/{n})", 100)
            tags = pk.hashtags
            desc = pk.description.strip()
            if long_title:
                desc += f"\n\nFull video: “{long_title}” — on the channel."
            desc += "\n\n" + " ".join([*tags, "#Shorts"])
            out.append({"index": day, "shot_from": a, "shot_to": b, "start_ms": by[a].start_ms, "end_ms": by[b].end_ms,
                        "series": series, "part": k if series else None, "parts": n if series else None,
                        "hook": pk.hook, "title": title, "description": desc[:4900], "hashtags": tags,
                        "score": pk.score, "why": _clip(pk.why, 300),
                        "meta": {"context_line": pk.context_line, "pinned_comment": pk.pinned_comment,
                                 "cover": pk.cover}})  # fmt: skip
    return out


def plan(project_id: str) -> None:
    """Worker: pick the Shorts. Keeps already-rendered ones; replaces the rest."""
    pid = uuid.UUID(project_id)
    with get_sessionmaker()() as db:
        p = db.get(Project, pid)
        if p is None:
            return
        try:
            shots = list(db.scalars(select(Shot).where(Shot.project_id == pid).order_by(Shot.index)))
            if not shots:
                raise PlanError("plan the long video's shots first")
            profile = style.active(db)
            b = profile.brand or {}
            long_title = (p.publish or {}).get("title") or p.topic or p.name
            user = (f"Long video: {long_title}\nAudience: {p.audience_level}\n\n## Timeline (shot [start, length] "
                    f"narration)\n{timeline(shots)}")  # fmt: skip
            cfg = routing.load()
            text, res = llm.chat(cfg.planner.model, [{"role": "user", "content": user}],
                                 system=PLAN_SYSTEM.format(brand=b.get("name", ""), tagline=b.get("tagline", "")),
                                 fallbacks=cfg.planner.fallbacks, max_tokens=16000, temperature=0.4)  # fmt: skip
            picks = []
            for it in extract_json(text).get("shorts", []):
                try:
                    picks.append(Pick.model_validate(it))
                except ValidationError:
                    continue
            keep = list(
                db.scalars(select(ShortClip).where(ShortClip.project_id == pid, ShortClip.status == "done"))
            )
            for old in db.scalars(
                select(ShortClip).where(ShortClip.project_id == pid, ShortClip.status != "done")
            ):
                db.delete(old)
            rows = normalise(picks, shots, [(k.shot_from, k.shot_to) for k in keep], long_title)
            base = max((k.index for k in keep), default=0)
            for r in rows:
                db.add(ShortClip(project_id=pid, **{**r, "index": base + r["index"]}))
            _status(
                p, status="done", made=len(rows), kept=len(keep), cost_usd=round(res.cost_usd, 4), error=None
            )
            db.commit()
        except Exception as ex:  # noqa: BLE001
            log.exception("shorts planning failed")
            db.rollback()
            p = db.get(Project, pid)
            _status(p, status="failed", error=f"{type(ex).__name__}: {ex}"[:500])
            db.commit()


def _status(p: Project, **kv) -> None:
    meta = dict(p.plan_meta or {})
    meta["shorts"] = {
        **(meta.get("shorts") or {}),
        **kv,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    p.plan_meta = meta
    flag_modified(p, "plan_meta")


def begin_plan(db: Session, p: Project) -> None:
    _status(p, status="running", error=None)
    db.commit()
    _dispatch["plan"](str(p.id))


# ---------------------------------------------------------------- rendering


def _stage_canvas(h: int = AUTHOR_H) -> style.Canvas:
    return style.Canvas(AUTHOR_W, h, W, H, STAGE_Y, True, [], False, None)


# where the captions and the talking head sit, in the templates' authoring coordinates: content stays above it
FACE_ZONE = {"name": "talking head + captions", "x": 0, "y": AUTHOR_CLEAR_H, "w": AUTHOR_W,
             "h": AUTHOR_H - AUTHOR_CLEAR_H}  # fmt: skip


def _words(db: Session, project_id: uuid.UUID) -> list[dict]:
    t = db.scalar(select(Transcript).where(Transcript.project_id == project_id, Transcript.is_final.is_(True))
                  .order_by(Transcript.version.desc()))  # fmt: skip
    return list(t.words) if t else []


def _bg(profile) -> str:
    return ((profile.animation_style or {}).get("background") or {}).get("to", "#060b14").lstrip("#")


def stage_segment(db: Session, p: Project, profile, shot: Shot, cache: Path, words: list[dict]) -> Path:
    """One shot on the vertical panel (1080×1520), exactly as many frames as the shot lasts. Cached."""
    from app.services.generation import GP, _best_take, icon_runtime, renderer, text_card_scene

    n = round(shot.end_ms * p.fps / 1000) - round(shot.start_ms * p.fps / 1000)
    take = _best_take(db, shot)
    try:
        parsed = T.parse(shot.prompt)
    except T.TemplateError:
        parsed = None
    native = parsed is not None or shot.visual_type == "text_card"
    version = STAGE_VERSION if parsed else FITTED_VERSION
    key = hashlib.blake2b(f"{version}|{shot.prompt}|{shot.theme}|{n}|{take.id if take else ''}|{p.pillar}"
                       .encode(), digest_size=10).hexdigest()  # fmt: skip
    out = cache / f"{shot.index:04d}_{key}.mp4"
    if out.exists():
        return out
    bg = _bg(profile)
    src, mode = None, "fit16x9"
    if native:  # templates and text cards are re-drawn natively for the vertical panel: sharp and free
        try:
            if parsed:  # the whole panel; the template keeps its content above the talking-head corner
                ents = {e["id"]: e for e in (p.style_guide or {}).get("entities", [])}
                from app.services.generation.icons import icons

                ctx = T.Ctx(w=AUTHOR_W, h=AUTHOR_H, short=True, zones=[FACE_ZONE], duration=shot.duration_ms / 1000,
                            words=[{"w": w["w"], "t": round((w["start_ms"] - shot.start_ms) / 1000, 2)}
                                   for w in words if shot.start_ms <= w["start_ms"] < shot.end_ms],
                            entities=ents, icons=frozenset(icons()))  # fmt: skip
                sc, ah, mode = T.build(parsed[0], parsed[1], ctx), AUTHOR_H, "panel"
            else:  # a centred card: drawn on the area above the talking head only
                sc, ah, mode = text_card_scene(shot), AUTHOR_CLEAR_H, "top"
            r = renderer.render(html=sc.html, css=sc.css, js=GP.RUNTIME_JS + "\n" + icon_runtime() + "\n" + sc.js,
                                theme_css=style.theme_css(profile, p, shot.theme, _stage_canvas(ah)), width=AUTHOR_W,
                                height=ah, fps=p.fps, duration_ms=shot.duration_ms, scale=AUTHOR_SCALE)  # fmt: skip
            src = r.video
        except Exception as ex:  # noqa: BLE001 — fall back to the 16:9 render
            log.warning("shot %s: vertical template render failed (%s); using the 16:9 take", shot.index, ex)
            mode = "fit16x9"
    if src is None and take and take.storage_key and get_storage().exists(take.storage_key):
        src = get_storage().path(take.storage_key)
    part = out.with_name(f"{out.stem}.{uuid.uuid4().hex[:6]}.part")
    common = ["-frames:v", str(n), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt",
              "yuv420p", "-r", str(p.fps), "-threads", "4", "-f", "mp4", str(part)]  # fmt: skip
    if src is None:
        _ffmpeg(
            "-f", "lavfi", "-i", f"color=c=0x{bg}:s={STAGE_W}x{STAGE_H}:r={p.fps}:d={n / p.fps:.3f}", *common
        )
    else:
        fit = {
            "panel": f"scale={STAGE_W}:{STAGE_H},",
            "top": f"scale={STAGE_W}:{CLEAR_H},pad={STAGE_W}:{STAGE_H}:0:0:color=0x{bg},",
            # a 16:9 scene: crop its empty margins and footer (content inside x 6–94 %, y 5–91 %), then fit it
            # into the area above the talking head
            "fit16x9": ("crop=iw*0.9:ih*0.86:iw*0.05:ih*0.05,"
                        f"scale={STAGE_W}:{CLEAR_H}:force_original_aspect_ratio=decrease,"
                        f"pad={STAGE_W}:{STAGE_H}:(ow-iw)/2:({CLEAR_H}-ih)/2:color=0x{bg},"),
        }[mode]  # fmt: skip
        _ffmpeg(
            "-i",
            str(src),
            "-vf",
            f"{fit}fps={p.fps},tpad=stop_mode=clone:stop_duration={n / p.fps:.3f}",
            *common,
        )
        if mode != "fit16x9":  # a temporary native render (never the stored take)
            Path(src).unlink(missing_ok=True)
    part.replace(out)
    return out


CAPTION_FONT = "/usr/share/fonts/brand/Poppins-SemiBold.ttf"
CTX_S = 3.5  # how long the context line stays on screen


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


_TECH = re.compile(r"[a-z][A-Z]|[A-Z].*[A-Z]|\d[A-Za-z]|[A-Za-z]\d")  # camelCase, ACRONYMS, LangChain4j, GPT4


def _misheard_term(spoken: list[dict], sw: list[str], j1: int, j2: int) -> bool:
    """Only a technical term or a product/person name from the script replaces what was heard: the creator's
    deliberate wording, numbers and placeholders (the script's "X" for the persona) stay as spoken."""
    import difflib

    if any(re.search(r"\d", w["w"]) and not _TECH.search(w["w"]) for w in spoken):
        return False  # "50,000" is right; the script's "fifty thousand" is just how it was written
    names = []
    for j in range(j1, j2):
        tok = sw[j].strip(".,!?;:\"'")
        prev = sw[j - 1] if j else "."
        if len(_norm(tok)) < 3 and not re.search(r"\d", tok):
            return False  # a placeholder like the script's "X" (the persona's name is said aloud)
        proper = tok[0].isupper() and not prev.endswith((".", "?", "!", ":"))
        names.append(bool(_TECH.search(tok)) or proper)
    if not any(names) or (j2 - j1 > len(spoken) and not all(names)):  # never add unspoken ordinary words
        return False
    heard, written = _norm("".join(w["w"] for w in spoken)), _norm("".join(sw[j1:j2]))
    return difflib.SequenceMatcher(None, heard, written).ratio() >= 0.3


def correct_words(words: list[dict], script: str) -> list[dict]:
    """Fix mis-heard words in the captions from the creator's own script ("Cloud" → "Claude", "Olama" → "Ollama",
    "LangChain 4G" → "LangChain4j"): align the transcript to the script and, where a short run of words (≤ 3) differs
    between two matching stretches, use the script's words, sharing out the original timing. Ad-libs the script
    doesn't contain are kept as spoken."""
    import difflib

    if not words or not script.strip():
        return words
    sw = [t.strip("*_`#>[]()") for t in re.sub(r"^\s*#+\s*", "", script, flags=re.M).split()]
    sw = [t for t in sw if _norm(t)]
    a, b = [_norm(w["w"]) for w in words], [_norm(t) for t in sw]
    ops = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    out: list[dict] = []
    for k, (tag, i1, i2, j1, j2) in enumerate(ops):
        anchored = (0 < k < len(ops) - 1 and ops[k - 1][0] == "equal" and ops[k + 1][0] == "equal"
                    and ops[k - 1][2] - ops[k - 1][1] >= 2 and ops[k + 1][2] - ops[k + 1][1] >= 2)  # fmt: skip
        if (tag == "replace" and anchored and i2 - i1 <= 3 and j2 - j1 <= i2 - i1 + 1
                and _misheard_term(words[i1:i2], sw, j1, j2)):  # fmt: skip
            t0, t1 = words[i1]["start_ms"], words[i2 - 1]["end_ms"]
            step = (t1 - t0) / (j2 - j1)
            trail = re.sub(r"^.*?([.,!?;:]*)$", r"\1", words[i2 - 1]["w"])  # keep the spoken punctuation
            for m, tok in enumerate(sw[j1:j2]):
                tok = tok.rstrip(".,!?;:") + (trail if m == j2 - j1 - 1 else "")
                out.append({**words[i1], "w": tok, "start_ms": round(t0 + m * step),
                            "end_ms": round(t0 + (m + 1) * step)})  # fmt: skip
        else:
            out.extend(words[i1:i2])  # equal, spoken-only (delete) or too different to trust
    return out


def _ass_time(ms: int) -> str:
    ms = max(0, ms)
    return f"{ms // 3600000}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000 // 10:02d}"


def captions_ass(words: list[dict], start_ms: int, end_ms: int, accent: str) -> str:
    """Word-by-word captions: short groups (≤ 3 words), the spoken word in the accent colour."""
    ws = [w for w in words if start_ms <= w["start_ms"] < end_ms and str(w.get("w", "")).strip()]
    groups, cur = [], []
    for w in ws:
        cur.append(w)
        text = " ".join(x["w"] for x in cur)
        if len(cur) >= 3 or len(text) >= 18 or str(w["w"])[-1:] in ".?!,:":
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    r, g, b = (accent.lstrip("#")[i : i + 2] for i in (0, 2, 4))
    hl = f"&H00{b}{g}{r}&".upper()

    def esc(s: str) -> str:
        return str(s).replace("\\", "/").replace("{", "(").replace("}", ")")

    lines = []
    for gi, grp in enumerate(groups):
        g_end = (
            groups[gi + 1][0]["start_ms"] if gi + 1 < len(groups) else min(end_ms, grp[-1]["end_ms"] + 400)
        )
        for wi, w in enumerate(grp):
            t0 = w["start_ms"]
            t1 = grp[wi + 1]["start_ms"] if wi + 1 < len(grp) else g_end
            text = " ".join(("{\\c" + hl + "}" + esc(x["w"]) + "{\\c&H00FFFFFF&}") if k == wi else esc(x["w"])
                            for k, x in enumerate(grp))  # fmt: skip
            lines.append(
                f"Dialogue: 0,{_ass_time(t0 - start_ms)},{_ass_time(max(t1, t0 + 80) - start_ms)},Cap,,0,0,0,,{text}"
            )
    margin_v = H - CAPTION_BOTTOM
    return ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Cap,Poppins SemiBold,76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,0,0,0,0,100,100,0,0,1,5,2,2,"
            f"70,70,{margin_v},1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
            "Effect, Text\n" + "\n".join(lines) + "\n")  # fmt: skip


CHROME_CSS = """
.sh-bg{position:absolute;inset:0;background:#03060c}
.sh-top{position:absolute;left:64px;right:64px;top:52px;height:176px;display:flex;flex-direction:column;
  align-items:flex-start;justify-content:flex-end;gap:12px}
.sh-row{display:flex;align-items:center;gap:14px}
.sh-part{font:700 36px/1 var(--f-head);letter-spacing:.04em;text-transform:uppercase;color:#fff;
  background:var(--accent);padding:8px 16px 6px;border-radius:10px;
  box-shadow:0 6px 24px color-mix(in srgb,var(--accent) 45%,transparent)}
.sh-badge{display:inline-flex;align-items:center;gap:10px;padding:8px 18px;border-radius:999px;
  background:color-mix(in srgb,var(--accent) 18%,#060b14);border:2px solid var(--accent);
  font:600 22px/1 var(--f-mono);letter-spacing:.14em;text-transform:uppercase;color:#fff}
.sh-hook{font:700 50px/1.02 var(--f-head);text-transform:uppercase;color:#fff;letter-spacing:.005em;
  text-shadow:0 4px 0 rgba(0,0,0,.35),0 0 30px rgba(0,0,0,.5)}
.sh-hook .hl{color:var(--accent)}
"""

FIT_JS = """
const tl = gsap.timeline(); window.__scene = tl;
const el = document.querySelector('.sh-hook'); let fs = parseFloat(getComputedStyle(el).fontSize);
while ((el.scrollWidth > 952 || el.offsetHeight > 104) && fs > 30) { fs -= 2; el.style.fontSize = fs + 'px'; }
tl.set({}, {}, 1.9);
"""


def chrome_png(p: Project, profile, clip: ShortClip) -> Path:
    """The static 9:16 frame: dark background and the top band (series badge, hook title)."""
    import html as h

    from app.services.generation import GP, icon_runtime, renderer

    cv = style.Canvas(W, H, W, H, 0, True, [], False, None)
    badge = clip.series or (profile.brand or {}).get("handle", "")
    part = f'<div class="sh-part">Part {clip.part}/{clip.parts}</div>' if clip.series else ""
    words = clip.hook.split()
    hook = " ".join(f'<span class="hl">{h.escape(w)}</span>' if k == len(words) - 1 else h.escape(w)
                    for k, w in enumerate(words))  # fmt: skip
    html = ('<div class="sh-bg"></div>'
            f'<div class="sh-top"><div class="sh-row">{part}'
            f'{f"<div class=sh-badge><i class=mh-led></i>{h.escape(badge)}</div>" if badge else ""}</div>'
            f'<div class="sh-hook">{hook}</div></div>')  # fmt: skip
    return renderer.render_still(html=html, css=CHROME_CSS, js=GP.RUNTIME_JS + icon_runtime() + FIT_JS,
                                 theme_css=style.theme_css(profile, p, "tech", cv), width=W, height=H)  # fmt: skip


END_S = 3.0  # the end card's length, over the last seconds of the panel (face and narration continue)
END_AUTHOR_H = round((FACE_Y - STAGE_Y) / AUTHOR_SCALE)  # 900: covers the panel down to the talking head

END_CSS = """
.ec{position:absolute;left:56px;right:56px;top:0;bottom:0;display:flex;flex-direction:column;justify-content:center;
  align-items:flex-start;gap:22px}
.ec-eyebrow{font:600 24px/1 var(--f-mono);letter-spacing:.16em;text-transform:uppercase;color:var(--accent)}
.ec-big{font:700 104px/.95 var(--f-head);text-transform:uppercase;color:#fff;letter-spacing:.005em}
.ec-big .hl{color:var(--accent);text-shadow:0 0 40px color-mix(in srgb,var(--accent) 60%,transparent)}
.ec-sub{font:500 34px/1.25 var(--f-body);color:#c9d4e3;max-width:860px;text-wrap:balance}
.ec-cta{display:inline-flex;align-items:center;gap:16px;margin-top:14px;padding:18px 28px;border-radius:999px;
  background:var(--accent);color:#fff;font:700 34px/1 var(--f-head);text-transform:uppercase;letter-spacing:.03em;
  box-shadow:0 10px 40px color-mix(in srgb,var(--accent) 55%,transparent)}
.ec-cta [data-icon]{font-size:40px;color:#fff}  /* icons default to the accent colour: invisible on the accent button */
#bell{transform-origin:50% 10%}
"""


def end_card(clip: ShortClip, next_clip: ShortClip | None, long_title: str) -> tuple[str, str]:
    """(html, js) of the closing card: the next part of a series, or 'follow for more' + the full video."""
    import html as h

    if clip.series and clip.part and clip.parts and clip.part < clip.parts:
        eyebrow = clip.series
        big = f'Part <span class="hl">{clip.part + 1}</span> next'
        sub = next_clip.hook if next_clip is not None else ""
        cta = "Follow so you don't miss it"
    else:
        eyebrow = clip.series or "Want the full picture?"
        big = 'Follow <span class="hl">for more</span>'
        sub = f"Full video: {long_title}" if long_title else ""
        cta = "Follow"
    html = ('<div class="mh-bg"></div><div class="mh-glow" style="left:40px;top:-120px"></div><div class="ec">'
            f'<div class="ec-eyebrow" id="e1">{h.escape(eyebrow)}</div><div class="ec-big" id="e2">{big}</div>'
            + (f'<div class="ec-sub" id="e3">{h.escape(sub)}</div>' if sub else "")
            + f'<div class="ec-cta" id="e4"><span id="bell"><i data-icon="bell-ring"></i></span>{h.escape(cta)}'
            '<i data-icon="arrow-right"></i></div></div>')  # fmt: skip
    js = (f"const tl = gsap.timeline(); window.__scene = tl; const D = {END_S};\n"
          "tl.from('#e1', {y:30, opacity:0, duration:.35, ease:'power3.out'}, .05);\n"
          "tl.from('#e2', {y:50, opacity:0, duration:.45, ease:'power3.out'}, .15);\n"
          "if (document.querySelector('#e3')) tl.from('#e3', {y:30, opacity:0, duration:.4, ease:'power3.out'}, .35);\n"
          "tl.from('#e4', {scale:.6, opacity:0, duration:.45, ease:'back.out(2)'}, .6);\n"
          "tl.to('#bell', {rotation:18, duration:.09, yoyo:true, repeat:9, ease:'sine.inOut'}, 1.1);\n"
          "tl.to('#e4', {scale:1.06, duration:.35, yoyo:true, repeat:3, ease:'sine.inOut'}, 1.3);\n"
          "tl.set({}, {}, D);")  # fmt: skip
    return html, js


def end_card_video(p: Project, profile, clip: ShortClip, next_clip: ShortClip | None, long_title: str,
                   cache: Path) -> Path:  # fmt: skip
    """The end card, 1080×1000: the whole panel down to the talking head (captions stay on top). Cached."""
    from app.services.generation import GP, icon_runtime, renderer

    html, js = end_card(clip, next_clip, long_title)
    key = hashlib.blake2b(f"e3|{html}|{p.pillar}".encode(), digest_size=10).hexdigest()
    out = cache / f"end_{key}.mp4"
    if out.exists():
        return out
    r = renderer.render(html=html, css=END_CSS, js=GP.RUNTIME_JS + "\n" + icon_runtime() + "\n" + js,
                        theme_css=style.theme_css(profile, p, "tech", _stage_canvas(END_AUTHOR_H)),
                        width=AUTHOR_W, height=END_AUTHOR_H, fps=p.fps, duration_ms=int(END_S * 1000),
                        scale=AUTHOR_SCALE)  # fmt: skip
    part = out.with_name(f"{out.stem}.{uuid.uuid4().hex[:6]}.part")
    _ffmpeg("-i", str(r.video), "-vf", f"scale={STAGE_W}:{FACE_Y - STAGE_Y},fps={p.fps}", "-an", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-f", "mp4", str(part))  # fmt: skip
    Path(r.video).unlink(missing_ok=True)
    part.replace(out)
    return out


def render(short_id: str) -> None:
    """Worker: build the vertical MP4 for one Short."""
    from app.services.ingest.pipeline import latest_asset

    sid = uuid.UUID(short_id)
    with get_sessionmaker()() as db:
        clip = db.get(ShortClip, sid)
        if clip is None:
            return
        p = db.get(Project, clip.project_id)
        try:
            clip.status, clip.error = "rendering", None
            db.commit()
            profile = style.active(db)
            audio = latest_asset(db, p.id, AssetKind.audio_final)
            if audio is None:
                raise RuntimeError("the project has no final narration")
            shots = list(db.scalars(select(Shot).where(Shot.project_id == p.id, Shot.index >= clip.shot_from,
                                                       Shot.index <= clip.shot_to).order_by(Shot.index)))  # fmt: skip
            storage = get_storage()
            cache = storage.root / "cache" / "shorts" / str(p.id)
            cache.mkdir(parents=True, exist_ok=True)
            script = latest_asset(db, p.id, AssetKind.script)
            words = correct_words(_words(db, p.id),
                                  storage.path(script.storage_key).read_text("utf-8-sig") if script else "")  # fmt: skip
            segs = [stage_segment(db, p, profile, s, cache, words) for s in shots]
            root = storage.root / "tmp"
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=root) as wd:
                work = Path(wd)
                (work / "list.txt").write_text("\n".join(f"file '{s}'" for s in segs))
                stage = work / "stage.mp4"
                _ffmpeg("-f", "concat", "-safe", "0", "-i", str(work / "list.txt"), "-c", "copy", str(stage))
                chrome = chrome_png(p, profile, clip)
                ass = work / "cap.ass"
                ass.write_text(
                    captions_ass(words, clip.start_ms, clip.end_ms, style.accent(profile, p)), "utf-8"
                )
                dur = clip.duration_ms / 1000
                out = work / "short.mp4"
                at = ["-ss", f"{clip.start_ms / 1000:.3f}", "-t", f"{dur:.3f}"]
                inputs = ["-loop", "1", "-framerate", str(p.fps), "-i", str(chrome), "-i", str(stage),
                          *at, "-i", str(storage.path(audio.storage_key))]  # fmt: skip
                graph = f"[0:v][1:v]overlay=0:{STAGE_Y}:shortest=1[stage]"
                # the closing card over the panel's last seconds: the next part of a series, or follow for more
                nxt = None
                if clip.series and clip.part:
                    nxt = db.scalar(select(ShortClip).where(ShortClip.project_id == p.id,
                                                            ShortClip.series == clip.series,
                                                            ShortClip.part == clip.part + 1))  # fmt: skip
                long_title = (p.publish or {}).get("title") or p.topic or p.name
                end = end_card_video(p, profile, clip, nxt, long_title, cache)
                inputs += ["-i", str(end)]
                graph += (f";[3:v]setpts=PTS-STARTPTS+{max(0.0, dur - END_S):.3f}/TB[end];"
                          f"[stage][end]overlay=0:{STAGE_Y}:eof_action=pass[base]")  # fmt: skip
                # the creator's talking head: the same moment of the face-cam recording (cut and synced with
                # the narration at ingest), over the panel's bottom-left
                cam = latest_asset(db, p.id, AssetKind.facecam_final)
                with_face = cam is not None and storage.exists(cam.storage_key)
                if with_face:
                    inputs += [*at, "-i", str(storage.path(cam.storage_key))]
                    graph += (f";[4:v]scale={FACE_W}:{FACE_H}:force_original_aspect_ratio=increase,"
                              f"crop={FACE_W}:{FACE_H},fps={p.fps},setpts=PTS-STARTPTS[face];"
                              f"[base][face]overlay={FACE_X}:{FACE_Y}:eof_action=repeat[withface]")  # fmt: skip
                cur = "withface" if with_face else "base"
                # the context line: the premise the narration assumes, on screen for the first seconds
                ctx_line = (clip.meta or {}).get("context_line", "")
                if ctx_line:
                    lines = textwrap.wrap(ctx_line, 34)[:2]
                    ctx_file = work / "ctx.txt"
                    ctx_file.write_text("\n".join(lines), "utf-8")
                    y0, bh = STAGE_Y + 20, 48 * len(lines) + 34
                    acc = style.accent(profile, p).lstrip("#")
                    on = f"lt(t,{CTX_S + 0.35})"
                    graph += (f";[{cur}]drawbox=x=48:y={y0}:w={W - 96}:h={bh}:color=0x0b1220@0.94:t=fill:enable='{on}',"
                              f"drawbox=x=48:y={y0}:w=8:h={bh}:color=0x{acc}:t=fill:enable='{on}',"
                              f"drawtext=fontfile={CAPTION_FONT}:textfile={ctx_file}:fontsize=38:line_spacing=12:"
                              f"fontcolor=white:x=84:y={y0 + 18}:"
                              f"alpha='if(lt(t,{CTX_S}),1,max(0,1-(t-{CTX_S})/0.35))'[ctx]")  # fmt: skip
                    cur = "ctx"
                graph += f";[{cur}]ass={ass}:fontsdir=/usr/share/fonts/brand[v]"
                _ffmpeg(*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "2:a", "-t", f"{dur:.3f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-r", str(p.fps), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out))  # fmt: skip
                Path(chrome).unlink(missing_ok=True)
                if clip.asset_id:  # re-render replaces the old file
                    old = db.get(Asset, clip.asset_id)
                    if old is not None:
                        storage.delete(old.storage_key)
                        db.delete(old)
                key = storage.new_key(p.id, "mp4")
                size, digest = storage.put_file(key, out)
                a = Asset(project_id=p.id, kind="short_video", storage_key=key, sha256=digest, size=size,
                          mime="video/mp4", original_name=f"short-{clip.index:02d}.mp4",
                          meta={"short_id": str(clip.id), "duration_ms": clip.duration_ms})  # fmt: skip
                db.add(a)
                db.flush()
                clip.asset_id, clip.status = a.id, "done"
                clip.meta = _merge_meta(db, clip.id, {"rendered_at": datetime.now(UTC).isoformat(timespec="seconds"),
                                                      "with_face": with_face, "layout": {"stage": [0, STAGE_Y, STAGE_W, STAGE_H],
                                                      "face_zone": [FACE_X, FACE_Y, FACE_W, FACE_H]}}, drop=("stale",))  # fmt: skip
            db.commit()
        except Exception as ex:  # noqa: BLE001
            log.exception("short render failed")
            db.rollback()
            clip = db.get(ShortClip, sid)
            clip.status, clip.error = "failed", f"{type(ex).__name__}: {ex}"[:1000]
            db.commit()


EDITABLE = {"hook", "title", "description", "hashtags"}


def edit(clip: ShortClip, changes: dict) -> None:
    if "hook" in changes:
        hook = _clip(changes["hook"], 44)
        if not hook:
            raise PlanError("The hook can't be empty")
        if hook != clip.hook:
            clip.hook = hook
            if clip.status == "done":
                clip.meta = {**(clip.meta or {}), "stale": True}  # the burned-in hook changed: render again
    if "title" in changes:
        t = " ".join(str(changes["title"] or "").split())
        if not t or len(t) > 100:
            raise PlanError("Titles need 1–100 characters")
        clip.title = t
    if "description" in changes:
        d = str(changes["description"] or "").strip()
        if len(d) > 5000 or "<" in d or ">" in d:
            raise PlanError("Descriptions are at most 5,000 characters, without < or >")
        clip.description = d
    if "hashtags" in changes:
        clip.hashtags = hashtags(changes["hashtags"])


def as_json(clip: ShortClip) -> dict:
    from app.core.media_urls import signed_media_url

    return {"id": str(clip.id), "index": clip.index, "shot_from": clip.shot_from, "shot_to": clip.shot_to,
            "start_ms": clip.start_ms, "end_ms": clip.end_ms, "duration_s": round(clip.duration_ms / 1000, 1),
            "series": clip.series, "part": clip.part, "parts": clip.parts, "hook": clip.hook, "title": clip.title,
            "description": clip.description, "hashtags": clip.hashtags, "why": clip.why, "score": clip.score,
            "status": clip.status, "error": clip.error, "stale": bool((clip.meta or {}).get("stale")),
            "with_face": bool((clip.meta or {}).get("with_face")),
            "context_line": (clip.meta or {}).get("context_line", ""),
            "pinned_comment": (clip.meta or {}).get("pinned_comment", ""),
            "cover_status": (clip.meta or {}).get("cover_status"),
            "cover_url": signed_media_url((clip.meta or {})["cover_asset_id"]) if (clip.meta or {}).get("cover_asset_id") else None,
            "url": signed_media_url(clip.asset_id) if clip.asset_id else None}  # fmt: skip


def recover_interrupted() -> int:
    """Worker boot: a Short still 'rendering' died with its worker. Mark it failed so it can be rendered again
    ('queued' ones keep their task in the durable queue and will run)."""
    with get_sessionmaker()() as db:
        stuck = list(db.scalars(select(ShortClip).where(ShortClip.status == "rendering")))
        for c in stuck:
            c.status, c.error = "failed", "Interrupted (worker or Docker restarted). Render again."
        db.commit()
        return len(stuck)


def make_cover(short_id: str) -> None:
    """Worker: the Short's 9:16 cover (AI-designed, the creator's real photo composited), from its cover concept."""
    from app.models.style import BrandAsset
    from app.services import covers_ai as AI

    sid = uuid.UUID(short_id)
    with get_sessionmaker()() as db:
        clip = db.get(ShortClip, sid)
        if clip is None:
            return
        p = db.get(Project, clip.project_id)
        meta = dict(clip.meta or {})
        try:
            profile = style.active(db)
            cover = meta.get("cover") or {"headline": clip.hook, "highlight": "", "subline": clip.title,
                                          "points": [], "visual": ""}  # fmt: skip
            head = db.scalar(select(BrandAsset).where(BrandAsset.status == "ready", BrandAsset.cutout_key.is_not(None))
                             .order_by(BrandAsset.created_at.desc()))  # fmt: skip
            storage = get_storage()
            person = storage.path(head.cutout_key) if head else None
            # a series names itself; a standalone Short is labelled with its own subject (the title before ":")
            eyebrow = clip.series or re.split(r"[:|(]", clip.title)[0].strip() or (p.topic or p.name)
            part = f"PART {clip.part}/{clip.parts}" if clip.series else ""
            jpg, cost, alias = AI.draw_short_cover(
                cover, _clip(eyebrow, 32).upper(), part, p, profile, person
            )
            old = db.get(Asset, meta["cover_asset_id"]) if meta.get("cover_asset_id") else None
            if old is not None:
                storage.delete(old.storage_key)
                db.delete(old)
            key = storage.new_key(p.id, "jpg")
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False, dir=storage.root) as f:
                f.write(jpg)
            size, digest = storage.put_file(key, Path(f.name))
            Path(f.name).unlink(missing_ok=True)
            a = Asset(project_id=p.id, kind="short_cover", storage_key=key, sha256=digest, size=size, mime="image/jpeg",
                      original_name=f"short-{clip.index:02d}-cover.jpg",
                      meta={"short_id": str(clip.id), "image_model": alias, "cost_usd": round(cost, 4)})  # fmt: skip
            db.add(a)
            db.flush()
            updates = {"cover_asset_id": str(a.id), "cover_status": "done", "cover_error": None,
                       "cover_cost_usd": round(cost, 4)}  # fmt: skip
        except Exception as ex:  # noqa: BLE001
            log.exception("short cover failed")
            db.rollback()
            clip = db.get(ShortClip, sid)
            updates = {"cover_status": "failed", "cover_error": f"{type(ex).__name__}: {ex}"[:500]}
        clip.meta = _merge_meta(db, sid, updates)  # a render may have saved its own fields meanwhile
        db.commit()


def _merge_meta(db: Session, short_id: uuid.UUID, updates: dict, drop: tuple[str, ...] = ()) -> dict:
    """The Short's meta as stored NOW (row-locked), with these changes: a render and a cover job for the same Short
    run in parallel, and neither may overwrite the other's fields with its stale copy."""
    current = (
        db.execute(select(ShortClip.meta).where(ShortClip.id == short_id).with_for_update()).scalar() or {}
    )
    return {**{k: v for k, v in current.items() if k not in drop}, **updates}
