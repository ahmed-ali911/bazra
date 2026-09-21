import { Loader2 } from "lucide-react";

interface LoadingStateProps {
  message?: string;
}

export function LoadingState({ message = "Loading…" }: LoadingStateProps) {
  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: "var(--space-2)",
        padding: "var(--space-6)",
      }}
    >
      <Loader2 className="animate-spin text-[var(--color-text-muted)]" size={24} aria-hidden="true" />
      <p className="text-[var(--color-text-muted)]">{message}</p>
    </div>
  );
}
