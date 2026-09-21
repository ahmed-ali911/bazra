import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("renders its children for each variant", () => {
    render(
      <>
        <StatusBadge variant="success">Done</StatusBadge>
        <StatusBadge variant="warning">At risk</StatusBadge>
        <StatusBadge variant="danger">Overdue</StatusBadge>
        <StatusBadge variant="info">Draft</StatusBadge>
      </>,
    );

    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.getByText("At risk")).toBeInTheDocument();
    expect(screen.getByText("Overdue")).toBeInTheDocument();
    expect(screen.getByText("Draft")).toBeInTheDocument();
  });

  // Background/text color computed via color-mix() is verified against a
  // real browser (Playwright), not asserted here — jsdom does not reliably
  // compute color-mix() output.
});
