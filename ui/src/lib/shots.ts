import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export type VisualType = "code_animation" | "ai_clip" | "still_image" | "text_card" | "a_roll";
export type ShotState =
  | "planned"
  | "keyframe_generating"
  | "keyframe_review"
  | "queued"
  | "generating"
  | "review"
  | "accepted"
  | "paused_budget"
  | "needs_edit"
  | "failed";

export type Take = {
  id: string;
  attempt: number;
  status: "pending" | "running" | "done" | "failed";
  generator: string;
  model: string | null;
  cost_usd: number;
  seed: number;
  feedback_note: string | null;
  error: string | null;
  logs: string[];
  video_url: string | null;
  thumb_url: string | null;
  meta: Record<string, unknown>;
  created_at: string;
};

export type Shot = {
  id: string;
  index: number;
  start_ms: number;
  end_ms: number;
  transcript_text: string;
  visual_type: VisualType;
  theme: ExplainMode;
  prompt: string;
  notes: string;
  depends_on_previous: boolean;
  uses_character: boolean;
  state: ShotState;
  accepted_take_id: string | null;
  override_model: string | null;
  error: string | null;
  generator: string;
  estimate_usd: number;
  takes: Take[];
};

export type ShotsResponse = {
  status: string;
  error: string | null;
  style_guide: Record<string, unknown> & { summary?: string; characters?: { id: string; name: string }[] };
  characters_drawn: string[];
  plan_meta: {
    cost_usd?: number;
    model?: string;
    preview?: { status: string; error?: string; missing_shots?: number };
    final?: { status: string; error?: string; missing_shots?: number };
    templatize?: {
      status: "running" | "done" | "paused" | "failed";
      total: number;
      done: number;
      converted: number;
      kept: number;
      cost_usd: number;
      error?: string | null;
    };
  };
  shots: Shot[];
  estimate_usd: number;
  spent_usd: number;
  preview: { url: string; meta: Record<string, unknown> } | null;
  final: { url: string; meta: Record<string, unknown>; size: number } | null;
  allowed_visual_types: VisualType[];
};

export const BUSY_STATES: ShotState[] = ["queued", "generating", "keyframe_generating"];

export function bestTake(s: Shot): Take | undefined {
  return (
    s.takes.find((t) => t.id === s.accepted_take_id) ??
    [...s.takes].reverse().find((t) => t.status === "done")
  );
}

const key = (pid: string) => ["projects", pid, "shots"];

export function useShots(pid: string, enabled = true) {
  return useQuery({
    queryKey: key(pid),
    queryFn: () => api<ShotsResponse>(`/projects/${pid}/shots`),
    enabled,
    refetchInterval: (q) => {
      const d = q.state.data;
      if (!d) return false;
      const busy =
        d.status === "planning" ||
        d.plan_meta.preview?.status === "running" ||
        d.plan_meta.final?.status === "running" ||
        d.plan_meta.templatize?.status === "running" ||
        d.shots.some((s) => BUSY_STATES.includes(s.state));
      return busy ? 2500 : false;
    },
  });
}

function useAct<T, B = void>(pid: string, fn: (b: B) => Promise<T>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => {
      qc.invalidateQueries({ queryKey: key(pid) });
      qc.invalidateQueries({ queryKey: ["projects", pid], exact: true });
    },
  });
}

const post = (path: string, body?: unknown) =>
  api(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const usePlan = (pid: string) => useAct(pid, () => post(`/projects/${pid}/plan`));
export const useTemplatize = (pid: string) => useAct(pid, () => post(`/projects/${pid}/templatize`));

export const TEMPLATE_PREFIX = "@template";
/** Name of the scene template a shot renders from, or null for a free-form (AI-written) scene. */
export const templateName = (s: Pick<Shot, "visual_type" | "prompt">) =>
  s.visual_type === "code_animation" && s.prompt.trimStart().startsWith(TEMPLATE_PREFIX)
    ? s.prompt.trimStart().slice(TEMPLATE_PREFIX.length).split("\n")[0].trim()
    : null;
export const useGenerate = (pid: string) =>
  useAct(pid, (shot_ids?: string[]) => post(`/projects/${pid}/generate`, { shot_ids: shot_ids ?? null }));
export const useRegenerate = (pid: string) =>
  useAct(pid, (b: { id: string; note?: string; keep_seed?: boolean }) =>
    post(`/shots/${b.id}/regenerate`, { note: b.note || null, keep_seed: !!b.keep_seed }),
  );
export const useAccept = (pid: string) =>
  useAct(pid, (b: { shotId: string; takeId: string }) => post(`/shots/${b.shotId}/takes/${b.takeId}/accept`));
export const usePatchShot = (pid: string) =>
  useAct(
    pid,
    (b: { id: string } & Partial<Pick<Shot, "visual_type" | "theme" | "prompt" | "start_ms" | "end_ms">>) => {
      const { id, ...body } = b;
      return api(`/shots/${id}`, { method: "PATCH", body: JSON.stringify(body) });
    },
  );
export const useSplit = (pid: string) =>
  useAct(pid, (b: { id: string; at_ms: number }) => post(`/shots/${b.id}/split`, { at_ms: b.at_ms }));
export const useMerge = (pid: string) =>
  useAct(pid, (ids: [string, string]) => post(`/shots/merge`, { shot_ids: ids }));
export const usePreview = (pid: string) => useAct(pid, () => post(`/projects/${pid}/preview`));

export function useVoices() {
  return useQuery({
    queryKey: ["tts", "voices"],
    queryFn: () => api<{ voices: string[] }>("/tts/voices"),
    staleTime: Infinity,
  });
}
export const useTTS = (pid: string) =>
  useAct(pid, (voice: string) => post(`/projects/${pid}/narration/tts`, { voice }));

export const TYPE_LABEL: Record<VisualType, string> = {
  code_animation: "animation",
  ai_clip: "AI clip",
  still_image: "image",
  text_card: "text card",
  a_roll: "face-cam",
};
export const TYPE_COLOR: Record<VisualType, string> = {
  code_animation: "#7C5CFF",
  ai_clip: "#F59E0B",
  still_image: "#22D3EE",
  text_card: "#8A93A6",
  a_roll: "#22C55E",
};

export type ExplainMode = "tech" | "flow" | "lecture" | "sketch" | "scenario" | "story";
export const MODES: { id: ExplainMode; label: string; hint: string }[] = [
  { id: "tech", label: "Tech", hint: "system explainer — panels, pills, wiring, code" },
  { id: "flow", label: "Flow", hint: "protocol / sequence — numbered steps on arrows" },
  { id: "lecture", label: "Lecture", hint: "tables, maths, numbered points with evidence" },
  { id: "sketch", label: "Sketch", hint: "hand-drawn whiteboard intuition" },
  { id: "scenario", label: "Scenario", hint: "X in a real situation — on-brand personas" },
  { id: "story", label: "Story (legacy)", hint: "flat cartoon — off-brand, off by default" },
];

export const useRenderFinal = (pid: string) => useAct(pid, () => post(`/projects/${pid}/render-final`));

export type LibraryScript = {
  asset_id: string;
  title: string;
  project: string;
  project_id: string;
  words: number | null;
  original_name: string | null;
  created_at: string;
};

export function useScriptLibrary() {
  return useQuery({ queryKey: ["scripts"], queryFn: () => api<LibraryScript[]>("/scripts") });
}

export const useUseLibraryScript = (pid: string) =>
  useAct(pid, (asset_id: string) => post(`/projects/${pid}/script/from-library`, { asset_id }));
