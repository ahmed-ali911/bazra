// Checkpoint 4.6 — all formatting here derives from ChatMessage.created_at
// (an authoritative, persisted UTC ISO string) parsed into a browser Date
// and read back via its LOCAL-timezone getters/formatters — the exact same
// "never a separate IANA string, just the browser's own local Date
// behavior" convention localDayBoundaries.ts already established. There is
// no frontend-now substitution for the timestamp itself: the instant shown
// always comes from created_at. `now` is accepted as an explicit parameter
// (defaulting to `new Date()` only at the real call site) purely so "Today"/
// "Yesterday" classification — which is inherently relative to the moment
// of viewing, same as any chat app's day separator — can be tested
// deterministically, never from actual wall-clock time in a test.

export function formatMessageTime(createdAt: string): string {
  return new Date(createdAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function isSameLocalDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function startOfLocalDay(date: Date): Date {
  const start = new Date(date);
  start.setHours(0, 0, 0, 0);
  return start;
}

// A stable key for grouping — local calendar day, not a UTC day (two
// messages at 11pm/1am local time on either side of UTC midnight must
// still land in the same/different local day correctly).
export function localDayKey(createdAt: string): string {
  const date = new Date(createdAt);
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;
}

export function formatDaySeparator(createdAt: string, now: Date = new Date()): string {
  const date = new Date(createdAt);
  if (isSameLocalDay(date, now)) {
    return "Today";
  }
  const yesterday = new Date(startOfLocalDay(now));
  yesterday.setDate(yesterday.getDate() - 1);
  if (isSameLocalDay(date, yesterday)) {
    return "Yesterday";
  }
  const sameYear = date.getFullYear() === now.getFullYear();
  return date.toLocaleDateString([], sameYear ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", year: "numeric" });
}
