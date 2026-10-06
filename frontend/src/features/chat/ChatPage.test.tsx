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
    expect(body.timezone).toBeDefined();

    expect(screen.getByLabelText("Chat message")).toHaveValue("");
  });

  it("shows an inline error when sending fails, without crashing", async () => {
    const user = userEvent.setup();
    mockFetch((_url, options) => {
      if (options?.method === "POST") {
        return {
          ok: false,
          status: 502,
          // Deliberately the OLD (pre-5.1) detail shape, with no
          // reason/message fields — confirms the generic fallback
          // still renders rather than crashing on a missing field.
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
    expect(screen.getByRole("alert")).toHaveTextContent("Something went wrong sending that message.");
  });

  it("shows the backend's own deterministic degradation message for a model failure (Checkpoint 5.1)", async () => {
    const user = userEvent.setup();
    mockFetch((_url, options) => {
      if (options?.method === "POST") {
        return {
          ok: false,
          status: 502,
          body: {
            detail: {
              error: "model_call_failed",
              reason: "billing_or_credits",
              message: "The model isn't available right now. Your message is saved, but I couldn't finish a reply.",
              user_message_id: 1,
            },
          },
        };
      }
      return { ok: true, status: 200, body: [] };
    });

    renderWithQueryClient(<ChatPage />);
    await screen.findByText("No messages yet");

    await user.type(screen.getByLabelText("Chat message"), "hi");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "The model isn't available right now. Your message is saved, but I couldn't finish a reply.",
    );
    // No fake assistant bubble is ever rendered for this failure — the
    // only message on screen is the user's own, plus this alert.
    expect(screen.queryByText("Something went wrong sending that message.")).not.toBeInTheDocument();
  });

  // ---- Checkpoint 5.7H: Manual Gemini Test Mode selector ----------------

  it("renders the AI provider selector with Default selected initially", async () => {
    mockFetch(() => ({ ok: true, status: 200, body: [] }));
    renderWithQueryClient(<ChatPage />);
    await screen.findByText("No messages yet");

    const group = screen.getByRole("radiogroup", { name: "AI provider" });
    const defaultOption = screen.getByRole("radio", { name: "Default" });
    const geminiOption = screen.getByRole("radio", { name: "Gemini Test" });

    expect(group).toBeInTheDocument();
    expect(defaultOption).toHaveAttribute("aria-checked", "true");
    expect(geminiOption).toHaveAttribute("aria-checked", "false");
  });

  it("sends model_provider_override: default when Default is selected (unchanged)", async () => {
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
    const body = JSON.parse(postCall![1].body as string);
    expect(body.model_provider_override).toBe("default");
  });

  it("selecting Gemini Test sends only the approved enum value", async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetch((_url, options) => {
      if (options?.method === "POST") {
        return {
          ok: true,
          status: 200,
          body: {
            user_message: { id: 1, role: "user", content: "hi", created_at: "2026-01-01T00:00:00Z" },
            assistant_message: { id: 2, role: "assistant", content: "pong", created_at: "2026-01-01T00:00:01Z" },
          },
        };
      }
      return { ok: true, status: 200, body: [] };
    });

    renderWithQueryClient(<ChatPage />);
    await screen.findByText("No messages yet");

    await user.click(screen.getByRole("radio", { name: "Gemini Test" }));
    expect(screen.getByRole("radio", { name: "Gemini Test" })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText(/this conversation will be sent to Google Gemini/i)).toBeInTheDocument();

    await user.type(screen.getByLabelText("Chat message"), "hi");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const postCall = fetchMock.mock.calls.find(([, options]) => options?.method === "POST");
    const body = JSON.parse(postCall![1].body as string);
    expect(body.model_provider_override).toBe("google_gemini_test");
    // Only the approved enum member — never a raw provider/model string.
    expect(body.provider).toBeUndefined();
    expect(body.model).toBeUndefined();
  });

  it("returning to Default restores ordinary behavior and clears the warning", async () => {
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

    await user.click(screen.getByRole("radio", { name: "Gemini Test" }));
    await user.click(screen.getByRole("radio", { name: "Default" }));

    expect(screen.getByRole("radio", { name: "Default" })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByText(/this conversation will be sent to Google Gemini/i)).not.toBeInTheDocument();

    await user.type(screen.getByLabelText("Chat message"), "hi");
    await user.click(screen.getByRole("button", { name: "Send" }));

    const postCall = fetchMock.mock.calls.find(([, options]) => options?.method === "POST");
    const body = JSON.parse(postCall![1].body as string);
    expect(body.model_provider_override).toBe("default");
  });

  it("a fresh mount always starts at Default — no persisted global preference", async () => {
    mockFetch(() => ({ ok: true, status: 200, body: [] }));
    const { unmount } = renderWithQueryClient(<ChatPage />);
    await screen.findByText("No messages yet");

    await userEvent.setup().click(screen.getByRole("radio", { name: "Gemini Test" }));
    expect(screen.getByRole("radio", { name: "Gemini Test" })).toHaveAttribute("aria-checked", "true");

    unmount();

    renderWithQueryClient(<ChatPage />);
    await screen.findByText("No messages yet");
    expect(screen.getByRole("radio", { name: "Default" })).toHaveAttribute("aria-checked", "true");
  });

  it("falls back to the generic message for an unrelated server error", async () => {
    const user = userEvent.setup();
    mockFetch((_url, options) => {
      if (options?.method === "POST") {
        return { ok: false, status: 500, body: { detail: "unrelated server error" } };
      }
      return { ok: true, status: 200, body: [] };
    });

    renderWithQueryClient(<ChatPage />);
    await screen.findByText("No messages yet");

    await user.type(screen.getByLabelText("Chat message"), "hi");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Something went wrong sending that message.");
  });
});
