import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { Task, TaskStatus } from "./types";

interface UpdateTaskInput {
  id: number;
  title?: string;
  description?: string | null;
  status?: TaskStatus;
  due_at?: string | null;
  life_area_id?: number | null;
}

export function useUpdateTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }: UpdateTaskInput) => api.patch<Task>(`/api/v1/tasks/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}
