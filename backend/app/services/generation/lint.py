"""Style lint for LLM-written animations (spec v2.1 §5.2). Issues go back to the LLM once to fix.

Checks: colours outside the palette · fonts other than the allowed ones · macOS window dots in code
demos · on-stage control elements (buttons, toggles, replay) · dashed borders / guide marks · person
placeholders · a hand-drawn footer/handle (mhsquare injects the real footer). Geometric checks
(face-cam safe area, off-stage text) run in the renderer's layout probe.
"""

import re

from app.services.generation.scene import Scene

DARK_PLUS = ["#569cd6", "#c586c0", "#ce9178", "#dcdcaa", "#b5cea8", "#6a9955", "#4ec9b0", "#9cdcfe", "#d4d4d4",
             "#858585", "#3c3c3c", "#333333", "#cccccc"]  # fmt: skip
MAC_DOTS = ["#ff5f56", "#ffbd2e", "#27c93f", "#ff5f57", "#febc2e", "#28c840", "#fc605c", "#fdbc40", "#34c84a"]
GENERIC_FONTS = {"monospace", "sans-serif", "serif", "inherit", "initial", "unset", "system-ui", "ui-monospace",
                 "segoe ui", "cursive"}  # fmt: skip

_HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_RGB = re.compile(r"rgba?\(\s*(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})")
_FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;}\"]+)", re.I)
_FONT_SHORT = re.compile(r"(?<![\w-])font\s*:\s*([^;}]+)", re.I)
_JS_FONT = re.compile(r"fontFamily\s*:\s*['\"]([^'\"]+)['\"]")


def _rgb(hexstr: str) -> tuple[int, int, int]:
    h = hexstr.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _near(c: tuple[int, int, int], allowed: list[tuple[int, int, int]], tol: int = 28) -> bool:
    return any(sum((a - b) ** 2 for a, b in zip(c, x, strict=True)) <= tol * tol for x in allowed)


def _is_neutral(c: tuple[int, int, int]) -> bool:
    """Greys/whites/blacks and the dark navy family are always fine."""
    r, g, b = c
    if max(c) - min(c) <= 18:
        return True
    return max(c) <= 70 and b >= r and b >= g  # very dark navy tones


def _colors(text: str) -> list[tuple[str, tuple[int, int, int]]]:
    out = [(m.group(0), _rgb(m.group(0))) for m in _HEX.finditer(text)]
    out += [(m.group(0) + ")", tuple(min(255, int(v)) for v in m.groups())) for m in _RGB.finditer(text)]  # type: ignore[misc]
    return out


def _fonts(text: str) -> set[str]:
    names: set[str] = set()
    for m in _FONT_FAMILY.finditer(text):
        names |= {n.strip().strip("'\"").lower() for n in m.group(1).split(",")}
    for m in _FONT_SHORT.finditer(text):
        val = m.group(1)
        if "," in val or "'" in val or '"' in val or "var(" in val:
            fam = val.split("/")[-1] if "/" in val else val
            fam = re.sub(r"^[\d.]+\w*\s+", "", fam.strip())  # drop the size if any
            names |= {n.strip().strip("'\"").lower() for n in fam.split(",") if n.strip()}
    for m in _JS_FONT.finditer(text):
        names |= {n.strip().strip("'\"").lower() for n in m.group(1).split(",")}
    return {n for n in names if n and not n.startswith("var(") and not re.match(r"^[\d.]+(px|em|rem|%)?$", n)
            and n.split()[0] not in {"bold", "italic", "normal", "400", "500", "600", "700", "800"}}  # fmt: skip


def lint(scene: Scene, *, palette: list[str], allowed_fonts: list[str], theme: str, story_palette: list[str],
         handle: str = "", tagline: str = "") -> list[str]:  # fmt: skip
    issues: list[str] = []
    src = "\n".join([scene.html, scene.css, scene.js])
    low = src.lower()

    allowed = [_rgb(c) for c in palette + DARK_PLUS + (story_palette if theme == "story" else [])]
    bad_colors = sorted({raw for raw, c in _colors(src) if not _is_neutral(c) and not _near(c, allowed)})
    if bad_colors:
        issues.append(f"colours outside the brand palette: {', '.join(bad_colors[:8])} — use var(--accent), "
                      "var(--warn), var(--good), pillar vars or neutrals")  # fmt: skip

    ok_fonts = {f.lower() for f in allowed_fonts} | GENERIC_FONTS
    bad_fonts = sorted(f for f in _fonts(src) if f not in ok_fonts)
    if bad_fonts:
        issues.append(
            f"fonts not allowed: {', '.join(bad_fonts)} — only {', '.join(allowed_fonts)} (use var(--f-*))"
        )

    if any(d in low for d in MAC_DOTS) or re.search(r"traffic[-_ ]?light|mac-?dots|window-dots", low) or \
            "mh-code-head" in low:  # fmt: skip
        issues.append(
            "macOS window dots in a code demo — use the VS Code Windows component (.vsc) with controls on the right"
        )

    if re.search(r"<\s*(button|input|select|textarea)\b", low) or re.search(
        r">\s*(replay|restart|play again|▶|⟳)\s*<", low
    ):
        issues.append(
            "on-stage control elements (buttons/inputs/replay) — nothing but the animation on stage"
        )
    if re.search(r"(border|outline)[^;{}]*\bdashed\b", low) or re.search(r"\b(guide|grid)-?marks?\b", low):
        issues.append("dashed borders / guide marks — remove them")
    if re.search(r"person[-_ ]?placeholder|avatar[-_ ]?placeholder|\[face\]|your face here", low):
        issues.append("person placeholders — the face cam is composited separately; don't draw a placeholder")
    if handle and handle.lower() in low or tagline and tagline.lower() in low:
        issues.append(
            "hand-drawn footer/handle/tagline — mhsquare adds the footer automatically; remove yours"
        )
    return issues
