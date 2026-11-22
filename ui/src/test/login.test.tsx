import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "@/App";
import { LogoMark } from "@/components/Logo";
import { StateChip } from "@/components/StateChip";

function renderApp(path = "/projects") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

describe("brand", () => {
  it("renders the mh logo", () => {
    render(<LogoMark />);
    expect(screen.getByRole("img", { name: /mhsquare logo/i })).toHaveTextContent("mh");
  });

  it("colours shot states", () => {
    render(<StateChip state="paused_budget" />);
    expect(screen.getByText("paused budget").className).toContain("text-warning");
  });
});

describe("login flow", () => {
  it("redirects to login when unauthenticated, then signs in", async () => {
    const me = { user: { id: "1", email: "me@example.com" }, csrf_token: "t" };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/api/auth/me")) return jsonResponse(401, { detail: "Not authenticated" });
      if (url.endsWith("/api/auth/login")) return jsonResponse(200, me);
      return jsonResponse(404, {});
    });

    renderApp();
    expect(await screen.findByRole("button", { name: /sign in/i })).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText(/email/i), "me@example.com");
    await userEvent.type(screen.getByLabelText(/password/i), "pw");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(screen.getAllByText("Projects").length).toBeGreaterThan(0));
    const loginCall = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/api/auth/login"));
    expect(JSON.parse(loginCall![1]!.body as string)).toEqual({ email: "me@example.com", password: "pw" });
  });

  it("shows the API error on bad credentials", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      String(input).endsWith("/api/auth/me")
        ? jsonResponse(401, { detail: "Not authenticated" })
        : jsonResponse(401, { detail: "Invalid email or password" }),
    );
    renderApp("/login");
    await userEvent.type(await screen.findByLabelText(/email/i), "me@example.com");
    await userEvent.type(screen.getByLabelText(/password/i), "bad");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  });
});
