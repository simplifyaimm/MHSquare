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
        person = strip_frame_ring(Image.open(person_png).convert("RGBA"))
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


# ---------------------------------------------------------------- Shorts / Reels covers (the creator's reference)
# 9:16: a text column on the left (pill, 2–3 line headline with the key phrase on a red block, a subline, 3–4 feature
# cards), the creator's photo LARGE on the right, a topic pipeline strip at the bottom, then handle + tagline.

SHORT_PHOTO_BOX = (0.52, 0.15, 1.00, 0.79)  # x0, y0, x1, y1 (fractions): kept empty for the creator's photo
CARD_COLOURS = ("amber", "cyan", "green", "violet")


def short_cover_prompt(cover: dict, eyebrow: str, part: str, accent: str, handle: str, tagline: str) -> str:
    headline = cover.get("headline") or ""
    hl = cover.get("highlight") or ""
    cards = []
    for k, pt in enumerate((cover.get("points") or [])[:4]):
        title, _, line = str(pt).partition(":")
        card = f'"{title.strip()}"' + (f' with one small line "{line.strip()}"' if line.strip() else "")
        cards.append(f"{CARD_COLOURS[k]}-outlined glowing card with a matching line icon: {card}")
    x0, y0, x1, y1 = (round(v * 100) for v in SHORT_PHOTO_BOX)
    return (
        "Design a premium vertical 9:16 cover (YouTube Shorts / Instagram Reels) for a developer-education channel. "
        "Dark near-black background with subtle red light and depth, crisp neon accents, premium and modern, very "
        "high contrast, readable as a small thumbnail. Spell every text EXACTLY as given; no other words, no "
        f'watermark.\n\nTOP: a red-outlined rounded pill at the top-left with a red dot: "{eyebrow}"'
        + (f', and a bold solid {accent} label "{part}" next to it' if part else "")
        + f'; at the top-right in clean white text: "{handle}".\n\n'
        f"LEFT COLUMN (the left 46% of the width; no text may run past it — wrap or shorten lines instead): a huge heavy condensed headline, slightly italic, on 2–3 lines: "
        f'"{headline}" — the words "{hl}" sit on a solid {accent} block (white text) with a short red underline '
        f'swoosh below; then a subline with a thick {accent} bar on its left: "{cover.get("subline", "")}" (the last '
        f"words in {accent}); then {len(cards)} stacked rounded cards, each with a thin coloured outline and glow: "
        + "; ".join(cards)
        + ".\n\nBOTTOM (full width, near the bottom): a rounded dark strip of 4–5 small icon tiles joined by red "
        f"arrows, illustrating this idea: {cover.get('visual', '')}. Each tile has a 1–2 word label (a concrete "
        "noun such as Data, Model, API, Java app — never a phrase from this description, never words like 'split "
        f"screen'); the last tile is highlighted in {accent}.\n"
        f'FOOTER: a thin red line, then "{handle}" at the bottom-left and "{tagline}" at the bottom-right '
        f"(the second half of the tagline in {accent}).\n\n"
        f"RIGHT SIDE, from {x0}% to {x1}% of the width and {y0}% to {y1}% of the height: keep it EMPTY — only the dark "
        f"background with a soft {accent} glow. No person, no text, no cards there (the presenter's photo is placed "
        "there later). A small floating UI card may overlap only the very top-right corner, above that zone."
    )


def _busy_box(design: bytes, size: tuple[int, int], box: tuple[float, float, float, float]) -> float:
    from PIL import Image, ImageFilter

    W, H = size  # noqa: N806
    img = _fit(Image.open(io.BytesIO(design)).convert("L"), W, H)
    crop = img.crop((round(W * box[0]), round(H * box[1]), round(W * box[2]), round(H * box[3])))
    hist = crop.resize((320, 360)).filter(ImageFilter.FIND_EDGES).histogram()
    return sum(hist[90:]) / max(1, sum(hist))


def strip_frame_ring(person):
    """Remove a glowing frame ring the source photo had (a blue arc with a white core along the cut-out's edge):
    clearly blue pixels anywhere, and near-white ones only in a thin band along the edge. Skin and a grey T-shirt are
    never strongly blue, so the person is untouched."""
    from PIL import Image, ImageChops, ImageFilter

    r, g, b, a = person.split()
    # blue glow and the navy photo background inside the ring (skin is red-dominant, a grey T-shirt is neutral)
    blue = ImageChops.multiply(ImageChops.subtract(b, r).point(lambda v: 255 if v > 25 else 0),
                               b.point(lambda v: 255 if v > 50 else 0))  # fmt: skip
    # the ring's bright white core, only in a thin band along the cut-out edge
    edge_band = ImageChops.subtract(a, a.filter(ImageFilter.MinFilter(11))).point(
        lambda v: 255 if v > 0 else 0
    )
    bright = ImageChops.multiply(ImageChops.multiply(r.point(lambda v: 255 if v > 228 else 0),
                                                     g.point(lambda v: 255 if v > 228 else 0)),
                                 b.point(lambda v: 255 if v > 228 else 0))  # fmt: skip
    kill = ImageChops.lighter(blue, ImageChops.multiply(bright, edge_band)).filter(ImageFilter.MaxFilter(3))
    a = ImageChops.subtract(a, kill)
    # a soft fade over the bottom quarter: the person blends into the design (and any ring trace disappears)
    w, h = person.size
    fade = Image.linear_gradient("L").rotate(180).resize((w, h))  # 255 at the top → 0 at the bottom
    fade = fade.point(lambda v: 255 if v > 64 else int(v * 255 / 64))
    person.putalpha(ImageChops.multiply(a, fade))
    return person


def compose_short_cover(design: bytes, person_png: Path | None, accent: str) -> bytes:
    """The AI design at 1080×1920 + the creator's real photo, large on the right, standing behind the bottom strip."""
    from PIL import Image, ImageChops, ImageFilter

    W, H = OUT["short"]  # noqa: N806
    canvas = _fit(Image.open(io.BytesIO(design)).convert("RGB"), W, H).convert("RGBA")
    if person_png is not None:
        x0, y0, x1, y1 = SHORT_PHOTO_BOX
        person = strip_frame_ring(Image.open(person_png).convert("RGBA"))
        ph = round(H * (y1 - y0 + 0.03))
        person = person.resize((round(person.width * ph / person.height), ph), Image.LANCZOS)
        # the face (the cut-out's centre) at ~78% of the width, as in the reference; whatever of a wide cut-out
        # (spread arms) would reach over the cards on the left is trimmed with a soft edge
        x = round(W * 0.78 - person.width / 2)
        left = round(W * (x0 - 0.01))
        if x < left:
            person = person.crop((left - x, 0, person.width, person.height))
            x = left
            fade_w = round(W * 0.05)
            edge = (
                Image.linear_gradient("L").rotate(90).resize((fade_w, person.height))
            )  # 0 → 255, left → right
            mask = Image.new("L", person.size, 255)
            mask.paste(edge, (0, 0))
            person.putalpha(ImageChops.multiply(person.getchannel("A"), mask))
        y = round(H * y1) - ph
        person = person.crop((0, 0, min(person.width, W - x), person.height))
        rgb = tuple(int(accent.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
        glow = Image.new("RGBA", person.size, (*rgb, 0))
        a = person.getchannel("A").filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(26))
        glow.putalpha(a.point(lambda v: int(v * 0.7)))
        canvas.alpha_composite(glow, (x, y))
        canvas.alpha_composite(person, (x, y))
    buf = io.BytesIO()
    canvas.convert("RGB").save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()


def draw_short_cover(cover: dict, eyebrow: str, part: str, project: Project, profile,
                     person_png: Path | None) -> tuple[bytes, float, str]:  # fmt: skip
    accent = style.accent(profile, project)
    b = profile.brand or {}
    prompt = short_cover_prompt(cover, eyebrow, part, accent, b.get("handle", ""), b.get("tagline", ""))
    best, cost, alias = None, 0.0, IMAGE_ALIAS
    for attempt in range(2 if person_png is not None else 1):
        img, res = llm.generate_image(IMAGE_ALIAS, prompt + (RETRY_NOTE if attempt else ""), size=CANVAS["short"],
                                      timeout=300)  # fmt: skip
        cost, alias = cost + res.cost_usd, res.alias
        busy = _busy_box(img, OUT["short"], SHORT_PHOTO_BOX) if person_png is not None else 0.0
        log.info("short cover attempt %d: photo-zone busy %.4f", attempt + 1, busy)
        if best is None or busy < best[1]:
            best = (img, busy)
        if busy <= ZONE_BUSY_MAX:
            break
    return compose_short_cover(best[0], person_png, accent), cost, alias
