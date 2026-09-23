import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQueryClient } from "../../test-utils";
import { TasksProbe } from "./TasksProbe";

function mockFetchSequence(responses: Array<{ ok: boolean; status: number; body: unknown }>) {
  const fn = vi.fn();
  for (const { ok, status, body } of responses) {
    fn.mockImplementationOnce(async () => ({ ok, status, json: async () => body }));
  }
  // Any call beyond the scripted sequence keeps returning the last response,
  // so an unexpected extra fetch (e.g. a refetch) doesn't crash the test.
  fn.mockImplementation(async () => {
    const last = responses[responses.length - 1];
    return { ok: last.ok, status: last.status, json: async () => last.body };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("TasksProbe", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows tasks once loaded", async () => {
    mockFetchSequence([
      { ok: true, status: 200, body: [{ id: 1, title: "Write report", status: "open", life_area_id: null }] },
      { ok: true, status: 200, body: [{ id: 1, name: "Work", slug: "work" }] },
    ]);

    renderWithQueryClient(<TasksProbe />);
    expect(await screen.findByText("Write report")).toBeInTheDocument();
  });

  it("shows an empty state when there are no tasks", async () => {
    mockFetchSequence([
      { ok: true, status: 200, body: [] },
      { ok: true, status: 200, body: [] },
    ]);

    renderWithQueryClient(<TasksProbe />);
    expect(await screen.findByText("No tasks yet")).toBeInTheDocument();
  });

  it("shows an ErrorState on a failed load", async () => {
    mockFetchSequence([{ ok: false, status: 500, body: { detail: "boom" } }]);

    renderWithQueryClient(<TasksProbe />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("submits the create form with the entered title", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetchSequence([
      { ok: true, status: 200, body: [] },
      { ok: true, status: 200, body: [] },
    ]);

    renderWithQueryClient(<TasksProbe />);
    await screen.findByText("No tasks yet");

    await user.type(screen.getByLabelText("New task title"), "New task");
    await user.click(screen.getByRole("button", { name: "Add task" }));

    const postCall = fetchMock.mock.calls.find(([, options]) => options?.method === "POST");
    expect(postCall).toBeDefined();
    expect(JSON.parse(postCall![1].body as string)).toMatchObject({ title: "New task" });
  });

  it("updates due_at when the due-date input loses focus", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetchSequence([
      { ok: true, status: 200, body: [{ id: 1, title: "Write report", status: "open", life_area_id: null, due_at: null }] },
      { ok: true, status: 200, body: [] },
    ]);

    renderWithQueryClient(<TasksProbe />);
    const dueInput = await screen.findByLabelText('Due date for "Write report"');

    await user.type(dueInput, "2026-06-15T09:30");
    await user.tab(); // blur

    const patchCall = fetchMock.mock.calls.find(([, options]) => options?.method === "PATCH");
    expect(patchCall).toBeDefined();
    const body = JSON.parse(patchCall![1].body as string);
    expect(body.due_at).toContain("2026-06-15");
  });
});
