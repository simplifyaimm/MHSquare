import { useMemo, useState, type FormEvent } from "react";
import { Plus, Trash2 } from "lucide-react";
import { TopicCheckHint } from "@/components/TopicCheckHint";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  CONTENT_TYPE_LABEL,
  useStyleProfile,
  useTopicMutations,
  useTopics,
  type ContentType,
  type Pillar,
} from "@/lib/style";

export function TopicsPage() {
  const { data, isLoading } = useTopics();
  const { data: profile } = useStyleProfile();
  const m = useTopicMutations();
  const [title, setTitle] = useState("");
  const [pillar, setPillar] = useState<Pillar | "">("");
  const [ctype, setCtype] = useState<ContentType | "">("");
  const [status, setStatus] = useState<"covered" | "planned">("planned");
  const [filter, setFilter] = useState("");

  const shown = useMemo(
    () => (data ?? []).filter((t) => t.title.toLowerCase().includes(filter.toLowerCase())),
    [data, filter],
  );
  const pillarColor = (p: Pillar | null) => (p && profile ? profile.brand.pillars[p]?.color : undefined);

  const add = (e: FormEvent) => {
    e.preventDefault();
    m.add.mutate(
      { title, pillar: pillar || null, content_type: ctype || null, status },
      { onSuccess: () => setTitle("") },
    );
  };

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Covered topics</h1>
        <p className="mt-1 text-sm text-muted">
          New projects are checked against this list (semantic similarity) so you don&apos;t repeat yourself.
          Finalised projects are added automatically.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Add a topic</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={add} className="flex flex-col gap-3">
            <Input
              placeholder="Topic title, e.g. Hybrid Search"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              aria-label="Topic title"
            />
            <TopicCheckHint title={title} />
            <div className="flex flex-wrap gap-2">
              <select
                value={pillar}
                onChange={(e) => setPillar(e.target.value as Pillar | "")}
                className="h-10 rounded-lg border border-border bg-surface-2 px-2 text-sm"
                aria-label="Pillar"
              >
                <option value="">pillar —</option>
                {(["series", "growth", "tools", "advice"] as const).map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
              <select
                value={ctype}
                onChange={(e) => setCtype(e.target.value as ContentType | "")}
                className="h-10 rounded-lg border border-border bg-surface-2 px-2 text-sm"
                aria-label="Content type"
              >
                <option value="">content type —</option>
                {(Object.keys(CONTENT_TYPE_LABEL) as ContentType[]).map((c) => (
                  <option key={c} value={c}>
                    {CONTENT_TYPE_LABEL[c]}
                  </option>
                ))}
              </select>
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value as "covered" | "planned")}
                className="h-10 rounded-lg border border-border bg-surface-2 px-2 text-sm"
                aria-label="Status"
              >
                <option value="planned">planned</option>
                <option value="covered">covered</option>
              </select>
              <Button type="submit" disabled={title.trim().length < 2 || m.add.isPending}>
                <Plus size={16} /> Add
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-center gap-3">
          <CardTitle>All topics ({data?.length ?? 0})</CardTitle>
          <Input
            placeholder="Filter"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="ml-auto max-w-56"
          />
        </CardHeader>
        <CardContent>
          {isLoading && <p className="text-muted">Loading…</p>}
          <ul className="divide-y divide-border">
            {shown.map((t) => (
              <li key={t.id} className="flex items-center gap-3 py-2.5 text-sm">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ background: pillarColor(t.pillar) ?? "var(--border)" }}
                />
                <span className="min-w-0 flex-1 truncate">{t.title}</span>
                <span className="hidden text-xs text-muted sm:inline">
                  {t.content_type ? CONTENT_TYPE_LABEL[t.content_type] : ""}
                </span>
                <button
                  onClick={() =>
                    m.patch.mutate({ id: t.id, status: t.status === "covered" ? "planned" : "covered" })
                  }
                  className={`rounded-full px-2.5 py-0.5 font-mono text-xs ${t.status === "covered" ? "bg-success/15 text-success" : "bg-warning/15 text-warning"}`}
                  title="Toggle covered / planned"
                >
                  {t.status}
                </button>
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={`Delete ${t.title}`}
                  onClick={() => m.remove.mutate(t.id)}
                >
                  <Trash2 size={14} />
                </Button>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
