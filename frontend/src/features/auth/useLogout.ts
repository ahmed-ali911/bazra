import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";

// Not wired to a visible UI trigger in Checkpoint 2.1 — there's no user menu
// in TopNav yet to put a logout button in (that's real screen design,
// deferred). Verified directly against the API, same as the login flow's
// underlying request, rather than inventing a temporary button.
export function useLogout() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: () => api.post<{ status: string }>("/api/v1/auth/logout"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["auth", "me"] });
    },
  });
}
