import { useState } from "react";
import { History, Loader2, MessageSquareReply, RotateCcw, Save } from "lucide-react";
import { CreatorPhotos } from "@/components/CreatorPhotos";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import {
  CONTENT_TYPE_LABEL,
  useActivateVersion,
  useResetStyle,
  useSaveStyle,
  useStyleProfile,
  useStyleVersions,
  useSuggestReply,
  type ContentType,
  type Pillar,
  type StyleProfile,
} from "@/lib/style";

const area =
  "w-full rounded-lg border border-border bg-surface-2 p-3 text-sm leading-relaxed focus:border-accent focus:outline-none";

export function BrandPage() {
  const { data, isLoading, error } = useStyleProfile();
  if (isLoading) return <p className="text-muted">Loading…</p>;
  if (error || !data) return <p className="text-danger">{error?.message}</p>;
  return <BrandEditor key={data.version} profile={data} />;
}

function BrandEditor({ profile }: { profile: StyleProfile }) {
  const [p, setP] = useState<StyleProfile>(profile);
  const [note, setNote] = useState("");
  const [jsonErr, setJsonErr] = useState<string | null>(null);
  const [presetsText, setPresetsText] = useState(JSON.stringify(profile.presets, null, 2));
  const [animText, setAnimText] = useState(JSON.stringify(profile.animation_style, null, 2));
  const save = useSaveStyle();
  const reset = useResetStyle();
  const set = <K extends keyof StyleProfile>(k: K, v: StyleProfile[K]) => setP((x) => ({ ...x, [k]: v }));
  const setBrand = (k: keyof StyleProfile["brand"], v: string) => set("brand", { ...p.brand, [k]: v });
  const setPillar = (k: Pillar, color: string) =>
    set("brand", { ...p.brand, pillars: { ...p.brand.pillars, [k]: { ...p.brand.pillars[k], color } } });
  const setLayout = <K extends keyof StyleProfile["layout"]>(k: K, v: StyleProfile["layout"][K]) =>
    set("layout", { ...p.layout, [k]: v });

  const submit = () => {
    let presets, animation_style;
    try {
      presets = JSON.parse(presetsText);
      animation_style = JSON.parse(animText);
      setJsonErr(null);
    } catch (e) {
      setJsonErr(`JSON: ${(e as Error).message}`);
      return;
    }
    // keep pillar colours in sync with the animation palette
    for (const k of Object.keys(p.brand.pillars) as Pillar[])
      animation_style.palette[k] = p.brand.pillars[k].color;
    save.mutate({
      note: note || "Edited in the UI",
      brand: p.brand,
      voice_rules: p.voice_rules,
      audience_default: p.audience_default,
      presets,
      animation_style,
      layout: p.layout,
      caption_formula: p.caption_formula,
      reply_framework: p.reply_framework,
      judge_rubric: p.judge_rubric,
    });
  };

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex-1">
          <h1 className="text-2xl font-semibold tracking-tight">Brand &amp; style</h1>
          <p className="mt-1 text-sm text-muted">
            The Style Profile is injected into every prompt that writes words or visuals. Each save creates a
            new version (v{profile.version} is active). Language is locked to English.
          </p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Brand</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          {(["name", "tagline", "handle", "watermark"] as const).map((k) => (
            <div key={k} className="flex flex-col gap-1.5">
              <Label htmlFor={`b-${k}`} className="capitalize">
                {k === "watermark" ? "Watermark text" : k}
              </Label>
              <Input id={`b-${k}`} value={p.brand[k]} onChange={(e) => setBrand(k, e.target.value)} />
            </div>
          ))}
          <div className="sm:col-span-2">
            <Label>Pillars</Label>
            <div className="mt-2 flex flex-wrap gap-3">
              {(Object.keys(p.brand.pillars) as Pillar[]).map((k) => (
                <label
                  key={k}
                  className="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm"
                >
                  <input
                    type="color"
                    value={p.brand.pillars[k].color}
                    onChange={(e) => setPillar(k, e.target.value)}
                    aria-label={`${k} colour`}
                    className="h-6 w-8 cursor-pointer bg-transparent"
                  />
                  {p.brand.pillars[k].label}
                  <span className="font-mono text-xs text-muted">{p.brand.pillars[k].color}</span>
                </label>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Voice &amp; teaching rules</CardTitle>
          <p className="text-sm text-muted">
            Used by the script judge, the planner and (later) Script Studio.
          </p>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <textarea
            rows={12}
            className={area}
            value={p.voice_rules}
            onChange={(e) => set("voice_rules", e.target.value)}
          />
          <div className="flex items-center gap-3 text-sm">
            <Label htmlFor="aud">Default audience</Label>
            <select
              id="aud"
              value={p.audience_default}
              onChange={(e) => set("audience_default", e.target.value as "beginner" | "advanced")}
              className="h-9 rounded-lg border border-border bg-surface-2 px-2"
            >
              <option value="beginner">beginner</option>
              <option value="advanced">advanced</option>
            </select>
          </div>
          <details>
            <summary className="cursor-pointer text-sm text-muted">
              Judge rubric ({p.judge_rubric.length} criteria)
            </summary>
            <ul className="mt-2 list-disc pl-5 text-sm text-muted">
              {p.judge_rubric.map((r) => (
                <li key={r.key}>
                  {r.label} <span className="font-mono text-xs">({r.kind})</span>
                </li>
              ))}
            </ul>
          </details>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Layout</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="fc">Face-cam circle (long-form)</Label>
            <select
              id="fc"
              value={p.layout.facecam_circle}
              onChange={(e) => setLayout("facecam_circle", e.target.value as "composite" | "leave_space")}
              className="h-10 rounded-lg border border-border bg-surface-2 px-2 text-sm"
            >
              <option value="composite">composite — mhsquare adds the circle</option>
              <option value="leave_space">leave space — Descript adds it</option>
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="asp">Shorts animation canvas</Label>
            <select
              id="asp"
              value={p.layout.shorts_canvas_aspect}
              onChange={(e) => setLayout("shorts_canvas_aspect", e.target.value as "3:4" | "9:16")}
              className="h-10 rounded-lg border border-border bg-surface-2 px-2 text-sm"
            >
              <option value="3:4">3:4 inside 1080×1920</option>
              <option value="9:16">9:16 full frame</option>
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Face-cam safe area (bottom-right, % of frame)</Label>
            <div className="flex gap-2">
              {(["w_pct", "h_pct"] as const).map((k) => (
                <Input
                  key={k}
                  type="number"
                  min={5}
                  max={45}
                  aria-label={k}
                  className="font-mono"
                  value={p.layout.facecam_safe_area[k]}
                  onChange={(e) =>
                    setLayout("facecam_safe_area", {
                      ...p.layout.facecam_safe_area,
                      [k]: Number(e.target.value),
                    })
                  }
                />
              ))}
            </div>
          </div>
          <label className="flex items-center gap-2 self-end text-sm">
            <input
              type="checkbox"
              checked={p.layout.footer_on_animations}
              onChange={(e) => setLayout("footer_on_animations", e.target.checked)}
            />
            Footer on animations (“{p.animation_style.footer.left}” · “{p.animation_style.footer.right}”)
          </label>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Content-type presets</CardTitle>
          <p className="text-sm text-muted">
            {Object.entries(p.presets)
              .map(([k, v]) => CONTENT_TYPE_LABEL[k as ContentType] ?? v.label)
              .join(" · ")}
          </p>
        </CardHeader>
        <CardContent>
          <textarea
            rows={10}
            spellCheck={false}
            className={`${area} font-mono text-xs`}
            value={presetsText}
            onChange={(e) => setPresetsText(e.target.value)}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Animation style</CardTitle>
          <p className="text-sm text-muted">
            Palette, fonts, grid, entry timing, footer, code-demo style and rules (checked by the style lint).
          </p>
        </CardHeader>
        <CardContent>
          <textarea
            rows={12}
            spellCheck={false}
            className={`${area} font-mono text-xs`}
            value={animText}
            onChange={(e) => setAnimText(e.target.value)}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Captions &amp; replies</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cap">Save-optimised caption formula</Label>
            <textarea
              id="cap"
              rows={5}
              className={area}
              value={p.caption_formula}
              onChange={(e) => set("caption_formula", e.target.value)}
            />
            {p.caption_formula.includes("PLACEHOLDER") && (
              <p className="text-xs text-warning">
                Still the placeholder — paste your exact caption formula here.
              </p>
            )}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rep">Comment-reply framework</Label>
            <textarea
              id="rep"
              rows={5}
              className={area}
              value={p.reply_framework}
              onChange={(e) => set("reply_framework", e.target.value)}
            />
          </div>
        </CardContent>
      </Card>

      <div className="sticky bottom-20 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-border bg-surface p-3 shadow-lg shadow-black/40 md:bottom-4">
        <Input
          placeholder="Version note (what changed?)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          className="min-w-48 flex-1"
        />
        <Button onClick={submit} disabled={save.isPending}>
          {save.isPending ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />} Save as v
          {profile.version + 1}
        </Button>
        {(jsonErr || save.error) && (
          <p className="w-full text-sm text-danger">{jsonErr ?? save.error?.message}</p>
        )}
      </div>

      <CreatorPhotos />
      <Versions resetting={reset.isPending} onReset={() => reset.mutate()} />
      <ReplyHelper />
    </div>
  );
}

function Versions({ onReset, resetting }: { onReset: () => void; resetting: boolean }) {
  const { data } = useStyleVersions();
  const activate = useActivateVersion();
  return (
    <Card>
      <CardHeader className="flex-row items-center gap-2">
        <History size={16} className="text-muted" />
        <CardTitle>Versions</CardTitle>
        <Button variant="ghost" size="sm" className="ml-auto" onClick={onReset} disabled={resetting}>
          <RotateCcw size={14} /> Reset to seeded defaults
        </Button>
      </CardHeader>
      <CardContent>
        <ul className="divide-y divide-border text-sm">
          {data?.map((v) => (
            <li key={v.version} className="flex items-center gap-3 py-2">
              <span className="font-mono">v{v.version}</span>
              <span className="min-w-0 flex-1 truncate text-muted">{v.note}</span>
              <span className="hidden font-mono text-xs text-muted sm:inline">
                {new Date(v.created_at).toLocaleString()}
              </span>
              {v.is_active ? (
                <span className="rounded-full bg-success/15 px-2 py-0.5 text-xs text-success">active</span>
              ) : (
                <Button size="sm" variant="ghost" onClick={() => activate.mutate(v.version)}>
                  Activate
                </Button>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function ReplyHelper() {
  const reply = useSuggestReply();
  const [comment, setComment] = useState("");
  const [context, setContext] = useState("");
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <MessageSquareReply size={16} /> Suggest reply
        </CardTitle>
        <p className="text-sm text-muted">
          Paste a viewer comment — drafts a reply with agree → amplify → tie back.
        </p>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <textarea
          rows={3}
          className={area}
          placeholder="Viewer comment"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          aria-label="Viewer comment"
        />
        <Input
          placeholder="Optional: which video / what it covered"
          value={context}
          onChange={(e) => setContext(e.target.value)}
        />
        <Button
          className="self-start"
          onClick={() => reply.mutate({ comment, context })}
          disabled={comment.trim().length < 2 || reply.isPending}
        >
          {reply.isPending ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <MessageSquareReply size={16} />
          )}{" "}
          Suggest reply
        </Button>
        {reply.data && (
          <div className="rounded-lg border border-border bg-surface-2 p-3 text-sm whitespace-pre-wrap">
            {reply.data.reply}
          </div>
        )}
        {reply.error && <p className="text-sm text-danger">{reply.error.message}</p>}
      </CardContent>
    </Card>
  );
}
