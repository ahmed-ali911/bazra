import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";

export function useDeleteCalendarEvent() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/api/v1/calendar/events/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calendar"] });
      // ...and out of Home's Coming Up section, if it was there.
      queryClient.invalidateQueries({ queryKey: ["home"] });
    },
  });
}
