import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { CalendarEvent } from "./types";

interface UpdateCalendarEventInput {
  id: number;
  title?: string;
  description?: string | null;
  starts_at?: string;
  ends_at?: string | null;
  life_area_id?: number | null;
}

export function useUpdateCalendarEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }: UpdateCalendarEventInput) =>
      api.patch<CalendarEvent>(`/api/v1/calendar/events/${id}`, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
    },
  });
}
