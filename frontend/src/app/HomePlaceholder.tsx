import { Button } from "../design-system/components/Button";
import { Card } from "../design-system/components/Card";
import { Divider } from "../design-system/components/Divider";
import { StatusBadge } from "../design-system/components/StatusBadge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../design-system/components/Table";

// The "/" route's page content, extracted from the old Layout.tsx —
// AppShell now provides the sidebar/top-nav chrome around it.
export function HomePlaceholder() {
  return (
    <Card>
      <h1 className="text-[var(--color-text-heading)]">Welcome to BAZRA.</h1>
      <p className="text-[var(--color-text-body)]">Your personal AI companion.</p>
      <Button>Get started</Button>
      <div style={{ margin: "var(--space-4) 0" }}>
        <Divider />
      </div>
      <StatusBadge variant="success">On track</StatusBadge>
      <div style={{ marginTop: "var(--space-4)" }}>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Task</TableHead>
              <TableHead align="right">Due</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell>Review credit analysis model</TableCell>
              <TableCell align="right">10:00 AM</TableCell>
            </TableRow>
            <TableRow>
              <TableCell>German lesson A2</TableCell>
              <TableCell align="right">2:00 PM</TableCell>
            </TableRow>
          </TableBody>
        </Table>
      </div>
    </Card>
  );
}
