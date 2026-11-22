import { Card, CardContent } from "@/components/ui/card";

export function PlaceholderPage({ title, phase }: { title: string; phase: string }) {
  return (
    <div className="mx-auto max-w-5xl">
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">{title}</h1>
      <Card>
        <CardContent className="text-sm text-muted">Coming in {phase}.</CardContent>
      </Card>
    </div>
  );
}
