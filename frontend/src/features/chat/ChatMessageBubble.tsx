import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";

import { formatMessageTime } from "./formatMessageDate";
import type { ChatMessage } from "./types";

// Checkpoint 4.6 — bidi strategy: `dir="auto"` on each BLOCK-level element
// (paragraph/list/list-item), never on the whole message or the bubble
// itself. This lets every block resolve its own direction independently
// from its own first strong character (per the browser's native bidi
// algorithm — no language detection, no manual LRM/RLM insertion), so a
// list mixing Arabic and English items, or a paragraph of English
// containing an Arabic quotation, each read naturally. The bubble's own
// alignment (who sent it) is a SEPARATE concern, set by the caller based on
// role alone and never touched here — see ChatPage's own bubble placement.
const markdownComponents = {
  p: ({ children }: ComponentPropsWithoutRef<"p">) => (
    <p dir="auto" style={{ margin: "0 0 var(--space-2) 0" }}>
      {children}
    </p>
  ),
  ul: ({ children }: ComponentPropsWithoutRef<"ul">) => (
    <ul dir="auto" style={{ margin: "0 0 var(--space-2) 0", paddingInlineStart: "var(--space-5)" }}>
      {children}
    </ul>
  ),
  ol: ({ children }: ComponentPropsWithoutRef<"ol">) => (
    <ol dir="auto" style={{ margin: "0 0 var(--space-2) 0", paddingInlineStart: "var(--space-5)" }}>
      {children}
    </ol>
  ),
  li: ({ children }: ComponentPropsWithoutRef<"li">) => (
    <li dir="auto" style={{ marginBottom: "var(--space-1)" }}>
      {children}
    </li>
  ),
  // Inline code / identifiers (e.g. "RCIP-6") stay isolated and left-to-
  // right regardless of surrounding Arabic — unicode-bidi: isolate is the
  // standards-based CSS primitive for exactly this, never a manual
  // Unicode direction-mark workaround.
  code: ({ children }: ComponentPropsWithoutRef<"code">) => (
    <code
      dir="ltr"
      style={{
        unicodeBidi: "isolate",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        background: "var(--color-bg-accent-subtle)",
        borderRadius: "var(--radius-sm)",
        padding: "0 4px",
        fontSize: "0.9em",
      }}
    >
      {children}
    </code>
  ),
  strong: ({ children }: ComponentPropsWithoutRef<"strong">) => <strong>{children}</strong>,
  em: ({ children }: ComponentPropsWithoutRef<"em">) => <em>{children}</em>,
};

interface ChatMessageBubbleProps {
  message: ChatMessage;
}

export function ChatMessageBubble({ message }: ChatMessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <div
      role="listitem"
      aria-label={`${message.role} message`}
      style={{
        alignSelf: isUser ? "flex-end" : "flex-start",
        background: isUser ? "var(--color-bg-accent-subtle)" : "var(--color-bg-subtle)",
        padding: "var(--space-3)",
        borderRadius: "var(--radius-md)",
        maxWidth: "70%",
        minWidth: 0,
        overflowWrap: "anywhere",
      }}
    >
      {isUser ? (
        // User text is preserved faithfully — no Markdown transformation
        // of what Ahmed actually typed — but still gets bidi support and
        // real line breaks.
        <p dir="auto" className="text-[var(--color-text-body)]" style={{ margin: 0, whiteSpace: "pre-wrap" }}>
          {message.content}
        </p>
      ) : (
        <div className="text-[var(--color-text-body)]" style={{ lineHeight: 1.5 }}>
          {/* skipHtml: explicit, not relied-on-as-default — raw HTML in
              ChatMessage.content (user, model, or deterministic app text,
              all untrusted presentation content) is never parsed as
              elements, so <script>/<img onerror>/javascript: links can
              never execute — see ChatMessageBubble.test.tsx. */}
          <ReactMarkdown remarkPlugins={[remarkBreaks]} skipHtml components={markdownComponents}>
            {message.content}
          </ReactMarkdown>
        </div>
      )}
      <time
        dateTime={message.created_at}
        dir="auto"
        className="text-[var(--color-text-muted)]"
        style={{ display: "block", marginTop: "var(--space-1)", fontSize: "12px" }}
      >
        {formatMessageTime(message.created_at)}
      </time>
    </div>
  );
}
