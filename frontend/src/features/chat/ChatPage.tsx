import { useState } from "react";
import type { FormEvent } from "react";

import { ApiError } from "../../services/api";
import { Button } from "../../design-system/components/Button";
import { EmptyState } from "../../design-system/components/EmptyState";
import { ErrorState } from "../../design-system/components/ErrorState";
import { LoadingState } from "../../design-system/components/LoadingState";
import { ChatMessageBubble } from "./ChatMessageBubble";
import { DaySeparator } from "./DaySeparator";
import { groupMessagesByDay } from "./groupMessagesByDay";
import type { ModelProviderOverride, ModelUnavailableErrorDetail } from "./types";
import { useChatMessages } from "./useChatMessages";
import { useSendChatMessage } from "./useSendChatMessage";

// Checkpoint 5.1 — returns the backend's own deterministic degradation
// wording for a primary-generation model failure, or null for any
// OTHER error shape (network failure, unrelated status code, an old/
// differently-shaped error body) — callers fall back to the existing
// generic message for null, exactly as before this checkpoint. Never
// invents wording here — the backend already picked Arabic/English to
// match the user's own message (see chat/service.py's
// _reply_for_model_unavailable), so this only ever displays it
// verbatim.
function modelUnavailableMessage(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 502) return null;
  const detail = error.detail as Partial<ModelUnavailableErrorDetail> | undefined;
  if (detail?.error !== "model_call_failed") return null;
  return detail.message ?? null;
}

// Checkpoint 5.7H — Manual Gemini Test Mode's own small, compact
// selector. A developer/test control, not a redesign: two plain
// buttons styled as a segmented toggle, reusing existing design tokens
// only (no new component). Component-local React state only (useState,
// not persisted anywhere) — resets to "Default" on every page load/
// remount, per the checkpoint's own explicit "session-scoped, not a
// global permanent preference" requirement.
function ModelProviderSelector({
  value,
  onChange,
}: {
  value: ModelProviderOverride;
  onChange: (next: ModelProviderOverride) => void;
}) {
  const options: { value: ModelProviderOverride; label: string }[] = [
    { value: "default", label: "Default" },
    { value: "google_gemini_test", label: "Gemini Test" },
  ];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-1)" }}>
      <div
        role="radiogroup"
        aria-label="AI provider"
        style={{ display: "inline-flex", gap: "2px", padding: "2px", borderRadius: "var(--radius-md, 6px)", background: "var(--color-bg-subtle)" }}
      >
        {options.map((option) => {
          const active = option.value === value;
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onChange(option.value)}
              className="rounded-md transition-colors"
              style={{
                padding: "4px 10px",
                fontSize: "0.8125rem",
                border: "none",
                cursor: "pointer",
                background: active ? "var(--color-bg-default)" : "transparent",
                color: active ? "var(--color-text-heading)" : "var(--color-text-muted)",
                boxShadow: active ? "var(--shadow-sm, 0 1px 2px rgba(0,0,0,0.08))" : "none",
              }}
            >
              {option.label}
            </button>
          );
        })}
      </div>
      {value === "google_gemini_test" ? (
        <p style={{ fontSize: "0.75rem", color: "var(--color-status-warning)", margin: 0 }}>
          Gemini Test: this conversation will be sent to Google Gemini for this reply only.
        </p>
      ) : null}
    </div>
  );
}

// Real, navigable Chat screen (Checkpoint 3.2) — functional-only, not a
// designed screen, per the standing Phase 2/3 rule. Read-only Q&A: no
// UI here can create/edit/delete anything; a clear write request gets
// a fixed unavailability reply generated server-side, without ever
// reaching the model.
export function ChatPage() {
  const [content, setContent] = useState("");
  const [modelProviderOverride, setModelProviderOverride] = useState<ModelProviderOverride>("default");
  const { data: messages, isPending, error, refetch } = useChatMessages();
  const sendMessage = useSendChatMessage();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!content.trim()) return;
    sendMessage.mutate({ content, modelProviderOverride }, { onSuccess: () => setContent("") });
  }

  if (isPending) {
    return <LoadingState message="Loading chat…" />;
  }

  if (error) {
    return <ErrorState description="Couldn't load chat." onRetry={() => refetch()} />;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "var(--space-4)" }}>
        <h1 className="text-[var(--color-text-heading)]">Chat</h1>
        <ModelProviderSelector value={modelProviderOverride} onChange={setModelProviderOverride} />
      </div>

      {messages && messages.length > 0 ? (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-2)" }} role="list">
          {groupMessagesByDay(messages).map((entry) =>
            entry.kind === "separator" ? (
              <DaySeparator key={entry.key} label={entry.label} />
            ) : (
              <ChatMessageBubble key={entry.key} message={entry.message} />
            ),
          )}
        </div>
      ) : (
        <EmptyState
          title="No messages yet"
          description="Ask something about your tasks, calendar, inbox, or life areas."
        />
      )}

      {sendMessage.isError ? (
        <p role="alert" className="text-[var(--color-status-danger)]">
          {modelUnavailableMessage(sendMessage.error) ?? "Something went wrong sending that message."}
        </p>
      ) : null}

      <form onSubmit={handleSubmit} style={{ display: "flex", gap: "var(--space-2)" }}>
        <input
          aria-label="Chat message"
          value={content}
          onChange={(event) => setContent(event.target.value)}
          className="rounded-md bg-[var(--color-bg-subtle)] text-[var(--color-text-body)]"
          style={{ padding: "8px 12px", border: "none", flex: 1 }}
        />
        <Button type="submit" disabled={sendMessage.isPending}>
          {sendMessage.isPending ? "Sending…" : "Send"}
        </Button>
      </form>
    </div>
  );
}
