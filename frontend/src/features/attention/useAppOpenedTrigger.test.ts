import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Checkpoint 4.5e frontend trigger tests. The hook keeps a MODULE-
// SCOPED once-per-page-load guard (an optimization only, never the
// correctness boundary — see the hook's own docstring) — vi.resetModules()
// + a fresh dynamic import before each test gives each test its own
// clean module instance, so these tests don't leak state into each
// other via that guard.

function mockFetch(handler: (url: string, options?: RequestInit) => { ok: boolean; status: number; body: unknown }) {
  const fn = vi.fn().mockImplementation(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const { ok, status, body } = handler(url, options);
    return { ok, status, json: async () => body };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function freshQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

async function loadFreshHook() {
  vi.resetModules();
  const mod = await import("./useAppOpenedTrigger");
  return mod.useAppOpenedTrigger;
}

describe("useAppOpenedTrigger", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fires exactly one POST to /api/v1/attention/app-opened on mount", async () => {
    const fetchMock = mockFetch(() => ({ ok: true, status: 200, body: { status: "silence" } }));
    const useAppOpenedTrigger = await loadFreshHook();
    const queryClient = freshQueryClient();

    function Harness() {
      useAppOpenedTrigger();
      return null;
    }

    render(createElement(QueryClientProvider, { client: queryClient }, createElement(Harness)));

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [url, options] = fetchMock.mock.calls[0];
    expect(String(url)).toContain("/api/v1/attention/app-opened");
    expect(options?.method).toBe("POST");
  });

  it("the request body contains only `timezone` — no candidate/score/narration/source id", async () => {
    const fetchMock = mockFetch(() => ({ ok: true, status: 200, body: { status: "silence" } }));
    const useAppOpenedTrigger = await loadFreshHook();
    const queryClient = freshQueryClient();

    function Harness() {
      useAppOpenedTrigger();
      return null;
    }

    render(createElement(QueryClientProvider, { client: queryClient }, createElement(Harness)));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));

    const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
    expect(Object.keys(body)).toEqual(["timezone"]);
    expect(typeof body.timezone).toBe("string");
  });

  it("a second mount within the same page load does not fire a second request", async () => {
    const fetchMock = mockFetch(() => ({ ok: true, status: 200, body: { status: "silence" } }));
    const useAppOpenedTrigger = await loadFreshHook();
    const queryClient = freshQueryClient();

    function Harness() {
      useAppOpenedTrigger();
      return null;
    }

    const first = render(createElement(QueryClientProvider, { client: queryClient }, createElement(Harness)));
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    first.unmount();

    render(createElement(QueryClientProvider, { client: queryClient }, createElement(Harness)));
    // Give any (incorrect) second effect a tick to fire before asserting.
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("invalidates the chat messages cache on a surfaced response", async () => {
    mockFetch(() => ({
      ok: true,
      status: 200,
      body: { status: "surfaced", message: { id: 1, role: "assistant", content: "x", created_at: "2026-01-01T00:00:00Z" } },
    }));
    const useAppOpenedTrigger = await loadFreshHook();
    const queryClient = freshQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    function Harness() {
      useAppOpenedTrigger();
      return null;
    }

    render(createElement(QueryClientProvider, { client: queryClient }, createElement(Harness)));

    await vi.waitFor(() => expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["chat", "messages"] }));
  });

  it("does not invalidate any cache on silence", async () => {
    mockFetch(() => ({ ok: true, status: 200, body: { status: "silence" } }));
    const useAppOpenedTrigger = await loadFreshHook();
    const queryClient = freshQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    function Harness() {
      useAppOpenedTrigger();
      return null;
    }

    render(createElement(QueryClientProvider, { client: queryClient }, createElement(Harness)));
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it("a network failure is swallowed silently — no throw, no cache invalidation", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    const useAppOpenedTrigger = await loadFreshHook();
    const queryClient = freshQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");

    function Harness() {
      useAppOpenedTrigger();
      return null;
    }

    expect(() =>
      render(createElement(QueryClientProvider, { client: queryClient }, createElement(Harness))),
    ).not.toThrow();
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(invalidateSpy).not.toHaveBeenCalled();
  });

  it("never reads or writes localStorage/sessionStorage", async () => {
    const fetchMock = mockFetch(() => ({ ok: true, status: 200, body: { status: "silence" } }));
    const useAppOpenedTrigger = await loadFreshHook();
    const queryClient = freshQueryClient();
    const localSpy = vi.spyOn(Storage.prototype, "setItem");
    const localGetSpy = vi.spyOn(Storage.prototype, "getItem");

    function Harness() {
      useAppOpenedTrigger();
      return null;
    }

    const { unmount } = render(
      createElement(QueryClientProvider, { client: queryClient }, createElement(Harness)),
    );
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    unmount();

    expect(localSpy).not.toHaveBeenCalled();
    expect(localGetSpy).not.toHaveBeenCalled();
    localSpy.mockRestore();
    localGetSpy.mockRestore();
  });

  it("never adds a visibility/focus/connectivity listener (structural check: the " +
    "test environment itself registers some of these ambiently, so a " +
    "runtime addEventListener spy would false-positive — checking the " +
    "hook's own source is the precise, unambiguous check)", async () => {
    const source = (await import("./useAppOpenedTrigger.ts?raw")).default as string;
    for (const forbidden of ["addEventListener", "visibilitychange", "offline"]) {
      expect(source).not.toContain(forbidden);
    }
  });
});
