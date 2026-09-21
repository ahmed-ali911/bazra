import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "./DropdownMenu";

describe("DropdownMenu", () => {
  // KNOWN JSDOM-ONLY ARTIFACT — read before "fixing" this test's structure.
  //
  // Symptom: if a Radix DropdownMenu is rendered, opened, and unmounted in
  // one test, then a SECOND, separate DropdownMenu instance is rendered in
  // another test in this same file, that second instance becomes
  // permanently unable to open (trigger click/fireEvent has no effect,
  // aria-expanded stays "false"). Reproduces regardless of userEvent vs raw
  // fireEvent, and regardless of whether the first instance was left open,
  // explicitly closed via Escape, or never opened at all before unmounting.
  // Root cause was not fully isolated after substantial diagnosis (ruled
  // out: stale document.body pointer-events/scroll-lock attributes — these
  // do reset correctly between tests; effect-cleanup timing/flush delays;
  // userEvent-specific pointer-sequence simulation).
  //
  // Confirmed jsdom-specific, not a real app bug: during Checkpoint 1.2,
  // opening, closing, and reopening a DropdownMenu was verified live in an
  // actual browser via Playwright against the running dev server, and it
  // worked correctly every time — reopen included. Dialog and Tabs do NOT
  // show this issue (multiple renders across separate tests work fine for
  // both); it appears specific to DropdownMenu's Radix internals under
  // jsdom.
  //
  // DO NOT attempt to "fix" this by changing DropdownMenu.tsx's behavior
  // chasing a bug that only exists in this test environment. The correct
  // handling — and the reason this stays a single test rather than several
  // — is to cover the full lifecycle (closed -> open -> interact -> closed
  // again) within ONE render() per test/file, sidestepping the artifact
  // entirely. If you need multiple DropdownMenu render() calls in one file
  // for some future test, re-verify against a real browser first before
  // concluding there's a real regression.
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
