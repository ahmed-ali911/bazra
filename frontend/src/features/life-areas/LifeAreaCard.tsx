import { useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";

import { Button } from "../../design-system/components/Button";
import { Card } from "../../design-system/components/Card";
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from "../../design-system/components/Dialog";
import { ApiError } from "../../services/api";
import { useDeleteLifeArea } from "./useDeleteLifeArea";
import { useRenameLifeArea } from "./useRenameLifeArea";
import type { LifeAreaSummary } from "./types";

interface BlockedDeletionDetail {
  error: string;
  task_count: number;
  calendar_event_count: number;
  total_count: number;
}

function isBlockedDeletionDetail(detail: unknown): detail is BlockedDeletionDetail {
  return typeof detail === "object" && detail !== null && "error" in detail && (detail as { error: unknown }).error === "life_area_has_linked_items";
}

// A real Life Area — distinct from UnassignedCard, which deliberately
// offers no rename/delete affordance at all since Unassigned isn't a
// Life Area row.
export function LifeAreaCard({ area }: { area: LifeAreaSummary }) {
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [name, setName] = useState(area.name);

  const renameLifeArea = useRenameLifeArea();
  const deleteLifeArea = useDeleteLifeArea();

  function handleRename(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    renameLifeArea.mutate(
      { id: area.id, name },
      { onSuccess: () => setRenameOpen(false) },
    );
  }

  const blockedDetail =
    deleteLifeArea.error instanceof ApiError && isBlockedDeletionDetail(deleteLifeArea.error.detail)
      ? deleteLifeArea.error.detail
      : null;

  return (
    <Card>
      <Link to={`/my-world/${area.id}`}>
        <h3 className="text-[var(--color-text-heading)]">{area.name}</h3>
      </Link>
      <p className="text-[var(--color-text-body)]">{area.open_task_count} open</p>
      <p className="text-[var(--color-text-muted)]">
        {area.most_urgent_due_at
          ? `Most urgent: ${new Date(area.most_urgent_due_at).toLocaleDateString()}`
          : "Nothing urgent"}
      </p>

      <div style={{ display: "flex", gap: "var(--space-2)", marginTop: "var(--space-2)" }}>
        <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
          <DialogTrigger asChild>
            <button type="button" aria-label={`Rename "${area.name}"`}>
              Rename
            </button>
          </DialogTrigger>
          <DialogContent>
            <DialogTitle>Rename life area</DialogTitle>
            <DialogDescription>Choose a new name for "{area.name}".</DialogDescription>
            <form onSubmit={handleRename} style={{ display: "flex", gap: "var(--space-2)", marginTop: "var(--space-3)" }}>
              <input
                aria-label="New name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="rounded-md bg-[var(--color-bg-subtle)] text-[var(--color-text-body)]"
                style={{ padding: "8px 12px", border: "none", flex: 1 }}
              />
              <Button type="submit">Save</Button>
            </form>
          </DialogContent>
        </Dialog>

        <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
          <DialogTrigger asChild>
            <button type="button" aria-label={`Delete "${area.name}"`} className="text-[var(--color-status-danger)]">
              Delete
            </button>
          </DialogTrigger>
          <DialogContent>
            <DialogTitle>Delete "{area.name}"?</DialogTitle>
            {blockedDetail ? (
              <DialogDescription>
                Can't delete — {blockedDetail.total_count} item{blockedDetail.total_count === 1 ? "" : "s"} still
                linked ({blockedDetail.task_count} task{blockedDetail.task_count === 1 ? "" : "s"},{" "}
                {blockedDetail.calendar_event_count} calendar event{blockedDetail.calendar_event_count === 1 ? "" : "s"}
                ). <Link to={`/my-world/${area.id}`}>Move them first</Link>.
              </DialogDescription>
            ) : (
              <DialogDescription>This can't be undone.</DialogDescription>
            )}
            <div style={{ display: "flex", gap: "var(--space-2)", marginTop: "var(--space-3)" }}>
              <Button
                type="button"
                onClick={() =>
                  deleteLifeArea.mutate(area.id, { onSuccess: () => setDeleteOpen(false) })
                }
              >
                Confirm delete
              </Button>
            </div>
          </DialogContent>
        </Dialog>
      </div>
    </Card>
  );
}
