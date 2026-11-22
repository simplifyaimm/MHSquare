"""Active Style Profile access, versioning, canvas geometry and prompt/CSS rendering from the profile."""

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Project
from app.models.style import CoveredTopic, StyleProfile
from app.services.style import defaults as D

FIELDS = ["language", "brand", "voice_rules", "judge_rubric", "audience_default", "presets", "animation_style",
          "layout", "caption_formula", "reply_framework"]  # fmt: skip
THEMES_DIR = Path(__file__).resolve().parent.parent / "generation" / "themes"


def as_dict(p: StyleProfile) -> dict:
    return {"id": str(p.id), "version": p.version, "is_active": p.is_active, "note": p.note,
            "created_at": p.created_at.isoformat() if p.created_at else None, **{f: getattr(p, f) for f in FIELDS}}  # fmt: skip


def ensure_seeded(db: Session) -> None:
    if db.scalar(select(func.count()).select_from(StyleProfile)) == 0:
        db.add(
            StyleProfile(
                version=1, is_active=True, note="Seeded Simplify AI defaults (v2.1)", **D.default_profile()
            )
        )
    if db.scalar(select(func.count()).select_from(CoveredTopic)) == 0:
        for title, pillar, ctype, status in D.SEED_TOPICS:
            db.add(CoveredTopic(title=title, pillar=pillar, content_type=ctype, status=status))
    db.commit()


def active(db: Session) -> StyleProfile:
    p = db.scalar(select(StyleProfile).where(StyleProfile.is_active.is_(True)))
    if p is None:
        ensure_seeded(db)
        p = db.scalar(select(StyleProfile).where(StyleProfile.is_active.is_(True)))
    return p


def save_version(db: Session, data: dict, note: str = "") -> StyleProfile:
    """Every save creates a new version and activates it (old versions stay for history/rollback)."""
    if data.get("language", "en") != "en":
        raise ValueError("language is locked to 'en'")
    cur = active(db)
    merged = {f: data.get(f, getattr(cur, f)) for f in FIELDS}
    nxt = (db.scalar(select(func.max(StyleProfile.version))) or 0) + 1
    db.execute(update(StyleProfile).values(is_active=False))
    p = StyleProfile(version=nxt, is_active=True, note=note[:300], **merged)
    db.add(p)
    db.commit()
    return p


def activate(db: Session, version: int) -> StyleProfile:
    p = db.scalar(select(StyleProfile).where(StyleProfile.version == version))
    if p is None:
        raise ValueError("unknown version")
    db.execute(update(StyleProfile).values(is_active=False))
    p.is_active = True
    db.commit()
    return p


# ---------------------------------------------------------------- canvas / layout per content type


@dataclass
class Canvas:
    stage_w: int  # the animation stage (what scenes are authored on)
    stage_h: int
    export_w: int  # the final frame
    export_h: int
    offset_y: int  # where the stage sits inside the export frame (shorts 3:4)
    is_short: bool
    safe_zones: list[dict]  # areas to keep free of important content (stage coordinates)
    footer: bool
    facecam_circle: str | None  # composite | leave_space | None

    @property
    def label(self) -> str:
        return f"{self.stage_w}×{self.stage_h}"


def is_short(content_type: str | None) -> bool:
    return (content_type or "long_form") != "long_form"


def canvas(profile: StyleProfile, project: Project) -> Canvas:
    lay = profile.layout or {}
    footer = bool(lay.get("footer_on_animations", True)) and bool(
        (profile.animation_style or {}).get("footer", {}).get("enabled", True)
    )
    if is_short(project.content_type):
        ew, eh = lay.get("shorts_export", [1080, 1920])
        if lay.get("shorts_canvas_aspect", "3:4") == "9:16":
            sw, sh = ew, eh
        else:
            sw, sh = ew, round(ew * 4 / 3)
        return Canvas(sw, sh, ew, eh, (eh - sh) // 2, True, [], footer, None)
    w, h = (1920, 1080)
    sa = lay.get("facecam_safe_area", {"w_pct": 22, "h_pct": 22})
    zw, zh = round(w * sa["w_pct"] / 100), round(h * sa["h_pct"] / 100)
    zones = [{"name": "face-cam circle (bottom-right)", "x": w - zw, "y": h - zh, "w": zw, "h": zh}]
    return Canvas(w, h, w, h, 0, False, zones, footer, lay.get("facecam_circle", "composite"))


def accent(profile: StyleProfile, project: Project) -> str:
    pal = (profile.animation_style or {}).get("palette", {})
    return pal.get(project.pillar or "series", pal.get("series", "#48cae4"))


# ---------------------------------------------------------------- CSS + footer rendered from the profile


def theme_css(profile: StyleProfile, project: Project, theme: str, cv: Canvas) -> str:
    a = profile.animation_style or {}
    bg, pal, fonts = a.get("background", {}), a.get("palette", {}), a.get("fonts", {})
    head = (
        f":root{{--accent:{accent(profile, project)};--warn:{pal.get('warning', '#f4a261')};"
        f"--good:{pal.get('success', '#2dd4a7')};--growth:{pal.get('growth', '#e63946')};"
        f"--series:{pal.get('series', '#48cae4')};--tools:{pal.get('tools', '#9d4edd')};"
        f"--advice:{pal.get('advice', '#3a86ff')};--bg-from:{bg.get('from', '#0a1626')};--bg-to:{bg.get('to', '#060b14')};"
        f"--grid:{bg.get('grid_px', 54)}px;--grid-color:{bg.get('grid_color', 'rgba(72,202,228,.07)')};"
        f"--f-head:'{fonts.get('headline', 'Oswald')}',sans-serif;--f-body:'{fonts.get('body', 'Poppins')}',sans-serif;"
        f"--f-mono:'{fonts.get('mono', 'JetBrains Mono')}',monospace;"
        f"--f-code:'{fonts.get('code', 'Consolas')}','JetBrains Mono',monospace;"
        f"--stage-w:{cv.stage_w}px;--stage-h:{cv.stage_h}px;--safe-x:{72 if cv.is_short else 120}px;"
        f"--safe-top:{80 if cv.is_short else 90}px}}\n"
    )
    name = "story" if theme == "story" else "brand"
    return (
        head
        + (THEMES_DIR / f"{name}.css").read_text("utf-8")
        + "\n"
        + (THEMES_DIR / "footer.css").read_text("utf-8")
    )


def footer_html(profile: StyleProfile, cv: Canvas) -> str:
    if not cv.footer:
        return ""
    f = (profile.animation_style or {}).get("footer", {})
    import html as h

    # long-form: the right-hand handle ends before the face-cam circle zone so they never collide
    right_pad = (cv.safe_zones[0]["w"] + 24) if cv.safe_zones else (40 if cv.is_short else 60)
    return (f'<div class="mh-footer" style="right:{right_pad}px"><span class="l">{h.escape(f.get("left", ""))}</span>'
            f'<span class="r">{h.escape(f.get("right", ""))}</span></div>')  # fmt: skip


# ---------------------------------------------------------------- prompt text from the profile


def preset(profile: StyleProfile, content_type: str | None) -> dict:
    return (profile.presets or {}).get(content_type or "long_form", {})


def planner_brief(profile: StyleProfile, project: Project, has_facecam: bool) -> str:
    b, ps = profile.brand or {}, preset(profile, project.content_type)
    cv = canvas(profile, project)
    pillar = (b.get("pillars", {}).get(project.pillar or "", {}) or {}).get("label", project.pillar or "—")
    lines = [
        f"Brand: {b.get('name')} — “{b.get('tagline')}” ({b.get('handle')}). Language: English only.",
        f"Content type: {ps.get('label', project.content_type)} · pillar: {pillar} · audience: {project.audience_level}"
        f" · topic kind: {project.topic_kind or '—'}",
        f"Preset rules: {ps.get('rules', '')}",
        "Visual rules: " + " ".join((profile.animation_style or {}).get("rules", [])),
    ]
    if cv.is_short:
        lines.append(f"SHORTS layout: animation canvas {cv.label} fills the frame; no face during animation segments."
                     + (" a_roll only for genuine talking-to-camera moments." if has_facecam else ""))  # fmt: skip
    else:
        lines.append("LONG-FORM layout: the full frame always shows visuals. NEVER plan a_roll shots — the face cam is a"
                     " persistent small circle bottom-right, not a shot. Keep the bottom-right 22%×22% free of"
                     " important content in every visual.")  # fmt: skip
    return "\n".join(lines)


def animation_rules(profile: StyleProfile, project: Project, theme: str, cv: Canvas) -> str:
    a = profile.animation_style or {}
    pal = a.get("palette", {})
    zones = (
        "; ".join(f"{z['name']}: x≥{z['x']}, y≥{z['y']} ({z['w']}×{z['h']})" for z in cv.safe_zones) or "none"
    )
    entry = a.get("entry", {}).get("stagger_total_s", [4, 6])
    return "\n".join([
        f"## Brand style (Style Profile v{profile.version}) — follow exactly",
        f"- Stage: {cv.stage_w}×{cv.stage_h} px, fixed, absolute positioning. "
        + ("Vertical SHORTS canvas: stack content vertically, big type, full-canvas animation, no face." if cv.is_short
           else "Landscape long-form."),  # fmt: skip
        f"- Accent colour for this video (pillar): {accent(profile, project)} (CSS var --accent). Warnings "
        f"{pal.get('warning')} (--warn), success {pal.get('success')} (--good). Other pillar colours: "
        f"series {pal.get('series')}, growth {pal.get('growth')}, tools {pal.get('tools')}, advice {pal.get('advice')}."
        " Neutrals: white/greys/navy only. NO other colours.",
        f"- Fonts: headlines {a.get('fonts', {}).get('headline')} (var(--f-head)), body {a.get('fonts', {}).get('body')}"
        f" (var(--f-body)), meta labels {a.get('fonts', {}).get('mono')} (var(--f-mono)), code "
        f"{a.get('fonts', {}).get('code')} (var(--f-code)). No other fonts.",
        f"- Entry: staggered over the first {entry[0]}–{entry[1]} s, each element landing on its spoken word, then HOLD"
        " the final frame.",
        *[f"- {r}" for r in a.get("rules", [])],
        f"- Keep these zones completely EMPTY (checked automatically): {zones}.",
        ("- A brand footer is added automatically along the bottom edge: keep the bottom 80 px free and never draw"
         " your own footer, handle or watermark." if cv.footer else "- No footer on this shot."),  # fmt: skip
        "- Theme: " + ("story (flat cartoon kit)." if theme == "story" else "brand (blueprint navy kit)."),
    ])  # fmt: skip


def watermark_text(profile: StyleProfile) -> str:
    return (profile.brand or {}).get("watermark") or (profile.brand or {}).get("handle", "")
