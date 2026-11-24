import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { ShortsPage } from "@/pages/Shorts";
import type { Short, ShortsResponse } from "@/lib/shorts";

afterEach(() => vi.restoreAllMocks());

const short = (over: Partial<Short>): Short => ({
  id: "s1",
  index: 1,
  shot_from: 6,
  shot_to: 12,
  start_ms: 58000,
  end_ms: 117000,
  duration_s: 59,
  series: "Two worlds of AI",
  part: 1,
  parts: 3,
  hook: "Where does 'learn Python' come from?",
  title: "ML engineering: why training runs on Python (Part 1/3)",
  description: "What you learn.\n\n#AI #Shorts",
  hashtags: ["#AI"],
  why: "clear setup",
  score: 7,
  status: "planned",
  error: null,
  stale: false,
  url: null,
  ...over,
});

const data: ShortsResponse = {
  status: { status: "done", made: 2, cost_usd: 0.12 },
  layout: { width: 1080, height: 1920, stage: [0, 300, 1080, 1000], face_zone: [0, 1300, 1080, 620] },
  shorts: [
    short({}),
    short({
      id: "s2",
      index: 2,
      series: null,
      part: null,
      parts: null,
      hook: "What is MCP?",
      title: "MCP explained",
      status: "done",
      url: "/api/media/a?exp=1&sig=x",
    }),
  ],
};

function setup() {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({
      url,
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    const body = url.endsWith("/projects/p1")
      ? { id: "p1", name: "Java AI", status: "planned", assets: [] }
      : url.endsWith("/shorts/render")
        ? { queued: 1 }
        : data;
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/projects/p1/shorts"]}>
        <Routes>
          <Route path="/projects/:id/shorts" element={<ShortsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return calls;
}

it("lists the Shorts with series parts, posting day and the talking-head zone", async () => {
  const calls = setup();
  expect(await screen.findByText("Two worlds of AI · Part 1/3")).toBeInTheDocument();
  expect(screen.getByText("Day 2")).toBeInTheDocument();
  expect(screen.getByText("your talking head")).toBeInTheDocument(); // layout preview for unrendered Shorts
  expect(screen.getByLabelText("Download Short")).toBeInTheDocument(); // the rendered one
  await userEvent.click(screen.getByRole("button", { name: /render 1/i }));
  await waitFor(() =>
    expect(calls.some((c) => c.url.endsWith("/projects/p1/shorts/render") && c.method === "POST")).toBe(true),
  );
});

it("saves an edited title", async () => {
  const calls = setup();
  const [title] = await screen.findAllByLabelText("Title");
  await userEvent.clear(title);
  await userEvent.type(title, "Why ML teams use Python");
  await userEvent.click(screen.getAllByRole("button", { name: /save/i })[0]);
  await waitFor(() =>
    expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/shorts/s1"))).toBe(true),
  );
  expect(calls.find((c) => c.method === "PUT")!.body).toMatchObject({ title: "Why ML teams use Python" });
});
