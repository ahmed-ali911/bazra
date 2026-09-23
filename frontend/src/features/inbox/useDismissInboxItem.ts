import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";

export function useDismissInboxItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/api/v1/inbox/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inbox"] });
      // Dismissing removes it from Home's Needs Attention too.
      queryClient.invalidateQueries({ queryKey: ["home"] });
    },
  });
}
