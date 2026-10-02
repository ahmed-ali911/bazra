import { formatDaySeparator, localDayKey } from "./formatMessageDate";
import type { ChatMessage } from "./types";

export type ChatTimelineEntry =
  | { kind: "separator"; key: string; label: string }
  | { kind: "message"; key: string; message: ChatMessage };

// One separator per LOCAL calendar day change, inserted before the first
// message of that day — never repeated under every message. `now` is
// explicit (see formatMessageDate.ts) so "Today"/"Yesterday" labeling is
// deterministically testable.
export function groupMessagesByDay(messages: ChatMessage[], now: Date = new Date()): ChatTimelineEntry[] {
  const entries: ChatTimelineEntry[] = [];
  let lastDayKey: string | null = null;

  for (const message of messages) {
    const dayKey = localDayKey(message.created_at);
    if (dayKey !== lastDayKey) {
      entries.push({ kind: "separator", key: `separator-${dayKey}`, label: formatDaySeparator(message.created_at, now) });
      lastDayKey = dayKey;
    }
    entries.push({ kind: "message", key: `message-${message.id}`, message });
  }

  return entries;
}
