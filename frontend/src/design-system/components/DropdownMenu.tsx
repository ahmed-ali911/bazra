import * as RadixDropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check } from "lucide-react";
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

// For multi-select filtering. Unlike DropdownMenuItem, selecting a checkbox
// does NOT close the menu — Radix's default (close on select, same as a
// regular item) would make picking more than one filter value painfully
// tedious. This is the only behavioral difference from DropdownMenuItem;
// checked-state, keyboard nav, and focus handling stay entirely Radix's own.
export const DropdownMenuCheckboxItem = forwardRef<
  HTMLDivElement,
  RadixDropdownMenu.DropdownMenuCheckboxItemProps
>(({ className = "", onSelect, children, ...props }, ref) => (
  <RadixDropdownMenu.CheckboxItem
    ref={ref}
    onSelect={(event) => {
      event.preventDefault();
      onSelect?.(event);
    }}
    className={[
      "flex items-center gap-[var(--space-2)] px-[var(--space-2)] py-[var(--space-2)] rounded-md text-[var(--color-text-body)]",
      "data-[highlighted]:bg-[var(--color-bg-accent-subtle)] data-[highlighted]:text-accent",
      "outline-none cursor-pointer",
      className,
    ].join(" ")}
    {...props}
  >
    {/* Fixed-width slot so checked/unchecked items in the same menu don't
        shift their label text depending on whether the check mark renders. */}
    <span style={{ width: 14, display: "inline-flex" }}>
      <RadixDropdownMenu.ItemIndicator>
        <Check size={14} aria-hidden="true" />
      </RadixDropdownMenu.ItemIndicator>
    </span>
    {children}
  </RadixDropdownMenu.CheckboxItem>
));
DropdownMenuCheckboxItem.displayName = "DropdownMenuCheckboxItem";

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
