import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export type ProjectStatus =
  "draft" | "ingesting" | "cuts_review" | "finalizing" | "ready" | "planning" | "planned" | "failed";

export type Asset = {
  id: string;
  kind: string;
  mime: string;
  size: number;
  original_name: string | null;
  meta: Record<string, unknown> & { duration_ms?: number };
  url: string;
  created_at: string;
};

export type IngestSettings = {
  fillers: string[];
  silence_ms: number;
  keep_pause_ms: number;
  language: string | null;
};

export type Project = {
  id: string;
  name: string;
  status: ProjectStatus;
  ingest_step: string | null;
  error: string | null;
  resolution: string;
  fps: number;
  style_notes: string;
  facecam_offset_ms: number;
  ingest_settings: IngestSettings;
  content_type: import("./style").ContentType;
  pillar: import("./style").Pillar | null;
  topic_kind: "reach" | "authority" | null;
  audience_level: "beginner" | "advanced";
  topic: string;
  script_check: import("./style").ScriptCheck | null;
  direction: import("./direction").Direction | null;
  style_profile_version: number | null;
  created_at: string;
  updated_at: string;
  assets: Asset[];
};

export type Cut = {
  id: string;
  start_ms: number;
  end_ms: number;
  kind: "filler" | "silence";
  text: string;
  enabled: boolean;
};

export type CutsResponse = {
  id: string;
  approved: boolean;
  ranges: Cut[];
  duration_ms: number;
  removed_ms: number;
  audio: Asset | null;
};

export type Word = { w: string; start_ms: number; end_ms: number; score?: number };
export type TranscriptResponse = {
  version: number;
  is_final: boolean;
  language: string | null;
  text: string;
  words: Word[];
};
export type Waveform = { peaks_per_second: number; duration_ms: number; peaks: number[] };

const BUSY: ProjectStatus[] = ["ingesting", "finalizing", "planning"];
export const isBusy = (s: ProjectStatus) => BUSY.includes(s);

export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: () => api<Project[]>("/projects"),
    refetchInterval: (q) => (q.state.data?.some((p) => isBusy(p.status)) ? 3000 : false),
  });
}

export function useProject(id: string) {
  return useQuery({
    queryKey: ["projects", id],
    queryFn: () => api<Project>(`/projects/${id}`),
    refetchInterval: (q) =>
      q.state.data && (isBusy(q.state.data.status) || q.state.data.ingest_step === "tts") ? 2000 : false,
  });
}

function useInvalidateProject(id?: string) {
  const qc = useQueryClient();
  return (p?: Project) => {
    if (p) qc.setQueryData(["projects", p.id], p);
    qc.invalidateQueries({ queryKey: id ? ["projects", id] : ["projects"] });
    qc.invalidateQueries({ queryKey: ["projects"], exact: true });
  };
}

export function useCreateProject() {
  const done = useInvalidateProject();
  return useMutation({
    mutationFn: (body: {
      name: string;
      resolution?: string;
      fps?: number;
      content_type?: string;
      pillar?: string | null;
      topic_kind?: string | null;
      audience_level?: string;
      topic?: string;
    }) => api<Project>("/projects", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: done,
  });
}

export function usePatchProject(id: string) {
  const done = useInvalidateProject(id);
  return useMutation({
    mutationFn: (
      body: Partial<
        Pick<Project, "name" | "style_notes" | "facecam_offset_ms" | "resolution" | "fps" | "ingest_settings">
      >,
    ) => api<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
    onSuccess: done,
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/projects/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useDeleteAsset(projectId: string) {
  const done = useInvalidateProject(projectId);
  return useMutation({
    mutationFn: (assetId: string) => api<void>(`/assets/${assetId}`, { method: "DELETE" }),
    onSuccess: () => done(),
  });
}

export function useStartIngest(id: string) {
  const done = useInvalidateProject(id);
  return useMutation({
    mutationFn: () => api<Project>(`/projects/${id}/ingest`, { method: "POST" }),
    onSuccess: done,
  });
}

export function useCuts(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["projects", id, "cuts"],
    queryFn: () => api<CutsResponse>(`/projects/${id}/cuts`),
    enabled,
  });
}

export function useApproveCuts(id: string) {
  const done = useInvalidateProject(id);
  return useMutation({
    mutationFn: (enabled_ids: string[]) =>
      api<Project>(`/projects/${id}/cuts/approve`, { method: "POST", body: JSON.stringify({ enabled_ids }) }),
    onSuccess: done,
  });
}

export function useTranscript(id: string, final: boolean, enabled: boolean) {
  return useQuery({
    queryKey: ["projects", id, "transcript", final],
    queryFn: () => api<TranscriptResponse>(`/projects/${id}/transcript?final=${final}`),
    enabled,
  });
}

export function useWaveform(id: string, stage: "clean" | "final", enabled: boolean) {
  return useQuery({
    queryKey: ["projects", id, "waveform", stage],
    queryFn: () => api<Waveform>(`/projects/${id}/waveform?stage=${stage}`),
    enabled,
    staleTime: Infinity,
  });
}

// ---------------------------------------------------------------- chunked, resumable upload

type UploadInit = { id: string; size: number; received: number; chunk_size: number };

export async function uploadFile(
  projectId: string,
  kind: "script" | "audio" | "facecam" | "character_ref",
  file: File,
  onProgress?: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<Asset> {
  const init = await api<UploadInit>(`/projects/${projectId}/uploads`, {
    method: "POST",
    body: JSON.stringify({ kind, filename: file.name, size: file.size }),
  });
  let offset = init.received;
  let retries = 0;
  while (offset < file.size) {
    if (signal?.aborted) throw new DOMException("Upload cancelled", "AbortError");
    const chunk = file.slice(offset, offset + init.chunk_size);
    try {
      const r = await api<UploadInit>(`/uploads/${init.id}?offset=${offset}`, {
        method: "PUT",
        body: chunk,
        headers: { "Content-Type": "application/octet-stream" },
        signal,
      });
      offset = r.received;
      retries = 0;
      onProgress?.(offset / file.size);
    } catch (e) {
      if (signal?.aborted || ++retries > 5) throw e;
      await new Promise((res) => setTimeout(res, 500 * 2 ** retries));
      offset = (await api<UploadInit>(`/uploads/${init.id}`)).received; // resume from server state
    }
  }
  return api<Asset>(`/uploads/${init.id}/complete`, { method: "POST" });
}

export function fmtMs(ms: number) {
  const s = Math.max(0, ms) / 1000;
  const m = Math.floor(s / 60);
  return `${m}:${(s % 60).toFixed(1).padStart(4, "0")}`;
}

export function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
