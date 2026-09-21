import * as RadixDropdownMenu from "@radix-ui/react-dropdown-menu";
import { forwardRef } from "react";

// Thin styled wrappers around @radix-ui/react-dropdown-menu. Radix's
// DropdownMenu is click/keyboard-activated by design (not hover — that's a
// different primitive meant for navigation), so there's nothing to configure
// for the trigger behavior. Portal rendering, focus trapping, and ARIA are
// all handled internally by Radix.

export const DropdownMenu = RadixDropdownMenu.Root;
export const DropdownMenuTrigger = RadixDropdownMenu.Trigger;

export const DropdownMenuContent = forwardRef<HTMLDivElement, RadixDropdownMenu.DropdownMenuContentProps>(
  ({ className = "", sideOffset = 4, ...props }, ref) => (
    <RadixDropdownMenu.Portal>
      <RadixDropdownMenu.Content
        ref={ref}
        sideOffset={sideOffset}
        className={[
          "bg-[var(--color-bg-default)] rounded-md shadow-md",
          "min-w-[160px] p-[var(--space-2)]",
          className,
        ].join(" ")}
        {...props}
      />
    </RadixDropdownMenu.Portal>
  ),
);
DropdownMenuContent.displayName = "DropdownMenuContent";

export const DropdownMenuItem = forwardRef<HTMLDivElement, RadixDropdownMenu.DropdownMenuItemProps>(
  ({ className = "", ...props }, ref) => (
    <RadixDropdownMenu.Item
      ref={ref}
      className={[
        "px-[var(--space-2)] py-[var(--space-2)] rounded-md text-[var(--color-text-body)]",
        // Reuses the exact tinted-hover language already established for the
        // active sidebar item, not a new pattern invented here.
        "data-[highlighted]:bg-[var(--color-bg-accent-subtle)] data-[highlighted]:text-accent",
        "outline-none cursor-pointer",
        className,
      ].join(" ")}
      {...props}
    />
  ),
);
DropdownMenuItem.displayName = "DropdownMenuItem";

export const DropdownMenuSeparator = forwardRef<HTMLDivElement, RadixDropdownMenu.DropdownMenuSeparatorProps>(
  ({ className = "", ...props }, ref) => (
    <RadixDropdownMenu.Separator
      ref={ref}
      className={className}
      style={{ borderTop: "var(--divider-thickness) solid var(--divider-color)", margin: "var(--space-2) 0" }}
      {...props}
    />
  ),
);
DropdownMenuSeparator.displayName = "DropdownMenuSeparator";
