"""Scene templates — hand-designed, brand-true animated layouts.

The model (normally the planner, once for the whole video) only fills a small JSON of CONTENT; the HTML/CSS/GSAP is
generated here deterministically. No per-shot AI call, identical quality on every video, readable sizes and a
balanced composition by construction (the content box avoids the footer and the face-cam zone).

A templated shot's prompt is `@template NAME` followed by the JSON params (editable in the UI).
Every element may carry `on`: a word or short phrase from the narration — it appears when that is spoken.
"""

from __future__ import annotations

import html as _html
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from app.services.generation import scene as S

Status = Literal["none", "good", "bad", "warn"]
_STATUS_ICON = {"good": "circle-check", "bad": "circle-x", "warn": "triangle-alert"}
_STATUS_CLASS = {
    "good": "ent-good",
    "bad": "ent-growth",
    "warn": "ent-warn",
    "none": "",
}


class TemplateError(ValueError):
    pass


# ---------------------------------------------------------------- params (what the model fills in)


class _P(BaseModel):
    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def _lenient(cls, data: object) -> object:
        """Models overshoot limits a little (a 4th item, a long badge): trim to the design limits instead of
        throwing the whole scene away. Required fields, types and enums are still strict."""
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for name, f in cls.model_fields.items():
            key = f.alias if f.alias and f.alias in out else name
            v = out.get(key)
            mx = next(
                (m.max_length for m in f.metadata if getattr(m, "max_length", None)),
                None,
            )
            if mx is None or v is None:
                continue
            if isinstance(v, str) and len(v) > mx:
                out[key] = v[: mx - 1].rstrip(" ,;:-") + "…"
            elif isinstance(v, list) and len(v) > mx:
                out[key] = v[:mx]
        return out


class _Named(_P):
    """Shown thing: needs its own label, or a vocabulary `entity` that supplies it."""

    @model_validator(mode="after")
    def _label_or_entity(self):
        if not (getattr(self, "label", "") or getattr(self, "entity", "")):
            raise ValueError("needs a label or an entity id")
        return self


class Head(_P):
    eyebrow: str = Field("", max_length=32)
    title: str = Field("", max_length=48)


class Item(_P):
    text: str = Field(min_length=1, max_length=40)
    icon: str = ""
    status: Status = "none"
    on: str = ""


class Side(_Named):
    label: str = Field("", max_length=26)
    sub: str = Field("", max_length=40)
    icon: str = ""
    entity: str = ""
    items: list[Item] = Field(default_factory=list, max_length=4)
    on: str = ""


class Compare(Head):
    parent: str = Field(
        "", max_length=20
    )  # optional thing that splits into the two sides ("AI")
    left: Side
    right: Side
    winner: Literal["left", "right", "none"] = "none"
    verdict: str = Field("", max_length=44)
    verdict_on: str = ""


class Step(_Named):
    label: str = Field("", max_length=24)
    sub: str = Field("", max_length=36)
    icon: str = ""
    entity: str = ""
    on: str = ""


class Steps(Head):
    steps: list[Step] = Field(min_length=2, max_length=5)
    active: int | None = Field(None, ge=1, le=5)  # 1-based step highlighted at the end
    active_on: str = ""


class Spoke(_Named):
    label: str = Field("", max_length=22)
    sub: str = Field("", max_length=30)
    icon: str = ""
    entity: str = ""
    status: Status = "none"
    on: str = ""


class Hub(Head):
    center: Spoke
    spokes: list[Spoke] = Field(min_length=2, max_length=6)


class Actor(_Named):
    label: str = Field("", max_length=18)
    icon: str = ""
    entity: str = ""


class Message(_P):
    src: int = Field(ge=1, le=4, alias="from")
    to: int = Field(ge=1, le=4)
    label: str = Field(min_length=1, max_length=32)
    kind: Literal["request", "response", "error"] = "request"
    on: str = ""
    model_config = {"extra": "forbid", "populate_by_name": True}


class Flow(Head):
    actors: list[Actor] = Field(min_length=2, max_length=4)
    messages: list[Message] = Field(min_length=1, max_length=6)

    @model_validator(mode="before")
    @classmethod
    def _one_based(cls, data: object) -> object:
        """Actors are numbered from 1; a model that counts from 0 is shifted instead of rejected."""
        if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
            return data
        msgs = [m for m in data["messages"] if isinstance(m, dict)]
        if any(m.get("from") == 0 or m.get("to") == 0 for m in msgs):
            data = {**data, "messages": [
                {**m, "from": int(m.get("from", 0)) + 1, "to": int(m.get("to", 0)) + 1} for m in msgs
            ]}  # fmt: skip
        return data


class Persona(_P):
    name: str = Field("", max_length=20)
    role: str = Field("", max_length=26)
    icon: str = "user-round"
    entity: str = ""


class Scenario(Head):
    persona: Persona
    say: str = Field("", max_length=64)
    say_on: str = ""
    items: list[Spoke] = Field(default_factory=list, max_length=3)
    badge: str = Field("", max_length=28)
    badge_status: Status = "good"
    badge_on: str = ""


class Highlight(_P):
    lines: list[int] = Field(min_length=1, max_length=6)
    kind: Literal["hl", "good", "bad"] = "hl"
    on: str = ""


class Code(Head):
    filename: str = Field(min_length=1, max_length=40)
    lang: Literal[
        "java",
        "python",
        "javascript",
        "typescript",
        "yaml",
        "json",
        "bash",
        "sql",
        "text",
    ] = "text"
    lines: list[str] = Field(min_length=1, max_length=14)
    highlights: list[Highlight] = Field(default_factory=list, max_length=3)
    callout: str = Field("", max_length=40)
    callout_line: int = Field(0, ge=0, le=14)
    callout_on: str = ""


class Stat(Head):
    value: str = Field(min_length=1, max_length=12)
    label: str = Field(min_length=1, max_length=40)
    sub: str = Field("", max_length=64)
    icon: str = ""
    before: str = Field(
        "", max_length=12
    )  # optional old value shown crossed out → value
    before_label: str = Field("", max_length=24)
    on: str = ""


class Point(_P):
    label: str = Field(min_length=1, max_length=20)
    sub: str = Field("", max_length=28)
    on: str = ""


class Timeline(Head):
    points: list[Point] = Field(min_length=3, max_length=6)
    current: int | None = Field(None, ge=1, le=6)


class Checklist(Head):
    items: list[Item] = Field(min_length=2, max_length=6)


class Table(Head):
    columns: list[str] = Field(min_length=2, max_length=4)
    rows: list[list[str]] = Field(min_length=1, max_length=6)
    highlight_row: int | None = Field(None, ge=1, le=6)
    highlight_on: str = ""


# ---------------------------------------------------------------- build context + helpers


@dataclass
class Ctx:
    w: int
    h: int
    short: bool
    zones: list[dict]
    duration: float  # seconds
    words: list[dict]  # [{"w": "Kafka", "t": 1.23}] relative to the shot
    entities: dict[str, dict] = field(
        default_factory=dict
    )  # vocabulary id → {label, icon, color, note}
    icons: frozenset[str] = frozenset()

    def box(self, header: bool) -> tuple[int, int, int, int]:
        """Content box: inside the safe margins, under the header, above the footer and the face-cam zone."""
        sx = 72 if self.short else 120
        top = (80 if self.short else 90) + (190 if header else 0)
        bottom = self.h - (110 if self.short else 96)
        for z in self.zones:
            if z["y"] > self.h * 0.5:
                bottom = min(bottom, z["y"] - 24)
        return sx, top, self.w - 2 * sx, bottom - top


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


class Beats:
    """Collects timed animation steps; `on` phrases resolve to the moment they are spoken."""

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.words = [_norm(w["w"]) for w in ctx.words]
        self.lines: list[str] = []
        self.auto = 0

    def at(self, phrase: str, k: int, n: int) -> float:
        d = self.ctx.duration
        tokens = [t for t in (_norm(x) for x in phrase.split()) if t]
        if tokens:
            for i in range(len(self.words) - len(tokens) + 1):
                if all(
                    self.words[i + j].startswith(tok) for j, tok in enumerate(tokens)
                ):
                    return round(
                        max(0.1, min(d - 1.0, self.ctx.words[i]["t"] - 0.12)), 2
                    )
        span = max(
            0.6, min(d * 0.6, d - 1.4)
        )  # unmatched: stagger across the first 60 % of the shot
        return round(0.25 + span * k / max(1, n), 2)

    def add(self, js: str) -> None:
        self.lines.append(js)

    def enter(self, sel: str, t: float, kind: str = "up") -> None:
        frm = {
            "up": "{opacity:0, y:28, filter:'blur(6px)'}",
            "pop": "{opacity:0, scale:.82, filter:'blur(6px)'}",
            "left": "{opacity:0, x:-40, filter:'blur(6px)'}",
            "fade": "{opacity:0}",
        }[kind]
        self.add(
            f"tl.from('{sel}', {{...{frm}, duration:.65, ease:'power3.out'}}, {t});"
        )

    def draw(self, sel: str, t: float, dur: float = 0.7) -> None:
        self.add(
            f"tl.from('{sel}', {{drawSVG:'0%', duration:{dur}, ease:'power2.inOut'}}, {t});"
        )

    def focus(self, sel: str, t: float) -> None:
        self.add(
            f"tl.to('{sel} > .hlr', {{opacity:1, duration:.45, ease:'power2.out'}}, {t});"
        )

    def dim(self, sel: str, t: float) -> None:
        self.add(f"tl.to('{sel}', {{opacity:.38, duration:.45}}, {t});")

    def js(self, drift: bool = True) -> str:
        d = self.ctx.duration
        head = f"const tl = gsap.timeline(); window.__scene = tl;\nconst D = {d:.3f};\n"
        cam = (
            "tl.fromTo('#cam', {scale:1}, {scale:1.025, duration:D, ease:'none'}, 0);\n"
            if drift
            else ""
        )
        return head + cam + "\n".join(self.lines) + "\ntl.set({}, {}, D);"


def e(s: str) -> str:
    return _html.escape(s or "", quote=True)


def _icon(ctx: Ctx, name: str, default: str = "sparkles") -> str:
    name = (name or "").strip()
    if ctx.icons and name not in ctx.icons:
        name = default if default in ctx.icons else sorted(ctx.icons)[0]
    return f'<i data-icon="{e(name or default)}"></i>'


def _resolve(ctx: Ctx, label: str, icon: str, entity: str) -> tuple[str, str, str, str]:
    """(label, icon html, colour class, note) — a vocabulary entity wins, so it looks identical in every shot."""
    ent = ctx.entities.get(entity or "") or {}
    lab = label or ent.get("label", "")
    ic = icon or ent.get("icon", "")
    cls = (
        f"ent-{ent['color']}"
        if ent.get("color") and ent.get("color") != "accent"
        else ""
    )
    return lab, _icon(ctx, ic), cls, ent.get("note", "")


def _header(p: Head) -> str:
    if not (p.title or p.eyebrow):
        return ""
    eb = f'<div class="mh-eyebrow">{e(p.eyebrow)}</div>' if p.eyebrow else ""
    tt = f'<div class="mh-title">{e(p.title)}</div>' if p.title else ""
    return f'<div class="mh-header" id="hd">{eb}{tt}</div>'


def _wrap(
    ctx: Ctx, p: Head, inner: str, *, center: bool = True, extra: str = ""
) -> str:
    x, y, w, h = ctx.box(bool(p.title or p.eyebrow))
    align = "display:flex;align-items:center;justify-content:center;" if center else ""
    return (
        '<div class="mh-bg"></div><div class="mh-glow" style="left:'
        f'{ctx.w // 2 - 450}px;top:{ctx.h // 2 - 450}px"></div>'
        f'<div id="cam" style="position:absolute;inset:0;transform-origin:50% 45%">{_header(p)}'
        f'<div id="ct" style="position:absolute;left:{x}px;top:{y}px;width:{w}px;height:{h}px;{align}">'
        f"{inner}</div>{extra}</div>"
    )


CSS = """
.tp-card{position:relative;box-sizing:border-box;background:linear-gradient(180deg,#132036,#0a1626);border:1px solid #1b2a40;
  border-radius:18px;box-shadow:0 20px 60px rgba(0,0,0,.45)}
.tp-card > .hlr{position:absolute;inset:-2px;border-radius:inherit;border:2px solid var(--accent);opacity:0;
  pointer-events:none;box-shadow:0 0 0 1px var(--accent),0 0 46px color-mix(in srgb,var(--accent) 40%,transparent)}
.tp-ic{display:flex;align-items:center;justify-content:center;width:84px;height:84px;border-radius:20px;flex:none;
  background:color-mix(in srgb,var(--accent) 14%,#0a1626);border:1.5px solid color-mix(in srgb,var(--accent) 55%,transparent)}
.tp-ic [data-icon]{font-size:46px}
.tp-label{font:600 40px/1.05 var(--f-head);text-transform:uppercase;letter-spacing:.01em;color:#fff;text-wrap:balance}
.tp-sub{font:400 24px/1.35 var(--f-body);color:#8a9ab3;text-wrap:balance}
.tp-num{font:500 22px/1 var(--f-mono);letter-spacing:.18em;color:var(--accent)}
.tp-row{display:flex;align-items:center;gap:22px}
.tp-items{display:flex;flex-direction:column;gap:14px;margin-top:26px}
.tp-item{position:relative;display:flex;align-items:center;gap:16px;padding:16px 20px;border-radius:12px;
  background:#0e1a2c;border:1px solid #1b2a40;font:500 26px/1.25 var(--f-body);color:#dbe4f0}
.tp-item [data-icon]{font-size:30px;flex:none}
.tp-vs{width:96px;height:96px;border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;
  font:700 34px/1 var(--f-head);color:#fff;background:#0a1626;border:2px solid var(--accent);
  box-shadow:0 0 34px color-mix(in srgb,var(--accent) 45%,transparent)}
.tp-verdict{position:absolute;left:50%;bottom:0;transform:translateX(-50%);font-size:28px;padding:16px 30px}
.tp-arrow [data-icon]{font-size:44px;color:#5b6b85}
.tp-node{position:absolute;transform:translate(-50%,-50%);display:flex;flex-direction:column;align-items:center;
  gap:12px;padding:22px 26px;min-width:220px;text-align:center}
.tp-node .tp-label{font-size:32px}
.tp-node.center{padding:30px 38px;min-width:280px}.tp-node.center .tp-label{font-size:44px}
.tp-node.center .tp-ic{width:110px;height:110px}.tp-node.center .tp-ic [data-icon]{font-size:62px}
.tp-led{position:absolute;top:14px;right:14px}
.tp-actor{position:absolute;transform:translateX(-50%);display:flex;flex-direction:column;align-items:center;gap:12px}
.tp-actor .tp-label{font-size:38px}
.tp-actor .tp-ic [data-icon]{font-size:76px}
.tp-msg{position:absolute;transform:translate(-50%,-100%);padding-bottom:10px;white-space:nowrap;
  font:500 32px/1 var(--f-body);color:#e8eef7;padding-bottom:14px}
.tp-msg.response{color:#a9b6c9}.tp-msg.error{color:var(--growth)}
svg.tp-svg{position:absolute;left:0;top:0;overflow:visible;pointer-events:none}
svg.tp-svg .w{fill:none;stroke:color-mix(in srgb,var(--accent) 70%,transparent);stroke-width:4}
svg.tp-svg .w.response{stroke:#5b6b85}svg.tp-svg .w.error{stroke:var(--growth)}
svg.tp-svg .life{stroke:#24364f;stroke-width:3}
svg.tp-svg .head{fill:color-mix(in srgb,var(--accent) 80%,transparent)}svg.tp-svg .head.response{fill:#5b6b85}
svg.tp-svg .head.error{fill:var(--growth)}
svg.tp-svg .dot{fill:var(--accent);filter:drop-shadow(0 0 8px var(--accent))}
.tp-persona{position:absolute;transform:translate(-50%,-50%);display:flex;flex-direction:column;align-items:center;gap:16px}
.tp-persona .ring{width:200px;height:200px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  background:radial-gradient(circle at 50% 40%,#132036,#0a1626 70%);border:3px solid var(--accent);
  box-shadow:0 0 0 7px color-mix(in srgb,var(--accent) 14%,transparent),0 0 50px color-mix(in srgb,var(--accent) 55%,transparent)}
.tp-persona .ring [data-icon]{font-size:104px;color:#e8eef7}
.tp-persona b{font:600 36px/1 var(--f-head);text-transform:uppercase;letter-spacing:.04em;color:#fff}
.tp-persona small{font:500 22px/1 var(--f-mono);letter-spacing:.14em;text-transform:uppercase;color:var(--accent)}
.tp-say{position:absolute;max-width:640px;padding:20px 28px;border-radius:20px;background:#132036;
  border:1.5px solid color-mix(in srgb,var(--accent) 60%,transparent);font:500 30px/1.3 var(--f-body);color:#fff;
  box-shadow:0 16px 48px rgba(0,0,0,.45)}
.tp-say::after{content:"";position:absolute;left:46px;bottom:-14px;width:24px;height:24px;background:#132036;
  border-right:1.5px solid color-mix(in srgb,var(--accent) 60%,transparent);
  border-bottom:1.5px solid color-mix(in srgb,var(--accent) 60%,transparent);transform:rotate(45deg)}
.tp-scard{position:absolute;transform:translateY(-50%);display:flex;align-items:center;gap:22px;padding:22px 28px}
.tp-scard .tp-label{font-size:34px}
.tp-badge{position:absolute;transform:translate(-50%,-50%);font-size:26px;padding:14px 26px}
.tp-code{position:relative;width:100%}
.tp-callout{position:absolute;transform:translateY(-50%);padding:18px 24px;max-width:420px;
  font:500 26px/1.3 var(--f-body);color:#fff}
.tp-big{font:700 260px/.9 var(--f-head);color:var(--accent);letter-spacing:-.01em;
  text-shadow:0 0 60px color-mix(in srgb,var(--accent) 35%,transparent)}
.tp-big.old{font-size:120px;color:#5b6b85;text-shadow:none;text-decoration:line-through;text-decoration-thickness:6px}
.tp-statlabel{font:600 46px/1.1 var(--f-head);text-transform:uppercase;color:#fff;text-align:center;text-wrap:balance}
.tp-track{position:absolute;height:6px;border-radius:999px;background:#1b2a40}
.tp-fill{position:absolute;height:6px;border-radius:999px;background:var(--accent);
  box-shadow:0 0 16px color-mix(in srgb,var(--accent) 60%,transparent);transform-origin:0 50%}
.tp-pt{position:absolute;transform:translateX(-50%);display:flex;flex-direction:column;align-items:center;gap:14px;
  text-align:center;width:260px}
.tp-pt .dotc{width:30px;height:30px;border-radius:50%;background:#0a1626;border:4px solid #5b6b85}
.tp-pt.done .dotc{border-color:var(--accent);background:var(--accent)}
.tp-pt.cur .dotc{border-color:var(--accent);box-shadow:0 0 0 8px color-mix(in srgb,var(--accent) 22%,transparent),
  0 0 30px var(--accent)}
.tp-pt .tp-label{font-size:32px}
.tp-check{display:flex;flex-direction:column;gap:16px;width:min(100%,1100px)}
.tp-check .tp-item{font-size:34px;padding:22px 30px}
.tp-check .tp-item [data-icon]{font-size:40px}
.tp-step .tp-ic{width:104px;height:104px}.tp-step .tp-ic [data-icon]{font-size:58px}.tp-step .tp-label{font-size:46px}
.tp-step .tp-sub{font-size:26px}
.tp-table{width:min(100%,1400px);border-collapse:separate;border-spacing:0;font:500 32px/1.25 var(--f-body)}
.tp-table th{font:500 24px/1 var(--f-mono);letter-spacing:.14em;text-transform:uppercase;color:var(--accent);
  text-align:left;padding:18px 24px;border-bottom:2px solid #1b2a40}
.tp-table td{padding:24px 30px;color:#dbe4f0;border-bottom:1px solid #16243a}
.tp-table td{background-color:rgba(0,0,0,0)}
.vsc-body .tp-ln{isolation:isolate}
.vsc-body .lnb{position:absolute;inset:0;z-index:-1;opacity:0;background:rgba(0,122,204,.26)}
.vsc-body .lnb.good{background:rgba(45,212,167,.18)}.vsc-body .lnb.bad{background:rgba(230,57,70,.22)}
"""


# ---------------------------------------------------------------- templates


def _side_card(ctx: Ctx, s: Side, sid: str, width: str) -> str:
    lab, ic, cls, _ = _resolve(ctx, s.label, s.icon, s.entity)
    items = "".join(
        f'<div class="tp-item {_STATUS_CLASS[it.status]}" id="{sid}i{k}">'
        f"{_icon(ctx, it.icon or _STATUS_ICON.get(it.status, 'check'))}<span>{e(it.text)}</span></div>"
        for k, it in enumerate(s.items)
    )
    sub = (
        f'<div class="tp-sub" style="margin-top:6px">{e(s.sub)}</div>' if s.sub else ""
    )
    return (
        f'<div class="tp-card {cls}" id="{sid}" style="width:{width};padding:34px 36px"><span class="hlr"></span>'
        f'<div class="tp-row"><div class="tp-ic">{ic}</div><div><div class="tp-label">{e(lab)}</div>{sub}</div></div>'
        + (f'<div class="tp-items">{items}</div>' if items else "")
        + "</div>"
    )


def build_compare(p: Compare, ctx: Ctx) -> S.Scene:
    b = Beats(ctx)
    x, y, w, h = ctx.box(bool(p.title or p.eyebrow))
    vertical = ctx.short
    width = "100%" if vertical else "44%"
    cards = (_side_card(ctx, p.left, "L", width) + '<div class="tp-vs" id="vs">VS</div>'
             + _side_card(ctx, p.right, "R", width))  # fmt: skip
    row = (f"<div style=\"display:flex;flex-direction:{'column' if vertical else 'row'};align-items:"
           f"{'center' if vertical else 'stretch'};justify-content:center;gap:{'28px' if vertical else '40px'};"
           f'width:100%">{cards}</div>')  # fmt: skip
    top = ""
    if p.parent:
        top = (f'<div class="mh-pill" id="par" style="font-size:32px;padding:14px 32px"><span class="mh-led">'
               f"</span>{e(p.parent)}</div>")  # fmt: skip
        if not vertical:  # bracket from the parent down into both cards (it splits into the two sides)
            total = 0.88 * w + 96 + 80
            lx = (w - total) / 2 + 0.22 * w
            rx = w - lx
            top += (f'<svg class="tp-svg" style="position:relative" width="{w}" height="56" viewBox="0 0 {w} 56">'
                    f'<path class="w" id="br" d="M{w / 2:.0f},0 V28 M{lx:.0f},56 V28 H{rx:.0f} V56"/></svg>')  # fmt: skip
        else:
            top += '<div style="height:24px"></div>'
    verdict = (f'<div class="mh-pill" id="vd" style="margin-top:36px;font-size:30px;padding:16px 32px">'
               f"{e(p.verdict)}</div>") if p.verdict else ""  # fmt: skip
    inner = (f'<div style="display:flex;flex-direction:column;align-items:center;width:100%">{top}{row}{verdict}'
             "</div>")  # fmt: skip
    n = 2 + len(p.left.items) + len(p.right.items) + bool(p.parent) + bool(p.verdict)
    k = 0
    if p.parent:
        b.enter("#par", b.at("", 0, n), "pop")
        if not vertical:
            b.draw("#br", b.at("", 0, n) + 0.3, 0.6)
        k += 1
    for sid, side in (("L", p.left), ("R", p.right)):
        b.enter(f"#{sid}", b.at(side.on, k, n), "up")
        k += 1
        for j, it in enumerate(side.items):
            b.enter(f"#{sid}i{j}", b.at(it.on, k, n), "left")
            k += 1
    b.enter(
        "#vs", b.at(p.right.on, 1, n) - 0.1 if p.right.on else b.at("", 1, n), "pop"
    )
    if p.verdict or p.winner != "none":
        t = b.at(p.verdict_on, k, n)
        if p.verdict:
            b.enter("#vd", t, "pop")
        if p.winner != "none":
            win, lose = ("#L", "#R") if p.winner == "left" else ("#R", "#L")
            b.focus(win, t)
            b.dim(lose, t + 0.1)
    return S.Scene(html=_wrap(ctx, p, inner), css=CSS + CMP_CSS, js=b.js())


CMP_CSS = """
#L .tp-label,#R .tp-label{font-size:48px}
#L .tp-item,#R .tp-item{font-size:28px;padding:18px 22px}
#L .tp-ic,#R .tp-ic{width:96px;height:96px}#L .tp-ic [data-icon],#R .tp-ic [data-icon]{font-size:54px}
"""


def build_steps(p: Steps, ctx: Ctx) -> S.Scene:
    b = Beats(ctx)
    n = len(p.steps)
    col = ctx.short or n > 4
    parts = []
    for k, s in enumerate(p.steps):
        lab, ic, cls, _ = _resolve(ctx, s.label, s.icon, s.entity)
        sub = f'<div class="tp-sub">{e(s.sub)}</div>' if s.sub else ""
        if col:
            card = (f'<div class="tp-card {cls}" id="s{k}" style="display:flex;align-items:center;gap:24px;'
                    f'padding:20px 28px;width:100%"><span class="hlr"></span><div class="tp-num">{k + 1:02d}</div>'
                    f'<div class="tp-ic">{ic}</div><div><div class="tp-label" style="font-size:36px">{e(lab)}</div>'
                    f"{sub}</div></div>")  # fmt: skip
        else:
            card = (f'<div class="tp-card tp-step {cls}" id="s{k}" style="flex:1;display:flex;flex-direction:column;'
                    f'align-items:center;text-align:center;gap:16px;padding:34px 22px"><span class="hlr"></span>'
                    f'<div class="tp-num">{k + 1:02d}</div><div class="tp-ic">{ic}</div>'
                    f'<div class="tp-label">{e(lab)}</div>{sub}</div>')  # fmt: skip
        parts.append(card)
        if k < n - 1 and not col:
            parts.append(
                f'<div class="tp-arrow" id="a{k}">{_icon(ctx, "arrow-right")}</div>'
            )
    flex = (
        "flex-direction:column;gap:18px;width:min(100%,980px)"
        if col
        else "flex-direction:row;gap:18px;width:100%"
    )
    inner = (
        f'<div style="display:flex;align-items:stretch;{flex}">{"".join(parts)}</div>'
    )
    for k, s in enumerate(p.steps):
        t = b.at(s.on, k, n)
        if k and not col:
            b.enter(f"#a{k - 1}", max(0.1, t - 0.2), "left")
        b.enter(f"#s{k}", t, "up")
    if p.active:
        b.focus(f"#s{p.active - 1}", b.at(p.active_on, n, n + 1))
    return S.Scene(html=_wrap(ctx, p, inner), css=CSS, js=b.js())


def _node(
    ctx: Ctx, s: Spoke, nid: str, cx: float, cy: float, center: bool = False
) -> str:
    lab, ic, cls, _ = _resolve(ctx, s.label, s.icon, s.entity)
    cls = cls or _STATUS_CLASS[s.status]
    led = (
        f'<span class="mh-led tp-led {s.status if s.status != "none" else ""}"></span>'
        if s.status != "none"
        else ""
    )
    sub = (
        f'<div class="tp-sub" style="font-size:22px">{e(s.sub)}</div>' if s.sub else ""
    )
    return (f'<div class="tp-card tp-node {"center" if center else ""} {cls}" id="{nid}" '
            f'style="left:{cx:.0f}px;top:{cy:.0f}px"><span class="hlr"></span>{led}<div class="tp-ic">{ic}</div>'
            f'<div class="tp-label">{e(lab)}</div>{sub}</div>')  # fmt: skip


def build_hub(p: Hub, ctx: Ctx) -> S.Scene:
    b = Beats(ctx)
    x, y, w, h = ctx.box(bool(p.title or p.eyebrow))
    cx, cy = w / 2, h / 2
    n = len(p.spokes)
    rx, ry = w * 0.34, h * (0.36 if not ctx.short else 0.4)
    pts = []
    for k in range(n):
        a = (
            -math.pi / 2 + 2 * math.pi * k / n
            if n > 2
            else (math.pi if k == 0 else 0.0)
        )
        if not ctx.short and n in (4, 6):
            a += (
                math.pi / n
            )  # landscape: avoid straight up/down spokes, they collide with the centre card
        pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    wires = "".join(
        f'<path class="w" id="w{k}" d="M{cx:.0f},{cy:.0f} L{px:.0f},{py:.0f}"/>'
        for k, (px, py) in enumerate(pts)
    )
    svg = f'<svg class="tp-svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{wires}</svg>'
    nodes = "".join(
        _node(ctx, s, f"n{k}", px, py)
        for k, (s, (px, py)) in enumerate(zip(p.spokes, pts, strict=True))
    )
    inner = svg + nodes + _node(ctx, p.center, "hub", cx, cy, center=True)
    b.enter("#hub", b.at(p.center.on, 0, n + 1), "pop")
    for k, s in enumerate(p.spokes):
        t = b.at(s.on, k + 1, n + 1)
        b.draw(f"#w{k}", t, 0.5)
        b.enter(f"#n{k}", t + 0.25, "pop")
    return S.Scene(html=_wrap(ctx, p, inner, center=False), css=CSS, js=b.js())


def build_flow(p: Flow, ctx: Ctx) -> S.Scene:
    b = Beats(ctx)
    x, y, w, h = ctx.box(bool(p.title or p.eyebrow))
    na = len(p.actors)
    span = (
        0.62 if na == 2 else 0.8 if na == 3 else 0.9
    )  # two actors shouldn't sit at the far edges
    xs = [w * (0.5 - span / 2 + span * (k + 0.5) / na) for k in range(na)]
    actor_h = 230
    nm = len(p.messages)
    # fewer messages → more room each; then centre the whole diagram vertically in the box
    step = max(110.0, min(190.0, (h - actor_h - 40) / (nm + 0.4)))
    total = actor_h + 30 + step * (nm + 0.25)
    oy = max(0.0, (h - total) / 2)
    top = oy + actor_h + 30
    parts, svg = [], []
    for k, a in enumerate(p.actors):
        lab, ic, cls, _ = _resolve(ctx, a.label, a.icon, a.entity)
        parts.append(f'<div class="tp-actor {cls}" id="ac{k}" style="left:{xs[k]:.0f}px;top:{oy:.0f}px">'
                     f'<div class="tp-ic" style="width:136px;height:136px;border-radius:28px">{ic}</div>'
                     f'<div class="tp-label">{e(lab)}</div></div>')  # fmt: skip
        svg.append(f'<line class="life" id="lf{k}" x1="{xs[k]:.0f}" y1="{oy + actor_h:.0f}" x2="{xs[k]:.0f}" '
                   f'y2="{top + step * (nm + 0.1):.0f}"/>')  # fmt: skip
    for k, m in enumerate(p.messages):
        a, c = xs[min(m.src, na) - 1], xs[min(m.to, na) - 1]
        yy = top + step * (k + 0.75)
        sgn = 1 if c >= a else -1
        x2 = c - sgn * 18
        svg.append(
            f'<path class="w {m.kind}" id="m{k}" d="M{a:.0f},{yy:.0f} L{x2:.0f},{yy:.0f}"/>'
        )
        svg.append(f'<polygon class="head {m.kind}" id="h{k}" points="{c:.0f},{yy:.0f} {x2 - sgn * 6:.0f},'
                   f'{yy - 13:.0f} {x2 - sgn * 6:.0f},{yy + 13:.0f}"/>')  # fmt: skip
        svg.append(
            f'<circle class="dot" id="d{k}" cx="{a:.0f}" cy="{yy:.0f}" r="10" style="opacity:0"/>'
        )
        parts.append(f'<div class="tp-msg {m.kind}" id="ml{k}" style="left:{(a + c) / 2:.0f}px;top:{yy:.0f}px">'
                     f'{_icon(ctx, "x") + " " if m.kind == "error" else ""}{e(m.label)}</div>')  # fmt: skip
    inner = (
        f'<svg class="tp-svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{"".join(svg)}</svg>'
        + "".join(parts)
    )
    for k in range(na):
        b.enter(f"#ac{k}", 0.15 + 0.18 * k, "up")
        b.draw(f"#lf{k}", 0.35 + 0.18 * k, 0.6)
    for k, m in enumerate(p.messages):
        a, c = xs[min(m.src, na) - 1], xs[min(m.to, na) - 1]
        t = max(b.at(m.on, k + 1, nm + 1), 0.9)
        b.draw(f"#m{k}", t, 0.55)
        b.enter(f"#h{k}", t + 0.5, "fade")
        b.add(f"tl.fromTo('#d{k}', {{attr:{{cx:{a:.0f}}}, opacity:1}}, {{attr:{{cx:{c:.0f}}}, duration:.55, "
              f"ease:'power1.inOut'}}, {t}); tl.to('#d{k}', {{opacity:0, duration:.2}}, {t + 0.55});")  # fmt: skip
        b.enter(f"#ml{k}", t + 0.15, "fade")
    return S.Scene(html=_wrap(ctx, p, inner, center=False), css=CSS, js=b.js())


def build_scenario(p: Scenario, ctx: Ctx) -> S.Scene:
    b = Beats(ctx)
    x, y, w, h = ctx.box(bool(p.title or p.eyebrow))
    ent = ctx.entities.get(p.persona.entity or "") or {}
    name = p.persona.name or ent.get("label", "")
    role = p.persona.role or ent.get("note", "")
    icon = (
        p.persona.icon
        if p.persona.icon != "user-round" or not ent.get("icon")
        else ent["icon"]
    )
    # persona anchor: same place in every scenario shot (x ≈ 20 % of the stage)
    px = (0.5 if ctx.short else 0.2) * ctx.w - x
    py = h * (0.3 if ctx.short else 0.52)
    persona = (f'<div class="tp-persona" id="pe" style="left:{px:.0f}px;top:{py:.0f}px"><div class="ring">'
               f'{_icon(ctx, icon, "user-round")}</div><b>{e(name)}</b>'
               + (f"<small>{e(role)}</small>" if role else "") + "</div>")  # fmt: skip
    n = len(p.items)
    parts, wires = [persona], []
    if ctx.short:
        col_x, ys = w * 0.08, [h * (0.58 + 0.15 * k) for k in range(n)]
    else:
        col_x = w * 0.42
        ys = [h * (0.52 + (k - (n - 1) / 2) * 0.27) for k in range(n)] if n else []
    for k, (it, yy) in enumerate(zip(p.items, ys, strict=True)):
        lab, ic, cls, _ = _resolve(ctx, it.label, it.icon, it.entity)
        cls = cls or _STATUS_CLASS[it.status]
        sub = f'<div class="tp-sub">{e(it.sub)}</div>' if it.sub else ""
        parts.append(f'<div class="tp-card tp-scard {cls}" id="it{k}" style="left:{col_x:.0f}px;top:{yy:.0f}px;'
                     f'width:{w * (0.84 if ctx.short else 0.5):.0f}px"><span class="hlr"></span><div class="tp-ic">'
                     f'{ic}</div><div><div class="tp-label">{e(lab)}</div>{sub}</div></div>')  # fmt: skip
        if not ctx.short:
            x1, y1, x2 = px + 120, py, col_x - 10
            wires.append(f'<path class="w" id="iw{k}" d="M{x1:.0f},{y1:.0f} C{x1 + 140:.0f},{y1:.0f} '
                         f'{x2 - 140:.0f},{yy:.0f} {x2:.0f},{yy:.0f}"/>')  # fmt: skip
    if p.say:  # above the persona, never over the item column
        if ctx.short:
            style_ = f"left:{w * 0.08:.0f}px;top:0;max-width:{w * 0.84:.0f}px"
        else:
            left = max(0.0, px - 110)
            style_ = (f"left:{left:.0f}px;bottom:{h - (py - 150):.0f}px;"
                      f"max-width:{max(360.0, col_x - left - 40):.0f}px")  # fmt: skip
        parts.append(f'<div class="tp-say" id="say" style="{style_}">{e(p.say)}</div>')
    if p.badge:
        bx = w * 0.5 if ctx.short else col_x + w * 0.25
        by = h * 0.97 if ctx.short else (ys[-1] + h * 0.17 if ys else h * 0.8)
        st = p.badge_status if p.badge_status != "none" else "good"
        parts.append(f'<div class="mh-pill tp-badge {st}" id="bd" style="left:{bx:.0f}px;top:{min(by, h - 30):.0f}px">'
                     f'{_icon(ctx, _STATUS_ICON.get(st, "check"))}{e(p.badge)}</div>')  # fmt: skip
    svg = f'<svg class="tp-svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{"".join(wires)}</svg>'
    inner = svg + "".join(parts)
    total = n + bool(p.say) + bool(p.badge) + 1
    b.enter("#pe", 0.15, "pop")
    k0 = 1
    if p.say:
        b.enter("#say", b.at(p.say_on, 1, total), "pop")
        k0 += 1
    for k, it in enumerate(p.items):
        t = b.at(it.on, k0 + k, total)
        if not ctx.short:
            b.draw(f"#iw{k}", t, 0.5)
        b.enter(f"#it{k}", t + 0.2, "left")
    if p.badge:
        b.enter("#bd", b.at(p.badge_on, total - 1, total), "pop")
    return S.Scene(html=_wrap(ctx, p, inner, center=False), css=CSS, js=b.js())


_KW = {
    "java": "public private protected class interface extends implements static final void return new if else for "
            "while try catch throw throws import package this null true false var record enum",
    "python": "def class return import from as if elif else for while try except raise with lambda None True False "
              "async await yield in not and or is pass",
    "javascript": "const let var function return if else for while class new import from export async await null "
                  "true false this try catch throw",
    "bash": "if then else fi for do done echo export sudo cd",
    "sql": "SELECT FROM WHERE JOIN ON GROUP BY ORDER INSERT INTO VALUES UPDATE SET DELETE CREATE TABLE AND OR AS LIMIT",
}  # fmt: skip
_KW["typescript"] = _KW["javascript"] + " interface type implements"
_TOKEN = re.compile(
    r'(//.*$|#.*$|"[^"]*"|\'[^\']*\'|\b\d[\d_.]*\b|@\w+|\b[A-Za-z_]\w*\b)'
)


def _highlight_line(line: str, lang: str) -> str:
    kws = set(_KW.get(lang, "").split())
    out, pos = [], 0
    for m in _TOKEN.finditer(line):
        out.append(e(line[pos : m.start()]))
        tok = m.group(0)
        if (tok.startswith("//") and lang in ("java", "javascript", "typescript")) or (
            tok.startswith("#") and lang in ("python", "bash", "yaml")
        ):
            cls = "cm"
        elif tok[0] in "\"'":
            cls = "str"
        elif tok[0].isdigit():
            cls = "num"
        elif tok.startswith("@"):
            cls = "fn"
        elif tok in kws:
            cls = "kw"
        elif tok[0].isupper() and lang in ("java", "typescript"):
            cls = "ty"
        else:
            cls = ""
        out.append(f'<span class="{cls}">{e(tok)}</span>' if cls else e(tok))
        pos = m.end()
    out.append(e(line[pos:]))
    return "".join(out) or " "


def build_code(p: Code, ctx: Ctx) -> S.Scene:
    b = Beats(ctx)
    x, y, w, h = ctx.box(bool(p.title or p.eyebrow))
    fs = (
        30 if len(p.lines) <= 8 else 26 if len(p.lines) <= 11 else 24
    )  # fewer lines → bigger, readable code
    win_w = w if ctx.short or not p.callout else w * 0.66
    kinds = {k: hl.kind for hl in p.highlights for k in hl.lines}
    body = "".join(
        f'<span class="ln tp-ln" id="cl{k}">'
        + (
            f'<span class="lnb {kinds[k + 1]}" id="lb{k}"></span>'
            if k + 1 in kinds
            else ""
        )
        + f"{_highlight_line(ln.rstrip(), p.lang)}</span>"
        for k, ln in enumerate(p.lines)
    )
    win = (f'<div class="vsc tp-code" id="win" style="width:{win_w:.0f}px"><div class="vsc-title"><span class="name">'
           f"{e(p.filename)} — Visual Studio Code</span><span class=\"ctl\">—</span><span class=\"ctl\">☐</span>"
           f'<span class="ctl">✕</span></div><div class="vsc-tabs"><span class="vsc-tab">{e(p.filename)}</span></div>'
           f'<div class="vsc-body" style="font-size:{fs}px">{body}</div><div class="vsc-status">{e(p.lang.upper())}  ·  UTF-8</div></div>')  # fmt: skip
    parts = [
        f'<div style="position:absolute;left:0;top:50%;transform:translateY(-50%)">{win}</div>'
    ]
    line_h, body_top = fs * 1.65, 40 + 40 + 16
    win_h = body_top + line_h * len(p.lines) + 16 + 30
    if p.callout:
        ln = max(1, min(p.callout_line or 1, len(p.lines)))
        cy = h / 2 - win_h / 2 + body_top + line_h * (ln - 0.5)
        cx = win_w + 60 if not ctx.short else w * 0.1
        cyy = cy if not ctx.short else min(h - 60, h / 2 + win_h / 2 + 70)
        parts.append(f'<div class="tp-card tp-callout" id="co" style="left:{cx:.0f}px;top:{cyy:.0f}px">'
                     f'<span class="hlr"></span>{e(p.callout)}</div>')  # fmt: skip
        if not ctx.short:
            parts.append(f'<svg class="tp-svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}"><path class="w" '
                         f'id="cw" d="M{win_w - 40:.0f},{cy:.0f} L{cx - 8:.0f},{cy:.0f}"/></svg>')  # fmt: skip
    inner = "".join(parts)
    b.enter("#win", 0.1, "up")
    for k in range(len(p.lines)):
        b.enter(f"#cl{k}", round(0.45 + 0.07 * k, 2), "fade")
    n = len(p.highlights) + bool(p.callout)
    for j, hl in enumerate(p.highlights):
        t = max(b.at(hl.on, j + 1, n + 1), 0.45 + 0.07 * len(p.lines))
        for ln in hl.lines:
            if 1 <= ln <= len(p.lines):
                b.add(f"tl.to('#lb{ln - 1}', {{opacity:1, duration:.35}}, {t});")
    if p.callout:
        t = max(b.at(p.callout_on, n, n + 1), 0.6 + 0.07 * len(p.lines))
        if not ctx.short:
            b.draw("#cw", t, 0.4)
        b.enter("#co", t + 0.2, "left")
    return S.Scene(html=_wrap(ctx, p, inner, center=False), css=CSS, js=b.js())


_NUM = re.compile(r"^([^\d]*)([\d][\d,]*\.?\d*)(.*)$")


def build_stat(p: Stat, ctx: Ctx) -> S.Scene:
    b = Beats(ctx)
    icon = (
        f'<div class="tp-ic" style="margin:0 auto 10px">{_icon(ctx, p.icon)}</div>'
        if p.icon
        else ""
    )
    big = f'<div class="tp-big" id="v">{e(p.value)}</div>'
    if p.before:
        old = (f'<div style="display:flex;flex-direction:column;align-items:center;gap:8px" id="old">'
               f'<div class="tp-big old">{e(p.before)}</div><div class="mh-meta">{e(p.before_label)}</div></div>'
               f'<div class="tp-arrow" id="ar" style="transform:scale(1.6)">{_icon(ctx, "arrow-right")}</div>')  # fmt: skip
        row = f'<div style="display:flex;align-items:center;gap:60px">{old}{big}</div>'
    else:
        row = big
    sub = (
        f'<div class="tp-sub" id="sb" style="font-size:30px;text-align:center">{e(p.sub)}</div>'
        if p.sub
        else ""
    )
    inner = (f'<div style="display:flex;flex-direction:column;align-items:center;gap:22px">{icon}{row}'
             f'<div class="tp-statlabel" id="lb">{e(p.label)}</div>{sub}</div>')  # fmt: skip
    t = b.at(p.on, 0, 3)
    if p.before:
        b.enter("#old", max(0.1, t - 0.6), "up")
        b.enter("#ar", max(0.2, t - 0.3), "left")
    b.enter("#v", t, "pop")
    m = _NUM.match(p.value)
    if m:  # count up to the number, keeping its prefix/suffix and thousands separators
        pre, num, suf = m.groups()
        target = float(num.replace(",", ""))
        dec = len(num.split(".")[1]) if "." in num else 0
        comma = "," in num
        b.add(f"(() => {{ const el = document.getElementById('v'); const o = {{v:0}}; "
              f"tl.to(o, {{v:{target}, duration:1.1, ease:'power2.out', onUpdate:() => {{ "
              f"let s = o.v.toFixed({dec}); if ({str(comma).lower()}) s = Number(s).toLocaleString('en-US', "
              f"{{minimumFractionDigits:{dec}, maximumFractionDigits:{dec}}}); "
              f"el.textContent = {json.dumps(pre)} + s + {json.dumps(suf)}; }} }}, {t}); }})();")  # fmt: skip
    b.enter("#lb", t + 0.35, "up")
    if p.sub:
        b.enter("#sb", t + 0.7, "up")
    return S.Scene(html=_wrap(ctx, p, inner), css=CSS, js=b.js())


def build_timeline(p: Timeline, ctx: Ctx) -> S.Scene:
    b = Beats(ctx)
    x, y, w, h = ctx.box(bool(p.title or p.eyebrow))
    n = len(p.points)
    pad = w * 0.08
    xs = [pad + (w - 2 * pad) * k / (n - 1) for k in range(n)]
    ly = h * 0.42
    cur = p.current or 0
    parts = [
        f'<div class="tp-track" style="left:{xs[0]:.0f}px;width:{xs[-1] - xs[0]:.0f}px;top:{ly - 3:.0f}px"></div>'
    ]
    if cur:
        parts.append(f'<div class="tp-fill" id="fill" style="left:{xs[0]:.0f}px;width:{xs[cur - 1] - xs[0]:.0f}px;'
                     f'top:{ly - 3:.0f}px"></div>')  # fmt: skip
    for k, pt in enumerate(p.points):
        state = "cur" if k + 1 == cur else ("done" if k + 1 < cur else "")
        sub = (
            f'<div class="tp-sub" style="font-size:22px">{e(pt.sub)}</div>'
            if pt.sub
            else ""
        )
        parts.append(f'<div class="tp-pt {state}" id="p{k}" style="left:{xs[k]:.0f}px;top:{ly - 15:.0f}px">'
                     f'<div class="dotc"></div><div class="tp-label">{e(pt.label)}</div>{sub}</div>')  # fmt: skip
    inner = "".join(parts)
    for k, pt in enumerate(p.points):
        b.enter(f"#p{k}", b.at(pt.on, k, n + 1), "up")
    if cur:
        b.add(f"tl.from('#fill', {{scaleX:0, duration:{0.4 + 0.25 * cur:.2f}, ease:'power2.inOut'}}, "
              f"{b.at('', n, n + 1)});")  # fmt: skip
    return S.Scene(html=_wrap(ctx, p, inner, center=False), css=CSS, js=b.js())


def build_checklist(p: Checklist, ctx: Ctx) -> S.Scene:
    b = Beats(ctx)
    rows = "".join(
        f'<div class="tp-item {_STATUS_CLASS[it.status]}" id="c{k}">'
        f"{_icon(ctx, it.icon or _STATUS_ICON.get(it.status, 'check'))}<span>{e(it.text)}</span></div>"
        for k, it in enumerate(p.items)
    )
    inner = f'<div class="tp-check">{rows}</div>'
    for k, it in enumerate(p.items):
        b.enter(f"#c{k}", b.at(it.on, k, len(p.items)), "left")
    return S.Scene(html=_wrap(ctx, p, inner), css=CSS, js=b.js())


def build_table(p: Table, ctx: Ctx) -> S.Scene:
    b = Beats(ctx)
    ncol = len(p.columns)
    head = "".join(f"<th>{e(c)}</th>" for c in p.columns)
    body = "".join(
        f'<tr id="r{k}">'
        + "".join(f"<td>{e(c)}</td>" for c in (r + [""] * ncol)[:ncol])
        + "</tr>"
        for k, r in enumerate(p.rows)
    )
    inner = f'<table class="tp-table"><thead id="th"><tr>{head}</tr></thead><tbody>{body}</tbody></table>'
    b.enter("#th", 0.15, "fade")
    for k in range(len(p.rows)):
        b.enter(f"#r{k}", round(0.4 + 0.18 * k, 2), "up")
    if p.highlight_row and p.highlight_row <= len(p.rows):
        t = b.at(p.highlight_on, len(p.rows), len(p.rows) + 1)
        b.add(
            "const A = getComputedStyle(document.getElementById('stage')).getPropertyValue('--accent').trim();"
        )
        b.add(f"tl.to('#r{p.highlight_row - 1} td', {{backgroundColor: A + '30', color:'#ffffff', "
              f"duration:.4}}, {t});")  # fmt: skip
    return S.Scene(html=_wrap(ctx, p, inner), css=CSS, js=b.js())


# ---------------------------------------------------------------- registry


@dataclass(frozen=True)
class Template:
    name: str
    model: type[BaseModel]
    build: Callable
    use: str  # when the planner should pick it
    example: dict


TEMPLATES: dict[str, Template] = {
    t.name: t
    for t in (
        Template("compare", Compare, build_compare, "two options / before-after / X vs Y, optional verdict",
                 {"parent": "AI", "left": {"label": "Build with models", "icon": "code", "items": [
                     {"text": "Spring Boot app", "icon": "code-xml"}]}, "right": {"label": "Build the models",
                     "icon": "brain", "items": [{"text": "Training", "icon": "activity"}]}, "winner": "left",
                  "verdict": "Java devs live here", "verdict_on": "most"}),
        Template("steps", Steps, build_steps, "a process / plan / ordered stages (2–5)",
                 {"title": "The 90-day plan", "steps": [{"label": "RAG", "icon": "database", "on": "first"},
                  {"label": "Evaluation", "icon": "gauge", "on": "then"}], "active": 1}),
        Template("hub", Hub, build_hub, "one thing connected to 2–6 parts / tools / consumers",
                 {"center": {"label": "Kafka", "icon": "server"}, "spokes": [{"label": "Orders", "icon":
                  "shopping-cart", "on": "orders"}, {"label": "Email", "icon": "mail", "status": "bad"}]}),
        Template("flow", Flow, build_flow, "request/response between 2–4 actors (sequence)",
                 {"actors": [{"label": "App", "icon": "laptop"}, {"label": "LLM", "icon": "bot"}],
                  "messages": [{"from": 1, "to": 2, "label": "prompt + docs", "on": "sends"},
                               {"from": 2, "to": 1, "label": "answer", "kind": "response"}]}),
        Template("scenario", Scenario, build_scenario, "the running real-world example / a person in a situation",
                 {"persona": {"entity": "robin"}, "say": "Do I need Python?", "items": [{"label": "Insurance client",
                  "icon": "building-2", "on": "client"}], "badge": "Plan ready", "badge_status": "good"}),
        Template("code", Code, build_code, "showing real code (≤14 lines) with highlighted lines / a callout",
                 {"filename": "RefundTest.java", "lang": "java", "lines": ["@Test", "void refundIs14Days() {",
                  "  assertEquals(14, policy.refundDays());", "}"], "highlights": [{"lines": [3], "kind": "good",
                  "on": "checks"}], "callout": "Checks the fact, not the wording", "callout_line": 3}),
        Template("stat", Stat, build_stat, "one big number / metric, optional before → after",
                 {"value": "50,000", "label": "policy documents", "sub": "and the chatbot must find the right one",
                  "icon": "files", "on": "fifty"}),
        Template("timeline", Timeline, build_timeline, "a roadmap / weeks / milestones (3–6 points)",
                 {"points": [{"label": "Day 0"}, {"label": "Day 30", "sub": "RAG"}, {"label": "Day 90",
                  "sub": "Agents"}], "current": 2}),
        Template("checklist", Checklist, build_checklist, "a list of 2–6 points, each optionally ✓ / ✗ / ⚠",
                 {"title": "What you need", "items": [{"text": "Python syntax", "status": "bad"},
                  {"text": "Evaluation skills", "status": "good"}]}),
        Template("table", Table, build_table, "small data / comparison table (≤4 columns, ≤6 rows)",
                 {"columns": ["Question", "Expected"], "rows": [["Refund window?", "14 days"]], "highlight_row": 1}),
    )
}  # fmt: skip

PREFIX = "@template"


def parse(prompt: str) -> tuple[str, dict] | None:
    """`@template NAME\\n{json}` → (name, params); None for a free-form prompt."""
    s = (prompt or "").lstrip()
    if not s.startswith(PREFIX):
        return None
    head, _, rest = s.partition("\n")
    name = head[len(PREFIX) :].strip()
    try:
        params = json.loads(rest.strip() or "{}")
    except json.JSONDecodeError as ex:
        raise TemplateError(
            f"template {name}: params are not valid JSON ({ex})"
        ) from ex
    return name, params


def to_prompt(name: str, params: dict) -> str:
    return f"{PREFIX} {name}\n{json.dumps(params, ensure_ascii=False, indent=1)}"


def validate(name: str, params: dict) -> BaseModel:
    t = TEMPLATES.get(name)
    if t is None:
        raise TemplateError(
            f"unknown template '{name}' (use one of: {', '.join(TEMPLATES)})"
        )
    try:
        return t.model.model_validate(params)
    except ValidationError as ex:
        errs = "; ".join(
            f"{'.'.join(str(x) for x in er['loc'])}: {er['msg']}"
            for er in ex.errors()[:6]
        )
        raise TemplateError(f"template {name}: {errs}") from ex


def build(name: str, params: dict, ctx: Ctx) -> S.Scene:
    model = validate(name, params)
    return TEMPLATES[name].build(model, ctx)


def catalogue() -> str:
    """Compact description for the planner prompt: when to use each template + a minimal example."""
    rows = []
    for t in TEMPLATES.values():
        fields = ", ".join(t.model.model_fields)
        rows.append(
            f"- {t.name}: {t.use}. Fields: {fields}.\n  e.g. {json.dumps(t.example, ensure_ascii=False)}"
        )
    return "\n".join(rows)
