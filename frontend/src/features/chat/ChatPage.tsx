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
import type { ModelUnavailableErrorDetail } from "./types";
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

// Real, navigable Chat screen (Checkpoint 3.2) — functional-only, not a
// designed screen, per the standing Phase 2/3 rule. Read-only Q&A: no
// UI here can create/edit/delete anything; a clear write request gets
// a fixed unavailability reply generated server-side, without ever
// reaching the model.
export function ChatPage() {
  const [content, setContent] = useState("");
  const { data: messages, isPending, error, refetch } = useChatMessages();
  const sendMessage = useSendChatMessage();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!content.trim()) return;
    sendMessage.mutate(content, { onSuccess: () => setContent("") });
  }

  if (isPending) {
    return <LoadingState message="Loading chat…" />;
  }

  if (error) {
    return <ErrorState description="Couldn't load chat." onRetry={() => refetch()} />;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <h1 className="text-[var(--color-text-heading)]">Chat</h1>

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
