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

FREEZE_CSS = "*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent}"


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


def render(req: dict) -> dict:
    job = req["job_id"]
    w, h, fps = int(req["width"]), int(req["height"]), int(req["fps"])
    scale = float(req.get("scale", 1))
    frames = max(1, round(int(req["duration_ms"]) * fps / 1000))
    out = OUT / f"{job}.mp4"
    thumb = OUT / f"{job}_thumb.jpg"
    logs: list[str] = []
    started = time.monotonic()

    ff = subprocess.Popen(  # noqa: S603
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "png",
         "-i", "-", "-vf", f"scale={int(w * scale)}:{int(h * scale)}:flags=lanczos,format=yuv420p",
         "-c:v", "libx264", "-preset", "medium", "-crf", "14", "-r", str(fps), "-frames:v", str(frames),
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
            mid = frames // 2
            for i in range(frames):
                if time.monotonic() - started > RENDER_TIMEOUT_S:
                    raise TimeoutError(f"render exceeded {RENDER_TIMEOUT_S}s at frame {i}/{frames}")
                page.evaluate("(t) => window.__mhSeek(t)", i / fps)
                shot = cdp.send("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True})
                png = base64.b64decode(shot["data"])
                ff.stdin.write(png)
                if i == mid:
                    thumb.write_bytes(base64.b64decode(cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 85})["data"]))
            late = page.evaluate("window.__mhErrors")
            browser.close()
        ff.stdin.close()
        if ff.wait(timeout=120) != 0:
            raise RuntimeError("ffmpeg: " + ff.stderr.read().decode(errors="replace")[-1000:])
        if late:
            logs.append("runtime errors: " + "; ".join(late)[:1000])
        return {
            "ok": True, "path": out.name, "thumb": thumb.name, "frames": frames, "timeline_s": tl_dur,
            "render_s": round(time.monotonic() - started, 1), "logs": logs[-50:],
        }  # fmt: skip
    except Exception as e:  # noqa: BLE001
        ff.kill()
        out.unlink(missing_ok=True)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:3000], "logs": logs[-50:]}


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
        if self.path != "/render":
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
        with _slots:
            self._json(200, render(req))

    def log_message(self, fmt: str, *args) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()  # noqa: S104
