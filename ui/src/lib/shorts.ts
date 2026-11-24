import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

/** A vertical Short / Reel cut from the long video (backend: app/services/shorts.py). */
export type Short = {
  id: string;
  index: number;
  shot_from: number;
  shot_to: number;
  start_ms: number;
  end_ms: number;
  duration_s: number;
  series: string | null;
  part: number | null;
  parts: number | null;
  hook: string;
  title: string;
  description: string;
  hashtags: string[];
  why: string;
  score: number;
  status: "planned" | "queued" | "rendering" | "done" | "failed";
  error: string | null;
  stale: boolean;
  url: string | null;
  context_line?: string;
  pinned_comment?: string;
  cover_status?: "running" | "done" | "failed" | null;
  cover_url?: string | null;
};

export type ShortsResponse = {
  status: {
    status?: "running" | "done" | "failed";
    made?: number;
    kept?: number;
    cost_usd?: number;
    error?: string;
  };
  layout: { width: number; height: number; stage: number[]; face_zone: number[] };
  shorts: Short[];
};

export type ShortEdit = Partial<Pick<Short, "hook" | "title" | "description">> & {
  hashtags?: string[] | string;
};

const BUSY = ["queued", "rendering"];
const key = (pid: string) => ["shorts", pid];

export function useShorts(pid: string) {
  return useQuery({
    queryKey: key(pid),
    queryFn: () => api<ShortsResponse>(`/projects/${pid}/shorts`),
    enabled: !!pid,
    refetchInterval: (q) => {
      const d = q.state.data;
      return d &&
        (d.status.status === "running" ||
          d.shorts.some((s) => BUSY.includes(s.status) || s.cover_status === "running"))
        ? 3000
        : false;
    },
  });
}

export function useShortMutations(pid: string) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: key(pid) });
  return {
    plan: useMutation({
      mutationFn: () => api(`/projects/${pid}/shorts/plan`, { method: "POST" }),
      onSuccess: done,
    }),
    renderAll: useMutation({
      mutationFn: () => api<{ queued: number }>(`/projects/${pid}/shorts/render`, { method: "POST" }),
      onSuccess: done,
    }),
    render: useMutation({
      mutationFn: (id: string) => api(`/shorts/${id}/render`, { method: "POST" }),
      onSuccess: done,
    }),
    edit: useMutation({
      mutationFn: ({ id, ...b }: ShortEdit & { id: string }) =>
        api<Short>(`/shorts/${id}`, { method: "PUT", body: JSON.stringify(b) }),
      onSuccess: done,
    }),
    cover: useMutation({
      mutationFn: (id: string) => api(`/shorts/${id}/cover`, { method: "POST" }),
      onSuccess: done,
    }),
    coverAll: useMutation({
      mutationFn: () => api<{ queued: number }>(`/projects/${pid}/shorts/covers`, { method: "POST" }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api<void>(`/shorts/${id}`, { method: "DELETE" }),
      onSuccess: done,
    }),
  };
}

export const isBusy = (s: Short) => BUSY.includes(s.status);
