import { cn } from "@/lib/utils";

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

const styles: Record<ShotState, string> = {
  planned: "bg-surface-2 text-muted",
  queued: "bg-surface-2 text-muted",
  keyframe_generating: "bg-accent-2/15 text-accent-2 animate-mh-pulse",
  generating: "bg-accent-2/15 text-accent-2 animate-mh-pulse",
  keyframe_review: "bg-accent/15 text-accent",
  review: "bg-accent/15 text-accent",
  accepted: "bg-success/15 text-success",
  paused_budget: "bg-warning/15 text-warning",
  needs_edit: "bg-danger/15 text-danger",
  failed: "bg-danger/15 text-danger",
};

export function StateChip({ state }: { state: ShotState }) {
  return (
    <span className={cn("inline-flex rounded-full px-2.5 py-0.5 font-mono text-xs", styles[state])}>
      {state.replaceAll("_", " ")}
    </span>
  );
}
