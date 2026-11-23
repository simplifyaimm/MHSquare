import { useEffect, useRef, useState } from "react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Clapperboard,
  Download,
  Film,
  LayoutTemplate,
  Loader2,
  Merge,
  Pencil,
  RefreshCw,
  Scissors,
  Sparkles,
  Wand2,
} from "lucide-react";
import { StateChip } from "./StateChip";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { fmtMs } from "@/lib/projects";
import {
  bestTake,
  BUSY_STATES,
  TYPE_COLOR,
  TYPE_LABEL,
  MODES,
  useAccept,
  useGenerate,
  useMerge,
  usePatchShot,
  usePreview,
  useRegenerate,
  useRenderFinal,
  useShots,
  useSplit,
  useTemplatize,
  templateName,
  type Shot,
  type ShotsResponse,
  type ExplainMode,
  type VisualType,
} from "@/lib/shots";
import { cn } from "@/lib/utils";

export function ShotBoard({ projectId }: { projectId: string }) {
  const { data, isLoading, error } = useShots(projectId);
  const [sel, setSel] = useState(0);
  if (isLoading) return <p className="text-muted">Loading shots…</p>;
  if (error || !data) return <p className="text-danger">{error?.message}</p>;
  const shots = data.shots;
  const current = shots[Math.min(sel, shots.length - 1)];

  return (
    <div className="flex flex-col gap-4">
      <BoardHeader pid={projectId} data={data} />
      <TimelineStrip shots={shots} selected={current?.index ?? 0} onSelect={setSel} />
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
        {current && (
          <ShotReview
            key={current.id}
            pid={projectId}
            shot={current}
            total={shots.length}
            onNav={(d) => setSel((i) => Math.max(0, Math.min(shots.length - 1, i + d)))}
            next={shots[current.index + 1]}
          />
        )}
        <ShotList shots={shots} selected={current?.index ?? 0} onSelect={setSel} />
      </div>
      <PreviewCard pid={projectId} data={data} />
    </div>
  );
}

function BoardHeader({ pid, data }: { pid: string; data: ShotsResponse }) {
  const generate = useGenerate(pid);
  const templatize = useTemplatize(pid);
  const tz = data.plan_meta.templatize;
  const converting = tz?.status === "running";
  const freeForm = data.shots.filter(
    (s) =>
      s.visual_type === "code_animation" &&
      !templateName(s) &&
      !["review", "accepted", ...BUSY_STATES].includes(s.state),
  ).length;
  const rechoosable = data.shots.filter(
    (s) => templateName(s) && !["accepted", ...BUSY_STATES].includes(s.state),
  ).length;
  const counts = data.shots.reduce<Record<string, number>>(
    (m, s) => ({ ...m, [s.state]: (m[s.state] ?? 0) + 1 }),
    {},
  );
  const pending = data.shots.filter((s) => ["planned", "failed", "paused_budget"].includes(s.state)).length;
  const busy = data.shots.filter((s) => BUSY_STATES.includes(s.state)).length;
  const accepted = counts.accepted ?? 0;
  return (
    <Card>
      <CardContent className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">{data.style_guide.summary || "Shot list"}</div>
          <div className="font-mono text-xs text-muted">
            {data.shots.length} shots · {accepted} accepted · {counts.review ?? 0} to review
            {busy ? ` · ${busy} generating` : ""} · spent ${data.spent_usd.toFixed(2)}
            {pending ? ` · est. $${data.estimate_usd.toFixed(2)} remaining` : ""}
          </div>
        </div>
        {(freeForm > 0 || converting) && (
          <Button
            variant="secondary"
            onClick={() => templatize.mutate(false)}
            disabled={converting || templatize.isPending || busy > 0}
            title="Rewrite unfinished AI-written scenes as brand templates: they then render with no AI cost"
          >
            {converting ? <Loader2 size={16} className="animate-spin" /> : <LayoutTemplate size={16} />}
            {converting
              ? `Converting ${tz?.done ?? 0}/${tz?.total ?? 0}…`
              : `Convert ${freeForm} to templates`}
          </Button>
        )}
        {freeForm === 0 && !converting && rechoosable > 0 && (
          <Button
            variant="secondary"
            onClick={() => templatize.mutate(true)}
            disabled={templatize.isPending || busy > 0}
            title="Pick templates again for a better mix (accepted shots are kept; changed shots need a new take, free to render)"
          >
            <LayoutTemplate size={16} />
            Re-choose templates
          </Button>
        )}
        <Button
          onClick={() => generate.mutate(undefined)}
          disabled={!pending || generate.isPending || converting}
        >
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}
          Generate {pending ? `${pending} shot${pending > 1 ? "s" : ""}` : "all"}
        </Button>
        {tz && tz.status !== "running" && (
          <p className={`w-full text-sm ${tz.status === "done" ? "text-muted" : "text-warning"}`}>
            Templates: {tz.converted} of {tz.total} shots converted (render with no AI cost)
            {tz.kept ? `, ${tz.kept} kept as custom scenes` : ""} · ${tz.cost_usd.toFixed(2)}
            {tz.status !== "done" && tz.error ? ` — stopped: ${tz.error}` : ""}
          </p>
        )}
        {generate.error && <p className="w-full text-sm text-danger">{generate.error.message}</p>}
        {templatize.error && <p className="w-full text-sm text-danger">{templatize.error.message}</p>}
      </CardContent>
    </Card>
  );
}

function TimelineStrip({
  shots,
  selected,
  onSelect,
}: {
  shots: Shot[];
  selected: number;
  onSelect: (i: number) => void;
}) {
  const total = shots.at(-1)?.end_ms ?? 1;
  return (
    <div
      className="flex h-10 w-full overflow-hidden rounded-lg border border-border"
      role="tablist"
      aria-label="Timeline"
    >
      {shots.map((s) => (
        <button
          key={s.id}
          role="tab"
          aria-selected={s.index === selected}
          title={`#${s.index + 1} ${TYPE_LABEL[s.visual_type]} · ${fmtMs(s.start_ms)}`}
          onClick={() => onSelect(s.index)}
          className={cn(
            "relative h-full border-r border-bg/60 transition-opacity last:border-r-0",
            s.index === selected ? "opacity-100" : "opacity-55 hover:opacity-85",
          )}
          style={{
            width: `${((s.end_ms - s.start_ms) / total) * 100}%`,
            background: TYPE_COLOR[s.visual_type],
          }}
        >
          {s.state === "accepted" && <Check size={12} className="absolute top-1 right-1 text-white" />}
          {BUSY_STATES.includes(s.state) && <span className="absolute inset-0 animate-pulse bg-white/20" />}
          {s.index === selected && <span className="absolute inset-x-0 bottom-0 h-1 bg-white" />}
        </button>
      ))}
    </div>
  );
}

function ShotList({
  shots,
  selected,
  onSelect,
}: {
  shots: Shot[];
  selected: number;
  onSelect: (i: number) => void;
}) {
  return (
    <Card className="max-h-[42rem] overflow-y-auto">
      <ul className="divide-y divide-border">
        {shots.map((s) => {
          const t = bestTake(s);
          return (
            <li key={s.id}>
              <button
                onClick={() => onSelect(s.index)}
                className={cn(
                  "flex w-full gap-3 p-3 text-left hover:bg-surface-2",
                  s.index === selected && "bg-surface-2",
                )}
              >
                <div className="relative aspect-video w-24 shrink-0 overflow-hidden rounded-md bg-bg">
                  {t?.thumb_url ? (
                    <img src={t.thumb_url} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <span className="absolute inset-0 grid place-items-center font-mono text-xs text-muted">
                      #{s.index + 1}
                    </span>
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ background: TYPE_COLOR[s.visual_type] }}
                    />
                    <span className="font-mono text-xs text-muted">
                      {fmtMs(s.start_ms)} · {((s.end_ms - s.start_ms) / 1000).toFixed(1)}s
                    </span>
                    <span className="ml-auto">
                      <StateChip state={s.state} />
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-2 text-sm text-muted">{s.transcript_text}</p>
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function ShotReview({
  pid,
  shot,
  total,
  onNav,
  next,
}: {
  pid: string;
  shot: Shot;
  total: number;
  onNav: (d: number) => void;
  next?: Shot;
}) {
  const accept = useAccept(pid);
  const regen = useRegenerate(pid);
  const generate = useGenerate(pid);
  const [takeId, setTakeId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [keepSeed, setKeepSeed] = useState(false);
  const [editing, setEditing] = useState(false);
  const touch = useRef<number | null>(null);

  const done = shot.takes.filter((t) => t.status === "done");
  const take = shot.takes.find((t) => t.id === takeId) ?? bestTake(shot) ?? shot.takes.at(-1);
  const busy = BUSY_STATES.includes(shot.state);
  const lastFailed = shot.takes.at(-1)?.status === "failed" ? shot.takes.at(-1) : undefined;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest("input,textarea,select")) return;
      if (e.key === "ArrowRight") onNav(1);
      if (e.key === "ArrowLeft") onNav(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onNav]);

  return (
    <Card
      onTouchStart={(e) => (touch.current = e.touches[0].clientX)}
      onTouchEnd={(e) => {
        if (touch.current === null) return;
        const dx = e.changedTouches[0].clientX - touch.current;
        if (Math.abs(dx) > 70) onNav(dx < 0 ? 1 : -1);
        touch.current = null;
      }}
    >
      <CardHeader className="flex-row flex-wrap items-center gap-2">
        <Button
          size="icon"
          variant="ghost"
          onClick={() => onNav(-1)}
          disabled={shot.index === 0}
          aria-label="Previous shot"
        >
          <ChevronLeft size={18} />
        </Button>
        <CardTitle>
          Shot {shot.index + 1}/{total}
        </CardTitle>
        <span
          className="rounded-full px-2 py-0.5 font-mono text-xs text-white"
          style={{ background: TYPE_COLOR[shot.visual_type] }}
        >
          {TYPE_LABEL[shot.visual_type]}
          {shot.visual_type === "code_animation"
            ? templateName(shot)
              ? ` · template: ${templateName(shot)}`
              : ` · ${shot.theme}`
            : ""}
        </span>
        <StateChip state={shot.state} />
        <Button
          size="icon"
          variant="ghost"
          className="ml-auto"
          onClick={() => onNav(1)}
          disabled={!next}
          aria-label="Next shot"
        >
          <ChevronRight size={18} />
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="relative aspect-video overflow-hidden rounded-lg bg-bg">
          {take?.video_url ? (
            <video
              key={take.id}
              src={take.video_url}
              poster={take.thumb_url ?? undefined}
              controls
              autoPlay
              muted
              loop
              playsInline
              className="h-full w-full"
            />
          ) : (
            <div className="absolute inset-0 grid place-items-center text-sm text-muted">
              {busy ? (
                <span className="flex items-center gap-2 text-accent-2">
                  <Loader2 size={18} className="animate-spin" /> Writing & rendering the scene…
                </span>
              ) : (
                "Not generated yet"
              )}
            </div>
          )}
        </div>

        <div>
          <div className="font-mono text-xs text-muted">
            {fmtMs(shot.start_ms)} → {fmtMs(shot.end_ms)} ·{" "}
            {((shot.end_ms - shot.start_ms) / 1000).toFixed(1)}s
          </div>
          <p className="mt-1 text-base leading-relaxed">“{shot.transcript_text}”</p>
        </div>

        {(shot.error || lastFailed?.error) && shot.state !== "review" && shot.state !== "accepted" && (
          <details className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm text-danger">
            <summary className="cursor-pointer">
              {shot.state === "paused_budget"
                ? "Paused: budget or provider credits exhausted"
                : shot.state === "needs_edit"
                  ? "Blocked by the provider's content policy — edit the brief"
                  : "Generation failed"}
            </summary>
            <pre className="mt-2 overflow-x-auto font-mono text-xs whitespace-pre-wrap text-muted">
              {shot.error}
              {lastFailed?.logs.length ? "\n\n" + lastFailed.logs.join("\n") : ""}
            </pre>
          </details>
        )}

        {done.length > 0 && (
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              <Button
                variant="success"
                size="lg"
                className="flex-1"
                disabled={
                  !take || take.status !== "done" || shot.accepted_take_id === take.id || accept.isPending
                }
                onClick={() =>
                  take &&
                  accept.mutate({ shotId: shot.id, takeId: take.id }, { onSuccess: () => next && onNav(1) })
                }
              >
                <Check size={18} /> {shot.accepted_take_id === take?.id ? "Accepted" : "Accept"}
              </Button>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="What should change? e.g. bigger text, show 3 partitions"
                className="h-12 flex-1 rounded-lg border border-border bg-surface-2 px-3 text-sm focus:border-accent focus:outline-none"
                aria-label="Revision note"
              />
              <Button
                variant="secondary"
                size="lg"
                disabled={busy || regen.isPending}
                onClick={() =>
                  regen.mutate({ id: shot.id, note, keep_seed: keepSeed }, { onSuccess: () => setNote("") })
                }
              >
                <RefreshCw size={16} /> Regenerate
              </Button>
            </div>
            <label className="flex items-center gap-2 text-xs text-muted">
              <input type="checkbox" checked={keepSeed} onChange={(e) => setKeepSeed(e.target.checked)} />{" "}
              keep seed
            </label>
          </div>
        )}

        {done.length === 0 && !busy && (
          <Button size="lg" onClick={() => generate.mutate([shot.id])} disabled={generate.isPending}>
            <Sparkles size={16} /> Generate this shot
          </Button>
        )}

        {shot.takes.length > 1 && (
          <div>
            <div className="mb-2 text-xs text-muted">Takes</div>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {shot.takes.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTakeId(t.id)}
                  className={cn(
                    "relative aspect-video w-28 shrink-0 overflow-hidden rounded-md border-2 bg-bg",
                    t.id === take?.id ? "border-accent" : "border-transparent",
                  )}
                  title={t.feedback_note ?? `take ${t.attempt}`}
                >
                  {t.thumb_url ? (
                    <img src={t.thumb_url} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <span className="grid h-full place-items-center font-mono text-[10px] text-muted">
                      {t.status}
                    </span>
                  )}
                  <span className="absolute bottom-0 left-0 bg-black/60 px-1 font-mono text-[10px]">
                    #{t.attempt}
                    {t.meta.theme ? ` ${String(t.meta.theme)}` : ""}
                    {t.id === shot.accepted_take_id ? " ✓" : ""}
                  </span>
                </button>
              ))}
            </div>
            {take?.feedback_note && <p className="mt-1 text-xs text-muted">Note: {take.feedback_note}</p>}
          </div>
        )}

        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" size="sm" onClick={() => setEditing((e) => !e)}>
            <Pencil size={14} /> {editing ? "Close editor" : "Edit brief / type"}
          </Button>
          {take?.status === "done" && take.generator.startsWith("html") && (
            <a href={`/api/takes/${take.id}/html`} className="inline-flex">
              <Button variant="ghost" size="sm" type="button">
                <Download size={14} /> HTML
              </Button>
            </a>
          )}
          {take && (
            <span className="ml-auto self-center font-mono text-xs text-muted">
              {take.model} · ${take.cost_usd.toFixed(3)}
              {take.meta.style_profile_version ? ` · style v${String(take.meta.style_profile_version)}` : ""}
            </span>
          )}
        </div>
        {take &&
        ((take.meta.lint_warnings as string[] | undefined)?.length ||
          (take.meta.layout_warnings as string[] | undefined)?.length) ? (
          <details className="rounded-lg border border-warning/30 bg-warning/10 p-2.5 text-xs text-warning">
            <summary className="cursor-pointer">
              Style lint / layout warnings remained after the fix round
            </summary>
            <ul className="mt-1 list-disc pl-5">
              {[
                ...((take.meta.lint_warnings as string[]) ?? []),
                ...((take.meta.layout_warnings as string[]) ?? []),
              ].map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          </details>
        ) : take?.meta.lint === "pass" ? (
          <p className="text-xs text-success">Style lint passed · layout OK</p>
        ) : null}
        {shot.visual_type === "code_animation" && !busy && <TryAnotherStyle pid={pid} shot={shot} />}
        {editing && <ShotEditor pid={pid} shot={shot} next={next} onDone={() => setEditing(false)} />}
      </CardContent>
    </Card>
  );
}

/** Re-explain this chunk in a different mode; every attempt stays as a take so you can keep the best. */
function TryAnotherStyle({ pid, shot }: { pid: string; shot: Shot }) {
  const patch = usePatchShot(pid);
  const regen = useRegenerate(pid);
  const others = MODES.filter((m) => m.id !== shot.theme);
  const [mode, setMode] = useState<ExplainMode>(others[0].id);
  const tried = new Set(shot.takes.map((t) => String(t.meta.theme ?? "")));
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-border p-2.5 text-sm">
      <span className="text-muted">Try this chunk as</span>
      <select
        value={mode}
        onChange={(e) => setMode(e.target.value as ExplainMode)}
        className="h-9 rounded-lg border border-border bg-surface-2 px-2 text-sm"
        aria-label="Try another explanation mode"
      >
        {others.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label}
            {tried.has(m.id) ? " ✓ tried" : ""} — {m.hint}
          </option>
        ))}
      </select>
      <Button
        size="sm"
        variant="secondary"
        disabled={patch.isPending || regen.isPending}
        onClick={() =>
          patch.mutate({ id: shot.id, theme: mode }, { onSuccess: () => regen.mutate({ id: shot.id }) })
        }
      >
        <Sparkles size={14} /> Generate
      </Button>
      {(patch.error || regen.error) && (
        <span className="text-danger">{(patch.error ?? regen.error)?.message}</span>
      )}
    </div>
  );
}

function ShotEditor({
  pid,
  shot,
  next,
  onDone,
}: {
  pid: string;
  shot: Shot;
  next?: Shot;
  onDone: () => void;
}) {
  const patch = usePatchShot(pid);
  const split = useSplit(pid);
  const merge = useMerge(pid);
  const [prompt, setPrompt] = useState(shot.prompt);
  const [type, setType] = useState<VisualType>(shot.visual_type);
  const [theme, setTheme] = useState(shot.theme);
  const mid = Math.round((shot.start_ms + shot.end_ms) / 2);
  const err = patch.error ?? split.error ?? merge.error;
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border p-3">
      <div className="flex flex-wrap gap-2">
        <select
          value={type}
          onChange={(e) => setType(e.target.value as VisualType)}
          className="h-10 rounded-lg border border-border bg-surface-2 px-2 text-sm"
        >
          {(Object.keys(TYPE_LABEL) as VisualType[]).map((t) => (
            <option key={t} value={t}>
              {TYPE_LABEL[t]}
            </option>
          ))}
        </select>
        <select
          value={theme}
          onChange={(e) => setTheme(e.target.value as ExplainMode)}
          className="h-10 rounded-lg border border-border bg-surface-2 px-2 text-sm"
          aria-label="Explanation mode"
        >
          {MODES.map((m) => (
            <option key={m.id} value={m.id}>
              {m.label} — {m.hint}
            </option>
          ))}
        </select>
      </div>
      <textarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={7}
        className="rounded-lg border border-border bg-surface-2 p-3 font-mono text-xs leading-relaxed focus:border-accent focus:outline-none"
        aria-label="Shot brief"
      />
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          onClick={() =>
            patch.mutate({ id: shot.id, prompt, visual_type: type, theme }, { onSuccess: onDone })
          }
        >
          Save brief
        </Button>
        <Button size="sm" variant="ghost" onClick={() => split.mutate({ id: shot.id, at_ms: mid })}>
          <Scissors size={14} /> Split at {fmtMs(mid)}
        </Button>
        {next && (
          <Button size="sm" variant="ghost" onClick={() => merge.mutate([shot.id, next.id])}>
            <Merge size={14} /> Merge with next
          </Button>
        )}
      </div>
      {err && <p className="text-sm text-danger">{err.message}</p>}
    </div>
  );
}

function PreviewCard({ pid, data }: { pid: string; data: ShotsResponse }) {
  const preview = usePreview(pid);
  const st = data.plan_meta.preview?.status;
  const ready = data.shots.filter((s) => bestTake(s)).length;
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center gap-3">
        <Film size={18} className="text-muted" />
        <CardTitle>Preview</CardTitle>
        <span className="text-xs text-muted">
          720p proxy · every shot's accepted (or latest) take on your narration
        </span>
        <Button
          className="ml-auto"
          variant="secondary"
          onClick={() => preview.mutate()}
          disabled={!ready || st === "running"}
        >
          {st === "running" ? <Loader2 size={16} className="animate-spin" /> : <Clapperboard size={16} />}
          {data.preview ? "Re-render preview" : "Render preview"}
        </Button>
      </CardHeader>
      <CardContent>
        {data.preview && st !== "running" ? (
          <video
            key={data.preview.url}
            src={data.preview.url}
            controls
            playsInline
            className="w-full rounded-lg bg-bg"
          />
        ) : (
          <p className="text-sm text-muted">
            {st === "running"
              ? "Rendering…"
              : `${ready}/${data.shots.length} shots have a take. Missing shots render as blank frames.`}
          </p>
        )}
        {st === "failed" && <p className="mt-2 text-sm text-danger">{data.plan_meta.preview?.error}</p>}
        <FinalRender pid={pid} data={data} ready={ready} />
      </CardContent>
    </Card>
  );
}

function FinalRender({ pid, data, ready }: { pid: string; data: ShotsResponse; ready: number }) {
  const render = useRenderFinal(pid);
  const st = data.plan_meta.final?.status;
  const m = data.final?.meta ?? {};
  return (
    <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-border pt-4">
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium">Final MP4</div>
        <div className="text-xs text-muted">
          Full quality (H.264 CRF 18 · AAC 320 kbps) with face-cam circle and watermark rules. FCPXML + SRT
          come with phase 7.
        </div>
      </div>
      <Button onClick={() => render.mutate()} disabled={!ready || st === "running" || render.isPending}>
        {st === "running" ? <Loader2 size={16} className="animate-spin" /> : <Film size={16} />}
        {st === "running"
          ? "Rendering… (a few minutes)"
          : data.final
            ? "Re-render final"
            : "Render final MP4"}
      </Button>
      {data.final && st !== "running" && (
        <a href={`${data.final.url}&dl=${encodeURIComponent("mhsquare-final.mp4")}`}>
          <Button variant="success" type="button">
            <Download size={16} /> Download ({String(m.width)}×{String(m.height)},{" "}
            {(data.final.size / 1024 ** 2).toFixed(1)} MB)
          </Button>
        </a>
      )}
      {st === "failed" && <p className="w-full text-sm text-danger">{data.plan_meta.final?.error}</p>}
    </div>
  );
}
