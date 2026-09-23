import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";

// On a blocked deletion (400), the backend's structured body
// ({ error, task_count, calendar_event_count, total_count }) is preserved
// on the thrown ApiError's .detail (see services/api.ts) — callers read
// mutation.error instanceof ApiError && error.detail to show the count,
// rather than this hook special-casing the error itself.
export function useDeleteLifeArea() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/api/v1/life-areas/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["life-areas"] });
    },
  });
}
