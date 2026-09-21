import * as RadixTabs from "@radix-ui/react-tabs";
import { forwardRef } from "react";

// Thin styled wrappers around @radix-ui/react-tabs — Radix owns behavior
// (roving-tabindex arrow-key navigation between triggers is automatic, no
// configuration needed), we only supply token-driven styling. Consumers
// compose these exactly like raw Radix: <Tabs><TabsList><TabsTrigger .../>

export const Tabs = RadixTabs.Root;

export const TabsList = forwardRef<HTMLDivElement, RadixTabs.TabsListProps>(
  ({ className = "", ...props }, ref) => (
    <RadixTabs.List
      ref={ref}
      className={["flex", className].join(" ")}
      style={{ borderBottom: "var(--divider-thickness) solid var(--divider-color)" }}
      {...props}
    />
  ),
);
TabsList.displayName = "TabsList";

export const TabsTrigger = forwardRef<HTMLButtonElement, RadixTabs.TabsTriggerProps>(
  ({ className = "", ...props }, ref) => (
    <RadixTabs.Trigger
      ref={ref}
      className={[
        "px-[var(--space-4)] py-[var(--space-2)] text-[var(--color-text-muted)]",
        "data-[state=active]:text-accent",
        // Underline reuses the exact same divider token as TabsList's own
        // bottom border, drawn as an inset shadow so it doesn't shift layout.
        "data-[state=active]:shadow-[inset_0_calc(var(--divider-thickness)*-1)_0_0_var(--color-accent)]",
        className,
      ].join(" ")}
      {...props}
    />
  ),
);
TabsTrigger.displayName = "TabsTrigger";

export const TabsContent = forwardRef<HTMLDivElement, RadixTabs.TabsContentProps>(
  ({ className = "", ...props }, ref) => (
    <RadixTabs.Content ref={ref} className={["pt-[var(--space-4)]", className].join(" ")} {...props} />
  ),
);
TabsContent.displayName = "TabsContent";
