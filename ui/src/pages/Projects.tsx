import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { ProjectStatusChip } from "@/components/ProjectStatusChip";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { fmtMs, useCreateProject, useProjects, type Project } from "@/lib/projects";

function narrationMs(p: Project) {
  const a = p.assets.find((a) => a.kind === "audio_final") ?? p.assets.find((a) => a.kind === "audio");
  return a?.meta.duration_ms;
}

export function ProjectsPage() {
  const { data, isLoading, error } = useProjects();
  const [creating, setCreating] = useState(false);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
        <Button onClick={() => setCreating(true)}>
          <Plus size={16} /> New project
        </Button>
      </div>

      {creating && <NewProjectCard onCancel={() => setCreating(false)} />}
      {isLoading && <p className="text-muted">Loading…</p>}
      {error && <p className="text-danger">{error.message}</p>}
      {data?.length === 0 && !creating && (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
            <p className="text-muted">No projects yet. Start with a script and your recorded narration.</p>
            <Button onClick={() => setCreating(true)}>
              <Plus size={16} /> New project
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {data?.map((p) => {
          const ms = narrationMs(p);
          return (
            <Link key={p.id} to={`/projects/${p.id}`} className="group">
              <Card className="h-full transition-colors group-hover:border-accent/50">
                <CardContent className="flex h-full flex-col gap-3">
                  <div className="flex items-start justify-between gap-2">
                    <h2 className="font-medium break-words">{p.name}</h2>
                    <ProjectStatusChip status={p.status} />
                  </div>
                  <div className="mt-auto flex justify-between font-mono text-xs text-muted">
                    <span>{ms ? fmtMs(ms) : "—"}</span>
                    <span>{new Date(p.created_at).toLocaleDateString()}</span>
                  </div>
                </CardContent>
              </Card>
            </Link>
          );
        })}
      </div>
    </div>
  );
}

function NewProjectCard({ onCancel }: { onCancel: () => void }) {
  const create = useCreateProject();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [resolution, setResolution] = useState("1920x1080");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate({ name, resolution }, { onSuccess: (p) => navigate(`/projects/${p.id}/setup`) });
  };

  return (
    <Card>
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-4 sm:flex-row sm:items-end">
          <div className="flex flex-1 flex-col gap-1.5">
            <Label htmlFor="pname">Project name</Label>
            <Input
              id="pname"
              autoFocus
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Kafka in 5 minutes"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="res">Resolution</Label>
            <select
              id="res"
              value={resolution}
              onChange={(e) => setResolution(e.target.value)}
              className="h-10 rounded-lg border border-border bg-surface-2 px-3 text-sm"
            >
              <option value="1920x1080">1080p (1920×1080)</option>
              <option value="3840x2160">4K (3840×2160)</option>
            </select>
          </div>
          <div className="flex gap-2">
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              Create
            </Button>
            <Button type="button" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </form>
        {create.error && <p className="mt-3 text-sm text-danger">{create.error.message}</p>}
      </CardContent>
    </Card>
  );
}
