"""Cover photos: YouTube thumbnail (1280×720) and Shorts/Reels cover (1080×1920).

Pipeline (spec v2.1 §6):
  1. Creator headshots live in the Brand Kit; each is cut out once (rembg, on the ML worker).
  2. The planner LLM proposes N concepts from the script: ≤ 4-word English headline, technical keywords to
     highlight in the pillar colour, an eyebrow tag and 2–3 icons from the fixed icon library.
  3. A deterministic, on-brand HTML template renders every concept in every format in the sandboxed renderer
     (same fonts / palette / grid as the videos). No image-model cost; consistent across all videos.
"""

import base64
import html as h
import io
import json
import logging
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.llm import client as llm
from app.models import Asset, AssetKind, Project
from app.models.style import BrandAsset
from app.services import routing, style
from app.services.generation import icons as I
from app.services.generation import renderer
from app.services.ingest.pipeline import latest_asset
from app.services.planner import extract_json
from app.services.storage import get_storage

log = logging.getLogger(__name__)

FORMATS = {"youtube": (1280, 720), "short": (1080, 1920)}
MAX_HEADLINE_WORDS = 4

# ---------------------------------------------------------------- 1. headshot cut-out (ML worker)


def cutout(brand_asset_id: str) -> None:
    with get_sessionmaker()() as db:
        a = db.get(BrandAsset, uuid.UUID(brand_asset_id))
        if a is None:
            return
        try:
            from PIL import Image
            from rembg import new_session, remove

            storage = get_storage()
            src = Image.open(storage.path(a.storage_key)).convert("RGB")
            out = remove(src, session=new_session(get_settings().cutout_model), post_process_mask=True)
            out = strip_thin_structures(out)
            bbox = out.getchannel("A").point(lambda v: 255 if v > 12 else 0).getbbox()
            if bbox is None:
                raise RuntimeError("no person found in the photo")
            out = out.crop(bbox)
            if out.height > 1600:
                out = out.resize((round(out.width * 1600 / out.height), 1600), Image.LANCZOS)
            buf = io.BytesIO()
            out.save(buf, "PNG", optimize=True)
            key = storage.new_key("brand", "png")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False, dir=storage.root) as f:
                f.write(buf.getvalue())
            storage.put_file(key, Path(f.name))
            a.cutout_key, a.status = key, "ready"
            a.meta = {
                **(a.meta or {}),
                "cutout_size": [out.width, out.height],
                "model": get_settings().cutout_model,
            }
        except Exception as e:  # noqa: BLE001
            log.exception("cutout failed")
            a.status, a.meta = "failed", {**(a.meta or {}), "error": f"{type(e).__name__}: {e}"[:500]}
        db.commit()


# ---------------------------------------------------------------- 2. concepts (LLM, from the Style Profile)

IDEAS_SYSTEM = """You design cover concepts (YouTube thumbnail + Shorts/Reels cover) for {brand} — “{tagline}” ({handle}).
Rules (Style Profile v{version}):
- English only. Headline ≤ 4 words, punchy, plain and accurate. No hype, no clickbait, no invented numbers.
- Pick 1–2 technical keywords in the headline to highlight in the pillar colour (e.g. RAG, KAFKA, MCP, AGENT).
- Eyebrow: ≤ 3 words tag above the headline (topic or series), uppercase-friendly.
- Icons: 2–3 names from this icon library that visualise the idea: {icons}
- layout: "icons" (icon tiles), or "versus" (two sides — set left/right labels ≤ 2 words each, icons[0] vs icons[1]).
- Make the concepts genuinely different (angle, wording, layout). Curiosity through clarity.
Reply with ONLY JSON: {{"options": [{{"headline": "...", "highlight": ["..."], "eyebrow": "...", "icons": ["..."],
"layout": "icons", "left": "", "right": ""}}]}}"""


def ideas(project: Project, profile, script: str, n: int, note: str = "") -> tuple[list[dict], float]:
    b = profile.brand or {}
    system = IDEAS_SYSTEM.format(brand=b.get("name"), tagline=b.get("tagline"), handle=b.get("handle"),
                                 version=profile.version, icons=", ".join(I.names()))  # fmt: skip
    user = (f"Topic: {project.topic or project.name}\nContent type: {project.content_type} · pillar: {project.pillar or '—'}"
            f" · audience: {project.audience_level}\nNumber of concepts: {n}\n"
            + (f"Creator's note: {note}\n" if note else "") + f"\nSCRIPT:\n{script[:6000]}")  # fmt: skip
    cfg = routing.load()
    cost, last = 0.0, ""
    messages = [{"role": "user", "content": user}]
    for _ in range(2):
        text, res = llm.chat(cfg.planner.model, messages, system=system, fallbacks=cfg.planner.fallbacks,
                             max_tokens=3000, temperature=0.8)  # fmt: skip
        cost += res.cost_usd
        try:
            opts = [normalise(o) for o in extract_json(text).get("options", [])][:n]
            if opts:
                return opts, cost
            last = "no options"
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            last = str(e)
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": f"Invalid: {last}. JSON only."},
        ]
    raise ValueError(f"cover concepts invalid: {last}")


def normalise(o: dict) -> dict:
    words = str(o.get("headline", "")).split()
    if not words:
        raise ValueError("empty headline")
    headline = " ".join(words[:MAX_HEADLINE_WORDS])
    hw = {w.strip(".,!?:").lower() for w in headline.split()}
    highlight = [x for x in (o.get("highlight") or []) if all(t.lower() in hw for t in str(x).split())][:2]
    known = I.icons()
    icons = [i for i in (o.get("icons") or []) if i in known][:3] or ["sparkles"]
    layout = o.get("layout") if o.get("layout") in ("icons", "versus") and len(icons) >= 2 else "icons"
    return {"headline": headline, "highlight": highlight, "eyebrow": " ".join(str(o.get("eyebrow", "")).split()[:3]),
            "icons": icons, "layout": layout, "left": str(o.get("left", ""))[:20], "right": str(o.get("right", ""))[:20]}  # fmt: skip


# ---------------------------------------------------------------- 3. template (deterministic, on brand)

COVER_CSS = """
.cv-bg{position:absolute;inset:0;background:
  radial-gradient(circle at var(--gx) var(--gy),color-mix(in srgb,var(--accent) 34%,transparent),transparent 42%),
  linear-gradient(var(--grid-color) 1px,transparent 1px) 0 0/var(--grid) var(--grid),
  linear-gradient(90deg,var(--grid-color) 1px,transparent 1px) 0 0/var(--grid) var(--grid),
  linear-gradient(160deg,var(--bg-from),var(--bg-to))}
.cv-vignette{position:absolute;inset:0;box-shadow:inset 0 0 220px 40px rgba(0,0,0,.55)}
.cv-person{position:absolute;bottom:0;background-repeat:no-repeat;background-size:contain;background-position:bottom center;
  filter:drop-shadow(0 0 3px var(--accent)) drop-shadow(0 0 26px color-mix(in srgb,var(--accent) 75%,transparent))}
.cv-eyebrow{position:absolute;display:inline-flex;align-items:center;gap:12px;padding:10px 18px;border-radius:999px;
  background:color-mix(in srgb,var(--accent) 18%,#060b14);border:2px solid var(--accent);
  font:600 var(--eb) var(--f-mono);letter-spacing:.16em;text-transform:uppercase;color:#ffffff}
.cv-eyebrow .mh-led{width:14px;height:14px}
.cv-head{position:absolute;font:700 var(--hs)/0.98 var(--f-head);text-transform:uppercase;color:#ffffff;letter-spacing:.005em;
  text-shadow:0 6px 0 rgba(0,0,0,.35),0 0 40px rgba(0,0,0,.6)}
.cv-head .hl{color:var(--accent);text-shadow:0 6px 0 rgba(0,0,0,.35),0 0 34px color-mix(in srgb,var(--accent) 70%,transparent)}
.cv-tiles{position:absolute;display:flex;gap:var(--tg)}
.cv-tile{width:var(--ts);height:var(--ts);border-radius:22%;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(180deg,#132036,#0a1626);border:3px solid var(--accent);
  box-shadow:0 0 0 6px color-mix(in srgb,var(--accent) 12%,transparent),0 0 40px color-mix(in srgb,var(--accent) 45%,transparent)}
.cv-tile [data-icon]{font-size:calc(var(--ts) * .56);color:#ffffff}
.cv-vs{position:absolute;display:flex;align-items:center;gap:calc(var(--tg) * 1.2)}
.cv-vs .side{display:flex;flex-direction:column;align-items:center;gap:12px;font:700 calc(var(--ts) * .26)/1 var(--f-head);
  color:#ffffff;text-transform:uppercase}
.cv-vs .vs{font:700 calc(var(--ts) * .42)/1 var(--f-head);color:var(--warn);text-shadow:0 0 24px rgba(0,0,0,.6)}
.cv-handle{position:absolute;font:500 var(--hd) var(--f-mono);color:rgba(232,238,247,.75);letter-spacing:.04em}
"""

FIT_JS = """
const tl = gsap.timeline(); window.__scene = tl;
// shrink the headline until it fits its box (max lines), deterministic
const el = document.querySelector('.cv-head'); const maxW = parseFloat(el.dataset.maxw), maxH = parseFloat(el.dataset.maxh);
let fs = parseFloat(getComputedStyle(el).fontSize);
while ((el.scrollWidth > maxW || el.offsetHeight > maxH) && fs > 30) { fs -= 2; el.style.fontSize = fs + 'px'; }
tl.set({}, {}, 1.9);
"""


def _headline_html(o: dict) -> str:
    hl = {t.lower() for x in o["highlight"] for t in x.split()}
    return " ".join(f'<span class="hl">{h.escape(w)}</span>' if w.strip(".,!?:").lower() in hl else h.escape(w)
                    for w in o["headline"].split())  # fmt: skip


def cover_scene(o: dict, fmt: str, handle: str, person_b64: str | None) -> tuple[str, str]:
    """Returns (html, css) for one concept in one format."""
    w, hgt = FORMATS[fmt]
    person = person_b64 is not None
    icons = "".join(f'<div class="cv-tile"><i data-icon="{i}"></i></div>' for i in o["icons"])
    if o["layout"] == "versus":
        visual = (f'<div class="cv-vs"><div class="side"><div class="cv-tile"><i data-icon="{o["icons"][0]}"></i></div>'
                  f'{h.escape(o["left"])}</div><div class="vs">VS</div><div class="side"><div class="cv-tile">'
                  f'<i data-icon="{o["icons"][1]}"></i></div>{h.escape(o["right"])}</div></div>')  # fmt: skip
    else:
        visual = f'<div class="cv-tiles">{icons}</div>'
    eyebrow = (
        f'<div class="cv-eyebrow"><i class="mh-led"></i>{h.escape(o["eyebrow"])}</div>'
        if o["eyebrow"]
        else ""
    )
    if fmt == "youtube":
        lay = {
            "gx": "78%" if person else "70%",
            "gy": "45%",
            "hs": "112px",
            "eb": "20px",
            "ts": "118px",
            "tg": "22px",
            "hd": "18px",
        }
        head_box = (650 if person else 1100, 330)
        pos = {
            ".cv-eyebrow": "left:64px;top:58px",
            ".cv-head": f"left:64px;top:128px;max-width:{head_box[0]}px",
            ".cv-tiles,.cv-vs": "left:64px;bottom:72px" if person else "right:70px;bottom:80px",
            ".cv-person": "right:-10px;width:620px;height:700px",
            ".cv-handle": "left:66px;bottom:30px" if person else "left:66px;bottom:30px",
        }
    else:
        lay = {
            "gx": "50%",
            "gy": "70%",
            "hs": "150px",
            "eb": "26px",
            "ts": "150px",
            "tg": "28px",
            "hd": "26px",
        }
        head_box = (960, 520)
        pos = {
            ".cv-eyebrow": "left:60px;top:150px",
            ".cv-head": f"left:60px;top:240px;max-width:{head_box[0]}px",
            ".cv-tiles,.cv-vs": "left:60px;top:800px" if person else "left:60px;top:860px",
            ".cv-person": "left:50%;transform:translateX(-50%);width:1020px;height:960px",
            ".cv-handle": "left:60px;top:1040px" if person else "left:60px;bottom:120px",
        }
    css_vars = ";".join(f"--{k}:{v}" for k, v in lay.items())
    css = COVER_CSS + f"#stage{{{css_vars}}}" + "".join(f"{sel}{{{v}}}" for sel, v in pos.items())
    if person:
        css += f".cv-person{{background-image:url(data:image/png;base64,{person_b64})}}"
    html = (
        '<div class="cv-bg"></div><div class="cv-vignette"></div>'
        + ('<div class="cv-person"></div>' if person else "")
        + eyebrow
        + f'<div class="cv-head" data-maxw="{head_box[0]}" data-maxh="{head_box[1]}">{_headline_html(o)}</div>'
        + visual
        + f'<div class="cv-handle">{h.escape(handle)}</div>'
    )
    return html, css


def render_cover(project: Project, profile, o: dict, fmt: str, person_b64: str | None) -> Path:
    w, hgt = FORMATS[fmt]
    cv = style.Canvas(w, hgt, w, hgt, 0, fmt == "short", [], False, None)
    html, css = cover_scene(o, fmt, (profile.brand or {}).get("handle", ""), person_b64)
    from app.services.generation import prompts as GP

    return renderer.render_still(html=html, css=css, js=GP.RUNTIME_JS + I.runtime_js() + FIT_JS,
                                 theme_css=style.theme_css(profile, project, "tech", cv), width=w, height=hgt,
                                 scale=1.0)  # fmt: skip


def person_b64(headshot: BrandAsset | None, max_h: int = 1100) -> str | None:
    if headshot is None or headshot.status != "ready" or not headshot.cutout_key:
        return None
    from PIL import Image

    img = Image.open(get_storage().path(headshot.cutout_key))
    if img.height > max_h:
        img = img.resize((round(img.width * max_h / img.height), max_h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------- orchestration (worker)


def generate(project_id: str, formats: list[str], headshot_id: str | None, n: int, note: str = "") -> None:
    pid = uuid.UUID(project_id)
    with get_sessionmaker()() as db:
        p = db.get(Project, pid)
        if p is None:
            return
        meta = dict(p.plan_meta or {})
        try:
            profile = style.active(db)
            script = latest_asset(db, pid, AssetKind.script)
            if script is None:
                raise RuntimeError("upload or choose a script first")
            text = get_storage().path(script.storage_key).read_text("utf-8-sig")
            headshot = db.get(BrandAsset, uuid.UUID(headshot_id)) if headshot_id else None
            if headshot is None:
                headshot = db.scalar(select(BrandAsset).where(BrandAsset.status == "ready")
                                     .order_by(BrandAsset.created_at.desc()))  # fmt: skip
            pb64 = person_b64(headshot)
            opts, cost = ideas(p, profile, text, n, note)
            batch = uuid.uuid4().hex[:8]
            storage = get_storage()
            made = 0
            for i, o in enumerate(opts):
                for fmt in formats:
                    png = render_cover(p, profile, o, fmt, pb64)
                    jpg = png.with_suffix(".jpg")  # JPEG: YouTube thumbnails must be < 2 MB
                    from app.services.generation import _ffmpeg

                    _ffmpeg("-i", str(png), "-q:v", "2", str(jpg))
                    png.unlink(missing_ok=True)
                    key = storage.new_key(p.id, "jpg")
                    size, digest = storage.put_file(key, jpg)
                    db.add(Asset(project_id=p.id, kind="cover", storage_key=key, sha256=digest, size=size,
                                 mime="image/jpeg", original_name=f"cover-{fmt}-{i + 1}.jpg",
                                 meta={**o, "format": fmt, "option": i + 1, "batch": batch, "chosen": False,
                                       "headshot_id": str(headshot.id) if pb64 and headshot else None,
                                       "style_profile_version": profile.version}))  # fmt: skip
                    made += 1
            db.commit()
            meta["covers"] = {"status": "done", "made": made, "cost_usd": round(cost, 4), "batch": batch,
                              "with_photo": pb64 is not None}  # fmt: skip
        except Exception as e:  # noqa: BLE001
            log.exception("cover generation failed")
            db.rollback()
            p = db.get(Project, pid)
            meta["covers"] = {"status": "failed", "error": f"{type(e).__name__}: {e}"[:1500]}
        p.plan_meta = meta
        db.commit()


def strip_thin_structures(img):
    """Remove thin foreground the segmenter kept (glowing rings, cables, frame lines) while keeping the person
    and fine hair detail: morphological opening of the mask decides WHERE the person is; the original soft
    alpha is kept inside that region."""
    from PIL import ImageChops, ImageFilter

    alpha = img.getchannel("A")
    k = max(9, round(min(img.size) * 0.02)) | 1  # odd kernel ≈ 2% of the image
    solid = alpha.point(lambda v: 255 if v > 96 else 0)
    opened = solid.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))
    keep = opened.filter(ImageFilter.MaxFilter(k // 2 | 1))  # small margin for hair / soft edges
    img.putalpha(ImageChops.multiply(alpha, keep))
    return img
