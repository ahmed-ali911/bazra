import { useParams } from "react-router-dom";

import { EmptyState } from "../../design-system/components/EmptyState";
import { ErrorState } from "../../design-system/components/ErrorState";
import { LoadingState } from "../../design-system/components/LoadingState";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../design-system/components/Table";
import { useUpdateTask } from "../tasks/useUpdateTask";
import { useTasks } from "../tasks/useTasks";
import { useLifeAreas } from "./useLifeAreas";

// Real, navigable per-area task list (Checkpoint 2.5b) — reused for both
// a real Life Area (/my-world/:id) and Unassigned (/my-world/unassigned,
// where :lifeAreaId is literally the string "unassigned"). Shows ALL
// non-archived tasks in the area (open AND done), not just open ones —
// deliberately broader than the open-only count shown on My World's
// cards, since the main reason to open an area is to move tasks out of
// it (including a done task that's still blocking deletion), not just
// to see what's outstanding.
export function LifeAreaDetailPage() {
  const { lifeAreaId } = useParams<{ lifeAreaId: string }>();
  const isUnassigned = lifeAreaId === "unassigned";
  const numericId = isUnassigned ? undefined : Number(lifeAreaId);

  const { data: lifeAreas } = useLifeAreas();
  const area = numericId !== undefined ? lifeAreas?.find((a) => a.id === numericId) : undefined;

  const { data: tasks, isPending, error, refetch } = useTasks(
    isUnassigned ? { unassigned: true } : { life_area_id: numericId },
  );
  const updateTask = useUpdateTask();

  const heading = isUnassigned ? "Unassigned" : (area?.name ?? "Life area");

  if (isPending) {
    return <LoadingState message="Loading tasks…" />;
  }

  if (error) {
    return <ErrorState description="Couldn't load these tasks." onRetry={() => refetch()} />;
  }

  return (
    <div>
      <h1 className="text-[var(--color-text-heading)]">{heading}</h1>

      {tasks && tasks.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Title</TableHead>
              <TableHead>Status</TableHead>
              <TableHead align="right">Due</TableHead>
              <TableHead align="right">Life area</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tasks.map((task) => (
              <TableRow key={task.id}>
                <TableCell>{task.title}</TableCell>
                <TableCell>{task.status}</TableCell>
                <TableCell align="right">{task.due_at ? new Date(task.due_at).toLocaleString() : "—"}</TableCell>
                <TableCell align="right">
                  <select
                    aria-label={`Life area for "${task.title}"`}
                    value={task.life_area_id ?? ""}
                    onChange={(event) =>
                      updateTask.mutate({
                        id: task.id,
                        life_area_id: event.target.value ? Number(event.target.value) : null,
                      })
                    }
                    className="rounded-md bg-[var(--color-bg-subtle)] text-[var(--color-text-body)]"
                    style={{ padding: "4px 8px", border: "none" }}
                  >
                    <option value="">Unassigned</option>
                    {lifeAreas?.map((option) => (
                      <option key={option.id} value={option.id}>
                        {option.name}
                      </option>
                    ))}
                  </select>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <EmptyState title="No tasks here" description="Nothing in this area yet." />
      )}
    </div>
  );
}
