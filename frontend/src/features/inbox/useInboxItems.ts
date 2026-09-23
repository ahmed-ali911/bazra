import { useQuery } from "@tanstack/react-query";

import { api } from "../../services/api";
import type { InboxItem } from "./types";

export function useInboxItems(unread?: boolean) {
  return useQuery({
    queryKey: ["inbox", { unread }],
    queryFn: () =>
      api.get<InboxItem[]>(`/api/v1/inbox${unread !== undefined ? `?unread=${unread}` : ""}`),
  });
}
