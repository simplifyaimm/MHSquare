"""Parse LLM scene output and statically reject unsafe or non-deterministic code.

The renderer is the real sandbox (no network, read-only FS, every request aborted); these checks
are defence in depth and give the LLM a precise error to fix.
"""

import re
from dataclasses import dataclass
from pathlib import Path

THEMES_DIR = Path(__file__).parent / "themes"
MAX_SCENE_BYTES = 300_000

_BLOCK = re.compile(r"```(html|css|js|javascript|svg)\s*\n(.*?)```", re.S | re.I)

FORBIDDEN = [
    (r"<\s*script", "no <script> tags in html"),
    (r"<\s*(img|link|iframe|video|audio|object|embed|meta|base|form)\b", "no external-resource elements"),
    (r"\b(https?|ftp|wss?|file):", "no URLs"),
    (r"['\"(=]\s*//[a-z0-9-]+\.[a-z]{2,}", "no URLs"),
    (r"@import", "no @import"),
    (r"url\(\s*['\"]?(?!#|data:image/svg)", "no url() except #fragment or inline svg"),
    (r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts)\b", "no network APIs"),
    (r"\bimport\s*\(", "no dynamic import"),
    (r"\b(eval|Function)\s*\(", "no eval/Function"),
    (r"\b(setTimeout|setInterval|requestAnimationFrame)\s*\(", "no timers — use the GSAP timeline"),
    (r"\b(localStorage|sessionStorage|indexedDB|document\.cookie|navigator\.|window\.open|postMessage)\b",
     "no storage/browser APIs"),
]  # fmt: skip
CSS_ONLY = [
    (
        r"@keyframes|(?<![\w-])animation\s*:|(?<![\w-])transition\s*:",
        "no CSS animations/transitions — use GSAP",
    )
]


class SceneError(ValueError):
    pass


@dataclass
class Scene:
    html: str
    css: str
    js: str

    def as_dict(self) -> dict:
        return {"html": self.html, "css": self.css, "js": self.js}


def parse(text: str) -> Scene:
    blocks: dict[str, str] = {}
    for lang, body in _BLOCK.findall(text):
        key = "js" if lang.lower() in ("js", "javascript") else lang.lower()
        blocks.setdefault(key, body.strip())
    if "html" not in blocks or "js" not in blocks:
        raise SceneError("expected ```html and ```js fenced blocks (and optional ```css)")
    return Scene(html=blocks["html"], css=blocks.get("css", ""), js=blocks["js"])


def parse_svg(text: str) -> str:
    m = re.search(r"```svg\s*\n(.*?)```", text, re.S) or re.search(r"(<svg\b.*?</svg>)", text, re.S)
    if not m:
        raise SceneError("expected an ```svg block")
    svg = m.group(1).strip()
    check_text(svg, "svg")
    return svg


def check_text(src: str, where: str) -> None:
    if len(src.encode()) > MAX_SCENE_BYTES:
        raise SceneError(f"{where}: too large")
    for pattern, msg in FORBIDDEN:
        if where == "svg" and msg.startswith("no URLs"):
            src_ = re.sub(r'xmlns(:\w+)?="http://www\.w3\.org/[^"]*"', "", src)
            if re.search(pattern, src_, re.I):
                raise SceneError(f"{where}: {msg}")
            continue
        if re.search(pattern, src, re.I):
            raise SceneError(f"{where}: {msg}")
    if where in ("css", "html", "svg"):
        for pattern, msg in CSS_ONLY:
            if re.search(pattern, src, re.I):
                raise SceneError(f"{where}: {msg}")


def check(scene: Scene) -> None:
    # SVG namespaces are fine in html/js (createElementNS)
    strip_ns = lambda s: re.sub(r"http://www\.w3\.org/(2000/svg|1999/xlink)", "", s)  # noqa: E731
    check_text(strip_ns(scene.html), "html")
    check_text(scene.css, "css")
    check_text(strip_ns(scene.js), "js")
    if "__scene" not in scene.js:
        raise SceneError("js must assign the timeline: window.__scene = tl")


def character_templates(svgs: dict[str, str]) -> str:
    css = (
        "<style>.mh-character .arm-l,.mh-character .arm-r,.mh-character .head,.mh-character .eyes,"
        ".mh-character .mouth{transform-box:fill-box}.mh-character .arm-l,.mh-character .arm-r{transform-origin:50% 8%}"
        ".mh-character .head{transform-origin:50% 95%}.mh-character .eyes,.mh-character .mouth{transform-origin:50% 50%}</style>"
    )
    return css + "".join(f'<template id="mh-char-{cid}">{svg}</template>' for cid, svg in svgs.items())
