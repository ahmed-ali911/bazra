import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("renders the app shell without crashing", () => {
    render(<App />);
    expect(screen.getByRole("navigation", { name: "Sidebar" })).toBeInTheDocument();
    expect(screen.getByText("Welcome to BAZRA.")).toBeInTheDocument();
  });
});
