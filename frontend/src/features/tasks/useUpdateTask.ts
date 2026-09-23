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
      // due_at (or status/archival, via a future edit) can change whether
      // this task appears in Calendar's agenda — keep an open agenda view
      // in sync too.
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
      // Marking a task done can generate a new InboxItem server-side
      // (tasks.service's open->done trigger) — an open Inbox view has no
      // other way to know that happened.
      queryClient.invalidateQueries({ queryKey: ["inbox"] });
      // Any of the above (status, due_at, archival) can move this task
      // into or out of Home's Focus Today/Coming Up/Anytime buckets, or
      // add a new Needs Attention item.
      queryClient.invalidateQueries({ queryKey: ["home"] });
    },
  });
}
