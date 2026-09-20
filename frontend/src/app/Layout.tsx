import { Button } from "../design-system/components/Button";
import { Card } from "../design-system/components/Card";

// Placeholder shell: sidebar stub + content area. Replaced by the real sidebar
// config and layout system in Phase 1 — this only exists to prove routing,
// rendering, and the design token pipeline work end to end.
export function Layout() {
  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <nav aria-label="Sidebar" style={{ width: 240 }}>
        BAZRA
      </nav>
      <main style={{ flex: 1, padding: "var(--space-6)" }}>
        <Card>
          <h1 className="text-[var(--color-text-heading)]">Welcome to BAZRA.</h1>
          <p className="text-[var(--color-text-body)]">Your personal AI companion.</p>
          <Button>Get started</Button>
        </Card>
      </main>
    </div>
  );
}
