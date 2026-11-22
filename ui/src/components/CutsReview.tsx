import { useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, Scissors } from "lucide-react";
import { Waveform } from "./Waveform";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { fmtMs, useApproveCuts, useCuts, useWaveform, type Cut, type Project } from "@/lib/projects";
import { cn } from "@/lib/utils";

/** Plays audio and, when `skipCuts`, jumps over enabled cut regions (live preview of the edit). */
function usePlayer(cuts: Cut[], skipCuts: boolean) {
  const [audio, setAudio] = useState<HTMLAudioElement | null>(null);
  const [ms, setMs] = useState(0);
  const [playing, setPlaying] = useState(false);
  const stopAt = useRef<number | null>(null);

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const a = audio;
      if (a) {
        let t = a.currentTime * 1000;
        if (skipCuts && !a.paused) {
          const c = cuts.find((c) => c.enabled && t >= c.start_ms && t < c.end_ms - 10);
          if (c) {
            a.currentTime = c.end_ms / 1000;
            t = c.end_ms;
          }
        }
        if (stopAt.current !== null && t >= stopAt.current) {
          a.pause();
          stopAt.current = null;
        }
        setMs(t);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [audio, cuts, skipCuts]);

  const seek = (t: number) => {
    if (audio) audio.currentTime = t / 1000;
    setMs(t);
  };
  const playRange = (from: number, to: number | null) => {
    seek(Math.max(0, from));
    stopAt.current = to;
    audio?.play();
  };
  const toggle = () => {
    const a = audio;
    if (!a) return;
    stopAt.current = null;
    if (a.paused) a.play();
    else a.pause();
  };
  return { setAudio, ms, playing, setPlaying, seek, playRange, toggle };
}

export function CutsReview({ project }: { project: Project }) {
  const { data, isLoading, error } = useCuts(project.id, true);
  const wave = useWaveform(project.id, "clean", true);
  const approve = useApproveCuts(project.id);
  const [enabled, setEnabled] = useState<Record<string, boolean> | null>(null);
  const [filter, setFilter] = useState<"all" | "filler" | "silence">("all");
  const [preview, setPreview] = useState(true);
  const [focus, setFocus] = useState<string | null>(null);

  const state = useMemo(
    () => enabled ?? Object.fromEntries((data?.ranges ?? []).map((c) => [c.id, c.enabled])),
    [enabled, data],
  );
  const cuts = useMemo(
    () => (data?.ranges ?? []).map((c) => ({ ...c, enabled: state[c.id] ?? c.enabled })),
    [data, state],
  );
  const player = usePlayer(cuts, preview);

  if (isLoading || wave.isLoading) return <p className="text-muted">Loading cuts…</p>;
  if (error || !data) return <p className="text-danger">{error?.message}</p>;

  const toggle = (id: string) => setEnabled({ ...state, [id]: !state[id] });
  const setAll = (v: boolean) =>
    setEnabled(
      Object.fromEntries(cuts.map((c) => [c.id, filter === "all" || c.kind === filter ? v : c.enabled])),
    );
  const removed = cuts.filter((c) => c.enabled).reduce((s, c) => s + (c.end_ms - c.start_ms), 0);
  const shown = cuts.filter((c) => filter === "all" || c.kind === filter);
  const counts = {
    filler: cuts.filter((c) => c.kind === "filler").length,
    silence: cuts.filter((c) => c.kind === "silence").length,
  };

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Review cuts</CardTitle>
          <p className="text-sm text-muted">
            Red regions get removed. Click one to keep it. With preview on, playback skips the cuts, so you
            hear the edit.
          </p>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {wave.data && (
            <Waveform
              data={wave.data}
              cuts={cuts}
              currentMs={player.ms}
              onSeek={player.seek}
              onToggleCut={toggle}
              focusCutId={focus}
            />
          )}
          {data.audio && (
            <audio
              ref={player.setAudio}
              src={data.audio.url}
              preload="auto"
              onPlay={() => player.setPlaying(true)}
              onPause={() => player.setPlaying(false)}
            />
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={player.toggle} size="lg" aria-label={player.playing ? "Pause" : "Play"}>
              {player.playing ? <Pause size={18} /> : <Play size={18} />}
            </Button>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={preview}
                onChange={(e) => setPreview(e.target.checked)}
                className="accent-[var(--accent)]"
              />
              Preview with cuts
            </label>
            <span className="ml-auto font-mono text-sm text-muted">
              −{fmtMs(removed)} → {fmtMs(data.duration_ms - removed)}
            </span>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            {(["all", "filler", "silence"] as const).map((f) => (
              <Button
                key={f}
                size="sm"
                variant={filter === f ? "secondary" : "ghost"}
                onClick={() => setFilter(f)}
              >
                {f === "all"
                  ? `All (${cuts.length})`
                  : f === "filler"
                    ? `Fillers (${counts.filler})`
                    : `Pauses (${counts.silence})`}
              </Button>
            ))}
            <span className="ml-auto flex gap-1">
              <Button size="sm" variant="ghost" onClick={() => setAll(true)}>
                Cut all
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setAll(false)}>
                Keep all
              </Button>
            </span>
          </div>

          {cuts.length === 0 && (
            <p className="py-4 text-center text-sm text-muted">No fillers or long pauses found.</p>
          )}
          <ul className="flex max-h-[28rem] flex-col divide-y divide-border overflow-y-auto">
            {shown.map((c) => (
              <li
                key={c.id}
                className={cn("flex items-center gap-3 py-2.5", focus === c.id && "bg-surface-2/60")}
                onMouseEnter={() => setFocus(c.id)}
              >
                <input
                  type="checkbox"
                  checked={c.enabled}
                  onChange={() => toggle(c.id)}
                  aria-label={`Cut ${c.text} at ${fmtMs(c.start_ms)}`}
                  className="h-5 w-5 accent-[var(--danger)]"
                />
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 font-mono text-[11px]",
                    c.kind === "filler" ? "bg-warning/15 text-warning" : "bg-surface-2 text-muted",
                  )}
                >
                  {c.kind === "filler" ? "filler" : "pause"}
                </span>
                <span
                  className={cn("min-w-0 flex-1 truncate text-sm", !c.enabled && "text-muted line-through")}
                >
                  {c.text}
                </span>
                <span className="hidden font-mono text-xs text-muted sm:inline">
                  {fmtMs(c.start_ms)} · {((c.end_ms - c.start_ms) / 1000).toFixed(2)}s
                </span>
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label="Play around this cut"
                  onClick={() => {
                    setFocus(c.id);
                    player.playRange(c.start_ms - 1500, c.end_ms + 1500);
                  }}
                >
                  <Play size={14} />
                </Button>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      <div className="sticky bottom-20 flex justify-end md:bottom-4">
        <Button
          size="lg"
          disabled={approve.isPending}
          onClick={() => approve.mutate(cuts.filter((c) => c.enabled).map((c) => c.id))}
          className="shadow-lg shadow-black/40"
        >
          <Scissors size={16} /> Approve {cuts.filter((c) => c.enabled).length} cuts
        </Button>
      </div>
      {approve.error && <p className="text-sm text-danger">{approve.error.message}</p>}
    </div>
  );
}
