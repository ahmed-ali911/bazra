import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { InboxItem } from "./types";

export function useMarkInboxItemRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, read }: { id: number; read: boolean }) =>
      api.patch<InboxItem>(`/api/v1/inbox/${id}`, { read }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inbox"] });
    },
  });
}
