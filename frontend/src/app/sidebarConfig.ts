import { Globe, Home } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface SidebarItem {
  id: string;
  label: string;
  href: string;
  icon: LucideIcon;
  enabled: boolean;
}

// Typed configuration array, not a database table — see Section 9 of the
// brief. Populated with exactly the routes that currently exist; adding a
// real module later (Tasks, Calendar, ...) is purely additive — appending an
// entry here, nothing that consumes this array needs to change.
export const sidebarConfig: SidebarItem[] = [
  { id: "home", label: "Home", href: "/", icon: Home, enabled: true },
  { id: "my-world", label: "My World", href: "/my-world", icon: Globe, enabled: true },
];
