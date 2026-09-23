import { useState } from "react";
import type { FormEvent } from "react";

import { Button } from "../../design-system/components/Button";
import { EmptyState } from "../../design-system/components/EmptyState";
import { ErrorState } from "../../design-system/components/ErrorState";
import { LoadingState } from "../../design-system/components/LoadingState";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../design-system/components/Table";
import { useLifeAreas } from "../life-areas/useLifeAreas";
import { useCreateTask } from "./useCreateTask";
import { useDeleteTask } from "./useDeleteTask";
import { useTasks } from "./useTasks";
import { useUpdateTask } from "./useUpdateTask";

// Minimum probe UI proving real CRUD against the real API — not a designed
// Tasks screen. Final composition waits for a written screen spec. due_at
// is editable here (not just at creation) because Calendar's agenda
// read-model depends on it changing through this real mutation path.
export function TasksProbe() {
  const [title, setTitle] = useState("");
  const [lifeAreaId, setLifeAreaId] = useState("");
  const [dueAt, setDueAt] = useState("");

  const { data: tasks, isPending, error, refetch } = useTasks();
  const { data: lifeAreas } = useLifeAreas();
  const createTask = useCreateTask();
  const updateTask = useUpdateTask();
  const deleteTask = useDeleteTask();

  function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!title.trim()) return;
    createTask.mutate(
      {
        title,
        life_area_id: lifeAreaId ? Number(lifeAreaId) : undefined,
        due_at: dueAt ? new Date(dueAt).toISOString() : undefined,
      },
      {
        onSuccess: () => {
          setTitle("");
          setDueAt("");
        },
      },
    );
  }

  if (isPending) {
    return <LoadingState message="Loading tasks…" />;
  }

  if (error) {
    return <ErrorState description="Couldn't load tasks." onRetry={() => refetch()} />;
  }

  return (
    <div>
      <form
        onSubmit={handleCreate}
        style={{ display: "flex", gap: "var(--space-2)", marginBottom: "var(--space-4)" }}
      >
        <input
          aria-label="New task title"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          className="rounded-md bg-[var(--color-bg-subtle)] text-[var(--color-text-body)]"
          style={{ padding: "8px 12px", border: "none", flex: 1 }}
        />
        <input
          type="datetime-local"
          aria-label="Due date"
          value={dueAt}
          onChange={(event) => setDueAt(event.target.value)}
          className="rounded-md bg-[var(--color-bg-subtle)] text-[var(--color-text-body)]"
          style={{ padding: "8px 12px", border: "none" }}
        />
        <select
          aria-label="Life area"
          value={lifeAreaId}
          onChange={(event) => setLifeAreaId(event.target.value)}
          className="rounded-md bg-[var(--color-bg-subtle)] text-[var(--color-text-body)]"
          style={{ padding: "8px 12px", border: "none" }}
        >
          <option value="">No life area</option>
          {lifeAreas?.map((area) => (
            <option key={area.id} value={area.id}>
              {area.name}
            </option>
          ))}
        </select>
        <Button type="submit">Add task</Button>
      </form>

      {tasks && tasks.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Done</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Due</TableHead>
              <TableHead align="right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tasks.map((task) => (
              <TableRow key={task.id}>
                <TableCell>
                  <input
                    type="checkbox"
                    aria-label={`Mark "${task.title}" as ${task.status === "done" ? "open" : "done"}`}
                    checked={task.status === "done"}
                    onChange={() =>
                      updateTask.mutate({ id: task.id, status: task.status === "done" ? "open" : "done" })
                    }
                  />
                </TableCell>
                <TableCell>{task.title}</TableCell>
                <TableCell>
                  <input
                    type="datetime-local"
                    aria-label={`Due date for "${task.title}"`}
                    defaultValue={task.due_at ? task.due_at.slice(0, 16) : ""}
                    onBlur={(event) =>
                      updateTask.mutate({
                        id: task.id,
                        due_at: event.target.value ? new Date(event.target.value).toISOString() : null,
                      })
                    }
                    className="rounded-md bg-[var(--color-bg-subtle)] text-[var(--color-text-body)]"
                    style={{ padding: "4px 8px", border: "none" }}
                  />
                </TableCell>
                <TableCell align="right">
                  <button
                    type="button"
                    aria-label={`Delete "${task.title}"`}
                    onClick={() => deleteTask.mutate(task.id)}
                    className="text-[var(--color-status-danger)]"
                  >
                    Delete
                  </button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <EmptyState title="No tasks yet" description="Add one above to get started." />
      )}
    </div>
  );
}
