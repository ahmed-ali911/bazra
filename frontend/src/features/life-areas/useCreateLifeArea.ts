import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { LifeArea } from "./types";

export function useCreateLifeArea() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.post<LifeArea>("/api/v1/life-areas", { name }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["life-areas"] });
    },
  });
}
