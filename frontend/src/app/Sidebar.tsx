import { NavLink } from "react-router-dom";

import { sidebarConfig, type SidebarItem } from "./sidebarConfig";

interface SidebarProps {
  items?: SidebarItem[];
}

export function Sidebar({ items = sidebarConfig }: SidebarProps) {
  const enabledItems = items.filter((item) => item.enabled);

  return (
    <nav aria-label="Sidebar" style={{ width: 240 }}>
      <div className="text-[var(--color-text-heading)]" style={{ padding: "var(--space-4)" }}>
        BAZRA
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {enabledItems.map((item) => {
          const Icon = item.icon;
          return (
            <li key={item.id}>
              <NavLink
                to={item.href}
                end
                className={({ isActive }) =>
                  [
                    "flex items-center gap-[var(--space-2)] px-[var(--space-4)] py-[var(--space-2)]",
                    isActive ? "bg-[var(--color-bg-accent-subtle)] text-accent" : "text-[var(--color-text-body)]",
                  ].join(" ")
                }
              >
                <Icon size={18} aria-hidden="true" />
                {item.label}
              </NavLink>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
