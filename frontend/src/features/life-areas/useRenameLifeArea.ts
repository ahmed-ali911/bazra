import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { LifeArea } from "./types";

export function useRenameLifeArea() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) =>
      api.patch<LifeArea>(`/api/v1/life-areas/${id}`, { name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["life-areas"] });
    },
  });
}
