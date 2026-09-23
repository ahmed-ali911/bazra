import { EmptyState } from "../../design-system/components/EmptyState";
import { ErrorState } from "../../design-system/components/ErrorState";
import { LoadingState } from "../../design-system/components/LoadingState";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../design-system/components/Table";
import { useHomeSummary } from "./useHomeSummary";

// Real Home aggregation — Focus Today / Coming Up / Needs Attention /
// Anytime, computed server-side by GET /home/summary from boundaries this
// hook computes in the browser's local timezone (see localDayBoundaries).
// Functional composition only, not a designed screen — still probe-level
// visually, per the standing Phase 2 rule; only the data behind it is real.
export function HomeSummaryView() {
  const { data, isPending, error, refetch } = useHomeSummary();

  if (isPending) {
    return <LoadingState message="Loading your day…" />;
  }

  if (error) {
    return <ErrorState description="Couldn't load your Home summary." onRetry={() => refetch()} />;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-4)" }}>
      <section>
        <h2 className="text-[var(--color-text-heading)]">Focus Today</h2>
        {data.focus_today.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead align="right">Due</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.focus_today.map((task) => (
                <TableRow key={task.id}>
                  <TableCell>{task.title}</TableCell>
                  <TableCell align="right">{task.due_at ? new Date(task.due_at).toLocaleString() : "—"}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <EmptyState title="Nothing due" description="Nothing overdue or due today." />
        )}
      </section>

      <section>
        <h2 className="text-[var(--color-text-heading)]">Coming Up</h2>
        {data.coming_up.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Source</TableHead>
                <TableHead>Title</TableHead>
                <TableHead align="right">When</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.coming_up.map((item) => (
                <TableRow key={`${item.source}-${item.id}`}>
                  <TableCell>{item.source}</TableCell>
                  <TableCell>{item.title}</TableCell>
                  <TableCell align="right">{new Date(item.starts_at).toLocaleString()}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <EmptyState title="Nothing coming up" description="Nothing scheduled in the next 7 days." />
        )}
      </section>

      <section>
        <h2 className="text-[var(--color-text-heading)]">Needs Attention</h2>
        {data.needs_attention.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.needs_attention.map((item) => (
                <TableRow key={item.id}>
                  <TableCell>{item.title}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <EmptyState title="All caught up" description="No unread inbox items." />
        )}
      </section>

      <section>
        <h2 className="text-[var(--color-text-heading)]">Anytime</h2>
        {data.anytime.length > 0 ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.anytime.map((task) => (
                <TableRow key={task.id}>
                  <TableCell>{task.title}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <EmptyState title="Nothing here" description="No open tasks without a due date." />
        )}
      </section>
    </div>
  );
}
