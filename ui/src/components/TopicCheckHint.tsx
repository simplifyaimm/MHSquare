import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";
import { checkTopic, type TopicCheck } from "@/lib/style";

/** Debounced repeat check against the covered-topics registry. */
export function TopicCheckHint({ title }: { title: string }) {
  const [res, setRes] = useState<TopicCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const q = title.trim();

  useEffect(() => {
    if (q.length < 3) return;
    let live = true;
    const t = setTimeout(() => {
      setBusy(true);
      checkTopic(q)
        .then((r) => live && setRes(r))
        .catch(() => live && setRes(null))
        .finally(() => live && setBusy(false));
    }, 600);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [q]);

  if (q.length < 3) return null;
  if (busy && !res)
    return (
      <p className="flex items-center gap-1 text-xs text-muted">
        <Loader2 size={12} className="animate-spin" /> checking for repeats…
      </p>
    );
  if (!res || res.title !== q) return null;
  if (!res.likely_repeat)
    return (
      <p className="flex items-center gap-1 text-xs text-success">
        <CheckCircle2 size={12} /> New topic — no close match in covered topics
      </p>
    );
  return (
    <div
      role="alert"
      className="rounded-lg border border-warning/40 bg-warning/10 p-2.5 text-xs text-warning"
    >
      <div className="flex items-center gap-1 font-medium">
        <AlertTriangle size={13} /> Likely repeat of:
      </div>
      <ul className="mt-1 list-disc pl-5">
        {res.matches.map((m) => (
          <li key={m.id}>
            {m.title}{" "}
            <span className="text-muted">
              ({m.status}, {Math.round(m.score * 100)}%)
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
