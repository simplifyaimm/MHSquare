import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { SettingsPage } from "@/pages/Settings";
import type { LLMSettings } from "@/lib/llmSettings";

const base: LLMSettings = {
  litellm: "ok",
  providers: [
    {
      id: "anthropic",
      name: "Anthropic",
      env_var: "ANTHROPIC_API_KEY",
      docs: "#",
      configured: false,
      hint: null,
    },
  ],
  models: [
    {
      alias: "planner-llm",
      label: "Shot planner",
      kind: "llm",
      model: "anthropic/claude-opus-5-5",
      default: "anthropic/claude-opus-5-5",
      is_default: true,
      provider: "anthropic",
      provider_configured: false,
      suggestions: [],
    },
  ],
  budget: { configured: false },
};

afterEach(() => vi.restoreAllMocks());

it("saves a provider key, clears the field and shows only the masked hint", async () => {
  const saved: LLMSettings = {
    ...base,
    providers: [{ ...base.providers[0], configured: true, hint: "…wxyz" }],
    restarting: true,
  };
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
    async (_input, init) =>
      new Response(JSON.stringify(init?.method === "PUT" ? saved : base), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  );

  render(
    <QueryClientProvider client={new QueryClient()}>
      <SettingsPage />
    </QueryClientProvider>,
  );

  expect(await screen.findByText("provider key not set")).toBeInTheDocument();
  const input = screen.getByLabelText("Anthropic API key");
  expect(input).toHaveAttribute("type", "password");
  await userEvent.type(input, "sk-ant-secret-wxyz");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));

  await waitFor(() => expect(screen.getByText("configured …wxyz")).toBeInTheDocument());
  expect(screen.queryByDisplayValue("sk-ant-secret-wxyz")).toBeNull();
  const put = fetchMock.mock.calls.find(([, i]) => i?.method === "PUT")!;
  expect(JSON.parse(put[1]!.body as string)).toEqual({ provider_keys: { anthropic: "sk-ant-secret-wxyz" } });
  expect(screen.getByText(/Applying changes/)).toBeInTheDocument();
});
