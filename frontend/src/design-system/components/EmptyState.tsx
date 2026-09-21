import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({ icon: Icon, title, description, action }: EmptyStateProps) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        textAlign: "center",
        gap: "var(--space-2)",
        padding: "var(--space-6)",
      }}
    >
      {Icon ? <Icon size={32} className="text-[var(--color-text-muted)]" aria-hidden="true" /> : null}
      <h3 className="text-[var(--color-text-heading)]">{title}</h3>
      {description ? <p className="text-[var(--color-text-muted)]">{description}</p> : null}
      {action}
    </div>
  );
}
