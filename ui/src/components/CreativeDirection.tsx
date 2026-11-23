import { Compass, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { TEMPLATES, useEditDirection, useRecommendDirection, type Direction } from "@/lib/direction";
import type { Project } from "@/lib/projects";
import { MODES } from "@/lib/shots";
import { cn } from "@/lib/utils";

const BRAND_MODES = MODES.filter((m) => m.id !== "story");

/** AI-recommended (creator-editable) visual approach for THIS script; the planner follows it. */
export function CreativeDirection({ p, compact = false }: { p: Project; compact?: boolean }) {
  const recommend = useRecommendDirection(p.id);
  const edit = useEditDirection(p.id);
  const d = p.direction as Direction | null;
  const busy = recommend.isPending || edit.isPending;
  const hasScript = p.assets.some((a) => a.kind === "script");

  const toggle = (list: string[], id: string) =>
    list.includes(id) ? list.filter((x) => x !== id) : [...list, id];

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center gap-2">
        <Compass size={16} className="text-muted" />
        <CardTitle>Creative direction</CardTitle>
        {d && (
          <span className="text-xs text-muted">
            {d.source === "edited"
              ? "edited by you"
              : d.auto
                ? "chosen automatically at planning"
                : "AI recommended"}
            {d.cost_usd ? ` · $${d.cost_usd.toFixed(3)}` : ""}
          </span>
        )}
        <Button
          className="ml-auto"
          size="sm"
          variant={d ? "ghost" : "default"}
          onClick={() => recommend.mutate()}
          disabled={busy || !hasScript}
          title={hasScript ? "" : "Upload the script first"}
        >
          {recommend.isPending ? (
            <Loader2 size={14} className="animate-spin" />
          ) : d ? (
            <RefreshCw size={14} />
          ) : (
            <Sparkles size={14} />
          )}
          {d ? "Re-recommend" : "Recommend for this script"}
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {!d && (
          <p className="text-sm text-muted">
            Before planning, the AI reads this script and picks the explanation style and scene templates that
            make it easiest to understand for your audience — so the video never starts from a default theme.
            You can adjust it. If you skip this, it runs automatically when you plan.
          </p>
        )}
        {d && (
          <>
            <div className="flex flex-col gap-2">
              <span className="text-xs font-medium uppercase tracking-wide text-muted">
                Explanation styles · click to toggle, ★ = primary
              </span>
              <div className="flex flex-wrap gap-2">
                {BRAND_MODES.map((m) => {
                  const on = d.modes.includes(m.id);
                  const primary = d.primary_mode === m.id;
                  return (
                    <span
                      key={m.id}
                      className={cn(
                        "inline-flex items-center overflow-hidden rounded-full border text-sm",
                        primary
                          ? "border-accent bg-accent/20"
                          : on
                            ? "border-accent/50"
                            : "border-border text-muted",
                      )}
                    >
                      <button
                        type="button"
                        title={primary ? `${m.hint} (primary — pick another primary to change)` : m.hint}
                        disabled={busy || primary}
                        aria-pressed={on}
                        onClick={() =>
                          edit.mutate({ modes: on ? d.modes.filter((x) => x !== m.id) : [...d.modes, m.id] })
                        }
                        className="px-3 py-1 hover:text-text"
                      >
                        {m.label}
                      </button>
                      {on && (
                        <button
                          type="button"
                          aria-label={primary ? `${m.label} is the primary style` : `Make ${m.label} primary`}
                          title={primary ? "Primary style" : "Make primary"}
                          disabled={busy || primary}
                          onClick={() => edit.mutate({ primary_mode: m.id })}
                          className={cn("pr-3", primary ? "text-accent" : "text-muted hover:text-accent")}
                        >
                          ★
                        </button>
                      )}
                    </span>
                  );
                })}
              </div>
              <span className="text-xs text-muted">
                Click a style to add or remove it · ★ makes it the primary (most-used) style.
              </span>
            </div>
            <div className="flex flex-col gap-2">
              <span className="text-xs font-medium uppercase tracking-wide text-muted">
                Preferred scene templates (free to render)
              </span>
              <div className="flex flex-wrap gap-2">
                {TEMPLATES.map((t) => {
                  const on = d.templates.includes(t.id);
                  return (
                    <button
                      key={t.id}
                      type="button"
                      title={t.hint}
                      disabled={busy}
                      onClick={() => edit.mutate({ templates: toggle(d.templates, t.id) })}
                      className={cn(
                        "rounded-full border px-3 py-1 text-sm",
                        on
                          ? "border-accent/60 bg-accent/10 text-text"
                          : "border-border text-muted hover:text-text",
                      )}
                    >
                      {t.label}
                    </button>
                  );
                })}
              </div>
            </div>
            {!compact && (
              <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
                {d.persona && (
                  <>
                    <dt className="text-muted">Running example</dt>
                    <dd>{d.persona}</dd>
                  </>
                )}
                {d.hook && (
                  <>
                    <dt className="text-muted">Opening hook</dt>
                    <dd>{d.hook}</dd>
                  </>
                )}
                <dt className="text-muted">Pacing</dt>
                <dd className="flex flex-wrap gap-2">
                  {(["calm", "medium", "fast"] as const).map((v) => (
                    <button
                      key={v}
                      type="button"
                      disabled={busy}
                      onClick={() => edit.mutate({ pacing: v })}
                      className={cn(
                        "rounded-md border px-2 py-0.5 text-xs",
                        d.pacing === v ? "border-accent text-text" : "border-border text-muted",
                      )}
                    >
                      {v}
                    </button>
                  ))}
                  <span className="text-muted">·</span>
                  {(["sparse", "balanced", "rich"] as const).map((v) => (
                    <button
                      key={v}
                      type="button"
                      disabled={busy}
                      onClick={() => edit.mutate({ density: v })}
                      className={cn(
                        "rounded-md border px-2 py-0.5 text-xs",
                        d.density === v ? "border-accent text-text" : "border-border text-muted",
                      )}
                    >
                      {v}
                    </button>
                  ))}
                </dd>
                {d.reasons.length > 0 && (
                  <>
                    <dt className="text-muted">Why</dt>
                    <dd>
                      <ul className="list-disc pl-4">
                        {d.reasons.map((r) => (
                          <li key={r}>{r}</li>
                        ))}
                      </ul>
                    </dd>
                  </>
                )}
                {d.avoid.length > 0 && (
                  <>
                    <dt className="text-muted">Avoid</dt>
                    <dd className="text-muted">{d.avoid.join(" · ")}</dd>
                  </>
                )}
              </dl>
            )}
          </>
        )}
        {recommend.error && <p className="text-sm text-danger">{recommend.error.message}</p>}
        {edit.error && <p className="text-sm text-danger">{edit.error.message}</p>}
      </CardContent>
    </Card>
  );
}
