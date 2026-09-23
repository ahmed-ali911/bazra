import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";

export function useDeleteTask() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/api/v1/tasks/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      // Archiving removes this task from Calendar's agenda too.
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
      // ...and from whichever Home bucket it was in.
      queryClient.invalidateQueries({ queryKey: ["home"] });
    },
  });
}
