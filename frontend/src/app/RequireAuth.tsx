import { Navigate, Outlet } from "react-router-dom";

import { ErrorState } from "../design-system/components/ErrorState";
import { LoadingState } from "../design-system/components/LoadingState";
import { useCurrentUser } from "../features/auth/useCurrentUser";
import { ApiError } from "../services/api";

// Only a 401 specifically means "you're logged out, go to /login". Any
// other failure (network down, 500, ...) shows an ErrorState instead —
// silently redirecting to login when the real problem is "the backend is
// unreachable" would be actively misleading, not just imprecise.
export function RequireAuth() {
  const { data, isPending, error, refetch } = useCurrentUser();

  if (isPending) {
    return <LoadingState message="Checking session…" />;
  }

  if (error instanceof ApiError && error.status === 401) {
    return <Navigate to="/login" replace />;
  }

  if (error || !data?.authenticated) {
    return <ErrorState description="Couldn't verify your session." onRetry={() => refetch()} />;
  }

  return <Outlet />;
}
