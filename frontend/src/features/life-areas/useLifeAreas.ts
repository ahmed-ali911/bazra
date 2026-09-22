import { useQuery } from "@tanstack/react-query";

import { api } from "../../services/api";

export interface LifeArea {
  id: number;
  name: string;
  slug: string;
}

export function useLifeAreas() {
  return useQuery({
    queryKey: ["life-areas"],
    queryFn: () => api.get<LifeArea[]>("/api/v1/life-areas"),
  });
}
