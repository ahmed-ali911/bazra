import { Link } from "react-router-dom";

import { Card } from "../../design-system/components/Card";
import type { UnassignedSummary } from "./types";

// Structurally separate from LifeAreaCard — not a LifeArea with a hidden
// rename/delete affordance, but a different component that never renders
// one at all, since Unassigned isn't a Life Area row and has neither an
// id nor a slug to rename or delete.
export function UnassignedCard({ summary }: { summary: UnassignedSummary }) {
  return (
    <Card>
      <Link to="/my-world/unassigned">
        <h3 className="text-[var(--color-text-heading)]">Unassigned</h3>
      </Link>
      <p className="text-[var(--color-text-body)]">{summary.open_task_count} open</p>
      <p className="text-[var(--color-text-muted)]">
        {summary.most_urgent_due_at
          ? `Most urgent: ${new Date(summary.most_urgent_due_at).toLocaleDateString()}`
          : "Nothing urgent"}
      </p>
    </Card>
  );
}
