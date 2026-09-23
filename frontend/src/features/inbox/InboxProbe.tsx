import { EmptyState } from "../../design-system/components/EmptyState";
import { ErrorState } from "../../design-system/components/ErrorState";
import { LoadingState } from "../../design-system/components/LoadingState";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../../design-system/components/Table";
import { useDismissInboxItem } from "./useDismissInboxItem";
import { useInboxItems } from "./useInboxItems";
import { useMarkInboxItemRead } from "./useMarkInboxItemRead";

// Minimum probe UI proving real InboxItem generation/state-transitions
// against the real API — not a designed Inbox screen. No create form:
// InboxItems are system-generated (currently by tasks.service on the
// open->done edge), never user-authored, so there's nothing to submit
// here. Final composition waits for a written screen spec.
export function InboxProbe() {
  const { data: items, isPending, error, refetch } = useInboxItems();
  const markRead = useMarkInboxItemRead();
  const dismiss = useDismissInboxItem();

  if (isPending) {
    return <LoadingState message="Loading inbox…" />;
  }

  if (error) {
    return <ErrorState description="Couldn't load the inbox." onRetry={() => refetch()} />;
  }

  return (
    <div>
      {items && items.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Read</TableHead>
              <TableHead>Title</TableHead>
              <TableHead align="right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id}>
                <TableCell>
                  <input
                    type="checkbox"
                    aria-label={`Mark "${item.title}" as ${item.read_at ? "unread" : "read"}`}
                    checked={item.read_at !== null}
                    onChange={() => markRead.mutate({ id: item.id, read: item.read_at === null })}
                  />
                </TableCell>
                <TableCell>{item.title}</TableCell>
                <TableCell align="right">
                  <button
                    type="button"
                    aria-label={`Dismiss "${item.title}"`}
                    onClick={() => dismiss.mutate(item.id)}
                    className="text-[var(--color-status-danger)]"
                  >
                    Dismiss
                  </button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : (
        <EmptyState title="Inbox is empty" description="Nothing needs your attention right now." />
      )}
    </div>
  );
}
