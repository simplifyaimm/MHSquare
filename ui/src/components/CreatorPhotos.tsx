import { useRef } from "react";
import { ImagePlus, Loader2, RotateCcw, Trash2, UserRound } from "lucide-react";
import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { useHeadshotMutations, useHeadshots } from "@/lib/covers";

/** Brand Kit: creator photos, cut out once and reused for every cover. */
export function CreatorPhotos() {
  const { data } = useHeadshots();
  const m = useHeadshotMutations();
  const input = useRef<HTMLInputElement>(null);
  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center gap-2">
        <UserRound size={16} className="text-muted" />
        <CardTitle>Creator photos</CardTitle>
        <span className="text-xs text-muted">used on cover photos · background removed automatically</span>
        <Button
          className="ml-auto"
          size="sm"
          variant="secondary"
          onClick={() => input.current?.click()}
          disabled={m.upload.isPending}
        >
          {m.upload.isPending ? <Loader2 size={14} className="animate-spin" /> : <ImagePlus size={14} />} Add
          photo
        </Button>
        <input
          ref={input}
          type="file"
          accept=".png,.jpg,.jpeg,.webp"
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) m.upload.mutate(f);
            e.target.value = "";
          }}
        />
      </CardHeader>
      <CardContent>
        {!data?.length && (
          <p className="text-sm text-muted">
            Add 2–3 talking-head photos (well lit, expressive, looking at the camera). They appear on your
            thumbnails.
          </p>
        )}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {data?.map((h) => (
            <div key={h.id} className="relative overflow-hidden rounded-lg border border-border bg-bg">
              <div className="aspect-square bg-[radial-gradient(circle,#132036,#060b14)]">
                <img src={h.cutout_url ?? h.url} alt="" className="h-full w-full object-contain" />
              </div>
              <div className="flex items-center gap-1 p-1.5 text-xs">
                <span
                  className={
                    h.status === "ready"
                      ? "text-success"
                      : h.status === "failed"
                        ? "text-danger"
                        : "text-accent-2"
                  }
                >
                  {h.status === "pending"
                    ? "cutting out…"
                    : h.status === "ready"
                      ? "ready"
                      : "cut-out failed"}
                </span>
                {h.status === "failed" && (
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-7 w-7"
                    aria-label="Retry cut-out"
                    onClick={() => m.retry.mutate(h.id)}
                  >
                    <RotateCcw size={12} />
                  </Button>
                )}
                <Button
                  size="icon"
                  variant="ghost"
                  className="ml-auto h-7 w-7"
                  aria-label="Delete photo"
                  onClick={() => m.remove.mutate(h.id)}
                >
                  <Trash2 size={12} />
                </Button>
              </div>
            </div>
          ))}
        </div>
        {m.upload.error && <p className="mt-2 text-sm text-danger">{m.upload.error.message}</p>}
      </CardContent>
    </Card>
  );
}
