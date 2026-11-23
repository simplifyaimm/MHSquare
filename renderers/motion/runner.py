"""Motion renderer: untrusted HTML/CSS/JS scene (GSAP timeline) → frame-exact MP4.

POST /render {job_id, html, css, js, theme_css, width, height, fps, duration_ms, scale}
  → writes /out/<job_id>.mp4 (+ /out/<job_id>_thumb.jpg) and returns logs/errors as JSON.

Safety: the container has no network route; additionally every request the page makes other
than the inline document is aborted. Time is virtual (Date/performance/rAF are frozen and
driven by the renderer) so renders are deterministic.
"""

import base64
import json
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

VENDOR = Path("/opt/runner/vendor")
OUT = Path("/out")
MAX_CONCURRENT = 2
RENDER_TIMEOUT_S = 180
SECONDS_PER_FRAME = 2.0  # ceiling for runaway scenes; a healthy render is ~0.3 s/frame, ~1 s/frame with both slots busy
_slots = threading.Semaphore(MAX_CONCURRENT)
_JOB = re.compile(r"^[a-zA-Z0-9_-]{6,64}$")

LIBS = "\n".join(
    (VENDOR / f"{n}.min.js").read_text() for n in ("gsap", "DrawSVGPlugin", "MotionPathPlugin", "TextPlugin", "SplitText")
)

# Virtual clock + seeded randomness, installed before any scene code runs.
DETERMINISM = r"""
(() => {
  let now = 0;
  window.__mhSetTime = (ms) => { now = ms; };
  const t0 = 1767225600000; // fixed epoch
  Date.now = () => t0 + now;
  performance.now = () => now;
  window.requestAnimationFrame = () => 0;
  window.cancelAnimationFrame = () => {};
  let s = 0x9e3779b9;
  Math.random = () => { s |= 0; s = (s + 0x6D2B79F5) | 0; let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
  window.__mhErrors = [];
  window.addEventListener('error', e => window.__mhErrors.push(String(e.message)));
})();
"""

class LayoutError(RuntimeError):
    pass


# Final-frame layout check: visible content inside a keep-clear zone (e.g. the face-cam circle) or
# text running off the stage. Backgrounds, wires, the footer and stage-sized wrappers are ignored.
PROBE_JS = r"""
([zones, MIN_PX]) => {
  const stage = document.getElementById('stage');
  const S = stage.getBoundingClientRect(), W = S.width, H = S.height, out = new Set();
  const texts = [], advice = [];
  let box = null;
  const ignore = '.mh-bg,.mh-footer,.mh-glow,.st-bg,.st-floor,.st-light,svg.mh-wires,.mh-sparkle,template';
  const chrome = '.vsc-title,.vsc-tab,.vsc-status,.vsc-gutter,.ln-n';  // realistic UI chrome: small by design
  const visible = (el) => { for (let e = el; e && e !== stage; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) < 0.08) return false; }
    return true; };
  const label = (el) => { const t = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40);
    return el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.split(' ')[0] : '') + (t ? ` "${t}"` : ''); };
  for (const el of stage.querySelectorAll('*')) {
    if (el.closest(ignore)) continue;
    const svgRoot = el.closest('svg');
    if (svgRoot && svgRoot !== el) continue;           // judge inline SVGs as a whole
    const ownText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (!(ownText || el.children.length === 0 || el.tagName.toLowerCase() === 'svg')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.width * r.height > 0.6 * W * H || !visible(el)) continue;
    const x = r.left - S.left, y = r.top - S.top;
    for (const z of zones) {
      const ix = Math.max(0, Math.min(x + r.width, z.x + z.w) - Math.max(x, z.x));
      const iy = Math.max(0, Math.min(y + r.height, z.y + z.h) - Math.max(y, z.y));
      if (ix * iy > 400) out.add(`${label(el)} overlaps the ${z.name} zone (keep x<${z.x} or y<${z.y})`);
    }
    if (ownText && (x < -4 || y < -4 || x + r.width > W + 4 || y + r.height > H + 4))
      out.add(`${label(el)} runs off the stage`);
    if (ownText) {
      const px = parseFloat(getComputedStyle(el).fontSize) * 1080 / Math.min(W, H);  // 1080p-equivalent
      if (px < MIN_PX - 0.5 && !el.closest(chrome)) out.add(`${label(el)} is ${Math.round(px)}px — minimum ${MIN_PX}px, unreadable on phones`);
      texts.push({el, x, y, w: r.width, h: r.height});
    }
    box = box ? {l: Math.min(box.l, x), t: Math.min(box.t, y), r: Math.max(box.r, x + r.width),
                 b: Math.max(box.b, y + r.height)} : {l: x, t: y, r: x + r.width, b: y + r.height};
  }
  for (let i = 0; i < texts.length; i++) for (let j = i + 1; j < texts.length; j++) {
    const a = texts[i], b = texts[j];
    if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
    const ix = Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x));
    const iy = Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
    if (ix * iy > 0.25 * Math.min(a.w * a.h, b.w * b.h)) out.add(`${label(a.el)} overlaps ${label(b.el)}`);
  }
  if (box) {  // balance: the final frame should use the stage, not one corner of it
    const cw = (box.r - box.l) / W, ch = (box.b - box.t) / H;
    const cx = (box.l + box.r) / 2 / W, cy = (box.t + box.b) / 2 / H;
    const pc = (v) => Math.round(v * 100) + '%', fix = [];
    if (cw < 0.45) fix.push(`it spans only ${pc(cw)} of the width — widen it to ≥ 55%`);
    if (ch < 0.35) fix.push(`it spans only ${pc(ch)} of the height — use ≥ 40%`);
    if (cx < 0.3) fix.push(`it sits in the left part (centre x=${pc(cx)}) — move it right`);
    if (cx > 0.7) fix.push(`it sits in the right part (centre x=${pc(cx)}) — move it left`);
    if (cy < 0.3) fix.push(`it is top-heavy (centre y=${pc(cy)}) with empty space below — move it down / use the middle band`);
    if (cy > 0.7) fix.push(`it sits low (centre y=${pc(cy)}) — move it up`);
    if (fix.length) advice.push('composition of the final frame: ' + fix.join('; '));
  }
  return {issues: [...out].slice(0, 8), advice};
}
"""
MIN_TEXT_PX = 20

FREEZE_CSS ="*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent}"


def build_page(req: dict) -> str:
    w, h = int(req["width"]), int(req["height"])
    return f"""<!doctype html><html><head><meta charset="utf-8">
<script>{DETERMINISM}</script>
<style>html,body{{margin:0;padding:0;width:{w}px;height:{h}px;overflow:hidden;background:#000}}
#stage{{position:relative;width:{w}px;height:{h}px;overflow:hidden}}
{req.get("theme_css", "")}
{req.get("css", "")}
{FREEZE_CSS}</style>
<script>{LIBS}</script>
<script>gsap.registerPlugin(DrawSVGPlugin, MotionPathPlugin, TextPlugin, SplitText);
gsap.ticker.lagSmoothing(0);</script>
</head><body><div id="stage">{req.get("html", "")}</div>
<script>
try {{
{req.get("js", "")}
}} catch (e) {{ window.__mhErrors.push('scene script: ' + e.message); }}
window.__mhTimeline = window.__scene || gsap.globalTimeline;
window.__mhTimeline.pause();
gsap.globalTimeline.pause();
window.__mhSeek = (sec) => {{
  window.__mhSetTime(sec * 1000);
  window.__mhTimeline.seek(sec, false);
  if (window.__mhTimeline !== gsap.globalTimeline) gsap.globalTimeline.seek(sec, false);
}};
window.__mhDuration = window.__mhTimeline.duration();
</script></body></html>"""


def render_still(req: dict) -> dict:
    """One PNG of the final state (cover photos / thumbnails) — same sandbox, no video encode."""
    job = req["job_id"]
    w, h = int(req["width"]), int(req["height"])
    scale = float(req.get("scale", 1))
    out = OUT / f"{job}.png"
    logs: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--disable-dev-shm-usage", "--disable-gpu", "--font-render-hinting=none"],
                                         chromium_sandbox=False)  # fmt: skip
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=scale, offline=True)
            ctx.route("**/*", lambda r: r.abort())
            page = ctx.new_page()
            page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"[:500]))
            page.set_content(build_page(req), wait_until="load", timeout=30_000)
            page.evaluate("document.fonts.ready")
            errors = page.evaluate("window.__mhErrors")
            if errors:
                raise RuntimeError("; ".join(errors)[:2000])
            page.evaluate("(t) => window.__mhSeek(t)", int(req["duration_ms"]) / 1000)
            cdp = ctx.new_cdp_session(page)
            out.write_bytes(base64.b64decode(cdp.send("Page.captureScreenshot", {"format": "png"})["data"]))
            browser.close()
        return {"ok": True, "png": out.name, "logs": logs[-20:]}
    except Exception as e:  # noqa: BLE001
        out.unlink(missing_ok=True)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:3000], "logs": logs[-20:]}


def render(req: dict) -> dict:
    if req.get("still"):
        return render_still(req)
    job = req["job_id"]
    w, h, fps = int(req["width"]), int(req["height"]), int(req["fps"])
    scale = float(req.get("scale", 1))
    frames = max(1, round(int(req["duration_ms"]) * fps / 1000))
    out = OUT / f"{job}.mp4"
    thumb = OUT / f"{job}_thumb.jpg"
    logs: list[str] = []
    started = time.monotonic()
    layout_warnings: list[str] = []
    qa_names: list[str] = []

    ff = subprocess.Popen(  # noqa: S603
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg",
         "-i", "-", "-vf", f"scale={int(w * scale)}:{int(h * scale)}:flags=lanczos,format=yuv420p",
         "-c:v", "libx264", "-preset", "fast", "-crf", "14", "-r", str(fps), "-frames:v", str(frames),
         "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )  # fmt: skip
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                args=["--disable-dev-shm-usage", "--disable-gpu", "--font-render-hinting=none",
                      "--disable-background-networking", "--no-first-run"],
                chromium_sandbox=False,  # the container is the sandbox (no net, ro fs, no caps)
            )  # fmt: skip
            ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=scale,
                                      java_script_enabled=True, offline=True)  # fmt: skip
            ctx.route("**/*", lambda r: r.abort())  # no requests other than the inline document
            page = ctx.new_page()
            page.on("console", lambda m: logs.append(f"console.{m.type}: {m.text}"[:500]))
            page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"[:500]))
            page.set_content(build_page(req), wait_until="load", timeout=30_000)
            cdp = ctx.new_cdp_session(page)  # CDP capture: ~4x faster than page.screenshot
            page.evaluate("document.fonts.ready")
            errors = page.evaluate("window.__mhErrors")
            tl_dur = page.evaluate("window.__mhDuration")
            if errors:
                raise RuntimeError("; ".join(errors)[:2000])
            probe = req.get("probe")
            if probe:
                page.evaluate("(t) => window.__mhSeek(t)", (frames - 1) / fps)
                res = page.evaluate(PROBE_JS, [probe.get("safe_zones", []), int(probe.get("min_text_px", MIN_TEXT_PX))])
                issues = res["issues"]
                if issues and probe.get("mode", "fail") == "fail":
                    raise LayoutError("layout: " + "; ".join(issues + res["advice"]))
                layout_warnings = issues + res["advice"]
                # fresh page so callbacks fired while probing can't leak into the real render
                page.set_content(build_page(req), wait_until="load", timeout=30_000)
                page.evaluate("document.fonts.ready")
            mid = frames // 2
            qa_at = {min(frames - 1, max(0, round(float(t) * fps))): k for k, t in enumerate(req.get("qa_frames") or [])}
            qa_clip = {"x": 0, "y": 0, "width": w, "height": h, "scale": min(1.0, 960 / w)}
            limit_s = max(RENDER_TIMEOUT_S, frames * SECONDS_PER_FRAME)  # long shots get proportionally longer
            for i in range(frames):
                if time.monotonic() - started > limit_s:
                    raise TimeoutError(f"render exceeded {limit_s:.0f}s at frame {i}/{frames}")
                page.evaluate("(t) => window.__mhSeek(t)", i / fps)
                shot = cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 95, "optimizeForSpeed": True})
                frame = base64.b64decode(shot["data"])
                ff.stdin.write(frame)
                if i == mid:
                    thumb.write_bytes(base64.b64decode(cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 85})["data"]))
                if i in qa_at:  # downscaled frames for the vision-QA review
                    shot = cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 82, "clip": qa_clip})
                    name = f"{job}_qa{qa_at[i]}.jpg"
                    (OUT / name).write_bytes(base64.b64decode(shot["data"]))
                    qa_names.append(name)
            late = page.evaluate("window.__mhErrors")
            browser.close()
        ff.stdin.close()
        if ff.wait(timeout=120) != 0:
            raise RuntimeError("ffmpeg: " + ff.stderr.read().decode(errors="replace")[-1000:])
        if late:
            logs.append("runtime errors: " + "; ".join(late)[:1000])
        return {
            "ok": True, "path": out.name, "thumb": thumb.name, "qa_frames": qa_names, "frames": frames, "timeline_s": tl_dur,
            "render_s": round(time.monotonic() - started, 1), "logs": logs[-50:], "layout_warnings": layout_warnings,
        }  # fmt: skip
    except Exception as e:  # noqa: BLE001
        ff.kill()
        out.unlink(missing_ok=True)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:3000], "logs": logs[-50:]}


_FONT_CSS: str | None = None


def embedded_fonts() -> str:
    """Brand fonts as data-URI @font-face rules so a standalone file looks identical on any machine."""
    global _FONT_CSS
    if _FONT_CSS is None:
        rules = []
        files = [("Oswald", "200 700", "/usr/share/fonts/brand/Oswald.ttf"),
                 ("Caveat", "400 700", "/usr/share/fonts/brand/Caveat.ttf")] + [
            ("Poppins", wt, f"/usr/share/fonts/brand/Poppins-{n}.ttf")
            for n, wt in (("Regular", "400"), ("Medium", "500"), ("SemiBold", "600"), ("Bold", "700"))
        ]
        for fam, weight, path in files:
            p = Path(path)
            if p.exists():
                b64 = base64.b64encode(p.read_bytes()).decode()
                rules.append(f"@font-face{{font-family:'{fam}';font-weight:{weight};"
                             f"src:url(data:font/ttf;base64,{b64}) format('truetype')}}")  # fmt: skip
        _FONT_CSS = "\n".join(rules)
    return _FONT_CSS


def build_standalone(req: dict) -> str:
    """Self-contained, auto-playing HTML (libraries + brand fonts inline) that scales the fixed stage to any window."""
    w, h = int(req["width"]), int(req["height"])
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{req.get("title", "mhsquare animation")}</title>
<style>{embedded_fonts()}
html,body{{margin:0;height:100%;background:#060b14;overflow:hidden}}
#stage{{position:absolute;left:50%;top:50%;width:{w}px;height:{h}px;overflow:hidden;transform-origin:center center}}
{req.get("theme_css", "")}
{req.get("css", "")}</style>
<script>{LIBS}</script>
<script>gsap.registerPlugin(DrawSVGPlugin, MotionPathPlugin, TextPlugin, SplitText);</script>
</head><body><div id="stage">{req.get("html", "")}</div>
<script>
const W = {w}, H = {h}, stage = document.getElementById("stage");
function fit() {{ const scale = Math.min(innerHeight / H, innerWidth / W);
  stage.style.transform = `translate(-50%,-50%) scale(${{scale}})`; }}
addEventListener("resize", fit); fit();
{req.get("js", "")}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"status": "ok", "gsap": "3.15.1"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/render", "/standalone"):
            return self._json(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length", 0))
        if n > 4_000_000:
            return self._json(413, {"error": "scene too large"})
        try:
            req = json.loads(self.rfile.read(n))
            if not _JOB.match(str(req.get("job_id", ""))):
                return self._json(400, {"error": "bad job_id"})
            if not (0 < int(req["duration_ms"]) <= 120_000 and int(req["fps"]) in (24, 25, 30, 50, 60)):
                return self._json(400, {"error": "bad duration/fps"})
        except (ValueError, KeyError) as e:
            return self._json(400, {"error": str(e)})
        if self.path == "/standalone":
            return self._json(200, {"html": build_standalone(req)})
        # Never queue inside the HTTP call: waiting here would eat the client's render timeout. Say "busy" and
        # let the client retry; the render timeout then only measures actual rendering.
        if not _slots.acquire(timeout=2):
            return self._json(503, {"error": "busy", "busy": True})
        try:
            self._json(200, render(req))
        finally:
            _slots.release()

    def log_message(self, fmt: str, *args) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()  # noqa: S104
