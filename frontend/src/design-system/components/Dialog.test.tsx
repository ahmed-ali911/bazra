import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "./Dialog";
import type { ComponentProps } from "react";

function renderDialog(dismissible?: ComponentProps<typeof DialogContent>["dismissible"]) {
  return render(
    <Dialog>
      <DialogTrigger>Open</DialogTrigger>
      <DialogContent dismissible={dismissible}>
        <DialogTitle>Confirm</DialogTitle>
        <DialogDescription>Are you sure?</DialogDescription>
      </DialogContent>
    </Dialog>,
  );
}

// Clicking the overlay (the dimmed backdrop) is the real "outside click" —
// Radix's modal Dialog intentionally makes the rest of the page inert
// (pointer-events: none) while open, so an unrelated sibling element is not
// a valid stand-in for "outside" the way it would be for a non-modal layer.
function clickOverlay(user: ReturnType<typeof userEvent.setup>) {
  return user.click(screen.getByTestId("dialog-overlay"));
}

describe("Dialog", () => {
  it("opens on trigger click and closes on Escape by default", async () => {
    const user = userEvent.setup();
    renderDialog();

    expect(screen.queryByText("Confirm")).not.toBeInTheDocument();

    await user.click(screen.getByText("Open"));
    expect(screen.getByText("Confirm")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByText("Confirm")).not.toBeInTheDocument();
  });

  it("closes on overlay click by default", async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByText("Open"));
    expect(screen.getByText("Confirm")).toBeInTheDocument();

    await clickOverlay(user);
    expect(screen.queryByText("Confirm")).not.toBeInTheDocument();
  });

  it("does not close on Escape or overlay click when dismissible is 'none'", async () => {
    const user = userEvent.setup();
    renderDialog("none");

    await user.click(screen.getByText("Open"));
    expect(screen.getByText("Confirm")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.getByText("Confirm")).toBeInTheDocument();

    await clickOverlay(user);
    expect(screen.getByText("Confirm")).toBeInTheDocument();
  });

  it("with 'escape-only', overlay click does not close but Escape does", async () => {
    const user = userEvent.setup();
    renderDialog("escape-only");

    await user.click(screen.getByText("Open"));
    await clickOverlay(user);
    expect(screen.getByText("Confirm")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByText("Confirm")).not.toBeInTheDocument();
  });
});
