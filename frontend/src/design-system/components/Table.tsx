import type { TableHTMLAttributes, HTMLAttributes, ThHTMLAttributes, TdHTMLAttributes } from "react";

type Align = "left" | "right" | "center";

const alignClass: Record<Align, string> = {
  left: "text-left",
  right: "text-right",
  center: "text-center",
};

// Real semantic <table> elements throughout — screen-reader table
// navigation comes from correct HTML, not ARIA patched on top. Wrapped in
// overflow-x-auto for narrow viewports; no sorting/pagination (see
// Table.tsx's companion decisions in the Checkpoint 1.4 proposal — those
// wait for a real screen to define the actual requirement) and no
// isLoading/empty props (composition happens at the call site: render
// <Table> or <LoadingState>/<EmptyState> instead of it, not both).

export function Table({ className = "", ...props }: TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="overflow-x-auto">
      <table className={["w-full border-collapse", className].join(" ")} {...props} />
    </div>
  );
}

export function TableHeader({ className = "", ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return (
    <thead
      className={className}
      style={{ borderBottom: "var(--divider-thickness) solid var(--divider-color)" }}
      {...props}
    />
  );
}

export function TableBody({ className = "", ...props }: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className={className} {...props} />;
}

export function TableRow({ className = "", ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr className={["hover:bg-[var(--color-bg-accent-subtle)]", className].join(" ")} {...props} />
  );
}

interface TableHeadProps extends ThHTMLAttributes<HTMLTableCellElement> {
  align?: Align;
}

export function TableHead({ className = "", align = "left", scope = "col", ...props }: TableHeadProps) {
  return (
    <th
      scope={scope}
      className={[
        "px-[var(--space-4)] py-[var(--space-2)] text-[var(--color-text-muted)] text-sm font-medium",
        alignClass[align],
        className,
      ].join(" ")}
      {...props}
    />
  );
}

interface TableCellProps extends TdHTMLAttributes<HTMLTableCellElement> {
  align?: Align;
}

export function TableCell({ className = "", align = "left", ...props }: TableCellProps) {
  return (
    <td
      className={[
        "px-[var(--space-4)] py-[var(--space-2)] text-[var(--color-text-body)]",
        alignClass[align],
        className,
      ].join(" ")}
      {...props}
    />
  );
}
