import { describe, expect, it } from "vitest";

import { formatDaySeparator, formatMessageTime, localDayKey } from "./formatMessageDate";

// Deterministic reference instants only — never real wall-clock "now"
// (Checkpoint 4.6 brief §23: "do not write flaky tests based on actual
// current wall-clock time").
const _REFERENCE_NOW = new Date(2026, 9, 2, 15, 0, 0); // 2 Oct 2026, 3:00 PM local

describe("formatMessageTime", () => {
  it("formats created_at using the browser's local time, never a frontend-now substitute", () => {
    const createdAt = new Date(2026, 9, 2, 16, 27, 0).toISOString();
    expect(formatMessageTime(createdAt)).toBe(
      new Date(createdAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
    );
  });

  it("never renders a full ISO timestamp", () => {
    const createdAt = new Date(2026, 9, 2, 16, 27, 0).toISOString();
    expect(formatMessageTime(createdAt)).not.toContain("T");
    expect(formatMessageTime(createdAt)).not.toContain("Z");
  });
});

describe("formatDaySeparator", () => {
  it('labels a message created earlier the same local day as "Today"', () => {
    const createdAt = new Date(2026, 9, 2, 9, 0, 0).toISOString();
    expect(formatDaySeparator(createdAt, _REFERENCE_NOW)).toBe("Today");
  });

  it('labels a message created the previous local day as "Yesterday"', () => {
    const createdAt = new Date(2026, 9, 1, 23, 0, 0).toISOString();
    expect(formatDaySeparator(createdAt, _REFERENCE_NOW)).toBe("Yesterday");
  });

  it("labels an older message with a short explicit date, not a repeated full date", () => {
    const createdAt = new Date(2026, 8, 15, 10, 0, 0).toISOString();
    const label = formatDaySeparator(createdAt, _REFERENCE_NOW);
    expect(label).not.toBe("Today");
    expect(label).not.toBe("Yesterday");
    expect(label.length).toBeLessThan(15); // short label, not a full ISO date
  });

  it("correctly classifies a message just after local midnight as a new day, not Today's predecessor", () => {
    // Reference: 2 Oct 2026, 00:30 local. A message at 1 Oct 2026, 23:45
    // local is "Yesterday" relative to that — the classic "spans midnight
    // in the user's own local timezone" case (§23).
    const referenceJustAfterMidnight = new Date(2026, 9, 2, 0, 30, 0);
    const createdAtLateThePriorNight = new Date(2026, 9, 1, 23, 45, 0).toISOString();
    expect(formatDaySeparator(createdAtLateThePriorNight, referenceJustAfterMidnight)).toBe("Yesterday");
  });
});

describe("localDayKey", () => {
  it("groups two messages on the same local day under the same key", () => {
    const a = new Date(2026, 9, 2, 1, 0, 0).toISOString();
    const b = new Date(2026, 9, 2, 23, 0, 0).toISOString();
    expect(localDayKey(a)).toBe(localDayKey(b));
  });

  it("gives messages on different local days different keys", () => {
    const a = new Date(2026, 9, 1, 23, 59, 0).toISOString();
    const b = new Date(2026, 9, 2, 0, 1, 0).toISOString();
    expect(localDayKey(a)).not.toBe(localDayKey(b));
  });
});
