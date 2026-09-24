import { useQuery } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { ChatMessage } from "./types";

export function useChatMessages() {
  return useQuery({
    queryKey: ["chat", "messages"],
    queryFn: () => api.get<ChatMessage[]>("/api/v1/chat/messages"),
  });
}
