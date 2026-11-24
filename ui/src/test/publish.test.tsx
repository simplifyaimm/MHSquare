import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { YouTubeDetails } from "@/components/YouTubeDetails";
import type { Project } from "@/lib/projects";
import { tagChars, type Publish } from "@/lib/publish";

afterEach(() => vi.restoreAllMocks());

const publish: Publish = {
  titles: ["Kafka explained for Java devs", "How Kafka really works"],
  title: "Kafka explained for Java devs",
  hook: "Kafka is a log, not a queue.",
  learn: ["what a log is"],
  chapters: [
    { at: "0:00", ms: 0, title: "Intro" },
    { at: "1:10", ms: 70000, title: "The log" },
    { at: "3:00", ms: 180000, title: "Consumers" },
  ],
  description: "Kafka is a log, not a queue.\n\nChapters:\n0:00 Intro",
  tags: ["kafka", "apache kafka"],
  hashtags: ["#Kafka"],
  pinned_comment: "What do you use Kafka for?",
  cover_headline: "KAFKA IS A LOG",
  source: "ai",
  cost_usd: 0.012,
};

const project = (d: Publish | null) =>
  ({ id: "p1", status: "planned", publish: d, assets: [{ kind: "script" }] }) as unknown as Project;

function setup(d: Publish | null) {
  const calls: { url: string; method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    calls.push({
      url: String(input),
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return new Response(JSON.stringify(publish), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <YouTubeDetails p={project(d)} />
    </QueryClientProvider>,
  );
  return calls;
}

it("offers to write the details when there are none", async () => {
  const calls = setup(null);
  await userEvent.click(screen.getByRole("button", { name: /write title & description/i }));
  await waitFor(() =>
    expect(calls.some((c) => c.url.endsWith("/projects/p1/publish") && c.method === "POST")).toBe(true),
  );
});

it("picks a title option, shows chapters and saves edits", async () => {
  const calls = setup(publish);
  expect(screen.getByText(/3 chapters from the shot timeline/i)).toBeInTheDocument();
  expect(screen.getByText(/Cover headline/)).toBeInTheDocument();
  const save = screen.getByRole("button", { name: /save changes/i });
  expect(save).toBeDisabled();
  await userEvent.click(screen.getByRole("radio", { name: /how kafka really works/i }));
  expect(screen.getByLabelText("Title")).toHaveValue("How Kafka really works");
  await userEvent.click(save);
  await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
  const put = calls.find((c) => c.method === "PUT")!;
  expect(put.body).toMatchObject({ title: "How Kafka really works", tags: ["kafka", "apache kafka"] });
});

it("counts tag characters the way YouTube does", () => {
  expect(tagChars(["kafka", "apache kafka"])).toBe(5 + 1 + 12 + 2);
});
