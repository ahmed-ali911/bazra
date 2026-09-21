import { Search } from "lucide-react";

// Styled shell only — no search logic. There's nothing to search yet.
export function TopNav() {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        padding: "var(--space-4)",
        borderBottom: "var(--divider-thickness) solid var(--divider-color)",
      }}
    >
      <div style={{ position: "relative", flex: 1, maxWidth: 400 }}>
        <Search
          size={16}
          aria-hidden="true"
          className="text-[var(--color-text-muted)]"
          style={{ position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)" }}
        />
        <input
          type="search"
          aria-label="Search"
          placeholder="Search..."
          className="bg-[var(--color-bg-subtle)] text-[var(--color-text-body)] rounded-md"
          style={{ width: "100%", padding: "6px 8px 6px 30px", border: "none" }}
        />
      </div>
    </header>
  );
}
