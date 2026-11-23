import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { CreativeDirection } from "@/components/CreativeDirection";
import type { Direction } from "@/lib/direction";
import type { Project } from "@/lib/projects";

afterEach(() => vi.restoreAllMocks());

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

const direction: Direction = {
  primary_mode: "tech",
  modes: ["tech", "scenario"],
  templates: ["compare", "steps"],
  persona: "Robin, a Java developer",
  hook: "Robin's plan card with a big question mark",
  pacing: "medium",
  density: "balanced",
  reasons: ["career decision explained with one carried example"],
  avoid: ["cartoons"],
  source: "ai",
  cost_usd: 0.004,
};

const project = (d: Direction | null) =>
  ({ id: "p1", status: "ready", direction: d, assets: [{ kind: "script" }] }) as unknown as Project;

function setup(d: Direction | null) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    calls.push({
      url: String(input),
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return json(direction);
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CreativeDirection p={project(d)} />
    </QueryClientProvider>,
  );
  return calls;
}

it("asks the AI for a recommendation before planning", async () => {
  const calls = setup(null);
  expect(screen.getByText(/never starts from a default theme/i)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /recommend for this script/i }));
  await waitFor(() =>
    expect(calls.some((c) => c.url.endsWith("/projects/p1/direction") && c.method === "POST")).toBe(true),
  );
});

it("shows the recommendation and saves the creator's edits", async () => {
  const calls = setup(direction);
  expect(screen.getByText("Robin, a Java developer")).toBeInTheDocument();
  expect(screen.getByText(/carried example/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Tech is the primary style" })).toBeDisabled();

  await userEvent.click(screen.getByRole("button", { name: "Make Scenario primary" }));
  await userEvent.click(screen.getAllByRole("button", { name: "Flow" })[0]); // the style (the template comes later)
  await userEvent.click(screen.getByRole("button", { name: "Steps" })); // drop a template
  await waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(3));
  const puts = calls.filter((c) => c.method === "PUT").map((c) => c.body);
  expect(puts).toEqual([
    { primary_mode: "scenario" },
    { modes: ["tech", "scenario", "flow"] },
    { templates: ["compare"] },
  ]);
});
