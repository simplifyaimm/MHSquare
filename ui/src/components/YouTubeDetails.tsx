import { useState } from "react";
import { Check, CirclePlay, Copy, Loader2, RefreshCw, Save, Sparkles } from "lucide-react";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Input } from "./ui/input";
import { LIMITS, tagChars, useEditPublish, useGeneratePublish, type Publish } from "@/lib/publish";
import type { Project } from "@/lib/projects";
import { cn } from "@/lib/utils";

const area =
  "w-full rounded-lg border border-border bg-surface-2 p-3 text-sm leading-relaxed focus:border-accent focus:outline-none";

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button
      size="sm"
      variant="ghost"
      aria-label={`Copy ${label}`}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard blocked (http / permissions): the text is still selectable */
        }
      }}
    >
      {done ? <Check size={14} /> : <Copy size={14} />}
      {done ? "Copied" : "Copy"}
    </Button>
  );
}

function Count({ n, max, soft }: { n: number; max: number; soft?: number }) {
  return (
    <span
      className={cn(
        "font-mono text-xs",
        n > max ? "text-danger" : soft && n > soft ? "text-warning" : "text-muted",
      )}
    >
      {n}/{soft ?? max}
    </span>
  );
}

/** Title options, description (with real chapters), tags and a pinned comment — ready to paste into YouTube. */
export function YouTubeDetails({ p }: { p: Project }) {
  const generate = useGeneratePublish(p.id);
  const d = p.publish ?? null;
  const hasScript = p.assets.some((a) => a.kind === "script");

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center gap-2">
        <CirclePlay size={16} className="text-muted" />
        <CardTitle>YouTube details</CardTitle>
        {d && (
          <span className="text-xs text-muted">
            {d.source === "edited" ? "edited by you" : "AI written"}
            {d.cost_usd ? ` · $${d.cost_usd.toFixed(3)}` : ""}
          </span>
        )}
        <Button
          className="ml-auto"
          size="sm"
          variant={d ? "ghost" : "default"}
          onClick={() => generate.mutate()}
          disabled={generate.isPending || !hasScript}
          title={hasScript ? "" : "Upload the script first"}
        >
          {generate.isPending ? (
            <Loader2 size={14} className="animate-spin" />
          ) : d ? (
            <RefreshCw size={14} />
          ) : (
            <Sparkles size={14} />
          )}
          {d ? "Write again" : "Write title & description"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {!d && (
          <p className="text-sm text-muted">
            Title options, a description with chapters at the real shot times, tags and a pinned comment —
            written from the script in your channel's voice, ready to paste into YouTube Studio. Choose a
            cover first and the titles will complement its headline.
          </p>
        )}
        {generate.error && <p className="text-sm text-danger">{generate.error.message}</p>}
        {/* remounts with fresh fields whenever new details arrive (a rewrite or a save) */}
        {d && <Editor key={`${d.at ?? ""}|${d.edited_at ?? ""}`} pid={p.id} d={d} />}
      </CardContent>
    </Card>
  );
}

function Editor({ pid, d }: { pid: string; d: Publish }) {
  const edit = useEditPublish(pid);
  const [title, setTitle] = useState(d.title);
  const [description, setDescription] = useState(d.description);
  const [tags, setTags] = useState(d.tags.join(", "));
  const [pinned, setPinned] = useState(d.pinned_comment);
  const tagList = tags
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
  const dirty =
    title !== d.title ||
    description !== d.description ||
    tagList.join(",") !== d.tags.join(",") ||
    pinned !== d.pinned_comment;

  return (
    <>
      {edit.error && <p className="text-sm text-danger">{edit.error.message}</p>}
      <section className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-muted">Title</span>
          <Count n={title.length} max={LIMITS.title} soft={LIMITS.titleShown} />
          <span className="ml-auto">
            <CopyButton text={title} label="title" />
          </span>
        </div>
        <Input value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Title" />
        <div className="flex flex-col gap-1" role="radiogroup" aria-label="Title options">
          {d.titles.map((t) => (
            <label
              key={t}
              className={cn(
                "flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm",
                t === title ? "border-accent bg-accent/10" : "border-border hover:border-muted",
              )}
            >
              <input
                type="radio"
                name="title"
                checked={t === title}
                onChange={() => setTitle(t)}
                className="accent-[var(--accent)]"
              />
              <span className="min-w-0 flex-1">{t}</span>
              <Count n={t.length} max={LIMITS.title} soft={LIMITS.titleShown} />
            </label>
          ))}
        </div>
        {d.cover_headline && <p className="text-xs text-muted">Cover headline: “{d.cover_headline}”</p>}
      </section>

      <section className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-muted">Description</span>
          <Count n={description.length} max={LIMITS.description} />
          {d.chapters.length > 0 && (
            <span className="text-xs text-muted">· {d.chapters.length} chapters from the shot timeline</span>
          )}
          <span className="ml-auto">
            <CopyButton text={description} label="description" />
          </span>
        </div>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={14}
          className={area}
          aria-label="Description"
        />
      </section>

      <section className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-muted">
            Tags · comma-separated
          </span>
          <Count n={tagChars(tagList)} max={LIMITS.tags} />
          <span className="ml-auto">
            <CopyButton text={tagList.join(", ")} label="tags" />
          </span>
        </div>
        <textarea
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          rows={3}
          className={area}
          aria-label="Tags"
        />
      </section>

      <section className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium uppercase tracking-wide text-muted">Pinned comment</span>
          <span className="ml-auto">
            <CopyButton text={pinned} label="pinned comment" />
          </span>
        </div>
        <textarea
          value={pinned}
          onChange={(e) => setPinned(e.target.value)}
          rows={2}
          className={area}
          aria-label="Pinned comment"
        />
      </section>

      <div className="flex items-center gap-2">
        <Button
          size="sm"
          disabled={!dirty || edit.isPending}
          onClick={() => edit.mutate({ title, description, tags: tagList, pinned_comment: pinned })}
        >
          {edit.isPending ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
          Save changes
        </Button>
        {dirty && <span className="text-xs text-muted">unsaved changes</span>}
      </div>
    </>
  );
}
