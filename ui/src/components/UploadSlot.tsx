import { useRef, useState } from "react";
import { CheckCircle2, FileUp, Trash2, X } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "./ui/button";
import { fmtBytes, fmtMs, uploadFile, useDeleteAsset, type Asset } from "@/lib/projects";
import { cn } from "@/lib/utils";

type Kind = "script" | "audio" | "facecam" | "character_ref";

export function UploadSlot({
  projectId,
  kind,
  accept,
  label,
  hint,
  assets,
  multiple = false,
  max = 1,
}: {
  projectId: string;
  kind: Kind;
  accept: string;
  label: string;
  hint?: string;
  assets: Asset[];
  multiple?: boolean;
  max?: number;
}) {
  const input = useRef<HTMLInputElement>(null);
  const qc = useQueryClient();
  const del = useDeleteAsset(projectId);
  const [progress, setProgress] = useState<Record<string, number>>({});
  const [error, setError] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setError(null);
    const list = Array.from(files).slice(0, Math.max(1, max - (multiple ? assets.length : 0)));
    abort.current = new AbortController();
    for (const f of list) {
      try {
        setProgress((p) => ({ ...p, [f.name]: 0 }));
        await uploadFile(
          projectId,
          kind,
          f,
          (x) => setProgress((p) => ({ ...p, [f.name]: x })),
          abort.current.signal,
        );
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setProgress((p) => {
          const { [f.name]: _, ...rest } = p;
          void _;
          return rest;
        });
        qc.invalidateQueries({ queryKey: ["projects", projectId] });
      }
    }
    if (input.current) input.current.value = "";
  };

  const uploading = Object.entries(progress);
  const canAdd = multiple ? assets.length + uploading.length < max : true;

  return (
    <div className="flex flex-col gap-3">
      <div>
        <div className="font-medium">{label}</div>
        {hint && <div className="text-sm text-muted">{hint}</div>}
      </div>

      {assets.map((a) => (
        <div
          key={a.id}
          className="flex items-center gap-3 rounded-lg border border-border bg-surface-2 px-3 py-2.5"
        >
          <CheckCircle2 size={18} className="shrink-0 text-success" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm">{a.original_name}</div>
            <div className="font-mono text-xs text-muted">
              {fmtBytes(a.size)}
              {a.meta.duration_ms ? ` · ${fmtMs(a.meta.duration_ms)}` : ""}
              {a.meta.width
                ? ` · ${String(a.meta.width)}×${String(a.meta.height)} @ ${String(a.meta.fps)}fps`
                : ""}
            </div>
          </div>
          {kind === "character_ref" && <img src={a.url} alt="" className="h-10 w-10 rounded object-cover" />}
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Remove ${a.original_name}`}
            onClick={() => del.mutate(a.id)}
          >
            <Trash2 size={16} />
          </Button>
        </div>
      ))}

      {uploading.map(([name, x]) => (
        <div key={name} className="flex flex-col gap-1.5 rounded-lg border border-accent-2/30 px-3 py-2.5">
          <div className="flex items-center justify-between gap-2 text-sm">
            <span className="truncate">{name}</span>
            <span className="flex items-center gap-2 font-mono text-xs text-accent-2">
              {Math.round(x * 100)}%
              <button
                onClick={() => abort.current?.abort()}
                aria-label="Cancel upload"
                className="text-muted hover:text-text"
              >
                <X size={14} />
              </button>
            </span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-surface-2">
            <div className="h-full bg-accent-2 transition-[width]" style={{ width: `${x * 100}%` }} />
          </div>
        </div>
      ))}

      {canAdd && (
        <button
          type="button"
          onClick={() => input.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            onFiles(e.dataTransfer.files);
          }}
          className={cn(
            "flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-4 py-6 text-sm text-muted",
            "hover:border-accent/60 hover:text-text",
          )}
        >
          <FileUp size={22} />
          {assets.length && !multiple ? "Replace file" : "Choose or drop a file"}
          <span className="font-mono text-xs">{accept.replaceAll(",", " ")}</span>
        </button>
      )}
      <input
        ref={input}
        type="file"
        accept={accept}
        multiple={multiple}
        hidden
        onChange={(e) => onFiles(e.target.files)}
        data-testid={`file-${kind}`}
      />
      {error && <p className="text-sm text-danger">{error}</p>}
    </div>
  );
}
