import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "./DropdownMenu";

// KNOWN JSDOM-ONLY ARTIFACT — read before "fixing" this test's structure.
//
// Symptom: if a Radix DropdownMenu is rendered, opened, and unmounted in one
// test, then a SECOND, separate DropdownMenu instance is rendered in another
// test in this same file, that second instance becomes permanently unable
// to open (trigger click/fireEvent has no effect, aria-expanded stays
// "false"). Reproduces regardless of userEvent vs raw fireEvent, and
// regardless of whether the first instance was left open, explicitly closed
// via Escape, or never opened at all before unmounting. Root cause was not
// fully isolated after substantial diagnosis in Checkpoint 1.2 (ruled out:
// stale document.body pointer-events/scroll-lock attributes — these do
// reset correctly between tests; effect-cleanup timing/flush delays;
// userEvent-specific pointer-sequence simulation). Re-confirmed in
// Checkpoint 1.5: adding a second, separate test with its own render() for
// DropdownMenuCheckboxItem reproduced this exact symptom immediately.
//
// Confirmed jsdom-specific, not a real app bug: during Checkpoint 1.2,
// opening, closing, and reopening a DropdownMenu was verified live in an
// actual browser via Playwright against the running dev server, and it
// worked correctly every time — reopen included. Dialog and Tabs do NOT
// show this issue (multiple renders across separate tests work fine for
// both); it appears specific to DropdownMenu's Radix internals under jsdom.
//
// DO NOT attempt to "fix" this by changing DropdownMenu.tsx's behavior
// chasing a bug that only exists in this test environment. The correct
// handling — and the reason this file is ONE test covering both
// DropdownMenuItem and DropdownMenuCheckboxItem rather than several — is to
// cover the full lifecycle within ONE render() per file, sidestepping the
// artifact entirely. If you need another DropdownMenu render() in this
// file, re-verify against a real browser first before concluding there's a
// real regression.
describe("DropdownMenu", () => {
  it("supports both regular items and multi-select checkbox filtering in one menu", async () => {
    const user = userEvent.setup();

    function TestMenu() {
      const [checked, setChecked] = useState<Record<string, boolean>>({});
      return (
        <DropdownMenu>
          <DropdownMenuTrigger>Actions</DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuItem>Edit</DropdownMenuItem>
            <DropdownMenuCheckboxItem
              checked={checked.open ?? false}
              onCheckedChange={(value) => setChecked((prev) => ({ ...prev, open: value }))}
            >
              Open
            </DropdownMenuCheckboxItem>
            <DropdownMenuCheckboxItem
              checked={checked.done ?? false}
              onCheckedChange={(value) => setChecked((prev) => ({ ...prev, done: value }))}
            >
              Done
            </DropdownMenuCheckboxItem>
          </DropdownMenuContent>
        </DropdownMenu>
      );
    }

    render(<TestMenu />);

    // Closed until the trigger is clicked, then reveals portal-rendered
    // items — content renders through Radix's Portal (outside this tree,
    // appended to <body>), proving screen queries actually reach it, not
    // assuming they do because Radix documents that it portals.
    expect(screen.queryByText("Edit")).not.toBeInTheDocument();
    await user.click(screen.getByText("Actions"));
    expect(screen.getByText("Edit")).toBeInTheDocument();

    const openItem = screen.getByRole("menuitemcheckbox", { name: "Open" });
    const doneItem = screen.getByRole("menuitemcheckbox", { name: "Done" });

    // (1) checked-state updates, and selecting a checkbox does NOT close
    // the menu (unlike a regular item — that's the whole point).
    expect(openItem).toHaveAttribute("aria-checked", "false");
    await user.click(openItem);
    expect(openItem).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Done")).toBeInTheDocument();

    // (2) a second selection accumulates rather than replacing the first.
    await user.click(doneItem);
    expect(doneItem).toHaveAttribute("aria-checked", "true");
    expect(openItem).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Open")).toBeInTheDocument();

    // (3) unchecking one selected option leaves the other untouched.
    await user.click(openItem);
    expect(openItem).toHaveAttribute("aria-checked", "false");
    expect(doneItem).toHaveAttribute("aria-checked", "true");
    expect(screen.getByText("Done")).toBeInTheDocument();

    // (4) keyboard interaction + (5) focus behavior: Space toggles the
    // focused checkbox item, and focus stays on it rather than being lost
    // or moved by the preventDefault() override in onSelect.
    openItem.focus();
    expect(document.activeElement).toBe(openItem);
    await user.keyboard(" ");
    expect(openItem).toHaveAttribute("aria-checked", "true");
    expect(document.activeElement).toBe(openItem);
    expect(screen.getByText("Done")).toBeInTheDocument();

    // (6) a regular DropdownMenuItem in the same menu still closes on
    // click, unaffected by the checkbox item's overridden onSelect.
    await user.click(screen.getByText("Edit"));
    expect(screen.queryByText("Edit")).not.toBeInTheDocument();
  });
});
