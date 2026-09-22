export type TaskStatus = "open" | "done";

export interface Task {
  id: number;
  title: string;
  description: string | null;
  status: TaskStatus;
  due_at: string | null;
  completed_at: string | null;
  life_area_id: number | null;
  created_at: string;
  updated_at: string;
}
