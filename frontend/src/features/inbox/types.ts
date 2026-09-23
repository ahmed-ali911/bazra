export interface InboxItem {
  id: number;
  title: string;
  task_id: number | null;
  read_at: string | null;
  created_at: string;
  updated_at: string;
}
