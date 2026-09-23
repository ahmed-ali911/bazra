import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { Task } from "./types";

interface CreateTaskInput {
  title: string;
  description?: string;
  due_at?: string;
  life_area_id?: number;
}

export function useCreateTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateTaskInput) => api.post<Task>("/api/v1/tasks", data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      // A task with a due_at can appear in Calendar's agenda read-model —
      // an open agenda view needs to refetch too, not just the task list.
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
      // A new task can land in Home's Focus Today, Coming Up, or Anytime.
      queryClient.invalidateQueries({ queryKey: ["home"] });
    },
  });
}
