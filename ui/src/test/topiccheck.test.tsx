import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TopicCheckHint } from "@/components/TopicCheckHint";

afterEach(() => vi.restoreAllMocks());

it("warns about a likely repeat (NotebookLM tips → NotebookLM)", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
    async () =>
      new Response(
        JSON.stringify({
          title: "NotebookLM tips",
          method: "embedding",
          likely_repeat: true,
          matches: [{ id: "1", title: "NotebookLM", status: "covered", score: 0.91, pillar: "tools" }],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
  );
  render(<TopicCheckHint title="NotebookLM tips" />);
  expect(await screen.findByRole("alert", {}, { timeout: 3000 })).toHaveTextContent(
    "NotebookLM (covered, 91%)",
  );
  const body = JSON.parse(fetchMock.mock.calls[0][1]!.body as string);
  expect(body).toEqual({ title: "NotebookLM tips" });
});
