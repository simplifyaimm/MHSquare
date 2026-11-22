import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export type Headshot = {
  id: string;
  status: "pending" | "ready" | "failed";
  original_name: string | null;
  url: string;
  cutout_url: string | null;
  error: string | null;
  created_at: string;
};

export type Cover = {
  id: string;
  url: string;
  size: number;
  format: "youtube" | "short";
  option: number;
  batch: string;
  chosen: boolean;
  headline: string;
  highlight: string[];
  eyebrow: string;
  icons: string[];
  layout: string;
  created_at: string;
};

export type CoversResponse = {
  status: { status?: string; error?: string; made?: number; cost_usd?: number; with_photo?: boolean };
  covers: Cover[];
};

export function useHeadshots() {
  return useQuery({
    queryKey: ["headshots"],
    queryFn: () => api<Headshot[]>("/brand/headshots"),
    refetchInterval: (q) => (q.state.data?.some((h) => h.status === "pending") ? 3000 : false),
  });
}

export function useHeadshotMutations() {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["headshots"] });
  return {
    upload: useMutation({
      mutationFn: (file: File) => {
        const fd = new FormData();
        fd.append("file", file);
        return api<Headshot>("/brand/headshots", { method: "POST", body: fd });
      },
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api<void>(`/brand/headshots/${id}`, { method: "DELETE" }),
      onSuccess: done,
    }),
    retry: useMutation({
      mutationFn: (id: string) => api<Headshot>(`/brand/headshots/${id}/retry`, { method: "POST" }),
      onSuccess: done,
    }),
  };
}

export function useCovers(pid: string) {
  return useQuery({
    queryKey: ["covers", pid],
    queryFn: () => api<CoversResponse>(`/projects/${pid}/covers`),
    refetchInterval: (q) => (q.state.data?.status.status === "running" ? 2500 : false),
  });
}

export function useCoverMutations(pid: string) {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["covers", pid] });
  return {
    generate: useMutation({
      mutationFn: (b: { formats: string[]; headshot_id: string | null; count: number; note: string }) =>
        api(`/projects/${pid}/covers`, { method: "POST", body: JSON.stringify(b) }),
      onSuccess: done,
    }),
    choose: useMutation({
      mutationFn: (id: string) => api(`/covers/${id}/choose`, { method: "POST" }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api<void>(`/covers/${id}`, { method: "DELETE" }),
      onSuccess: done,
    }),
  };
}
