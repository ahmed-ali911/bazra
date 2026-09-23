import { useQuery } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { AgendaItem } from "./types";

interface AgendaRange {
  from: string;
  to: string;
}

export function useAgenda(range: AgendaRange) {
  return useQuery({
    queryKey: ["calendar", "agenda", range],
    queryFn: () =>
      api.get<AgendaItem[]>(
        `/api/v1/calendar/agenda?from=${encodeURIComponent(range.from)}&to=${encodeURIComponent(range.to)}`,
      ),
  });
}
