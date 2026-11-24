"""AI-designed covers: the planner model plans the content, an image model (via the `image-gen` LiteLLM alias,
e.g. OpenAI gpt-image) draws the designed thumbnail, and the creator's REAL cut-out photo is composited on top.

Why this split:
  - The model plans WHAT is on the cover (headline, highlight, subline, pills, a diagram with real tool names,
    a feature strip) as validated JSON — short, accurate, no hype.
  - The image prompt is built HERE from one fixed brand template (pillar colour, dark grid, neon glow, layout and
    a reserved empty zone), so every cover looks like the same channel.
  - The face is never generated: the creator's cut-out headshot is placed into the reserved zone with a rim
    light in the pillar colour, so it is always exactly them.
"""

import io
import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.llm import client as llm
from app.models import Project
from app.services import routing, style
from app.services.planner import extract_json

log = logging.getLogger(__name__)

# final sizes, the image-model canvas for each, and where the photo goes
OUT = {"youtube": (1920, 1080), "short": (1080, 1920)}
# exact 16:9 / 9:16 (sizes must be multiples of 16): nothing the model draws is cropped away
CANVAS = {"youtube": "1920x1088", "short": "1088x1920"}
IMAGE_ALIAS = "image-gen"  # LiteLLM alias; its model is chosen in Settings → AI providers
PHOTO_ZONE = 0.42  # youtube: the right share of the width kept empty for the creator's photo


def _clip(v: object, n: int) -> str:
    """Shorten at a word boundary: a cover must never show half a word ("mode" for "model")."""
    s = " ".join(str(v or "").split())
    if len(s) <= n:
        return s
    cut = s[: n + 1].rsplit(" ", 1)[0]
    return (cut if len(cut) >= n // 2 else s[:n]).rstrip(" ,;:-+&")


class Visual(BaseModel):
    kind: str = "hub"  # hub | versus | steps | stack
    center: str = ""
    items: list[str] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def _k(cls, v: object) -> str:
        return v if v in ("hub", "versus", "steps", "stack") else "hub"

    @field_validator("center", mode="before")
    @classmethod
    def _c(cls, v: object) -> str:
        return _clip(v, 20)

    @field_validator("items", mode="before")
    @classmethod
    def _i(cls, v: object) -> list[str]:
        return [_clip(x, 22) for x in (v or []) if str(x or "").strip()][:6]


class Concept(BaseModel):
    eyebrow: str = ""
    headline: list[str] = Field(min_length=1)  # 1–2 lines
    highlight: str = ""  # the line (or word) drawn in the pillar colour
    subline: str = ""
    pills: list[str] = Field(default_factory=list)
    visual: Visual = Field(default_factory=Visual)
    strip: list[str] = Field(default_factory=list)

    @field_validator("eyebrow", mode="before")
    @classmethod
    def _e(cls, v: object) -> str:
        return _clip(v, 28).upper()

    @field_validator("headline", mode="before")
    @classmethod
    def _h(cls, v: object) -> list[str]:
        lines = [v] if isinstance(v, str) else list(v or [])
        out = [_clip(x, 18).upper() for x in lines if str(x or "").strip()][:2]
        if not out:
            raise ValueError("empty headline")
        return out

    @field_validator("highlight", mode="before")
    @classmethod
    def _hl(cls, v: object) -> str:
        return _clip(v, 18).upper()

    @field_validator("subline", mode="before")
    @classmethod
    def _s(cls, v: object) -> str:
        return _clip(v, 48)

    @field_validator("pills", mode="before")
    @classmethod
    def _p(cls, v: object) -> list[str]:
        return [_clip(x, 30).upper() for x in (v or []) if str(x or "").strip()][:2]

    @field_validator("strip", mode="before")
    @classmethod
    def _st(cls, v: object) -> list[str]:
        return [_clip(x, 16) for x in (v or []) if str(x or "").strip()][:4]

    @property
    def headline_text(self) -> str:
        return " ".join(self.headline)


PLAN_SYSTEM = """You plan YouTube cover content for the channel "{brand}" — “{tagline}”. The cover must make the
video's value obvious in one second at small size, and be accurate: no hype, no clickbait, no invented numbers.

Return {n} genuinely different concepts (different angle and wording). Each concept:
- eyebrow: the audience or topic, ≤ 3 words (e.g. "JAVA DEVELOPERS", "MODEL CONTEXT PROTOCOL")
- headline: 1–2 short lines, ≤ 3 words per line, ≤ 14 characters per line — the answer or the promise
  (e.g. ["MCP", "EXPLAINED"], ["JAVA IS", "ENOUGH"]). It must complement the video title, not repeat it.
- highlight: the one line or word drawn in the accent colour (usually the key term)
- subline: one plain sentence ≤ 45 characters that adds context (e.g. "Not just another API wrapper.")
- pills: 0–2 short badges ≤ 28 characters (e.g. "THE STANDARD FOR AI TOOLS")
- visual: the diagram that shows the idea:
    {{"kind": "hub", "center": "MCP", "items": ["Slack", "GitHub", "Postgres", "Notion"]}}  (3–6 items) or
    {{"kind": "versus", "items": ["Python first", "Spring AI"]}}  (2 items: the wrong path, the right path) or
    {{"kind": "steps", "items": ["Chunk", "Embed", "Store", "Retrieve"]}}  (3–4 items) or
    {{"kind": "stack", "items": ["Agent", "Tools", "Model"]}}  (3–4 layers, top first)
  Item and center labels ≤ 16 characters each. Use REAL product / technology names from the script where they
  fit (their logos will be drawn).
- strip: 0–4 short feature labels ≤ 13 characters for a bottom strip (e.g. "RAG", "Evaluation")

Reply with ONLY JSON: {{"concepts": [{{"eyebrow": "...", "headline": ["..."], "highlight": "...",
"subline": "...", "pills": ["..."], "visual": {{"kind": "hub", "center": "...", "items": ["..."]}},
"strip": ["..."]}}]}}"""


def plan(
    project: Project, profile, script: str, n: int, note: str = "", title: str = ""
) -> tuple[list[Concept], float]:
    b = profile.brand or {}
    system = PLAN_SYSTEM.format(brand=b.get("name", ""), tagline=b.get("tagline", ""), n=n)
    user = (f"Topic: {project.topic or project.name}\nFormat: {project.content_type} · audience: {project.audience_level}\n"
            + (f"Video title: {title}\n" if title else "") + (f"Creator's note: {note}\n" if note else "")
            + f"\n## Script\n{script[:12000]}")  # fmt: skip
    cfg = routing.load()
    cost, last = 0.0, ""
    messages = [{"role": "user", "content": user}]
    for _ in range(2):
        text, res = llm.chat(cfg.planner.model, messages, system=system, fallbacks=cfg.planner.fallbacks,
                             max_tokens=4000, temperature=0.8)  # fmt: skip
        cost += res.cost_usd
        try:
            out = []
            for c in extract_json(text).get("concepts", [])[:n]:
                try:
                    out.append(Concept.model_validate(c))
                except ValidationError as ex:
                    last = str(ex)[:200]
            if out:
                return out, cost
            last = last or "no concepts"
        except (ValueError, KeyError, json.JSONDecodeError) as ex:
            last = str(ex)[:200]
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content": f"Invalid: {last}. JSON only."},
        ]
    raise ValueError(f"cover concepts invalid: {last}")


def _visual_prompt(v: Visual, accent: str) -> str:
    if v.kind == "versus" and len(v.items) >= 2:
        return (
            f'a clear comparison: a dim, desaturated card "{v.items[0]}" with a small red ✗ versus a bright '
            f'glowing {accent} card "{v.items[1]}" with a green ✓, joined by a bold "VS"'
        )
    if v.kind == "steps" and len(v.items) >= 2:
        steps = ", ".join(f'"{x}"' for x in v.items)
        return f"a left-to-right chain of numbered glowing step cards ({steps}) linked by neon arrows"
    if v.kind == "stack" and len(v.items) >= 2:
        layers = ", ".join(f'"{x}"' for x in v.items)
        return f"an isometric stack of glowing layers labelled top to bottom: {layers}"
    nodes = ", ".join(f'"{x}"' for x in v.items) or '"Tools"'
    center = v.center or (v.items[0] if v.items else "AI")
    return (
        f'a glowing circular hub labelled "{center}" connected by thin dashed neon lines to small dark rounded '
        f"cards with these labels: {nodes}. A card that names a real product or company shows that product's "
        "real logo; a card that names a concept shows a simple matching line icon (never a company logo)"
    )


def image_prompt(c: Concept, fmt: str, accent: str, person: bool) -> str:
    """The brand template for the image model: content from the concept, style and layout fixed here."""
    lines = []
    for ln in c.headline:
        hot = c.highlight and (c.highlight == ln or c.highlight in ln.split())
        lines.append(f'"{ln}" in {"bold " + accent + " with a soft glow" if hot else "white"}')
    pills = " and ".join(
        f'a rounded pill badge "{p}" outlined in {accent if i == 0 else "white"}'
        for i, p in enumerate(c.pills)
    )
    strip = (f"a slim rounded strip with four small line icons and labels: {', '.join(repr(s) for s in c.strip)}"
             if c.strip else "")  # fmt: skip
    text_block = "; ".join(x for x in (
        f'a small {accent} label with an underline: "{c.eyebrow}"' if c.eyebrow else "",
        f"a huge heavy condensed headline on {len(c.headline)} line(s): " + ", then ".join(lines),
        f'one subline in light grey: "{c.subline}"' if c.subline else "",
        pills,
    ) if x)  # fmt: skip
    style_txt = (f"Style: dark navy-black background (#060b14) with a faint grid and subtle circuit lines, cinematic "
                 f"depth, crisp neon glow in {accent} with small white and teal accents. Premium, modern, high "
                 "contrast, readable at small size. Heavy condensed sans-serif headline, clean sans-serif labels. "
                 "Spell every text EXACTLY as given; no other words, no watermark, no fake UI text.")  # fmt: skip
    visual = _visual_prompt(c.visual, accent)
    if fmt == "youtube":
        pct = round(PHOTO_ZONE * 100)
        reserve = (f"RIGHT {pct}% OF THE WIDTH: keep it completely EMPTY — only dark background with a soft rim glow "
                   f"in {accent} and faint circuit lines. No person, no face, no text, no cards, no lines and no "
                   "graphics there (a photo of the presenter is placed there later).") if person else ""  # fmt: skip
        zone = f"the left {100 - pct}% of the width" if person else "the full width"
        return (f"Design a premium 16:9 YouTube thumbnail for a tech-education channel. All text and graphics stay "
                f"inside {zone}, with a comfortable margin from every edge.\n\n{style_txt}\n\n"
                f"LEFT PART (text): {text_block}" + (f"; along the bottom, {strip}" if strip else "")
                + f".\n\nNEXT TO THE TEXT (still outside the empty right zone): {visual}.\n\n{reserve}")  # fmt: skip
    reserve = ("BOTTOM 42% OF THE HEIGHT: keep it EMPTY — only dark background with a soft rim glow in "
               f"{accent}. No person, no text, no graphics there (a photo of the presenter is placed there later)."
               if person else "")  # fmt: skip
    return (f"Design a premium 9:16 vertical cover (YouTube Shorts / Reels) for a tech-education channel, with a "
            f"comfortable margin from every edge.\n\n{style_txt}\n\nTOP PART: {text_block}.\n\nBELOW IT: {visual}"
            + (f"; then {strip}" if strip else "") + f".\n\n{reserve}")  # fmt: skip


def _fit(img, w: int, h: int):
    """Scale to cover (w, h) and centre-crop."""
    from PIL import Image

    s = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def composite(design: bytes, fmt: str, person_png: Path | None, accent: str) -> bytes:
    """Final cover JPEG: the AI design at the output size + the real cut-out photo with a rim light."""
    from PIL import Image, ImageFilter

    W, H = OUT[fmt]  # noqa: N806
    canvas = _fit(Image.open(io.BytesIO(design)).convert("RGB"), W, H).convert("RGBA")
    if person_png is not None:
        person = Image.open(person_png).convert("RGBA")
        ph = round(H * (0.94 if fmt == "youtube" else 0.46))
        person = person.resize((round(person.width * ph / person.height), ph), Image.LANCZOS)
        if fmt == "youtube":
            # the photo starts at the reserved zone and may run off the right edge (never over the design)
            x = round(W * (1 - PHOTO_ZONE)) + 8
            person = person.crop((0, 0, min(person.width, W - x), person.height))
        else:
            x = (W - person.width) // 2
        y = H - person.height
        rgb = tuple(int(accent.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
        glow = Image.new("RGBA", person.size, (*rgb, 0))
        a = person.getchannel("A").filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(22))
        glow.putalpha(a.point(lambda v: int(v * 0.85)))
        canvas.alpha_composite(glow, (x, y))
        canvas.alpha_composite(person, (x, y))
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)  # YouTube: < 2 MB
    if buf.tell() > 1_900_000:
        buf = io.BytesIO()
        canvas.convert("RGB").save(buf, "JPEG", quality=82, optimize=True)
    return buf.getvalue()


ZONE_BUSY_MAX = (
    0.002  # strong-edge share allowed in the photo zone; measured: clean 0.0000–0.0004, cards crossing 0.0062
)
RETRY_NOTE = ("\n\nIMPORTANT: the previous attempt put cards or text into the right zone, where they would be "
              "hidden behind the presenter's photo. Keep the right zone EMPTY; make the diagram smaller and move it "
              "left if needed.")  # fmt: skip


def zone_busy(design: bytes, fmt: str) -> float:
    """How much drawn detail (strong edges: text, cards, logos) the model put where the photo will go."""
    from PIL import Image, ImageFilter

    W, H = OUT[fmt]  # noqa: N806
    img = _fit(Image.open(io.BytesIO(design)).convert("L"), W, H)
    box = (round(W * (1 - PHOTO_ZONE)), 0, W, H) if fmt == "youtube" else (0, round(H * 0.58), W, H)
    edges = img.crop(box).resize((320, 360)).filter(ImageFilter.FIND_EDGES)
    hist = edges.histogram()
    return sum(hist[90:]) / max(1, sum(hist))


def draw(
    c: Concept, fmt: str, project: Project, profile, person_png: Path | None
) -> tuple[bytes, float, str]:
    """Draw a cover; if graphics landed in the photo zone, redraw once and keep the cleaner design."""
    accent = style.accent(profile, project)
    prompt = image_prompt(c, fmt, accent, person_png is not None)
    best, cost, alias = None, 0.0, IMAGE_ALIAS
    for attempt in range(2 if person_png is not None else 1):
        img, res = llm.generate_image(IMAGE_ALIAS, prompt + (RETRY_NOTE if attempt else ""), size=CANVAS[fmt],
                                      timeout=300)  # fmt: skip
        cost, alias = cost + res.cost_usd, res.alias
        busy = zone_busy(img, fmt) if person_png is not None else 0.0
        log.info("cover %s attempt %d: photo-zone busy %.4f", fmt, attempt + 1, busy)
        if best is None or busy < best[1]:
            best = (img, busy)
        if busy <= ZONE_BUSY_MAX:
            break
    return composite(best[0], fmt, person_png, accent), cost, alias
