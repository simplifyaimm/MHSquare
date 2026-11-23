"""Creative direction: before planning, decide HOW this script is best explained on screen.

One small model call reads the script (plus content type, audience, pillar, reach/authority) and picks, from the
brand's enabled explanation modes and the scene-template library, the visual approach that makes THIS script
easiest to understand and most watchable. The creator can review and edit it; the planner and the template
converter follow it. No video starts on a default theme.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.llm import client as llm
from app.models import Project
from app.models.style import StyleProfile
from app.services import routing, style
from app.services.generation import templates as T
from app.services.planner import extract_json


class DirectionError(ValueError):
    pass


class Direction(BaseModel):
    primary_mode: str
    modes: list[str] = Field(min_length=1)  # trimmed to the brand cap in normalise()
    templates: list[str] = Field(default_factory=list)  # preferred, most-used first
    persona: str = Field(
        "", max_length=60
    )  # the carried real-world example ("Robin, a Java developer"), or ""
    hook: str = Field("", max_length=240)  # the opening visual idea (first 15 s)
    pacing: Literal["calm", "medium", "fast"] = "medium"
    density: Literal["sparse", "balanced", "rich"] = "balanced"
    reasons: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)

    @field_validator("reasons", "avoid")
    @classmethod
    def _short(cls, v: list[str]) -> list[str]:
        return [s.strip()[:200] for s in v if s and s.strip()][:5]


SYSTEM = """You are the creative director of a YouTube explainer channel. Before any visuals are planned, you decide
HOW a script is best explained on screen so the viewer understands it easily and keeps watching (retention).

You choose from the channel's EXPLANATION MODES (visual languages) and its SCENE TEMPLATES (ready-made animated
layouts). Judge from the script itself: what kind of content is it (system architecture, step-by-step protocol,
career advice, concept/maths, a story with a person, a tool demo…)? Who is the audience? What would make the hard
part click? One video = one visual language: pick ONE primary mode that fits most of the script and at most
{max_modes} modes in total; add a secondary mode only for a clearly different kind of moment.

Explanation modes (the only ones allowed):
{modes}

Scene templates (prefer the ones that fit the content; order by how much they should be used):
{templates}

Reply with ONLY JSON:
{{"primary_mode": "tech", "modes": ["tech", "scenario"], "templates": ["compare", "steps", "scenario"],
 "persona": "Robin, a Java developer (carried example) — or empty if the script has none",
 "hook": "the opening visual in one sentence (what is on screen in the first 15 seconds)",
 "pacing": "calm|medium|fast", "density": "sparse|balanced|rich",
 "reasons": ["why this approach fits THIS script (2–4 short bullets)"],
 "avoid": ["visual approaches that would confuse or bore this audience"]}}"""


def _system(profile: StyleProfile) -> str:
    a = profile.animation_style or {}
    desc = a.get("explain_modes") or {}
    modes = "\n".join(f"- {m}: {desc.get(m, '')}" for m in style.enabled_modes(profile))
    tpls = "\n".join(f"- {t.name}: {t.use}" for t in T.TEMPLATES.values())
    return SYSTEM.format(max_modes=int(a.get("max_modes_per_video", 3)), modes=modes, templates=tpls)


def _user(p: Project, profile: StyleProfile, script: str) -> str:
    ps = style.preset(profile, p.content_type)
    pillar = ((profile.brand or {}).get("pillars", {}).get(p.pillar or "", {}) or {}).get(
        "label", p.pillar or "—"
    )
    return (f"Content type: {ps.get('label', p.content_type)} · pillar: {pillar} · audience: {p.audience_level} · "
            f"topic kind: {p.topic_kind or '—'} (reach = broad audience, authority = depth)\n"
            f"Topic: {p.topic or p.name}\nCreator's style notes: {(p.style_notes or '').strip() or '(none)'}\n\n"
            f"## Script\n{script.strip()[:20000]}")  # fmt: skip


def normalise(data: dict, profile: StyleProfile) -> Direction:
    """Validate the model's answer and keep it inside what the brand allows."""
    enabled = style.enabled_modes(profile)
    cap = int((profile.animation_style or {}).get("max_modes_per_video", 3))
    try:
        d = Direction.model_validate(data)
    except ValidationError as ex:
        raise DirectionError(str(ex)[:500]) from ex
    modes = [m for m in dict.fromkeys(d.modes) if m in enabled]
    primary = d.primary_mode if d.primary_mode in enabled else (modes[0] if modes else enabled[0])
    modes = [primary, *[m for m in modes if m != primary]][:cap]
    tpls = [t for t in dict.fromkeys(d.templates) if t in T.TEMPLATES][:6]
    return d.model_copy(update={"primary_mode": primary, "modes": modes, "templates": tpls})


def recommend(p: Project, profile: StyleProfile, script: str) -> dict:
    if not script.strip():
        raise DirectionError("Upload the script first")
    cfg = routing.load()
    text, res = llm.chat(cfg.planner.model, [{"role": "user", "content": _user(p, profile, script)}],
                         system=_system(profile), fallbacks=cfg.planner.fallbacks, max_tokens=8000,
                         temperature=0.3)  # fmt: skip
    try:
        d = normalise(extract_json(text), profile)
    except (ValueError, KeyError) as ex:
        raise DirectionError(f"could not read the recommendation: {ex}") from ex
    return {**d.model_dump(), "source": "ai", "model": res.alias, "cost_usd": round(res.cost_usd, 4),
            "at": datetime.now(UTC).isoformat(timespec="seconds"), "style_profile_version": profile.version}  # fmt: skip


def edit(current: dict | None, changes: dict, profile: StyleProfile) -> dict:
    """Creator edits (mode/template choices etc.) — validated the same way, marked as edited."""
    base = {k: v for k, v in (current or {}).items() if k in Direction.model_fields}
    if not base:
        enabled = style.enabled_modes(profile)
        base = {"primary_mode": enabled[0], "modes": [enabled[0]]}
    d = normalise({**base, **{k: v for k, v in changes.items() if k in Direction.model_fields}}, profile)
    return {**(current or {}), **d.model_dump(), "source": "edited",
            "at": datetime.now(UTC).isoformat(timespec="seconds")}  # fmt: skip


def brief(direction: dict | None) -> str:
    """Planner / converter instructions from the approved direction."""
    if not direction:
        return ""
    lines = [
        "## CREATIVE DIRECTION (approved for this video — follow it)",
        f"- Primary explanation mode: {direction.get('primary_mode')}; allowed modes: "
        f"{', '.join(direction.get('modes') or [])} (no others).",
    ]
    if direction.get("templates"):
        lines.append(f"- Preferred scene templates, most-used first: {', '.join(direction['templates'])}.")
    if direction.get("persona"):
        lines.append(
            f"- Carried real-world example: {direction['persona']} (show with the scenario mode/template)."
        )
    if direction.get("hook"):
        lines.append(f"- Opening hook visual (first 15 s): {direction['hook']}")
    lines.append(
        f"- Pacing: {direction.get('pacing', 'medium')}; visual density: {direction.get('density', 'balanced')}."
    )
    if direction.get("avoid"):
        lines.append(f"- Avoid: {'; '.join(direction['avoid'])}.")
    return "\n".join(lines)
