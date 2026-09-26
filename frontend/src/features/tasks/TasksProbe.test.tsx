import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { renderWithQueryClient } from "../../test-utils";
import { TasksProbe, toLocalDateTimeInputValue } from "./TasksProbe";

// This project's tsconfig deliberately has no @types/node (browser-only
// types) — a minimal, local ambient shim for the one Node global these
// tests need, rather than pulling in the full Node type surface for a
// single test-only env var.
declare const process: { env: Record<string, string | undefined> };

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

  // ---- Checkpoint 3.12a: datetime-local display correction --------------------

  describe("with a non-UTC local timezone (Africa/Cairo, UTC+03:00)", () => {
    const originalTZ = process.env.TZ;

    beforeAll(() => {
      // Explicit, self-contained override (not relying on whatever the
      // host/CI machine's own default happens to be) — reliably respected
      // by Node/jsdom's Date and Intl, verified directly before writing
      // these tests. Restored in afterAll so it never leaks into other
      // test files (Vitest isolates test files into separate workers by
      // default, but this keeps the test self-documenting regardless).
      process.env.TZ = "Africa/Cairo";
    });

    afterAll(() => {
      process.env.TZ = originalTZ;
    });

    it("converts a UTC API instant to local wall-clock digits, not the raw UTC digits", () => {
      // The exact real-world case the bug report described: a task whose
      // API due_at is "2026-09-26T07:00:00Z" (UTC) must display as
      // 10:00 local in a UTC+03:00 timezone, not 07:00.
      const result = toLocalDateTimeInputValue("2026-09-26T07:00:00Z");
      expect(result).toBe("2026-09-26T10:00");
      expect(result).not.toBe("2026-09-26T07:00"); // not merely the sliced UTC digits
    });

    it("renders the per-row due-date input with the local-converted value, not raw UTC digits", async () => {
      mockFetchSequence([
        {
          ok: true, status: 200,
          body: [{ id: 1, title: "أكلم حسين", status: "open", life_area_id: null, due_at: "2026-09-26T07:00:00Z" }],
        },
        { ok: true, status: 200, body: [] },
      ]);

      renderWithQueryClient(<TasksProbe />);
      const dueInput = await screen.findByLabelText('Due date for "أكلم حسين"');

      expect((dueInput as HTMLInputElement).value).toBe("2026-09-26T10:00");
      expect((dueInput as HTMLInputElement).value).not.toBe("2026-09-26T07:00");
    });

    it("null due_at still renders as an empty input", async () => {
      mockFetchSequence([
        {
          ok: true, status: 200,
          body: [{ id: 1, title: "No due date task", status: "open", life_area_id: null, due_at: null }],
        },
        { ok: true, status: 200, body: [] },
      ]);

      renderWithQueryClient(<TasksProbe />);
      const dueInput = await screen.findByLabelText('Due date for "No due date task"');

      expect((dueInput as HTMLInputElement).value).toBe("");
    });

    it("saving the same displayed local value round-trips to the identical original UTC instant", async () => {
      const user = userEvent.setup();
      const fetchMock = mockFetchSequence([
        {
          ok: true, status: 200,
          body: [{ id: 1, title: "أكلم حسين", status: "open", life_area_id: null, due_at: "2026-09-26T07:00:00Z" }],
        },
        { ok: true, status: 200, body: [] },
      ]);

      renderWithQueryClient(<TasksProbe />);
      const dueInput = await screen.findByLabelText('Due date for "أكلم حسين"');
      expect((dueInput as HTMLInputElement).value).toBe("2026-09-26T10:00");

      // Blur without changing anything — the displayed local value, saved
      // back through the EXISTING (unchanged) save path, must resolve to
      // the exact same absolute instant the API originally sent.
      dueInput.focus();
      await user.tab();

      const patchCall = fetchMock.mock.calls.find(([, options]) => options?.method === "PATCH");
      expect(patchCall).toBeDefined();
      const body = JSON.parse(patchCall![1].body as string);
      expect(new Date(body.due_at).getTime()).toBe(new Date("2026-09-26T07:00:00Z").getTime());
    });
  });
});
