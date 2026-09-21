import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";

export function useLogin() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (password: string) => api.post<{ status: string }>("/api/v1/auth/login", { password }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
  });
}
