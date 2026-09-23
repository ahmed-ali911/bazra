import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { CalendarEvent } from "./types";

interface CreateCalendarEventInput {
  title: string;
  description?: string;
  starts_at: string;
  ends_at?: string;
  life_area_id?: number;
}

export function useCreateCalendarEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: CreateCalendarEventInput) => api.post<CalendarEvent>("/api/v1/calendar/events", data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
    },
  });
}
