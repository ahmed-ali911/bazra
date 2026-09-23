import { useQuery } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { Task, TaskStatus } from "./types";

interface TaskFilters {
  status?: TaskStatus;
  life_area_id?: number;
  unassigned?: boolean;
}

export function useTasks(filters: TaskFilters = {}) {
  const params = new URLSearchParams();
  if (filters.status) {
    params.set("status", filters.status);
  }
  if (filters.unassigned) {
    params.set("unassigned", "true");
  } else if (filters.life_area_id !== undefined) {
    params.set("life_area_id", String(filters.life_area_id));
  }
  const query = params.toString() ? `?${params.toString()}` : "";

  return useQuery({
    queryKey: ["tasks", filters],
    queryFn: () => api.get<Task[]>(`/api/v1/tasks${query}`),
  });
}
