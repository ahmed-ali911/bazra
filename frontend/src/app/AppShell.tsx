import { Outlet } from "react-router-dom";

import { Sidebar } from "./Sidebar";
import { TopNav } from "./TopNav";

// Persistent chrome for every route: sidebar + top nav + routed content.
// Replaces the old Layout.tsx placeholder — see the layout-route + <Outlet>
// restructure in App.tsx.
export function AppShell() {
  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <Sidebar />
      <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
        <TopNav />
        <main style={{ flex: 1, padding: "var(--space-6)" }}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
