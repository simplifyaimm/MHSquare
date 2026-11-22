import { useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, AudioLines, Check, Loader2, Play } from "lucide-react";
import { UploadSlot } from "@/components/UploadSlot";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { usePatchProject, useProject, useStartIngest, type Project } from "@/lib/projects";
import { useTTS, useVoices } from "@/lib/shots";
import { cn } from "@/lib/utils";

const STEPS = ["Script", "Narration", "Characters", "Style", "Start"] as const;

export function ProjectSetupPage() {
  const { id = "" } = useParams();
  const { data: p, isLoading, error } = useProject(id);
  const [step, setStep] = useState(0);

  if (isLoading) return <p className="text-muted">Loading…</p>;
  if (error || !p) return <p className="text-danger">{error?.message ?? "Not found"}</p>;
  if (p.status !== "draft" && p.status !== "failed") return <Navigate to={`/projects/${id}`} replace />;

  const has = (k: string) => p.assets.some((a) => a.kind === k);
  const done = [has("script"), has("audio"), true, true, false];

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <Link to="/projects" className="text-muted hover:text-text" aria-label="Back to projects">
          <ArrowLeft size={20} />
        </Link>
        <h1 className="truncate text-2xl font-semibold tracking-tight">{p.name}</h1>
      </div>

      <ol className="flex gap-1 overflow-x-auto">
        {STEPS.map((s, i) => (
          <li key={s} className="flex-1">
            <button
              onClick={() => setStep(i)}
              className={cn(
                "flex w-full min-w-16 flex-col gap-1.5 text-left text-xs",
                i === step ? "text-text" : "text-muted hover:text-text",
              )}
            >
              <span
                className={cn(
                  "h-1 rounded-full",
                  i === step ? "bg-accent" : done[i] ? "bg-success/60" : "bg-surface-2",
                )}
              />
              <span className="flex items-center gap-1">
                {done[i] && i !== step && <Check size={12} className="text-success" />}
                {s}
              </span>
            </button>
          </li>
        ))}
      </ol>

      <Card>
        <CardContent className="flex flex-col gap-6">
          {step === 0 && (
            <UploadSlot
              projectId={p.id}
              kind="script"
              accept=".md,.txt"
              label="Script"
              hint="The script you recorded from. Used for intent when planning shots."
              assets={p.assets.filter((a) => a.kind === "script")}
            />
          )}
          {step === 1 && <NarrationStep p={p} />}
          {step === 2 && (
            <UploadSlot
              projectId={p.id}
              kind="character_ref"
              accept=".png,.jpg,.jpeg,.webp"
              label="Character references (optional, up to 3)"
              hint="Used to keep a recurring character consistent in AI clips."
              assets={p.assets.filter((a) => a.kind === "character_ref")}
              multiple
              max={3}
            />
          )}
          {step === 3 && <StyleStep p={p} />}
          {step === 4 && <StartStep p={p} ready={done[0] && done[1]} />}
        </CardContent>
      </Card>

      <div className="flex justify-between">
        <Button variant="ghost" disabled={step === 0} onClick={() => setStep((s) => s - 1)}>
          <ArrowLeft size={16} /> Back
        </Button>
        {step < STEPS.length - 1 && (
          <Button variant="secondary" onClick={() => setStep((s) => s + 1)}>
            Next <ArrowRight size={16} />
          </Button>
        )}
      </div>
    </div>
  );
}

function NarrationStep({ p }: { p: Project }) {
  const patch = usePatchProject(p.id);
  const [offset, setOffset] = useState(String(p.facecam_offset_ms));
  const hasCam = p.assets.some((a) => a.kind === "facecam");
  return (
    <>
      <UploadSlot
        projectId={p.id}
        kind="audio"
        accept=".wav,.m4a,.mp3"
        label="Narration audio"
        hint="Your recorded voice-over. It gets cleaned and normalised to −14 LUFS."
        assets={p.assets.filter((a) => a.kind === "audio")}
      />
      <DraftNarration p={p} />
      <UploadSlot
        projectId={p.id}
        kind="facecam"
        accept=".mp4,.mov,.webm,.mkv"
        label="Face-cam video (optional)"
        hint="Recorded at the same time as the narration. The same cuts are applied to it."
        assets={p.assets.filter((a) => a.kind === "facecam")}
      />
      {hasCam && (
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="offset">Face-cam offset (ms)</Label>
          <div className="flex gap-2">
            <Input
              id="offset"
              type="number"
              inputMode="numeric"
              className="max-w-40 font-mono"
              value={offset}
              onChange={(e) => setOffset(e.target.value)}
            />
            <Button
              variant="secondary"
              disabled={Number(offset) === p.facecam_offset_ms || patch.isPending}
              onClick={() => patch.mutate({ facecam_offset_ms: Math.round(Number(offset) || 0) })}
            >
              Save
            </Button>
          </div>
          <p className="text-xs text-muted">
            Positive if the video started recording before the audio. Leave 0 if they started together.
          </p>
        </div>
      )}
    </>
  );
}

function StyleStep({ p }: { p: Project }) {
  const patch = usePatchProject(p.id);
  const [notes, setNotes] = useState(p.style_notes);
  const s = p.ingest_settings;
  const [fillers, setFillers] = useState(s.fillers.join(", "));
  const [silence, setSilence] = useState(String(s.silence_ms));
  const dirty =
    notes !== p.style_notes || fillers !== s.fillers.join(", ") || Number(silence) !== s.silence_ms;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="notes">Style notes (optional)</Label>
        <textarea
          id="notes"
          rows={5}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="e.g. clean dark-mode diagrams, neon violet/cyan accents, calm documentary pacing"
          className="rounded-lg border border-border bg-surface-2 p-3 text-sm focus:border-accent focus:outline-none"
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="fillers">Filler words to remove</Label>
          <Input
            id="fillers"
            className="font-mono"
            value={fillers}
            onChange={(e) => setFillers(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="silence">Shorten pauses longer than (ms)</Label>
          <Input
            id="silence"
            type="number"
            min={200}
            max={5000}
            className="font-mono"
            value={silence}
            onChange={(e) => setSilence(e.target.value)}
          />
        </div>
      </div>
      {patch.error && <p className="text-sm text-danger">{patch.error.message}</p>}
      <div className="flex justify-end">
        <Button
          variant="secondary"
          disabled={!dirty || patch.isPending}
          onClick={() =>
            patch.mutate({
              style_notes: notes,
              ingest_settings: {
                ...s,
                fillers: fillers
                  .split(",")
                  .map((f) => f.trim().toLowerCase())
                  .filter(Boolean),
                silence_ms: Number(silence) || 600,
              },
            })
          }
        >
          Save
        </Button>
      </div>
    </div>
  );
}

function StartStep({ p, ready }: { p: Project; ready: boolean }) {
  const start = useStartIngest(p.id);
  const navigate = useNavigate();
  return (
    <div className="flex flex-col items-start gap-4">
      <p className="text-sm text-muted">
        mhsquare cleans the audio, transcribes it with word timestamps, and proposes filler and silence cuts
        for you to review. Transcription runs locally and costs nothing.
      </p>
      {p.status === "failed" && p.error && (
        <p className="w-full rounded-lg border border-danger/30 bg-danger/10 p-3 font-mono text-xs text-danger">
          {p.error}
        </p>
      )}
      {!ready && <p className="text-sm text-warning">Upload the script and the narration audio first.</p>}
      <Button
        size="lg"
        disabled={!ready || start.isPending}
        onClick={() => start.mutate(undefined, { onSuccess: () => navigate(`/projects/${p.id}`) })}
      >
        <Play size={16} /> {p.status === "failed" ? "Retry ingest" : "Start ingest"}
      </Button>
      {start.error && <p className="text-sm text-danger">{start.error.message}</p>}
    </div>
  );
}

function DraftNarration({ p }: { p: Project }) {
  const voices = useVoices();
  const tts = useTTS(p.id);
  const [voice, setVoice] = useState("Kore");
  const running = p.ingest_step === "tts";
  const hasScript = p.assets.some((a) => a.kind === "script");
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed border-border p-4">
      <div className="flex items-center gap-2 text-sm font-medium">
        <AudioLines size={16} className="text-accent-2" /> No recording yet? Test the script with an AI voice
      </div>
      <p className="text-xs text-muted">
        Reads your script aloud (draft quality) and uses it as the narration. Replace it with your own
        recording later.
      </p>
      <div className="flex flex-wrap gap-2">
        <select
          value={voice}
          onChange={(e) => setVoice(e.target.value)}
          className="h-10 rounded-lg border border-border bg-surface-2 px-3 text-sm"
          aria-label="Voice"
        >
          {(voices.data?.voices ?? ["Kore"]).map((v) => (
            <option key={v}>{v}</option>
          ))}
        </select>
        <Button
          variant="secondary"
          disabled={!hasScript || running || tts.isPending}
          onClick={() => tts.mutate(voice)}
        >
          {running ? <Loader2 size={16} className="animate-spin" /> : <AudioLines size={16} />}
          {running ? "Generating narration…" : "Generate draft narration"}
        </Button>
      </div>
      {!hasScript && <p className="text-xs text-warning">Upload the script first (step 1).</p>}
      {p.error?.startsWith("Draft narration") && <p className="text-xs text-danger">{p.error}</p>}
      {tts.error && <p className="text-xs text-danger">{tts.error.message}</p>}
    </div>
  );
}
