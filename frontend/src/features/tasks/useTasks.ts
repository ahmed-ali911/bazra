import { useQuery } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { Task, TaskStatus } from "./types";

interface TaskFilters {
  status?: TaskStatus;
}

export function useTasks(filters: TaskFilters = {}) {
  const query = filters.status ? `?status=${filters.status}` : "";
  return useQuery({
    queryKey: ["tasks", filters],
    queryFn: () => api.get<Task[]>(`/api/v1/tasks${query}`),
  });
}
