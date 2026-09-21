import type { HTMLAttributes } from "react";

export type StatusVariant = "success" | "warning" | "danger" | "info";

interface StatusBadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant: StatusVariant;
}

// Named StatusBadge, not the generic "Badge" — this is the four SYSTEM
// STATUS colors only (success/warning/danger/info). Category/life-area
// tags (Work, Learning, Personal, ...) are a separate semantic system with
// no palette of their own yet — see the project memory on BAZRA's visual
// direction. Do not extend this component's variant union with category
// values; a CategoryTag component, if/when one is needed, stays separate.
//
// Background and text both derive from the same single --color-status-*
// token via color-mix() — a systematic derivation, not four hand-picked
// "light" hex values. jsdom does not reliably compute color-mix(), so this
// is verified against real computed styles in a live browser, not a jsdom
// test assertion.
export function StatusBadge({ variant, className = "", style, children, ...props }: StatusBadgeProps) {
  return (
    <span
      className={[
        "inline-flex items-center rounded-md px-[var(--space-2)] py-[2px] text-sm font-medium",
        className,
      ].join(" ")}
      style={{
        backgroundColor: `color-mix(in srgb, var(--color-status-${variant}) 12%, transparent)`,
        color: `var(--color-status-${variant})`,
        ...style,
      }}
      {...props}
    >
      {children}
    </span>
  );
}
