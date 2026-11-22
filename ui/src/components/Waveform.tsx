import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ZoomIn, ZoomOut } from "lucide-react";
import { Button } from "./ui/button";
import type { Cut, Waveform as WaveformData } from "@/lib/projects";
import { fmtMs } from "@/lib/projects";

const ZOOMS = [0, 20, 60, 150]; // px per second; 0 = fit to width
const HEIGHT = 120;

function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#888";
}

/** Canvas waveform that renders only the visible window, so long narrations stay sharp when zoomed. */
export function Waveform({
  data,
  cuts = [],
  currentMs,
  onSeek,
  onToggleCut,
  focusCutId,
}: {
  data: WaveformData;
  cuts?: Cut[];
  currentMs: number;
  onSeek: (ms: number) => void;
  onToggleCut?: (id: string) => void;
  focusCutId?: string | null;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [width, setWidth] = useState(600);
  const [zoomIdx, setZoomIdx] = useState(cuts.length ? 1 : 0);
  const [scrollLeft, setScrollLeft] = useState(0);

  const durationS = Math.max(0.001, data.duration_ms / 1000);
  const pxPerS = ZOOMS[zoomIdx] || width / durationS;
  const totalPx = Math.max(width, durationS * pxPerS);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);

  // keep the playhead / focused cut in view
  useEffect(() => {
    const el = scroller.current;
    if (!el || !ZOOMS[zoomIdx]) return;
    const focus = cuts.find((c) => c.id === focusCutId);
    const x = ((focus ? focus.start_ms : currentMs) / 1000) * pxPerS;
    if (x < el.scrollLeft + 40 || x > el.scrollLeft + width - 40) el.scrollLeft = Math.max(0, x - width / 3);
  }, [currentMs, focusCutId, pxPerS, width, zoomIdx, cuts]);

  const draw = useCallback(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.round(width * dpr);
    c.height = Math.round(HEIGHT * dpr);
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, HEIGHT);

    const t0 = scrollLeft / pxPerS;
    const t1 = (scrollLeft + width) / pxPerS;
    const x = (ms: number) => (ms / 1000) * pxPerS - scrollLeft;

    // cut regions
    for (const cut of cuts) {
      if (cut.end_ms / 1000 < t0 || cut.start_ms / 1000 > t1) continue;
      const x0 = x(cut.start_ms);
      const w = Math.max(2, x(cut.end_ms) - x0);
      ctx.fillStyle = cut.enabled ? "rgba(239,68,68,0.22)" : "rgba(138,147,166,0.10)";
      ctx.fillRect(x0, 0, w, HEIGHT);
      ctx.fillStyle = cut.enabled ? cssVar("--danger") : cssVar("--text-muted");
      ctx.fillRect(x0, 0, w, 3);
      if (cut.id === focusCutId) {
        ctx.strokeStyle = cssVar("--accent");
        ctx.lineWidth = 2;
        ctx.strokeRect(x0 + 1, 1, w - 2, HEIGHT - 2);
      }
    }

    // peaks
    const pps = data.peaks_per_second;
    const mid = HEIGHT / 2;
    ctx.fillStyle = cssVar("--accent-2");
    for (let px = 0; px < width; px++) {
      const a = Math.floor(((scrollLeft + px) / pxPerS) * pps);
      const b = Math.max(a + 1, Math.floor(((scrollLeft + px + 1) / pxPerS) * pps));
      let peak = 0;
      for (let i = a; i < b && i < data.peaks.length; i++) peak = Math.max(peak, data.peaks[i]);
      const h = Math.max(1, peak * (HEIGHT - 12));
      const inCut = cuts.some(
        (c) =>
          c.enabled &&
          (scrollLeft + px) / pxPerS >= c.start_ms / 1000 &&
          (scrollLeft + px) / pxPerS <= c.end_ms / 1000,
      );
      ctx.globalAlpha = inCut ? 0.3 : 0.85;
      ctx.fillRect(px, mid - h / 2, 1, h);
    }
    ctx.globalAlpha = 1;

    // playhead
    const ph = x(currentMs);
    if (ph >= 0 && ph <= width) {
      ctx.fillStyle = cssVar("--text");
      ctx.fillRect(ph, 0, 1.5, HEIGHT);
    }
  }, [cuts, currentMs, data, focusCutId, pxPerS, scrollLeft, width]);

  useEffect(draw, [draw]);

  const onClick = (e: React.MouseEvent) => {
    const rect = (e.target as HTMLElement).getBoundingClientRect();
    const ms = ((scrollLeft + e.clientX - rect.left) / pxPerS) * 1000;
    const hit = cuts.find((c) => ms >= c.start_ms && ms <= c.end_ms);
    if (hit && onToggleCut) onToggleCut(hit.id);
    else onSeek(Math.max(0, Math.min(data.duration_ms, ms)));
  };

  return (
    <div className="flex flex-col gap-2">
      <div
        ref={scroller}
        className="relative overflow-x-auto rounded-lg border border-border bg-bg"
        onScroll={(e) => setScrollLeft((e.target as HTMLDivElement).scrollLeft)}
      >
        <div style={{ width: totalPx, height: HEIGHT }}>
          <canvas
            ref={canvas}
            onClick={onClick}
            className="sticky left-0 cursor-pointer"
            style={{ width, height: HEIGHT }}
            aria-label="Waveform. Click to seek; click a red region to toggle that cut."
          />
        </div>
      </div>
      <div className="flex items-center justify-between font-mono text-xs text-muted">
        <span>
          {fmtMs(currentMs)} / {fmtMs(data.duration_ms)}
        </span>
        <span className="flex gap-1">
          <Button
            variant="ghost"
            size="sm"
            disabled={zoomIdx === 0}
            onClick={() => setZoomIdx((z) => z - 1)}
            aria-label="Zoom out"
          >
            <ZoomOut size={14} />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={zoomIdx === ZOOMS.length - 1}
            onClick={() => setZoomIdx((z) => z + 1)}
            aria-label="Zoom in"
          >
            <ZoomIn size={14} />
          </Button>
        </span>
      </div>
    </div>
  );
}
