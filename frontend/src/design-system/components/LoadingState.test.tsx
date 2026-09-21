import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LoadingState } from "./LoadingState";

describe("LoadingState", () => {
  it("renders as an announced status region with a default message", () => {
    render(<LoadingState />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading…");
  });

  it("accepts a custom message", () => {
    render(<LoadingState message="Fetching tasks…" />);
    expect(screen.getByRole("status")).toHaveTextContent("Fetching tasks…");
  });
});
