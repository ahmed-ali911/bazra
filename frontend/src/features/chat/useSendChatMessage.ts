import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../../services/api";
import { localDayBoundaries } from "../../utils/localDayBoundaries";
import type { ModelProviderOverride, SendMessageResponse } from "./types";

export interface SendChatMessageInput {
  content: string;
  // Checkpoint 5.7H — optional, defaults to "default" (pre-5.7H
  // behavior) so every existing caller of this hook keeps working
  // unchanged.
  modelProviderOverride?: ModelProviderOverride;
}

export function useSendChatMessage() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ content, modelProviderOverride = "default" }: SendChatMessageInput) => {
      const { tomorrowStart, windowEnd } = localDayBoundaries();
      return api.post<SendMessageResponse>("/api/v1/chat/messages", {
        content,
        tomorrow_start: tomorrowStart,
        window_end: windowEnd,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        model_provider_override: modelProviderOverride,
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
