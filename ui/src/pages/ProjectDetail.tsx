import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Check, Clapperboard, ImageIcon, Loader2, RotateCcw, Trash2, Wand2 } from "lucide-react";
import { CreativeDirection } from "@/components/CreativeDirection";
import { CutsReview } from "@/components/CutsReview";
import { ProjectStatusChip } from "@/components/ProjectStatusChip";
import { ShotBoard } from "@/components/ShotBoard";
import { YouTubeDetails } from "@/components/YouTubeDetails";
import { usePlan } from "@/lib/shots";
import { CONTENT_TYPE_LABEL, useMarkFinal } from "@/lib/style";
import { Waveform } from "@/components/Waveform";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  fmtMs,
  useDeleteProject,
  useProject,
  useStartIngest,
  useTranscript,
  useWaveform,
  type Project,
} from "@/lib/projects";
import { cn } from "@/lib/utils";

const PIPELINE: { step: string; label: string }[] = [
  { step: "cleanup", label: "Clean audio (high-pass, −14 LUFS)" },
  { step: "transcribe_raw", label: "Transcribe with word timestamps" },
  { step: "detect_cuts", label: "Find fillers and long pauses" },
  { step: "review", label: "You review the cuts" },
  { step: "apply_cuts", label: "Apply cuts to audio and face-cam" },
  { step: "transcribe_final", label: "Final transcript (source of truth)" },
];

function currentIndex(p: Project) {
  if (p.status === "cuts_review") return 3;
  if (p.status === "ready") return PIPELINE.length;
  const i = PIPELINE.findIndex((s) => s.step === p.ingest_step);
  return i < 0 ? 0 : i;
}

export function ProjectDetailPage() {
  const { id = "" } = useParams();
  const { data: p, isLoading, error } = useProject(id);
  const del = useDeleteProject();
  const navigate = useNavigate();
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (isLoading) return <p className="text-muted">Loading…</p>;
  if (error || !p) return <p className="text-danger">{error?.message ?? "Not found"}</p>;
  if (p.status === "draft") return <Navigate to={`/projects/${id}/setup`} replace />;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/projects" className="text-muted hover:text-text" aria-label="Back to projects">
          <ArrowLeft size={20} />
        </Link>
        <h1 className="min-w-0 truncate text-2xl font-semibold tracking-tight">{p.name}</h1>
        <ProjectStatusChip status={p.status} />
        <span className="rounded-full bg-surface-2 px-2.5 py-0.5 text-xs text-muted">
          {CONTENT_TYPE_LABEL[p.content_type] ?? p.content_type}
          {p.pillar ? ` · ${p.pillar}` : ""}
        </span>
        {p.status === "planned" && <MarkFinal pid={p.id} />}
        <Link to={`/projects/${p.id}/covers`}>
          <Button size="sm" variant="ghost">
            <ImageIcon size={14} /> Covers
          </Button>
        </Link>
        {p.status === "planned" && (
          <Link to={`/projects/${p.id}/shorts`}>
            <Button size="sm" variant="ghost">
              <Clapperboard size={14} /> Shorts
            </Button>
          </Link>
        )}
        <span className="ml-auto">
          {confirmDelete ? (
            <span className="flex items-center gap-2 text-sm">
              Delete project and all files?
              <Button
                size="sm"
                variant="danger"
                onClick={() => del.mutate(p.id, { onSuccess: () => navigate("/projects") })}
              >
                Delete
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>
                Cancel
              </Button>
            </span>
          ) : (
            <Button
              size="icon"
              variant="ghost"
              aria-label="Delete project"
              disabled={p.status === "ingesting" || p.status === "finalizing"}
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 size={16} />
            </Button>
          )}
        </span>
      </div>

      {!["cuts_review", "planning", "planned", "ready"].includes(p.status) && <PipelineSteps p={p} />}
      {p.status === "failed" && <FailedCard p={p} />}
      {p.status === "cuts_review" && <CutsReview project={p} />}
      {(p.status === "ready" || p.status === "planning") && <CreativeDirection p={p} />}
      {(p.status === "ready" || p.status === "planning") && <PlanCard p={p} />}
      {p.status === "planned" && <ShotBoard projectId={p.id} />}
      {(p.status === "planned" || p.status === "ready") && <YouTubeDetails p={p} />}
      {p.status === "planned" && (
        <details className="rounded-xl border border-border bg-surface p-4">
          <summary className="cursor-pointer text-sm text-muted">
            Creative direction
            {p.direction
              ? ` · ${p.direction.primary_mode} + ${p.direction.templates.slice(0, 3).join(", ")}`
              : ""}
            {" — re-plan or convert to templates to apply changes"}
          </summary>
          <div className="mt-4">
            <CreativeDirection p={p} />
          </div>
        </details>
      )}
      {p.status === "planned" && (
        <details className="rounded-xl border border-border bg-surface p-4">
          <summary className="cursor-pointer text-sm text-muted">Narration and final transcript</summary>
          <div className="mt-4 flex flex-col gap-4">
            <ReadyView p={p} />
          </div>
        </details>
      )}
      {p.status === "ready" && <ReadyView p={p} />}
    </div>
  );
}

function PipelineSteps({ p }: { p: Project }) {
  const cur = currentIndex(p);
  return (
    <Card>
      <CardContent>
        <ol className="flex flex-col gap-3">
          {PIPELINE.map((s, i) => {
            const done = i < cur;
            const active = i === cur && (p.status === "ingesting" || p.status === "finalizing");
            const failed = i === cur && p.status === "failed";
            return (
              <li key={s.step} className="flex items-center gap-3 text-sm">
                <span
                  className={cn(
                    "grid h-6 w-6 shrink-0 place-items-center rounded-full border",
                    done && "border-success bg-success/15 text-success",
                    active && "border-accent-2 text-accent-2",
                    failed && "border-danger text-danger",
                    !done && !active && !failed && "border-border text-muted",
                  )}
                >
                  {done ? (
                    <Check size={14} />
                  ) : active ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    i + 1
                  )}
                </span>
                <span className={cn(done || active ? "text-text" : "text-muted", failed && "text-danger")}>
                  {s.label}
                </span>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}

function FailedCard({ p }: { p: Project }) {
  const retry = useStartIngest(p.id);
  return (
    <Card className="border-danger/40">
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-danger">This step failed:</p>
        <pre className="overflow-x-auto rounded-lg bg-bg p-3 font-mono text-xs whitespace-pre-wrap text-muted">
          {p.error}
        </pre>
        <div className="flex gap-2">
          <Button onClick={() => retry.mutate()} disabled={retry.isPending}>
            <RotateCcw size={16} /> Retry from this step
          </Button>
          <Link to={`/projects/${p.id}/setup`}>
            <Button variant="ghost">Change inputs</Button>
          </Link>
        </div>
        {retry.error && <p className="text-sm text-danger">{retry.error.message}</p>}
      </CardContent>
    </Card>
  );
}

function ReadyView({ p }: { p: Project }) {
  const wave = useWaveform(p.id, "final", true);
  const transcript = useTranscript(p.id, true, true);
  const audio = p.assets.find((a) => a.kind === "audio_final");
  const cam = p.assets.find((a) => a.kind === "facecam_final");
  const audioEl = useRef<HTMLAudioElement>(null);
  const [ms, setMs] = useState(0);

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      if (audioEl.current) setMs(audioEl.current.currentTime * 1000);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const seek = (t: number) => {
    if (audioEl.current) audioEl.current.currentTime = t / 1000;
  };
  const removed = Number(audio?.meta.removed_ms ?? 0);

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Cleaned narration</CardTitle>
          <p className="font-mono text-xs text-muted">
            {audio?.meta.duration_ms ? fmtMs(audio.meta.duration_ms) : "—"} · {fmtMs(removed)} removed
            {cam ? " · face-cam cut to match" : ""}
          </p>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {wave.data && <Waveform data={wave.data} currentMs={ms} onSeek={seek} />}
          {audio && <audio ref={audioEl} src={audio.url} controls className="w-full" preload="metadata" />}
          {cam && (
            <details className="text-sm">
              <summary className="cursor-pointer text-muted">Face-cam (cut)</summary>
              <video src={cam.url} controls className="mt-2 w-full max-w-xl rounded-lg" preload="metadata" />
            </details>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Final transcript</CardTitle>
          <p className="text-sm text-muted">
            These word timestamps are the source of truth for shot planning. Click a word to jump to it.
          </p>
        </CardHeader>
        <CardContent>
          {transcript.isLoading && <p className="text-muted">Loading…</p>}
          {transcript.data && (
            <p className="leading-8">
              {transcript.data.words.map((w, i) => {
                const active = ms >= w.start_ms && ms < w.end_ms;
                return (
                  <button
                    key={i}
                    onClick={() => seek(w.start_ms)}
                    title={`${fmtMs(w.start_ms)}–${fmtMs(w.end_ms)}`}
                    className={cn("rounded px-0.5 hover:bg-surface-2", active && "bg-accent/25 text-text")}
                  >
                    {w.w}
                  </button>
                );
              })}
            </p>
          )}
        </CardContent>
      </Card>
    </>
  );
}

function PlanCard({ p }: { p: Project }) {
  const plan = usePlan(p.id);
  const planning = p.status === "planning";
  return (
    <Card className="border-accent/40">
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <div className="font-medium">Plan the shot list</div>
            <p className="text-sm text-muted">
              One AI pass over the final transcript and your script, following the creative direction above:
              every moment gets a visual — brand scene templates, custom animations, text cards
              {p.assets.some((a) => a.kind === "facecam_final") ? ", face-cam" : ""}. You can edit everything
              before generating.
            </p>
          </div>
          <Button size="lg" onClick={() => plan.mutate()} disabled={planning || plan.isPending}>
            {planning ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={16} />}
            {planning ? "Planning… (about a minute)" : "Plan shots"}
          </Button>
        </div>
        {p.error && <p className="text-sm text-danger">{p.error}</p>}
        {plan.error && <p className="text-sm text-danger">{plan.error.message}</p>}
      </CardContent>
    </Card>
  );
}

function MarkFinal({ pid }: { pid: string }) {
  const mark = useMarkFinal(pid);
  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={() => mark.mutate()}
      disabled={mark.isPending || mark.isSuccess}
      title="Add this topic to Covered topics"
    >
      <Check size={14} /> {mark.isSuccess ? `Added “${mark.data.title}” to topics` : "Mark script final"}
    </Button>
  );
}
