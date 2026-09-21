import { BrowserRouter, Route, Routes } from "react-router-dom";

import { AppShell } from "./AppShell";
import { HomePlaceholder } from "./HomePlaceholder";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<HomePlaceholder />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
