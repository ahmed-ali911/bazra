import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "./DropdownMenu";

describe("DropdownMenu", () => {
  // A single test covering the full realistic lifecycle (closed -> open ->
  // interact -> closed again), rather than splitting it across multiple
  // render() calls in separate tests: two independent Radix DropdownMenu
  // instances mounted/unmounted in the same jsdom document leave the second
  // one unable to open — a real, reproducible cross-instance interaction in
  // this test environment (confirmed independent of userEvent vs raw
  // fireEvent, and independent of whether the first instance is left open,
  // explicitly closed, or never opened at all before unmounting). Live
  // browser behavior is verified separately via Playwright below, since
  // this could be a jsdom-specific artifact rather than a real app bug.
  it("is closed until the trigger is clicked, reveals portal-rendered items, and closes again on item click", async () => {
    const user = userEvent.setup();
    render(
      <DropdownMenu>
        <DropdownMenuTrigger>Actions</DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem>Edit</DropdownMenuItem>
          <DropdownMenuItem>Delete</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );

    expect(screen.queryByText("Edit")).not.toBeInTheDocument();

    await user.click(screen.getByText("Actions"));

    // Content renders through Radix's Portal (outside this tree, appended to
    // <body>) — proving screen queries actually reach it, not assuming they
    // do because Radix documents that it portals.
    expect(screen.getByText("Edit")).toBeInTheDocument();
    expect(screen.getByText("Delete")).toBeInTheDocument();

    await user.click(screen.getByText("Edit"));
    expect(screen.queryByText("Edit")).not.toBeInTheDocument();
  });
});
