import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQueryClient } from "../../test-utils";
import { MyWorldPage } from "./MyWorldPage";

function renderMyWorldPage() {
  return renderWithQueryClient(
    <MemoryRouter>
      <MyWorldPage />
    </MemoryRouter>,
  );
}

function mockFetch(handler: (url: string, options?: RequestInit) => { ok: boolean; status: number; body: unknown }) {
  const fn = vi.fn().mockImplementation(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const { ok, status, body } = handler(url, options);
    return { ok, status, json: async () => body };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const SUMMARY = {
  life_areas: [
    { id: 1, name: "Work", slug: "work", open_task_count: 2, most_urgent_due_at: "2020-01-01T00:00:00Z" },
  ],
  unassigned: { open_task_count: 1, most_urgent_due_at: null },
};

describe("MyWorldPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows life areas and the Unassigned card once loaded", async () => {
    mockFetch(() => ({ ok: true, status: 200, body: SUMMARY }));

    renderMyWorldPage();
    expect(await screen.findByText("Work")).toBeInTheDocument();
    expect(screen.getByText("Unassigned")).toBeInTheDocument();
    expect(screen.getByText("2 open")).toBeInTheDocument();
    expect(screen.getByText("1 open")).toBeInTheDocument();
  });

  it("shows an ErrorState on a failed load", async () => {
    mockFetch(() => ({ ok: false, status: 500, body: { detail: "boom" } }));
    renderMyWorldPage();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("the Unassigned card offers no rename or delete affordance", async () => {
    mockFetch(() => ({ ok: true, status: 200, body: SUMMARY }));

    renderMyWorldPage();
    await screen.findByText("Unassigned");
    expect(screen.queryByRole("button", { name: /rename "unassigned"/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete "unassigned"/i })).not.toBeInTheDocument();
    // The real Life Area, by contrast, does have both.
    expect(screen.getByRole("button", { name: 'Rename "Work"' })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: 'Delete "Work"' })).toBeInTheDocument();
  });

  it("submits the create form with the entered name", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch((url) => {
      if (url.includes("/life-areas/summary")) {
        return { ok: true, status: 200, body: { life_areas: [], unassigned: { open_task_count: 0, most_urgent_due_at: null } } };
      }
      return { ok: true, status: 201, body: { id: 9, name: "New Area", slug: "new-area" } };
    });

    renderMyWorldPage();
    await screen.findByText("Unassigned");

    await user.type(screen.getByLabelText("New life area name"), "New Area");
    await user.click(screen.getByRole("button", { name: "Add life area" }));

    const postCall = fetchMock.mock.calls.find(([, options]) => options?.method === "POST");
    expect(postCall).toBeDefined();
    expect(JSON.parse(postCall![1].body as string)).toEqual({ name: "New Area" });
  });

  it("shows the blocked-deletion message with the linked-item counts when delete is confirmed", async () => {
    const user = userEvent.setup();
    mockFetch((_url, options) => {
      if (options?.method === "DELETE") {
        return {
          ok: false,
          status: 400,
          body: { detail: { error: "life_area_has_linked_items", task_count: 3, calendar_event_count: 1, total_count: 4 } },
        };
      }
      return { ok: true, status: 200, body: SUMMARY };
    });

    renderMyWorldPage();
    await screen.findByText("Work");

    await user.click(screen.getByRole("button", { name: 'Delete "Work"' }));
    await user.click(screen.getByRole("button", { name: "Confirm delete" }));

    expect(await screen.findByText(/4 items still linked/)).toBeInTheDocument();
    expect(screen.getByText(/3 tasks/)).toBeInTheDocument();
    expect(screen.getByText(/1 calendar event/)).toBeInTheDocument();
  });
});
