import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQueryClient } from "../../test-utils";
import { HomeSummaryView } from "./HomeSummaryView";

function mockFetchOnce(body: unknown) {
  const fn = vi.fn().mockImplementation(async () => ({ ok: true, status: 200, json: async () => body }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("HomeSummaryView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows every section's empty state when nothing is in any bucket", async () => {
    mockFetchOnce({ focus_today: [], coming_up: [], needs_attention: [], anytime: [] });

    renderWithQueryClient(<HomeSummaryView />);
    expect(await screen.findByText("Nothing due")).toBeInTheDocument();
    expect(screen.getByText("Nothing coming up")).toBeInTheDocument();
    expect(screen.getByText("All caught up")).toBeInTheDocument();
    expect(screen.getByText("Nothing here")).toBeInTheDocument();
  });

  it("shows items once loaded, in their respective sections", async () => {
    mockFetchOnce({
      focus_today: [
        { id: 1, title: "Overdue thing", description: null, status: "open", due_at: "2020-01-01T00:00:00Z", completed_at: null, life_area_id: null, created_at: "2020-01-01T00:00:00Z", updated_at: "2020-01-01T00:00:00Z" },
      ],
      coming_up: [
        { source: "event", id: 2, title: "Team sync", starts_at: "2030-06-16T09:00:00Z", ends_at: null, life_area_id: null },
      ],
      needs_attention: [
        { id: 3, title: "Completed: Ship it", task_id: 9, read_at: null, created_at: "2026-06-15T09:00:00Z", updated_at: "2026-06-15T09:00:00Z" },
      ],
      anytime: [
        { id: 4, title: "Someday task", description: null, status: "open", due_at: null, completed_at: null, life_area_id: null, created_at: "2026-06-15T09:00:00Z", updated_at: "2026-06-15T09:00:00Z" },
      ],
    });

    renderWithQueryClient(<HomeSummaryView />);
    expect(await screen.findByText("Overdue thing")).toBeInTheDocument();
    expect(screen.getByText("Team sync")).toBeInTheDocument();
    expect(screen.getByText("Completed: Ship it")).toBeInTheDocument();
    expect(screen.getByText("Someday task")).toBeInTheDocument();
  });

  it("shows an ErrorState on a failed load", async () => {
    const fn = vi.fn().mockImplementation(async () => ({ ok: false, status: 500, json: async () => ({ detail: "boom" }) }));
    vi.stubGlobal("fetch", fn);

    renderWithQueryClient(<HomeSummaryView />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
