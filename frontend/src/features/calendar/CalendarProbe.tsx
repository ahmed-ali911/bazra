import { useState } from "react";
import type { FormEvent } from "react";

import { Button } from "../../design-system/components/Button";
import { EmptyState } from "../../design-system/components/EmptyState";
import { ErrorState } from "../../design-system/components/ErrorState";
import { LoadingState } from "../../design-system/components/LoadingState";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../design-system/components/Table";
import { useAgenda } from "./useAgenda";
import { useCreateCalendarEvent } from "./useCreateCalendarEvent";
import { useDeleteCalendarEvent } from "./useDeleteCalendarEvent";

function defaultRange() {
  const from = new Date();
  from.setHours(0, 0, 0, 0);
  const to = new Date(from);
  to.setDate(to.getDate() + 30);
  return { from: from.toISOString(), to: to.toISOString() };
}

// Minimum probe UI proving the real agenda read-model against the real API
// — not a designed Calendar screen. No grid/month view; a flat list is
// enough to verify aggregation, source tagging, and live invalidation.
export function CalendarProbe() {
  const [range] = useState(defaultRange);
  const [title, setTitle] = useState("");
  const [startsAt, setStartsAt] = useState("");

  const { data: items, isPending, error, refetch } = useAgenda(range);
  const createEvent = useCreateCalendarEvent();
  const deleteEvent = useDeleteCalendarEvent();

  function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!title.trim() || !startsAt) return;
    createEvent.mutate(
      { title, starts_at: new Date(startsAt).toISOString() },
      {
        onSuccess: () => {
          setTitle("");
          setStartsAt("");
        },
      },
    );
  }

  if (isPending) {
    return <LoadingState message="Loading calendar…" />;
  }

  if (error) {
    return <ErrorState description="Couldn't load the calendar." onRetry={() => refetch()} />;
  }

  return (
    <div>
      <form
        onSubmit={handleCreate}
        style={{ display: "flex", gap: "var(--space-2)", marginBottom: "var(--space-4)" }}
      >
        <input
          aria-label="New event title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          className="rounded-md bg-[var(--color-bg-subtle)] text-[var(--color-text-body)]"
          style={{ padding: "8px 12px", border: "none", flex: 1 }}
        />
        <input
          type="datetime-local"
          aria-label="Event start"
          value={startsAt}
          onChange={(event) => setStartsAt(event.target.value)}
          className="rounded-md bg-[var(--color-bg-subtle)] text-[var(--color-text-body)]"
          style={{ padding: "8px 12px", border: "none" }}
        />
        <Button type="submit">Add event</Button>
      </form>

      {items && items.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Source</TableHead>
              <TableHead>Title</TableHead>
              <TableHead align="right">When</TableHead>
              <TableHead align="right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={`${item.source}-${item.id}`}>
                <TableCell>{item.source}</TableCell>
                <TableCell>{item.title}</TableCell>
                <TableCell align="right">{new Date(item.starts_at).toLocaleString()}</TableCell>
                <TableCell align="right">
                  {item.source === "event" ? (
                    <button
                      type="button"
                      aria-label={`Delete "${item.title}"`}
                      onClick={() => deleteEvent.mutate(item.id)}
                      className="text-[var(--color-status-danger)]"
                    >
                      Delete
                    </button>
                  ) : null}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <EmptyState title="Nothing on the calendar" description="Add an event above, or a Task with a due date." />
      )}
    </div>
  );
}
