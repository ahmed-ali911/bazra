import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "./api";

describe("api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses a successful JSON response into a typed value", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ hello: "world" }),
      }),
    );

    const result = await api.get<{ hello: string }>("/test");
    expect(result).toEqual({ hello: "world" });
  });

  it("returns undefined for a 204 without attempting to parse a body", async () => {
    const json = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 204, json }));

    const result = await api.post("/test");
    expect(result).toBeUndefined();
    expect(json).not.toHaveBeenCalled();
  });

  it("throws ApiError with the backend's detail message on a non-2xx JSON error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ detail: "Not authenticated" }),
      }),
    );

    await expect(api.get("/test")).rejects.toBeInstanceOf(ApiError);
    await expect(api.get("/test")).rejects.toMatchObject({ status: 401, message: "Not authenticated" });
  });

  it("throws ApiError with a safe fallback message on a non-2xx response with no usable JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Internal Server Error",
        json: async () => {
          throw new Error("not json");
        },
      }),
    );

    await expect(api.get("/test")).rejects.toMatchObject({ status: 500, message: "Internal Server Error" });
  });

  it("preserves a structured (non-string) detail object on .detail, separate from .message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        statusText: "Bad Request",
        json: async () => ({ detail: { error: "life_area_has_linked_items", task_count: 3 } }),
      }),
    );

    await expect(api.get("/test")).rejects.toMatchObject({
      status: 400,
      message: "Bad Request", // detail isn't a string, so message falls back to statusText
      detail: { error: "life_area_has_linked_items", task_count: 3 },
    });
  });

  it("propagates a network failure as-is, distinguishable from ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(api.get("/test")).rejects.toBeInstanceOf(TypeError);
    await expect(api.get("/test")).rejects.not.toBeInstanceOf(ApiError);
  });
});
