import { useState } from "react";
import { Library, Loader2 } from "lucide-react";
import { Button } from "./ui/button";
import { useScriptLibrary, useUseLibraryScript } from "@/lib/shots";

/** Reuse a script that already exists in mhsquare (another project) instead of uploading. */
export function ScriptLibraryPicker({ projectId }: { projectId: string }) {
  const { data, isLoading } = useScriptLibrary();
  const use = useUseLibraryScript(projectId);
  const [sel, setSel] = useState("");
  const options = (data ?? []).filter((s) => s.project_id !== projectId);
  if (isLoading) return null;
  if (!options.length)
    return <p className="text-xs text-muted">No scripts in other projects yet — upload one above.</p>;
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Library size={16} className="text-accent" /> Or use a script already in mhsquare
      </div>
      <div className="flex flex-col gap-2 sm:flex-row">
        <select
          value={sel}
          onChange={(e) => setSel(e.target.value)}
          className="h-10 min-w-0 flex-1 rounded-lg border border-border bg-surface-2 px-3 text-sm"
          aria-label="Script from library"
        >
          <option value="">Choose a script…</option>
          {options.map((s) => (
            <option key={s.asset_id} value={s.asset_id}>
              {s.title} — {s.project}
              {s.words ? ` · ${s.words} words` : ""} · {new Date(s.created_at).toLocaleDateString()}
            </option>
          ))}
        </select>
        <Button
          variant="secondary"
          disabled={!sel || use.isPending}
          onClick={() => use.mutate(sel, { onSuccess: () => setSel("") })}
        >
          {use.isPending ? <Loader2 size={16} className="animate-spin" /> : <Library size={16} />} Use this
          script
        </Button>
      </div>
      {use.error && <p className="text-xs text-danger">{use.error.message}</p>}
    </div>
  );
}
