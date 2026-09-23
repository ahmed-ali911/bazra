import { useQuery } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { MyWorldSummary } from "./types";

export function useMyWorldSummary() {
  return useQuery({
    queryKey: ["life-areas", "summary"],
    queryFn: () => api.get<MyWorldSummary>("/api/v1/life-areas/summary"),
  });
}
