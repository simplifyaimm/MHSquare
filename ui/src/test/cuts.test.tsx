import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { CutsReview } from "@/components/CutsReview";
import type { CutsResponse, Project } from "@/lib/projects";

const project = { id: "p1", status: "cuts_review" } as Project;
const cuts: CutsResponse = {
  id: "cp1",
  approved: false,
  duration_ms: 10_000,
  removed_ms: 0,
  audio: null,
  ranges: [
    { id: "c1", start_ms: 1000, end_ms: 1400, kind: "filler", text: "um", enabled: true },
    { id: "c2", start_ms: 4000, end_ms: 6000, kind: "silence", text: "2.3s pause", enabled: true },
    { id: "c3", start_ms: 8000, end_ms: 8300, kind: "filler", text: "uh", enabled: true },
  ],
};

afterEach(() => vi.restoreAllMocks());

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

it("toggles cuts, filters by kind and approves only the enabled ids", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes("/cuts/approve")) return json({ ...project, status: "finalizing", assets: [] });
    if (url.includes("/cuts")) return json(cuts);
    if (url.includes("/waveform"))
      return json({ peaks_per_second: 50, duration_ms: 10_000, peaks: [0.5, 0.2] });
    return json({});
  });

  render(
    <QueryClientProvider client={new QueryClient()}>
      <CutsReview project={project} />
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("button", { name: /approve 3 cuts/i })).toBeInTheDocument();
  expect(screen.getByText("−0:02.7 → 0:07.3")).toBeInTheDocument();

  await userEvent.click(screen.getByLabelText(/cut 2.3s pause/i)); // keep the pause
  expect(screen.getByRole("button", { name: /approve 2 cuts/i })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /fillers \(2\)/i }));
  expect(screen.queryByText("2.3s pause")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: /keep all/i })); // only fillers affected
  expect(screen.getByRole("button", { name: /approve 0 cuts/i })).toBeInTheDocument();
  await userEvent.click(screen.getByLabelText(/cut uh/i));

  await userEvent.click(screen.getByRole("button", { name: /approve 1 cuts/i }));
  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([u]) => String(u).includes("/cuts/approve"));
    expect(JSON.parse(call![1]!.body as string)).toEqual({ enabled_ids: ["c3"] });
  });
});
