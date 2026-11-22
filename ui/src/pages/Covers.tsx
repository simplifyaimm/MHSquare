import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Check, Download, ImageIcon, Loader2, Trash2, Wand2 } from "lucide-react";
import { CreatorPhotos } from "@/components/CreatorPhotos";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useCoverMutations, useCovers, useHeadshots, type Cover } from "@/lib/covers";
import { useProject } from "@/lib/projects";
import { cn } from "@/lib/utils";

const FORMAT_LABEL = { youtube: "YouTube thumbnail · 1280×720", short: "Shorts / Reels cover · 1080×1920" };

export function CoversPage() {
  const { id = "" } = useParams();
  const { data: p } = useProject(id);
  const { data, isLoading } = useCovers(id);
  const { data: photos } = useHeadshots();
  const m = useCoverMutations(id);
  const isShort = p ? p.content_type !== "long_form" : false;
  const [formats, setFormats] = useState<string[] | null>(null);
  const fm = formats ?? (isShort ? ["short", "youtube"] : ["youtube", "short"]);
  const [photo, setPhoto] = useState<string>("");
  const [count, setCount] = useState(4);
  const [note, setNote] = useState("");
  const running = data?.status.status === "running";
  const ready = (photos ?? []).filter((h) => h.status === "ready");

  const byFormat = useMemo(() => {
    const g: Record<string, Cover[]> = { youtube: [], short: [] };
    for (const c of data?.covers ?? []) g[c.format]?.push(c);
    return g;
  }, [data]);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-center gap-3">
        <Link to={`/projects/${id}`} className="text-muted hover:text-text" aria-label="Back to project">
          <ArrowLeft size={20} />
        </Link>
        <h1 className="truncate text-2xl font-semibold tracking-tight">Cover photos · {p?.name}</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Wand2 size={16} /> Generate covers
          </CardTitle>
          <p className="text-sm text-muted">
            ≤ 4-word English headline, technical keywords in your pillar colour, your cut-out photo with the
            brand glow, icons from the same library as your videos. Free to render; one small AI call for the
            headline ideas.
          </p>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-4 text-sm">
            {(["youtube", "short"] as const).map((f) => (
              <label key={f} className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={fm.includes(f)}
                  onChange={(e) => setFormats(e.target.checked ? [...fm, f] : fm.filter((x) => x !== f))}
                />
                {FORMAT_LABEL[f]}
              </label>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <select
              value={photo}
              onChange={(e) => setPhoto(e.target.value)}
              className="h-10 rounded-lg border border-border bg-surface-2 px-3 text-sm"
              aria-label="Creator photo"
            >
              <option value="">{ready.length ? "Latest creator photo" : "No photo (icons only)"}</option>
              {ready.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.original_name}
                </option>
              ))}
            </select>
            <select
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
              className="h-10 rounded-lg border border-border bg-surface-2 px-3 text-sm"
              aria-label="Number of concepts"
            >
              {[2, 3, 4, 5, 6].map((n) => (
                <option key={n} value={n}>
                  {n} concepts
                </option>
              ))}
            </select>
            <Input
              className="min-w-56 flex-1"
              placeholder="Optional direction, e.g. focus on the cost saving"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            <Button
              disabled={!fm.length || running || m.generate.isPending}
              onClick={() => m.generate.mutate({ formats: fm, headshot_id: photo || null, count, note })}
            >
              {running ? <Loader2 size={16} className="animate-spin" /> : <ImageIcon size={16} />}
              {running ? "Designing…" : "Generate covers"}
            </Button>
          </div>
          {data?.status.status === "failed" && <p className="text-sm text-danger">{data.status.error}</p>}
          {m.generate.error && <p className="text-sm text-danger">{m.generate.error.message}</p>}
        </CardContent>
      </Card>

      {!ready.length && <CreatorPhotos />}
      {isLoading && <p className="text-muted">Loading…</p>}

      {(["youtube", "short"] as const).map((f) =>
        byFormat[f].length ? (
          <section key={f} className="flex flex-col gap-3">
            <h2 className="text-sm font-medium text-muted">{FORMAT_LABEL[f]}</h2>
            <div
              className={cn(
                "grid gap-4",
                f === "youtube"
                  ? "sm:grid-cols-2 lg:grid-cols-3"
                  : "grid-cols-2 sm:grid-cols-3 lg:grid-cols-5",
              )}
            >
              {byFormat[f].map((c) => (
                <div
                  key={c.id}
                  className={cn(
                    "overflow-hidden rounded-xl border-2 bg-surface",
                    c.chosen ? "border-success" : "border-border",
                  )}
                >
                  <img src={c.url} alt={c.headline} className="w-full" loading="lazy" />
                  <div className="flex items-center gap-1 p-2">
                    <span className="min-w-0 flex-1 truncate text-sm">{c.headline}</span>
                    <Button
                      size="sm"
                      variant={c.chosen ? "success" : "secondary"}
                      onClick={() => m.choose.mutate(c.id)}
                      disabled={c.chosen}
                    >
                      <Check size={14} /> {c.chosen ? "Chosen" : "Use"}
                    </Button>
                    <a
                      href={`${c.url}&dl=${encodeURIComponent(`cover-${f}-${c.option}.jpg`)}`}
                      aria-label="Download cover"
                    >
                      <Button size="icon" variant="ghost" type="button">
                        <Download size={14} />
                      </Button>
                    </a>
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label="Delete cover"
                      onClick={() => m.remove.mutate(c.id)}
                    >
                      <Trash2 size={14} />
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </section>
        ) : null,
      )}
    </div>
  );
}
