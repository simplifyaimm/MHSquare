import { AlertTriangle, CheckCircle2, Loader2, ShieldCheck, XCircle } from "lucide-react";
import { Button } from "./ui/button";
import type { Project } from "@/lib/projects";
import { useScriptCheck, type ScriptCheck } from "@/lib/style";
import { cn } from "@/lib/utils";

/** Runs the Style Profile judge on the uploaded script (English-only, presenter-intro, length, rubric). */
export function ScriptCheckPanel({ p }: { p: Project }) {
  const check = useScriptCheck(p.id);
  const r: ScriptCheck | null = check.data ?? p.script_check;
  const hasScript = p.assets.some((a) => a.kind === "script");
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-center gap-3">
        <ShieldCheck size={18} className="text-accent" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">Script check</div>
          <div className="text-xs text-muted">
            English only · no presenter intro · mechanism · carried example · takeaway · length
          </div>
        </div>
        <Button variant="secondary" disabled={!hasScript || check.isPending} onClick={() => check.mutate()}>
          {check.isPending ? <Loader2 size={16} className="animate-spin" /> : <ShieldCheck size={16} />}
          {r ? "Re-check" : "Check script"}
        </Button>
      </div>
      {check.error && <p className="text-sm text-danger">{check.error.message}</p>}
      {r && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {r.passed === false ? (
              <span className="flex items-center gap-1 rounded-full bg-danger/15 px-2.5 py-0.5 text-danger">
                <XCircle size={14} /> rejected
              </span>
            ) : r.verdict === "needs_work" ? (
              <span className="flex items-center gap-1 rounded-full bg-warning/15 px-2.5 py-0.5 text-warning">
                <AlertTriangle size={14} /> needs work
              </span>
            ) : r.passed ? (
              <span className="flex items-center gap-1 rounded-full bg-success/15 px-2.5 py-0.5 text-success">
                <CheckCircle2 size={14} /> good
              </span>
            ) : null}
            {r.english_only && (
              <span
                className={cn(
                  "rounded-full px-2.5 py-0.5 font-mono text-xs",
                  r.english_only === "pass" ? "bg-success/15 text-success" : "bg-danger/15 text-danger",
                )}
              >
                English-only: {r.english_only}
              </span>
            )}
            <span className="font-mono text-xs text-muted">
              {r.deterministic.words} words ≈ {r.deterministic.est_minutes} min @ {r.deterministic.wpm} WPM
              {r.average != null ? ` · avg ${r.average}/10` : ""}
            </span>
          </div>
          {r.error && (
            <p className="text-xs text-warning">
              Judge unavailable ({r.error}); showing automatic checks only.
            </p>
          )}
          {r.deterministic.flags.length > 0 && (
            <ul className="flex flex-col gap-1 text-sm">
              {r.deterministic.flags.map((f, i) => (
                <li key={i} className={f.severity === "fail" ? "text-danger" : "text-warning"}>
                  {f.severity === "fail" ? "✗" : "!"} {f.message}
                </li>
              ))}
            </ul>
          )}
          {r.summary && <p className="text-sm text-muted">{r.summary}</p>}
          {r.scores && r.scores.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <tbody>
                  {r.scores.map((s) => (
                    <tr key={s.key} className="border-t border-border align-top">
                      <td className="py-1.5 pr-3 font-mono text-xs whitespace-nowrap text-muted">{s.key}</td>
                      <td className="py-1.5 pr-3 font-mono whitespace-nowrap">
                        {s.score != null ? `${s.score}/10` : s.result}
                      </td>
                      <td className="py-1.5 text-muted">{s.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {r.issues && r.issues.length > 0 && (
            <ul className="list-disc pl-5 text-sm">
              {r.issues.map((x, i) => (
                <li key={i}>{x}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
