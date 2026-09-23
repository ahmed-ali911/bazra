import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQueryClient } from "../../test-utils";
import { CalendarProbe } from "./CalendarProbe";

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

describe("CalendarProbe", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows agenda items once loaded, tagged by source", async () => {
    mockFetchSequence([
      {
        ok: true,
        status: 200,
        body: [
          { source: "event", id: 1, title: "Standup", starts_at: "2026-06-15T09:00:00Z", ends_at: null, life_area_id: null },
          { source: "task", id: 2, title: "File taxes", starts_at: "2026-06-15T14:00:00Z", ends_at: null, life_area_id: null },
        ],
      },
    ]);

    renderWithQueryClient(<CalendarProbe />);
    expect(await screen.findByText("Standup")).toBeInTheDocument();
    expect(screen.getByText("File taxes")).toBeInTheDocument();
  });

  it("shows an empty state when nothing is on the calendar", async () => {
    mockFetchSequence([{ ok: true, status: 200, body: [] }]);
    renderWithQueryClient(<CalendarProbe />);
    expect(await screen.findByText("Nothing on the calendar")).toBeInTheDocument();
  });

  it("shows an ErrorState on a failed load", async () => {
    mockFetchSequence([{ ok: false, status: 500, body: { detail: "boom" } }]);
    renderWithQueryClient(<CalendarProbe />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("submits the create form with the entered title and start time", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetchSequence([{ ok: true, status: 200, body: [] }]);

    renderWithQueryClient(<CalendarProbe />);
    await screen.findByText("Nothing on the calendar");

    await user.type(screen.getByLabelText("New event title"), "New event");
    await user.type(screen.getByLabelText("Event start"), "2026-06-15T09:30");
    await user.click(screen.getByRole("button", { name: "Add event" }));

    const postCall = fetchMock.mock.calls.find(([, options]) => options?.method === "POST");
    expect(postCall).toBeDefined();
    expect(JSON.parse(postCall![1].body as string)).toMatchObject({ title: "New event" });
  });
});
