import type { ProjectStatus } from "@/lib/projects";
import { cn } from "@/lib/utils";

const styles: Record<ProjectStatus, string> = {
  draft: "bg-surface-2 text-muted",
  ingesting: "bg-accent-2/15 text-accent-2 animate-mh-pulse",
  finalizing: "bg-accent-2/15 text-accent-2 animate-mh-pulse",
  cuts_review: "bg-accent/15 text-accent",
  ready: "bg-success/15 text-success",
  planning: "bg-accent-2/15 text-accent-2 animate-mh-pulse",
  planned: "bg-accent/15 text-accent",
  failed: "bg-danger/15 text-danger",
};

const labels: Record<ProjectStatus, string> = {
  draft: "draft",
  ingesting: "ingesting",
  cuts_review: "review cuts",
  finalizing: "finalizing",
  ready: "ready to plan",
  planning: "planning",
  planned: "in production",
  failed: "failed",
};

export function ProjectStatusChip({ status }: { status: ProjectStatus }) {
  return (
    <span
      className={cn(
        "inline-flex rounded-full px-2.5 py-0.5 font-mono text-xs whitespace-nowrap",
        styles[status],
      )}
    >
      {labels[status]}
    </span>
  );
}
