import type { HTMLAttributes } from "react";

// Horizontal only — Section 5 only discusses horizontal section dividers;
// a vertical variant isn't built until a real need appears. For a divider
// applied as a container's own border (e.g. under a header), use the
// tokens directly via style, as TopNav/AppShell already do — this
// component is for a standalone divider between sibling content blocks.
export function Divider({ className = "", ...props }: HTMLAttributes<HTMLHRElement>) {
  return (
    <hr
      className={className}
      style={{ border: "none", borderTop: "var(--divider-thickness) solid var(--divider-color)", margin: 0 }}
      {...props}
    />
  );
}
