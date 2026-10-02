import { Outlet } from "react-router-dom";

import { useAppOpenedTrigger } from "../features/attention/useAppOpenedTrigger";
import { Sidebar } from "./Sidebar";
import { TopNav } from "./TopNav";

// Persistent chrome for every route: sidebar + top nav + routed content.
// Replaces the old Layout.tsx placeholder — see the layout-route + <Outlet>
// restructure in App.tsx.
//
// Checkpoint 4.5e: this is also the V1 APP_OPENED trigger point —
// AppShell mounts exactly once per authenticated session (it sits
// right after RequireAuth's own gate in App.tsx) and persists across
// Home/My World/Chat navigation via its own <Outlet> below, never
// remounting on route change. That makes it exactly the "fresh App
// mount, evaluate once" boundary useAppOpenedTrigger's own docstring
// describes — never a per-page trigger.
export function AppShell() {
  useAppOpenedTrigger();

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
