import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export type ContentType =
  "long_form" | "short_series" | "short_growth" | "short_tools" | "short_advice" | "reel";
export type Pillar = "series" | "growth" | "tools" | "advice";

export const CONTENT_TYPE_LABEL: Record<ContentType, string> = {
  long_form: "YouTube long-form",
  short_series: "Short — Series",
  short_growth: "Short — Growth",
  short_tools: "Short — Tools",
  short_advice: "Short — Advice",
  reel: "Instagram Reel",
};

export type StyleProfile = {
  id: string;
  version: number;
  is_active: boolean;
  note: string;
  created_at: string | null;
  language: "en";
  brand: {
    name: string;
    tagline: string;
    handle: string;
    watermark: string;
    pillars: Record<Pillar, { label: string; color: string }>;
  };
  voice_rules: string;
  judge_rubric: { key: string; label: string; kind: string }[];
  audience_default: "beginner" | "advanced";
  presets: Record<
    ContentType,
    Record<string, unknown> & { label: string; rules: string; warn_words?: number | null; wpm?: number }
  >;
  animation_style: Record<string, unknown> & {
    palette: Record<string, string>;
    allowed_fonts: string[];
    footer: { enabled: boolean; left: string; right: string };
    rules: string[];
  };
  layout: {
    facecam_circle: "composite" | "leave_space";
    facecam_safe_area: { w_pct: number; h_pct: number };
    watermark_position: string;
    footer_on_animations: boolean;
    shorts_canvas_aspect: "3:4" | "9:16";
    shorts_export: number[];
  };
  caption_formula: string;
  reply_framework: string;
};

export type Topic = {
  id: string;
  title: string;
  pillar: Pillar | null;
  content_type: ContentType | null;
  status: "covered" | "planned";
  source_project_id: string | null;
  created_at: string;
};

export type TopicCheck = {
  title: string;
  method: "embedding" | "lexical";
  likely_repeat: boolean;
  matches: { id: string; title: string; status: string; score: number; pillar: string | null }[];
};

export function useStyleProfile() {
  return useQuery({ queryKey: ["style", "profile"], queryFn: () => api<StyleProfile>("/style/profile") });
}

export function useStyleVersions() {
  return useQuery({
    queryKey: ["style", "versions"],
    queryFn: () =>
      api<{ version: number; is_active: boolean; note: string; created_at: string }[]>("/style/versions"),
  });
}

function useStyleMutation<B>(fn: (b: B) => Promise<StyleProfile>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: ["style"] }) });
}

export const useSaveStyle = () =>
  useStyleMutation((b: Partial<StyleProfile> & { note?: string }) =>
    api<StyleProfile>("/style/profile", { method: "PUT", body: JSON.stringify(b) }),
  );
export const useActivateVersion = () =>
  useStyleMutation((v: number) => api<StyleProfile>(`/style/versions/${v}/activate`, { method: "POST" }));
export const useResetStyle = () =>
  useStyleMutation(() => api<StyleProfile>("/style/reset", { method: "POST" }));

export function useTopics() {
  return useQuery({ queryKey: ["topics"], queryFn: () => api<Topic[]>("/topics") });
}

export function useTopicMutations() {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["topics"] });
  return {
    add: useMutation({
      mutationFn: (b: Partial<Topic>) => api<Topic>("/topics", { method: "POST", body: JSON.stringify(b) }),
      onSuccess: done,
    }),
    patch: useMutation({
      mutationFn: ({ id, ...b }: Partial<Topic> & { id: string }) =>
        api<Topic>(`/topics/${id}`, { method: "PATCH", body: JSON.stringify(b) }),
      onSuccess: done,
    }),
    remove: useMutation({
      mutationFn: (id: string) => api<void>(`/topics/${id}`, { method: "DELETE" }),
      onSuccess: done,
    }),
  };
}

export const checkTopic = (title: string) =>
  api<TopicCheck>("/topics/check", { method: "POST", body: JSON.stringify({ title }) });

export function useSuggestReply() {
  return useMutation({
    mutationFn: (b: { comment: string; context?: string }) =>
      api<{ reply: string; cost_usd: number }>("/tools/suggest-reply", {
        method: "POST",
        body: JSON.stringify(b),
      }),
  });
}

export type ScriptCheck = {
  passed: boolean | null;
  verdict?: "rejected" | "needs_work" | "good";
  english_only?: "pass" | "fail";
  error?: string;
  deterministic: {
    words: number;
    est_minutes: number;
    wpm: number;
    english_only: boolean;
    flags: { kind: string; severity: "fail" | "warn"; message: string }[];
  };
  scores?: { key: string; score?: number; result?: string; reason: string }[];
  average?: number | null;
  issues?: string[];
  summary?: string;
};

export function useScriptCheck(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<ScriptCheck>(`/projects/${pid}/script-check`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects", pid] }),
  });
}

export function useProjectStats() {
  return useQuery({
    queryKey: ["projects", "stats"],
    queryFn: () =>
      api<{ reach: number; authority: number; untagged: number; on_target: boolean }>("/projects-stats"),
  });
}

export function useMarkFinal(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ title: string }>(`/projects/${pid}/mark-final`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["topics"] }),
  });
}
