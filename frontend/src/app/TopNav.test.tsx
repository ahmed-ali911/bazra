import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TopNav } from "./TopNav";

describe("TopNav", () => {
  it("renders a search input", () => {
    render(<TopNav />);
    expect(screen.getByRole("searchbox", { name: "Search" })).toBeInTheDocument();
  });
});
