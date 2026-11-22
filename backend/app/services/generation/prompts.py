"""Prompts for the scene writer (code-llm) and the character designer."""

RUNTIME_JS = r"""
// ---- mhsquare scene helpers (available to every scene) ----
window.mhCharacter = function (id, o) {
  o = Object.assign({ x: 960, y: 900, scale: 1, flip: false, parent: '#stage' }, o || {});
  const tpl = document.getElementById('mh-char-' + id);
  if (!tpl) throw new Error('unknown character ' + id);
  const wrap = document.createElement('div');
  wrap.className = 'mh-character'; wrap.dataset.char = id;
  wrap.style.cssText = 'position:absolute;width:400px;height:800px;transform-origin:50% 100%;left:' + (o.x - 200) +
    'px;top:' + (o.y - 800) + 'px;transform:scale(' + (o.flip ? -o.scale : o.scale) + ',' + o.scale + ')';
  wrap.innerHTML = tpl.innerHTML;
  document.querySelector(o.parent).appendChild(wrap);
  return wrap;
};
window.mhSparkle = function (x, y, size, color) {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '-10 -10 20 20'); s.setAttribute('class', 'mh-sparkle');
  size = size || 40;
  s.style.cssText = 'position:absolute;overflow:visible;left:' + (x - size / 2) + 'px;top:' + (y - size / 2) + 'px;width:' + size + 'px;height:' + size + 'px';
  s.innerHTML = '<path d="M0,-10 L2,-2 L10,0 L2,2 L0,10 L-2,2 L-10,0 L-2,-2Z" fill="' + (color || '#fff7c2') + '"/>';
  document.getElementById('stage').appendChild(s);
  return s;
};
window.mhWire = function (svg, x1, y1, x2, y2, bend, cls) {
  const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  const mx = (x1 + x2) / 2;
  p.setAttribute('d', bend === 'straight' ? `M${x1},${y1} L${x2},${y2}` : `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);
  if (cls) p.setAttribute('class', cls);
  (typeof svg === 'string' ? document.querySelector(svg) : svg).appendChild(p);
  return p;
};
window.mhCount = function (tl, el, from, to, at, dur, fmt) {
  el = typeof el === 'string' ? document.querySelector(el) : el;
  const o = { v: from }; el.textContent = fmt ? fmt(from) : Math.round(from);
  tl.to(o, { v: to, duration: dur, ease: 'power2.inOut', onUpdate: () => { el.textContent = fmt ? fmt(o.v) : Math.round(o.v); } }, at);
};
"""

COMMON_RULES = """You write ONE animated scene for a YouTube explainer, as HTML + CSS + JavaScript (GSAP 3).
A headless browser renders it frame by frame into video, in sync with the narrator's voice.

## Output format — exactly three fenced blocks, nothing else
```html
<!-- markup placed inside #stage (1920×1080, position:relative, overflow:hidden) -->
```
```css
/* optional extra CSS for this scene */
```
```js
const tl = gsap.timeline(); window.__scene = tl;
// … build the whole animation on tl with ABSOLUTE positions (third argument, seconds) …
tl.set({}, {}, DURATION);   // the timeline must end exactly at the shot duration
```

## Hard technical rules (violations are rejected)
- Use absolute px positioning on the 1920×1080 stage. Inline SVG is allowed and encouraged.
- No <script>, <img>, <link>, <iframe>, <video>, external URLs, @import, web fonts, emoji images.
  Fonts: only "Inter" (UI) and "JetBrains Mono" (code) — both installed.
- No setTimeout, setInterval, requestAnimationFrame, fetch, Date, CSS animations or transitions.
  ALL motion is GSAP tweens on `tl` (GSAP 3 with DrawSVGPlugin, MotionPathPlugin, TextPlugin, SplitText).
- The renderer seeks the timeline forward frame by frame: t=0 must already look correct
  (use tl.from / gsap.set for initial states). tl.call / onUpdate callbacks are fine.
- Helpers already defined: mhCharacter(id,{x,y,scale,flip}) → element, mhSparkle(x,y,size,color) → element,
  mhWire(svgOrSelector,x1,y1,x2,y2,'curve'|'straight',cls) → path, mhCount(tl,el,from,to,at,dur,fmt).

## Direction (this is what makes it feel natural)
- Sync to the voice: you get each spoken word's time. Start each beat ~0.15 s BEFORE its trigger word.
- One idea per beat. At most 3–4 focal elements on screen; remove or dim what is no longer discussed.
- Entrances: 0.5–0.9 s, ease "power3.out", from opacity 0 + y 24–40 px + filter "blur(8px)".
  Exits: 0.35–0.5 s fade/blur. Emphasis: brief scale 1.04, highlight bar, glow, colour change.
- Move the eye with the narration: whatever is being said should be what's moving or highlighted.
- Give the frame life: a slow camera drift (scale a wrapper 1.0 → 1.03 over the shot) is good.
- Safe area: keep text ≥ 120 px from left/right edges and ≥ 90 px from top/bottom. Minimum font 20 px.
- Keep on-screen text short (labels ≤ 6 words). Never paste the narration as paragraphs.
- End on a calm, composed frame held for ≥ 0.8 s. Do NOT fade everything out at the end."""

TECH_DOC = """## House theme "tech" — use these classes (already loaded); match this exact look
Background: `<div class="mh-bg"></div>` first (navy radial gradient + vignette). Optional `<div class="mh-glow" style="left:..;top:..">`.
Layout helpers: .abs .center .row .col .gap-8/16/24/32/48/64 .tc .nowrap
Type: .mh-eyebrow (small uppercase blue label) · .mh-title (.xl .lg .sm) · .mh-sub (muted) · .mh-body · .mh-num
      colours: .mh-accent .mh-good .mh-bad .mh-warn
Cards: .mh-card (glass card; modifiers .tight .focus .danger .success) — eyebrow + title + sub inside.
Diagram node: <div class="mh-node"><small>SERVICE</small><b>checkout</b></div> (.focus to highlight)
Chips: .mh-chip (rounded rect label) · .mh-pill (.bad .good) · .mh-badge (.warn .bad) e.g. "$$$"
List panel: <div class="mh-list"><div class="mh-item on">OpenAI<small>hosted API</small></div>…</div> (.on selected, .dim)
Code card:
  <div class="mh-code"><div class="mh-code-head"><i></i><i></i><i></i><span>app.py</span></div>
  <div class="mh-code-body"><span class="ln"><span class="k">from</span> litellm <span class="k">import</span> completion</span>
  <span class="ln hl">    model=<span class="s">"gpt-4o"</span>,</span></div></div>
  token classes: .k keyword .s string .f function .n number .c comment .p punctuation; line states .hl .bad .good .dim;
  inline highlight: <span class="mh-mark">…</span>
Connectors: <svg class="mh-wires" viewBox="0 0 1920 1080"></svg> then mhWire(svg, x1,y1,x2,y2) → animate with
  tl.from(path,{drawSVG:0,duration:.6}); class "hot" = glowing active wire; circle.dot = travelling packet
  (animate with motionPath:{path, align:path, alignOrigin:[.5,.5]}).
Dividers: .mh-divider-v / .mh-divider-h · Progress: <div class="mh-bar"><i style="width:40%"></i></div>
Signature moves from the reference: chips orbiting a central hub card; a counter rolling up (mhCount);
a "routed to" list whose selected row moves in sync with a highlighted code argument; split screen
"OPTION ONE / OPTION TWO" with a vertical divider; many service nodes wiring into one gateway node;
red-tinted code lines for the problem, then a clean replacement."""

STORY_DOC = """## House theme "story" — warm flat 2D cartoon illustration; match this exact look
Background: `<div class="st-bg"></div><div class="st-floor"></div><div class="st-light"></div>` (soft teal room).
Draw props as inline SVG in a flat style with dark outlines: use classes on shapes — .ink (2b2f36 outline 4px),
.skin .hair .paper .wood .wood-dark, or explicit fills from the palette (teal #7fc6ba, red #e0473c,
yellow #f6d04d, orange #f39a5b, green #8ccf7e, blue #6aa6e8, paper #fffdf6). Rounded, friendly shapes.
Typical props: desk, laptop with code lines, coffee mug, plant, shelf with folders, whiteboard, wall sign.
UI: .st-bubble (speech bubble, set style="--tail-x:30%"; .small) · .st-note (sticky note) · .st-sign (wall sign)
    .st-badge (.red .green .orange .yellow .blue; round icon badge with an inline <svg> icon + 1–2 word label)
    .st-caption (bottom caption pill) · .st-check (green ✓ circle) · mhSparkle(x,y) for twinkles.
Characters: if CHARACTERS are listed below, ALWAYS draw them with mhCharacter('<id>', {x, y, scale, flip})
  (x,y = feet position; the figure is 400×800 at scale 1). Animate parts inside the returned element:
  .head (nod/tilt), .arm-l / .arm-r (wave, point, type: rotate with transformOrigin "50% 8%"), .eyes (blink: scaleY .1),
  .mouth (talk: scaleY), the whole figure (walk in with x, small bounce). Never redraw a character by hand.
Signature moves from the reference: name intro bubble "Meet Robin!" with a wave; the camera pushes in
(scale a wrapper 1 → 1.15 toward the action); topic badges pop in one by one with a slight overshoot
(ease "back.out(1.7)") and sparkles; a green check + thumbs-up for success; gentle idle motion (blink, breathe)."""

CHARACTER_SYSTEM = """You are a character designer for a flat 2D cartoon explainer (warm, friendly, clean dark outlines
#2b2f36 4px, flat fills, soft cel shading, slightly big head ~1/4 body height, expressive eyes, glasses if described).
Draw ONE full-body standing character as SVG markup for a 400×800 box: <svg viewBox="0 0 400 800" width="400" height="800">.
Feet stand on y≈790, body centred on x=200, facing the viewer slightly 3/4.
Required groups (exact class names, each a <g>), drawn back-to-front:
  .legs · .body (torso, clothes, tie…) · .arm-l and .arm-r (each a whole arm incl. hand; the shoulder joint at the
  TOP of the group so rotating with transform-origin "50% 8%" swings it naturally) · .head (contains .hair, .face,
  .eyes, .mouth groups; neck at the bottom of the head group).
Use transform-box:fill-box friendly geometry (no transforms on the groups themselves).
Output ONLY the <svg>…</svg> markup in a ```svg fenced block. No scripts, no external refs, no text."""


def scene_system(theme: str) -> str:
    return f"{COMMON_RULES}\n\n{STORY_DOC if theme == 'story' else TECH_DOC}"


def scene_user(*, prompt: str, duration_ms: int, words: list[dict], style_guide: dict, theme: str,
               characters: list[dict], previous: str | None, feedback: str | None, transcript: str) -> str:  # fmt: skip
    timing = "\n".join(f"{w['t']:.2f}s  {w['w']}" for w in words) or "(no speech)"
    chars = "\n".join(f"- id '{c['id']}': {c['name']} — {c['description']}" for c in characters) or "(none)"
    parts = [
        f"DURATION = {duration_ms / 1000:.3f} seconds (timeline must end exactly here).",
        f"Theme: {theme}",
        f"Narration in this shot: “{transcript}”",
        f"Spoken word timings (seconds from shot start):\n{timing}",
        f"Shot brief from the director:\n{prompt}",
        f"Global style guide: {style_guide.get('summary', '')} | mood: {style_guide.get('mood', '')} | "
        f"camera: {style_guide.get('camera_language', '')}",
        f"CHARACTERS (use mhCharacter):\n{chars}",
    ]
    if previous:
        parts.append(
            "This shot CONTINUES the previous one. Start from its final frame (same positions/elements) and "
            f"evolve it. Previous scene source:\n{previous[:12000]}"
        )
    if feedback:
        parts.append(f"Revision feedback from the creator (must address): {feedback}")
    return "\n\n".join(parts)


def fix_user(error: str, logs: list[str]) -> str:
    return (
        "The scene failed to render. Fix it and return all three blocks again.\n"
        f"Error: {error}\nBrowser logs:\n" + "\n".join(logs[-15:])
    )
