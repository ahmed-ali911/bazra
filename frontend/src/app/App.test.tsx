import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

describe("App", () => {
  // "/" is now gated behind RequireAuth (Checkpoint 2.1) — mock a
  // successful session check so this test exercises the real guarded route
  // rather than bypassing it. Content only appears once that async check
  // resolves, hence findByRole below instead of getByRole.
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ authenticated: true }),
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the app shell without crashing", async () => {
    render(<App />);
    expect(await screen.findByRole("navigation", { name: "Sidebar" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search" })).toBeInTheDocument();
    expect(screen.getByText("Welcome to BAZRA.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Get started" })).toBeInTheDocument();
  });
});
