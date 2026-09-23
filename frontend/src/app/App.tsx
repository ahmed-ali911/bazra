import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { LifeAreaDetailPage } from "../features/life-areas/LifeAreaDetailPage";
import { MyWorldPage } from "../features/life-areas/MyWorldPage";
import { AppShell } from "./AppShell";
import { HomePlaceholder } from "./HomePlaceholder";
import { LoginPage } from "./LoginPage";
import { RequireAuth } from "./RequireAuth";

export function App() {
  // Created in component state, not module scope: a fresh QueryClient per
  // mount keeps test renders isolated from each other (no cache leaking
  // between tests), while the real app only ever mounts App once anyway.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: 1, refetchOnWindowFocus: false },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireAuth />}>
            <Route element={<AppShell />}>
              <Route path="/" element={<HomePlaceholder />} />
              <Route path="/my-world" element={<MyWorldPage />} />
              <Route path="/my-world/:lifeAreaId" element={<LifeAreaDetailPage />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
