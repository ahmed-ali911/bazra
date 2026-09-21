import { render, screen } from "@testing-library/react";
import { Home, Settings } from "lucide-react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { Sidebar } from "./Sidebar";
import type { SidebarItem } from "./sidebarConfig";

const testItems: SidebarItem[] = [
  { id: "home", label: "Home", href: "/", icon: Home, enabled: true },
  { id: "hidden", label: "Hidden Module", href: "/hidden", icon: Settings, enabled: false },
];

describe("Sidebar", () => {
  it("renders enabled items and omits disabled items entirely from the output", () => {
    render(
      <MemoryRouter>
        <Sidebar items={testItems} />
      </MemoryRouter>,
    );

    expect(screen.getByText("Home")).toBeInTheDocument();
    // The real assertion: a disabled item is absent from the rendered DOM,
    // not merely that rendering with one present didn't crash.
    expect(screen.queryByText("Hidden Module")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /hidden module/i })).not.toBeInTheDocument();
  });

  it("applies the active-item treatment to the current route", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <Sidebar items={testItems} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: /home/i })).toHaveClass("text-accent");
  });
});
