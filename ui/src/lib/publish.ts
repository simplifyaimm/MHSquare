import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

/** YouTube details for a video (backend: app/services/publish.py). */
export type Publish = {
  titles: string[];
  title: string;
  hook: string;
  learn: string[];
  chapters: { at: string; ms: number; title: string }[];
  description: string;
  tags: string[];
  hashtags: string[];
  pinned_comment: string;
  cover_headline: string;
  source: "ai" | "edited";
  model?: string;
  cost_usd?: number;
  at?: string;
  edited_at?: string;
};

export type PublishEdit = Partial<Pick<Publish, "title" | "description" | "pinned_comment">> & {
  tags?: string[] | string;
};

/** YouTube limits (the backend enforces them too). */
export const LIMITS = { title: 100, titleShown: 70, description: 5000, tags: 500 };

/** Characters YouTube counts for a tag list: each tag, quotes around multi-word tags, commas between. */
export const tagChars = (tags: string[]) =>
  tags.reduce((n, t, i) => n + t.length + (t.includes(" ") ? 2 : 0) + (i ? 1 : 0), 0);

function usePublishAct<B>(pid: string, fn: (b: B) => Promise<Publish>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects", pid] }),
  });
}

export const useGeneratePublish = (pid: string) =>
  usePublishAct(pid, () => api<Publish>(`/projects/${pid}/publish`, { method: "POST" }));

export const useEditPublish = (pid: string) =>
  usePublishAct(pid, (b: PublishEdit) =>
    api<Publish>(`/projects/${pid}/publish`, { method: "PUT", body: JSON.stringify(b) }),
  );
