import { describe, expect, it } from "vitest";

import { groupMessagesByDay } from "./groupMessagesByDay";
import type { ChatMessage } from "./types";

const _REFERENCE_NOW = new Date(2026, 9, 2, 15, 0, 0);

function message(id: number, role: "user" | "assistant", createdAt: Date): ChatMessage {
  return { id, role, content: `message ${id}`, created_at: createdAt.toISOString() };
}

describe("groupMessagesByDay", () => {
  it("inserts exactly one separator before the first message of each new local day", () => {
    const messages = [
      message(1, "user", new Date(2026, 9, 1, 9, 0, 0)),
      message(2, "assistant", new Date(2026, 9, 1, 9, 1, 0)),
      message(3, "user", new Date(2026, 9, 2, 10, 0, 0)),
    ];

    const entries = groupMessagesByDay(messages, _REFERENCE_NOW);

    expect(entries.map((e) => e.kind)).toEqual(["separator", "message", "message", "separator", "message"]);
  });

  it("never repeats a separator under every message on the same day", () => {
    const messages = [
      message(1, "user", new Date(2026, 9, 2, 9, 0, 0)),
      message(2, "assistant", new Date(2026, 9, 2, 9, 1, 0)),
      message(3, "user", new Date(2026, 9, 2, 9, 2, 0)),
    ];

    const entries = groupMessagesByDay(messages, _REFERENCE_NOW);
    const separators = entries.filter((e) => e.kind === "separator");

    expect(separators).toHaveLength(1);
  });

  it("labels separators using formatDaySeparator's own Today/Yesterday logic", () => {
    const messages = [message(1, "user", new Date(2026, 9, 2, 9, 0, 0))];
    const entries = groupMessagesByDay(messages, _REFERENCE_NOW);

    expect(entries[0]).toMatchObject({ kind: "separator", label: "Today" });
  });

  it("preserves message order and identity", () => {
    const messages = [
      message(1, "user", new Date(2026, 9, 2, 9, 0, 0)),
      message(2, "assistant", new Date(2026, 9, 2, 9, 1, 0)),
    ];
    const entries = groupMessagesByDay(messages, _REFERENCE_NOW);
    const messageEntries = entries.filter((e) => e.kind === "message");

    expect(messageEntries.map((e) => (e.kind === "message" ? e.message.id : null))).toEqual([1, 2]);
  });
});
