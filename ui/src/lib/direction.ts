import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";

export type Direction = {
  primary_mode: string;
  modes: string[];
  templates: string[];
  persona: string;
  hook: string;
  pacing: "calm" | "medium" | "fast";
  density: "sparse" | "balanced" | "rich";
  reasons: string[];
  avoid: string[];
  source: "ai" | "edited";
  auto?: boolean;
  model?: string;
  cost_usd?: number;
  at?: string;
};

export type DirectionEdit = Partial<
  Pick<Direction, "primary_mode" | "modes" | "templates" | "persona" | "hook" | "pacing" | "density">
>;

/** Scene templates the planner can use (backend: app/services/generation/templates.py). */
export const TEMPLATES: { id: string; label: string; hint: string }[] = [
  { id: "compare", label: "Compare", hint: "X vs Y, before/after, with a verdict" },
  { id: "steps", label: "Steps", hint: "a process or plan, 2–5 stages" },
  { id: "hub", label: "Hub", hint: "one thing connected to its parts" },
  { id: "flow", label: "Flow", hint: "requests between 2–4 systems" },
  { id: "scenario", label: "Scenario", hint: "the running example / a person in a situation" },
  { id: "code", label: "Code", hint: "real code with highlighted lines" },
  { id: "stat", label: "Stat", hint: "one big number, before → after" },
  { id: "timeline", label: "Timeline", hint: "roadmap / milestones" },
  { id: "checklist", label: "Checklist", hint: "points with ✓ / ✗ / ⚠" },
  { id: "table", label: "Table", hint: "small data comparison" },
];

function useDirectionAct<B>(pid: string, fn: (b: B) => Promise<Direction>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects", pid] }),
  });
}

export const useRecommendDirection = (pid: string) =>
  useDirectionAct(pid, () => api<Direction>(`/projects/${pid}/direction`, { method: "POST" }));

export const useEditDirection = (pid: string) =>
  useDirectionAct(pid, (b: DirectionEdit) =>
    api<Direction>(`/projects/${pid}/direction`, { method: "PUT", body: JSON.stringify(b) }),
  );
