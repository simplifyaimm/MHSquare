import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { ShotBoard } from "@/components/ShotBoard";
import type { Shot, ShotsResponse, Take } from "@/lib/shots";

const take = (id: string, attempt: number): Take => ({
  id,
  attempt,
  status: "done",
  generator: "html_motion",
  model: "code-llm",
  cost_usd: 0.1,
  seed: 1,
  feedback_note: null,
  error: null,
  logs: [],
  video_url: `/api/media/${id}`,
  thumb_url: null,
  meta: {},
  created_at: "",
});

const shot = (i: number, extra: Partial<Shot> = {}): Shot => ({
  id: `s${i}`,
  index: i,
  start_ms: i * 4000,
  end_ms: (i + 1) * 4000,
  transcript_text: `line ${i}`,
  visual_type: "code_animation",
  theme: "tech",
  prompt: "brief",
  notes: "",
  depends_on_previous: false,
  uses_character: false,
  state: "review",
  accepted_take_id: null,
  override_model: null,
  error: null,
  generator: "html_motion",
  estimate_usd: 0.12,
  takes: [take(`t${i}`, 1)],
  ...extra,
});

const data: ShotsResponse = {
  status: "planned",
  error: null,
  style_guide: { summary: "navy explainer" },
  characters_drawn: [],
  plan_meta: {},
  shots: [shot(0), shot(1, { state: "planned", takes: [] })],
  estimate_usd: 0.12,
  spent_usd: 0.1,
  preview: null,
  final: null,
  allowed_visual_types: ["code_animation", "text_card"],
};

afterEach(() => vi.restoreAllMocks());

it("accepts a take and regenerates with a note", async () => {
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation(
      async () =>
        new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ShotBoard projectId="p1" />
    </QueryClientProvider>,
  );

  expect(await screen.findByText("Shot 1/2")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /generate 1 shot/i })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /^accept$/i }));
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(
        ([u, i]) => String(u).endsWith("/shots/s0/takes/t0/accept") && i?.method === "POST",
      ),
    ).toBe(true),
  );

  // accepting advances to the next shot (fast review on a phone); go back to shot 1
  expect(await screen.findByText("Shot 2/2")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /previous shot/i }));
  await userEvent.type(screen.getByLabelText("Revision note"), "bigger labels");
  await userEvent.click(screen.getByRole("button", { name: /regenerate/i }));
  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/shots/s0/regenerate"));
    expect(JSON.parse(call![1]!.body as string)).toEqual({ note: "bigger labels", keep_seed: false });
  });
});
