import { AlertCircle } from "lucide-react";

import { Button } from "./Button";

interface ErrorStateProps {
  title?: string;
  description?: string;
  onRetry?: () => void;
}

export function ErrorState({ title = "Something went wrong", description, onRetry }: ErrorStateProps) {
  return (
    <div
      role="alert"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        textAlign: "center",
        gap: "var(--space-2)",
        padding: "var(--space-6)",
      }}
    >
      <AlertCircle size={32} className="text-[var(--color-status-danger)]" aria-hidden="true" />
      <h3 className="text-[var(--color-text-heading)]">{title}</h3>
      {description ? <p className="text-[var(--color-text-muted)]">{description}</p> : null}
      {onRetry ? <Button onClick={onRetry}>Try again</Button> : null}
    </div>
  );
}
