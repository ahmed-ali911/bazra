import { useQuery } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { LifeArea } from "./types";

export type { LifeArea };

export function useLifeAreas() {
  return useQuery({
    queryKey: ["life-areas"],
    queryFn: () => api.get<LifeArea[]>("/api/v1/life-areas"),
  });
}
