import { useQuery } from "@tanstack/react-query";

import { api } from "../../services/api";

interface CurrentUserResponse {
  authenticated: boolean;
}

export function useCurrentUser() {
  return useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => api.get<CurrentUserResponse>("/api/v1/auth/me"),
    // A 401 here is an expected state (logged out), not a transient
    // failure — retrying it just delays the redirect to /login.
    retry: false,
  });
}
