import { screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQueryClient } from "../test-utils";
import { AppShell } from "./AppShell";

// Checkpoint 4.5e: AppShell now fires the APP_OPENED trigger on mount
// (useAppOpenedTrigger), which needs a QueryClientProvider ancestor —
// matching App.tsx's own real provider nesting, which previously
// wasn't exercised by this file since neither Sidebar nor TopNav used
// react-query before this checkpoint. fetch is stubbed to a quiet
// "silence" response so these structural-rendering tests aren't
// coupled to the trigger's own behavior (see useAppOpenedTrigger.test.ts
// for that).
function stubSilentAppOpened() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ status: "silence" }) }),
  );
}

describe("AppShell", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the sidebar, top nav, and routed content together", () => {
    stubSilentAppOpened();
    renderWithQueryClient(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<div>Page content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("navigation", { name: "Sidebar" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search" })).toBeInTheDocument();
    expect(screen.getByText("Page content")).toBeInTheDocument();
  });
});
