// Placeholder shell: sidebar stub + content area. Replaced by the real sidebar
// config and layout system in Phase 1 — this only exists to prove routing and
// rendering work end to end.
export function Layout() {
  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <nav aria-label="Sidebar" style={{ width: 240 }}>
        BAZRA
      </nav>
      <main style={{ flex: 1 }}>
        <p>Welcome to BAZRA.</p>
      </main>
    </div>
  );
}
