import { useQuery } from "@tanstack/react-query";

import { api } from "../../services/api";
import { localDayBoundaries } from "../../utils/localDayBoundaries";
import type { HomeSummary } from "./types";

export function useHomeSummary() {
  const { tomorrowStart, windowEnd } = localDayBoundaries();
  return useQuery({
    queryKey: ["home", "summary", tomorrowStart, windowEnd],
    queryFn: () =>
      api.get<HomeSummary>(
        `/api/v1/home/summary?tomorrow_start=${encodeURIComponent(tomorrowStart)}&window_end=${encodeURIComponent(windowEnd)}`,
      ),
  });
}
