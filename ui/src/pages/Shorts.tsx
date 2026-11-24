import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Check,
  Clapperboard,
  Copy,
  Download,
  Film,
  Loader2,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useProject } from "@/lib/projects";
import { isBusy, useShortMutations, useShorts, type Short } from "@/lib/shorts";
import { cn } from "@/lib/utils";

const area =
  "w-full rounded-lg border border-border bg-surface-2 p-2 text-sm leading-relaxed focus:border-accent focus:outline-none";
const mmss = (ms: number) =>
  `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button
      size="sm"
      variant="ghost"
      aria-label={`Copy ${label}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard blocked: the text is still selectable */
        }
      }}
    >
      {done ? <Check size={14} /> : <Copy size={14} />}
    </Button>
  );
}

/** Shorts / Reels cut from the long video: plan, review, render, copy the details. */
export function ShortsPage() {
  const { id = "" } = useParams();
  const { data: p } = useProject(id);
  const { data, isLoading } = useShorts(id);
  const m = useShortMutations(id);
  const planning = data?.status.status === "running";
  const shorts = data?.shorts ?? [];
  const toRender = shorts.filter((s) => s.status === "planned" || s.status === "failed" || s.stale).length;
  const busy = shorts.filter(isBusy).length;
  const total = shorts.reduce((n, s) => n + s.duration_s, 0);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <Link to={`/projects/${id}`} className="text-muted hover:text-text" aria-label="Back to project">
          <ArrowLeft size={20} />
        </Link>
        <h1 className="truncate text-2xl font-semibold tracking-tight">Shorts & Reels · {p?.name}</h1>
      </div>

      <Card>
        <CardHeader className="flex-row flex-wrap items-center gap-2">
          <Clapperboard size={16} className="text-muted" />
          <CardTitle>Cut Shorts from this video</CardTitle>
          <span className="ml-auto flex gap-2">
            <Button
              variant={shorts.length ? "ghost" : "default"}
              size="sm"
              onClick={() => m.plan.mutate()}
              disabled={planning || m.plan.isPending || p?.status !== "planned"}
              title={p?.status === "planned" ? "" : "Plan the long video's shots first"}
            >
              {planning ? (
                <Loader2 size={14} className="animate-spin" />
              ) : shorts.length ? (
                <RefreshCw size={14} />
              ) : (
                <Sparkles size={14} />
              )}
              {planning ? "Finding Shorts…" : shorts.length ? "Find again" : "Find Shorts"}
            </Button>
            {shorts.length > 0 && (
              <Button
                size="sm"
                onClick={() => m.renderAll.mutate()}
                disabled={!toRender || m.renderAll.isPending}
              >
                {busy ? <Loader2 size={14} className="animate-spin" /> : <Film size={14} />}
                {busy ? `Rendering ${busy}…` : toRender ? `Render ${toRender}` : "All rendered"}
              </Button>
            )}
          </span>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm text-muted">
          <p>
            The AI picks self-contained 25–60 s moments (whole shots, so your narration never starts or ends
            mid-sentence), groups multi-step ideas into a series (Part 1, Part 2…) and orders them for daily
            posting. Each vertical video has the hook at the top, your animation re-drawn as a tall 9:16
            panel with word-by-word captions, and <b className="text-text">your talking head over its
            bottom-left corner</b> (the same moment of your face-cam recording; the scenes keep that corner
            clear). Rendering is free; finding Shorts is one small AI call. Already-rendered Shorts are kept
            when you find again.
          </p>
          {data?.status.status === "done" && (
            <p>
              {shorts.length} Shorts · {Math.round(total / 60)} min in total
              {data.status.cost_usd ? ` · $${data.status.cost_usd.toFixed(2)}` : ""}
            </p>
          )}
          {data?.status.status === "failed" && <p className="text-danger">{data.status.error}</p>}
          {(m.plan.error ?? m.renderAll.error) && (
            <p className="text-danger">{(m.plan.error ?? m.renderAll.error)?.message}</p>
          )}
        </CardContent>
      </Card>

      {isLoading && <p className="text-muted">Loading…</p>}
      <div className="flex flex-col gap-4">
        {shorts.map((s) => (
          <ShortCard key={`${s.id}|${s.title}|${s.hook}|${s.description}`} s={s} pid={id} />
        ))}
      </div>
    </div>
  );
}

function ShortCard({ s, pid }: { s: Short; pid: string }) {
  const m = useShortMutations(pid);
  const [hook, setHook] = useState(s.hook);
  const [title, setTitle] = useState(s.title);
  const [description, setDescription] = useState(s.description);
  const dirty = hook !== s.hook || title !== s.title || description !== s.description;
  const caption = `${title}\n\n${description}`;

  return (
    <Card>
      <CardContent className="flex flex-col gap-4 pt-5 md:flex-row">
        <div className="w-full shrink-0 md:w-56">
          {s.url && s.status === "done" ? (
            <video
              src={s.url}
              controls
              preload="metadata"
              className="aspect-[9/16] w-full rounded-lg bg-black"
            />
          ) : (
            <div className="relative aspect-[9/16] w-full overflow-hidden rounded-lg border border-border bg-black text-xs text-muted">
              {/* the real layout, in % of 1080×1920: band 0–240, panel 240–1760, face 64,1240 600×620 */}
              <div className="absolute inset-x-2 top-[4%] h-[8%] font-medium leading-tight text-text">{hook}</div>
              <div className="absolute inset-x-0 top-[12.5%] h-[79.2%] bg-surface-2 p-2 text-center">animation</div>
              <div className="absolute left-[5.9%] top-[64.6%] flex h-[32.3%] w-[55.6%] items-center justify-center border-2 border-dashed border-accent/60 bg-surface">
                your talking head
              </div>
              {isBusy(s) && (
                <div className="absolute inset-0 flex items-center justify-center bg-black/50">
                  <Loader2 className="animate-spin text-text" />
                </div>
              )}
            </div>
          )}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <span className="rounded-full bg-accent/20 px-2 py-0.5 font-medium text-text">Day {s.index}</span>
            {s.series && (
              <span className="rounded-full border border-accent px-2 py-0.5 text-text">
                {s.series} · Part {s.part}/{s.parts}
              </span>
            )}
            <span>
              {s.duration_s}s · {mmss(s.start_ms)}–{mmss(s.end_ms)} · shots {s.shot_from}–{s.shot_to} · score{" "}
              {s.score}/10
            </span>
            <span className="ml-auto flex items-center gap-1">
              {s.status === "failed" && <span className="text-danger">render failed</span>}
              {s.stale && <span className="text-warning">hook changed — render again</span>}
              {s.url && s.status === "done" && (
                <a
                  href={`${s.url}&dl=${encodeURIComponent(`short-${String(s.index).padStart(2, "0")}.mp4`)}`}
                  aria-label="Download Short"
                >
                  <Button size="sm" variant="ghost" type="button">
                    <Download size={14} />
                  </Button>
                </a>
              )}
              <Button
                size="sm"
                variant="ghost"
                aria-label="Render this Short"
                disabled={isBusy(s) || m.render.isPending}
                onClick={() => m.render.mutate(s.id)}
              >
                {isBusy(s) ? <Loader2 size={14} className="animate-spin" /> : <Film size={14} />}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label="Delete Short"
                onClick={() => m.remove.mutate(s.id)}
              >
                <Trash2 size={14} />
              </Button>
            </span>
          </div>
          {s.error && s.status === "failed" && <p className="text-xs text-danger">{s.error}</p>}
          <label className="flex flex-col gap-1 text-xs text-muted">
            On-screen hook
            <Input value={hook} onChange={(e) => setHook(e.target.value)} aria-label="Hook" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            <span className="flex items-center gap-2">
              Title <span className="font-mono">{title.length}/100</span>
              <span className="ml-auto">
                <CopyButton text={title} label="title" />
              </span>
            </span>
            <Input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Title" />
          </label>
          <label className="flex flex-col gap-1 text-xs text-muted">
            <span className="flex items-center gap-2">
              Description & hashtags (YouTube / Instagram caption)
              <span className="ml-auto flex">
                <CopyButton text={description} label="description" />
              </span>
            </span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={5}
              className={area}
              aria-label="Description"
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              disabled={!dirty || m.edit.isPending}
              onClick={() => m.edit.mutate({ id: s.id, hook, title, description })}
            >
              <Save size={14} /> Save
            </Button>
            <CopyButton text={caption} label="title and description" />
            <span className="text-xs text-muted">copy title + description</span>
            {s.why && <span className={cn("ml-auto text-xs text-muted")}>Why: {s.why}</span>}
          </div>
          {m.edit.error && <p className="text-xs text-danger">{m.edit.error.message}</p>}
        </div>
      </CardContent>
    </Card>
  );
}
