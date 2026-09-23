import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQueryClient } from "../../test-utils";
import { LifeAreaDetailPage } from "./LifeAreaDetailPage";

function mockFetch(handler: (url: string, options?: RequestInit) => { ok: boolean; status: number; body: unknown }) {
  const fn = vi.fn().mockImplementation(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const { ok, status, body } = handler(url, options);
    return { ok, status, json: async () => body };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const LIFE_AREAS = [
  { id: 1, name: "Work", slug: "work" },
  { id: 2, name: "Personal", slug: "personal" },
];

function renderAt(path: string) {
  return renderWithQueryClient(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/my-world/:lifeAreaId" element={<LifeAreaDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("LifeAreaDetailPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows the area's tasks, both open and done", async () => {
    mockFetch((url) => {
      if (url.includes("/life-areas")) {
        return { ok: true, status: 200, body: LIFE_AREAS };
      }
      return {
        ok: true,
        status: 200,
        body: [
          { id: 1, title: "Open one", description: null, status: "open", due_at: null, completed_at: null, life_area_id: 1, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
          { id: 2, title: "Done one", description: null, status: "done", due_at: null, completed_at: "2026-01-02T00:00:00Z", life_area_id: 1, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-02T00:00:00Z" },
        ],
      };
    });

    renderAt("/my-world/1");
    expect(await screen.findByText("Open one")).toBeInTheDocument();
    expect(screen.getByText("Done one")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Work" })).toBeInTheDocument();
  });

  it("shows 'Unassigned' as the heading for the reserved unassigned segment", async () => {
    mockFetch((url) => {
      if (url.includes("/life-areas")) {
        return { ok: true, status: 200, body: LIFE_AREAS };
      }
      return { ok: true, status: 200, body: [] };
    });

    renderAt("/my-world/unassigned");
    expect(await screen.findByRole("heading", { name: "Unassigned" })).toBeInTheDocument();
  });

  it("reassigning a task's life area via the dropdown sends the expected PATCH", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch((url) => {
      if (url.includes("/life-areas")) {
        return { ok: true, status: 200, body: LIFE_AREAS };
      }
      return {
        ok: true,
        status: 200,
        body: [
          { id: 5, title: "Movable task", description: null, status: "open", due_at: null, completed_at: null, life_area_id: 1, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z" },
        ],
      };
    });

    renderAt("/my-world/1");
    const select = await screen.findByLabelText('Life area for "Movable task"');
    await user.selectOptions(select, "2");

    const patchCall = fetchMock.mock.calls.find(([, options]) => options?.method === "PATCH");
    expect(patchCall).toBeDefined();
    expect(JSON.parse(patchCall![1].body as string)).toEqual({ life_area_id: 2 });
  });

  it("shows an empty state when the area has no tasks", async () => {
    mockFetch((url) => {
      if (url.includes("/life-areas")) {
        return { ok: true, status: 200, body: LIFE_AREAS };
      }
      return { ok: true, status: 200, body: [] };
    });

    renderAt("/my-world/1");
    expect(await screen.findByText("No tasks here")).toBeInTheDocument();
  });
});
