import * as RadixDialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { forwardRef } from "react";
import type { ComponentPropsWithoutRef, ElementRef } from "react";

export const Dialog = RadixDialog.Root;
export const DialogTrigger = RadixDialog.Trigger;

export const DialogTitle = forwardRef<HTMLHeadingElement, RadixDialog.DialogTitleProps>(
  ({ className = "", ...props }, ref) => (
    <RadixDialog.Title ref={ref} className={["text-[var(--color-text-heading)]", className].join(" ")} {...props} />
  ),
);
DialogTitle.displayName = "DialogTitle";

export const DialogDescription = forwardRef<HTMLParagraphElement, RadixDialog.DialogDescriptionProps>(
  ({ className = "", ...props }, ref) => (
    <RadixDialog.Description
      ref={ref}
      className={["text-[var(--color-text-body)]", className].join(" ")}
      {...props}
    />
  ),
);
DialogDescription.displayName = "DialogDescription";

// "both" (Radix's own default) is right for most dialogs. "escape-only" and
// "none" are for cases like a confirmation on a destructive action, or an
// in-progress operation that genuinely shouldn't be dismissible until it
// completes — "none" isn't used by anything yet, included now because
// extending this union later, once components depend on a two-value
// version, is more expensive than adding the third value up front.
type DismissMode = "both" | "escape-only" | "none";

interface DialogContentProps extends ComponentPropsWithoutRef<typeof RadixDialog.Content> {
  dismissible?: DismissMode;
}

export const DialogContent = forwardRef<ElementRef<typeof RadixDialog.Content>, DialogContentProps>(
  ({ className = "", dismissible = "both", onEscapeKeyDown, onPointerDownOutside, children, ...props }, ref) => {
    const blockEscape = dismissible === "none";
    const blockOutsideClick = dismissible === "none" || dismissible === "escape-only";

    return (
      <RadixDialog.Portal>
        <RadixDialog.Overlay
          data-testid="dialog-overlay"
          className="fixed inset-0"
          style={{ backgroundColor: "rgba(18, 56, 71, 0.4)" }}
        />
        <RadixDialog.Content
          ref={ref}
          onEscapeKeyDown={(event) => {
            if (blockEscape) event.preventDefault();
            onEscapeKeyDown?.(event);
          }}
          onPointerDownOutside={(event) => {
            if (blockOutsideClick) event.preventDefault();
            onPointerDownOutside?.(event);
          }}
          className={[
            "fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2",
            "bg-[var(--color-bg-default)] rounded-lg shadow-md p-[var(--space-6)]",
            "w-full max-w-[480px]",
            className,
          ].join(" ")}
          {...props}
        >
          {children}
          <RadixDialog.Close
            aria-label="Close"
            className="absolute top-[var(--space-4)] right-[var(--space-4)] text-[var(--color-text-muted)]"
          >
            <X size={18} aria-hidden="true" />
          </RadixDialog.Close>
        </RadixDialog.Content>
      </RadixDialog.Portal>
    );
  },
);
DialogContent.displayName = "DialogContent";
