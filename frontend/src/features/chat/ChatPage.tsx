import { useState } from "react";
import type { FormEvent } from "react";

import { Button } from "../../design-system/components/Button";
import { EmptyState } from "../../design-system/components/EmptyState";
import { ErrorState } from "../../design-system/components/ErrorState";
import { LoadingState } from "../../design-system/components/LoadingState";
import { useChatMessages } from "./useChatMessages";
import { useSendChatMessage } from "./useSendChatMessage";

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
          {messages.map((message) => (
            <div
              key={message.id}
              role="listitem"
              aria-label={`${message.role} message`}
              style={{
                alignSelf: message.role === "user" ? "flex-end" : "flex-start",
                background: message.role === "user" ? "var(--color-bg-accent-subtle)" : "var(--color-bg-subtle)",
                padding: "var(--space-3)",
                borderRadius: "var(--radius-md)",
                maxWidth: "70%",
              }}
            >
              <p className="text-[var(--color-text-body)]">{message.content}</p>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No messages yet"
          description="Ask something about your tasks, calendar, inbox, or life areas."
        />
      )}

      {sendMessage.isError ? (
        <p role="alert" className="text-[var(--color-status-danger)]">
          Something went wrong sending that message.
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
