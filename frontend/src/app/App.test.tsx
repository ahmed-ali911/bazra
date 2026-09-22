import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

describe("App", () => {
  // "/" is now gated behind RequireAuth (Checkpoint 2.1), and its content
  // (Checkpoint 2.2's TasksProbe) fetches tasks/life-areas too — mock every
  // endpoint it actually calls rather than one blanket response, so this
  // test exercises the real guarded route and the real page, not a
  // coincidentally-shaped stub. Content only appears once those async
  // calls resolve, hence findByRole/findByText below instead of getBy*.
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
        const url = typeof input === "string" ? input : input.toString();
        if (url.includes("/auth/me")) {
          return { ok: true, status: 200, json: async () => ({ authenticated: true }) };
        }
        if (url.includes("/tasks")) {
          return { ok: true, status: 200, json: async () => [] };
        }
        if (url.includes("/life-areas")) {
          return { ok: true, status: 200, json: async () => [] };
        }
        throw new Error(`Unexpected fetch in App.test.tsx: ${url}`);
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
    expect(await screen.findByText("No tasks yet")).toBeInTheDocument();
  });
});
