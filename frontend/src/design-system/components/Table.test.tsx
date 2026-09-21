import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./Table";

describe("Table", () => {
  it("renders as real semantic table markup with column headers and cells", () => {
    render(
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Task</TableHead>
            <TableHead align="right">Due</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          <TableRow>
            <TableCell>Review credit analysis</TableCell>
            <TableCell align="right">10:00 AM</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );

    expect(screen.getByRole("table")).toBeInTheDocument();
    const columnHeaders = screen.getAllByRole("columnheader");
    expect(columnHeaders).toHaveLength(2);
    expect(columnHeaders[0]).toHaveTextContent("Task");
    expect(columnHeaders[0]).toHaveAttribute("scope", "col");

    expect(screen.getByRole("cell", { name: "Review credit analysis" })).toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(2); // header row + one body row
  });

  it("supports a row-header scope override for a labeled first column", () => {
    render(
      <Table>
        <TableBody>
          <TableRow>
            <TableHead scope="row">Monday</TableHead>
            <TableCell>Gym</TableCell>
          </TableRow>
        </TableBody>
      </Table>,
    );

    expect(screen.getByRole("rowheader", { name: "Monday" })).toBeInTheDocument();
  });

  // Hover-tint (:hover pseudo-class) and the overflow-x-auto responsive
  // wrapper are layout/pseudo-class concerns jsdom cannot reliably verify —
  // checked live via Playwright instead, same as color-mix() in Checkpoint
  // 1.3 and the DropdownMenu reopen behavior in Checkpoint 1.2.
});
