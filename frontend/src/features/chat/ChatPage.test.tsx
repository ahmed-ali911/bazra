import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQueryClient } from "../../test-utils";
import { ChatPage } from "./ChatPage";

function mockFetch(handler: (url: string, options?: RequestInit) => { ok: boolean; status: number; body: unknown }) {
  const fn = vi.fn().mockImplementation(async (input: RequestInfo | URL, options?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const { ok, status, body } = handler(url, options);
    return { ok, status, json: async () => body };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("ChatPage", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows an empty state when there are no messages yet", async () => {
    mockFetch(() => ({ ok: true, status: 200, body: [] }));
    renderWithQueryClient(<ChatPage />);
    expect(await screen.findByText("No messages yet")).toBeInTheDocument();
  });

  it("shows existing messages once loaded", async () => {
    mockFetch(() => ({
      ok: true,
      status: 200,
      body: [
        { id: 1, role: "user", content: "what's due today?", created_at: "2026-01-01T00:00:00Z" },
        { id: 2, role: "assistant", content: "Nothing is due today.", created_at: "2026-01-01T00:00:01Z" },
      ],
    }));

    renderWithQueryClient(<ChatPage />);
    expect(await screen.findByText("what's due today?")).toBeInTheDocument();
    expect(screen.getByText("Nothing is due today.")).toBeInTheDocument();
  });

  it("shows an ErrorState on a failed load", async () => {
    mockFetch(() => ({ ok: false, status: 500, body: { detail: "boom" } }));
    renderWithQueryClient(<ChatPage />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("submits a message and clears the input on success", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch((_url, options) => {
      if (options?.method === "POST") {
        return {
          ok: true,
          status: 200,
          body: {
            user_message: { id: 1, role: "user", content: "hi", created_at: "2026-01-01T00:00:00Z" },
            assistant_message: { id: 2, role: "assistant", content: "hello!", created_at: "2026-01-01T00:00:01Z" },
          },
        };
      }
      return { ok: true, status: 200, body: [] };
    });

    renderWithQueryClient(<ChatPage />);
    await screen.findByText("No messages yet");

    await user.type(screen.getByLabelText("Chat message"), "hi");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const postCall = fetchMock.mock.calls.find(([, options]) => options?.method === "POST");
    expect(postCall).toBeDefined();
    const body = JSON.parse(postCall![1].body as string);
    expect(body.content).toBe("hi");
    expect(body.tomorrow_start).toBeDefined();
    expect(body.window_end).toBeDefined();

    expect(screen.getByLabelText("Chat message")).toHaveValue("");
  });

  it("shows an inline error when sending fails, without crashing", async () => {
    const user = userEvent.setup();
    mockFetch((_url, options) => {
      if (options?.method === "POST") {
        return {
          ok: false,
          status: 502,
          body: { detail: { error: "model_call_failed", user_message_id: 1 } },
        };
      }
      return { ok: true, status: 200, body: [] };
    });

    renderWithQueryClient(<ChatPage />);
    await screen.findByText("No messages yet");

    await user.type(screen.getByLabelText("Chat message"), "hi");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
