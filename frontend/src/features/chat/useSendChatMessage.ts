import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";
import { localDayBoundaries } from "../../utils/localDayBoundaries";
import type { SendMessageResponse } from "./types";

export function useSendChatMessage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (content: string) => {
      const { tomorrowStart, windowEnd } = localDayBoundaries();
      return api.post<SendMessageResponse>("/api/v1/chat/messages", {
        content,
        tomorrow_start: tomorrowStart,
        window_end: windowEnd,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      });
    },
    onSettled: () => {
      // Fires on both success AND failure — the user's own message is
      // persisted server-side even when the model call itself fails
      // (502), so refetching either way is what surfaces it in the
      // scrollback rather than only existing in this mutation's own
      // transient state.
      queryClient.invalidateQueries({ queryKey: ["chat", "messages"] });
    },
  });
}
