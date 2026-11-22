import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { ProjectStatusChip } from "@/components/ProjectStatusChip";
import { TopicCheckHint } from "@/components/TopicCheckHint";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { fmtMs, useCreateProject, useProjects, type Project } from "@/lib/projects";
import {
  CONTENT_TYPE_LABEL,
  useProjectStats,
  useStyleProfile,
  type ContentType,
  type Pillar,
} from "@/lib/style";
import { cn } from "@/lib/utils";

function narrationMs(p: Project) {
  const a = p.assets.find((a) => a.kind === "audio_final") ?? p.assets.find((a) => a.kind === "audio");
  return a?.meta.duration_ms;
}

const select = "h-10 rounded-lg border border-border bg-surface-2 px-3 text-sm";

export function ProjectsPage() {
  const { data, isLoading, error } = useProjects();
  const { data: profile } = useStyleProfile();
  const [creating, setCreating] = useState(false);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex flex-wrap items-center gap-4">
        <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
        <RatioChip />
        <Button className="ml-auto" onClick={() => setCreating(true)}>
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
          const color = p.pillar && profile ? profile.brand.pillars[p.pillar]?.color : undefined;
          return (
            <Link key={p.id} to={`/projects/${p.id}`} className="group">
              <Card className="h-full overflow-hidden transition-colors group-hover:border-accent/50">
                {color && <div className="h-1" style={{ background: color }} />}
                <CardContent className="flex h-full flex-col gap-3">
                  <div className="flex items-start justify-between gap-2">
                    <h2 className="font-medium break-words">{p.name}</h2>
                    <ProjectStatusChip status={p.status} />
                  </div>
                  <div className="flex flex-wrap gap-1.5 text-xs text-muted">
                    <span className="rounded bg-surface-2 px-1.5 py-0.5">
                      {CONTENT_TYPE_LABEL[p.content_type] ?? p.content_type}
                    </span>
                    {p.topic_kind && (
                      <span className="rounded bg-surface-2 px-1.5 py-0.5">{p.topic_kind}</span>
                    )}
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

function RatioChip() {
  const { data } = useProjectStats();
  if (!data || data.reach + data.authority === 0) return null;
  return (
    <span
      title="Reach : Authority ratio across projects (target 2 : 1)"
      className={cn(
        "rounded-full px-2.5 py-0.5 font-mono text-xs",
        data.on_target ? "bg-success/15 text-success" : "bg-warning/15 text-warning",
      )}
    >
      reach {data.reach} : authority {data.authority} · target 2:1
    </span>
  );
}

function NewProjectCard({ onCancel }: { onCancel: () => void }) {
  const create = useCreateProject();
  const { data: profile } = useStyleProfile();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [topic, setTopic] = useState("");
  const [contentType, setContentType] = useState<ContentType>("long_form");
  const [pillar, setPillar] = useState<Pillar | "">("");
  const [topicKind, setTopicKind] = useState<"reach" | "authority" | "">("");
  const [audience, setAudience] = useState<"beginner" | "advanced">("beginner");
  const [resolution, setResolution] = useState("1920x1080");
  const isShort = contentType !== "long_form";

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate(
      {
        name,
        topic: topic || name,
        content_type: contentType,
        pillar: pillar || null,
        topic_kind: topicKind || null,
        audience_level: audience,
        resolution: isShort ? "1920x1080" : resolution,
      },
      { onSuccess: (p) => navigate(`/projects/${p.id}/setup`) },
    );
  };

  return (
    <Card>
      <CardContent>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="pname">Project name</Label>
              <Input
                id="pname"
                autoFocus
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="RAG evaluation explained"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ptopic">Topic</Label>
              <Input
                id="ptopic"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="defaults to the name"
              />
            </div>
          </div>
          <TopicCheckHint title={topic || name} />
          <div className="flex flex-wrap gap-2">
            <select
              aria-label="Content type"
              value={contentType}
              onChange={(e) => setContentType(e.target.value as ContentType)}
              className={select}
            >
              {(Object.keys(CONTENT_TYPE_LABEL) as ContentType[]).map((c) => (
                <option key={c} value={c}>
                  {CONTENT_TYPE_LABEL[c]}
                </option>
              ))}
            </select>
            <select
              aria-label="Pillar"
              value={pillar}
              onChange={(e) => setPillar(e.target.value as Pillar | "")}
              className={select}
            >
              <option value="">pillar —</option>
              {(["series", "growth", "tools", "advice"] as const).map((p) => (
                <option key={p} value={p}>
                  {profile?.brand.pillars[p]?.label ?? p}
                </option>
              ))}
            </select>
            <select
              aria-label="Topic kind"
              value={topicKind}
              onChange={(e) => setTopicKind(e.target.value as "reach" | "authority" | "")}
              className={select}
            >
              <option value="">reach / authority —</option>
              <option value="reach">Reach (broad demand)</option>
              <option value="authority">Authority (deep teaching)</option>
            </select>
            <select
              aria-label="Audience"
              value={audience}
              onChange={(e) => setAudience(e.target.value as "beginner" | "advanced")}
              className={select}
            >
              <option value="beginner">beginner</option>
              <option value="advanced">advanced</option>
            </select>
            {!isShort && (
              <select
                aria-label="Resolution"
                value={resolution}
                onChange={(e) => setResolution(e.target.value)}
                className={select}
              >
                <option value="1920x1080">1080p</option>
                <option value="3840x2160">4K</option>
              </select>
            )}
          </div>
          <p className="text-xs text-muted">
            {isShort
              ? `Vertical 1080×1920 with a ${profile?.layout.shorts_canvas_aspect ?? "3:4"} full-canvas animation area; ≤ ~1.2 min.`
              : "16:9 with the face cam as a small circle bottom-right; no presenter intro, third person around “X”."}
          </p>
          <div className="flex gap-2">
            <Button type="submit" disabled={!name.trim() || create.isPending}>
              Create
            </Button>
            <Button type="button" variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          </div>
          {create.error && <p className="text-sm text-danger">{create.error.message}</p>}
        </form>
      </CardContent>
    </Card>
  );
}
