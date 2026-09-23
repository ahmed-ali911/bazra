import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQueryClient } from "../../test-utils";
import { InboxProbe } from "./InboxProbe";

function mockFetchSequence(responses: Array<{ ok: boolean; status: number; body: unknown }>) {
  const fn = vi.fn();
  for (const { ok, status, body } of responses) {
    fn.mockImplementationOnce(async () => ({ ok, status, json: async () => body }));
  }
  fn.mockImplementation(async () => {
    const last = responses[responses.length - 1];
    return { ok: last.ok, status: last.status, json: async () => last.body };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("InboxProbe", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows inbox items once loaded", async () => {
    mockFetchSequence([
      {
        ok: true,
        status: 200,
        body: [
          { id: 1, title: "Completed: Write report", task_id: 5, read_at: null, created_at: "2026-06-15T09:00:00Z", updated_at: "2026-06-15T09:00:00Z" },
        ],
      },
    ]);

    renderWithQueryClient(<InboxProbe />);
    expect(await screen.findByText("Completed: Write report")).toBeInTheDocument();
  });

  it("shows an empty state when the inbox is empty", async () => {
    mockFetchSequence([{ ok: true, status: 200, body: [] }]);
    renderWithQueryClient(<InboxProbe />);
    expect(await screen.findByText("Inbox is empty")).toBeInTheDocument();
  });

  it("shows an ErrorState on a failed load", async () => {
    mockFetchSequence([{ ok: false, status: 500, body: { detail: "boom" } }]);
    renderWithQueryClient(<InboxProbe />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("marks an item read via the checkbox", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetchSequence([
      {
        ok: true,
        status: 200,
        body: [
          { id: 1, title: "Completed: Write report", task_id: 5, read_at: null, created_at: "2026-06-15T09:00:00Z", updated_at: "2026-06-15T09:00:00Z" },
        ],
      },
    ]);

    renderWithQueryClient(<InboxProbe />);
    const checkbox = await screen.findByLabelText('Mark "Completed: Write report" as read');
    await user.click(checkbox);

    const patchCall = fetchMock.mock.calls.find(([, options]) => options?.method === "PATCH");
    expect(patchCall).toBeDefined();
    expect(JSON.parse(patchCall![1].body as string)).toEqual({ read: true });
  });

  it("dismisses an item via the Dismiss button", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetchSequence([
      {
        ok: true,
        status: 200,
        body: [
          { id: 1, title: "Completed: Write report", task_id: 5, read_at: null, created_at: "2026-06-15T09:00:00Z", updated_at: "2026-06-15T09:00:00Z" },
        ],
      },
    ]);

    renderWithQueryClient(<InboxProbe />);
    const dismissButton = await screen.findByRole("button", { name: 'Dismiss "Completed: Write report"' });
    await user.click(dismissButton);

    const deleteCall = fetchMock.mock.calls.find(([, options]) => options?.method === "DELETE");
    expect(deleteCall).toBeDefined();
  });
});
