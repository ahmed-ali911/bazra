// A quiet, centered day-grouping label — "Today" / "Yesterday" / "1 Oct" —
// never a full date repeated under every message. The label text itself
// (e.g. "Today") is an English UI string, like the rest of this screen's
// static chrome, so no dir="auto" is needed here — only message CONTENT
// needs bidi resolution, not app-owned labels.
export function DaySeparator({ label }: { label: string }) {
  return (
    <div
      role="separator"
      aria-label={label}
      style={{
        textAlign: "center",
        color: "var(--color-text-muted)",
        fontSize: "12px",
        margin: "var(--space-2) 0",
      }}
    >
      {label}
    </div>
  );
}
