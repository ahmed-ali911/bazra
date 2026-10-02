import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { api } from "../../services/api";

interface AppOpenedChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

interface AppOpenedResponse {
  status: "silence" | "surfaced";
  message?: AppOpenedChatMessage | null;
}

// Checkpoint 4.5e — V1 trigger semantics, locked: a fresh App mount
// evaluates ONCE. This is a module-scoped (not component-state) guard
// purely as an OPTIMIZATION against React StrictMode's dev-only
// double-invoke of effects — it is NEVER the correctness boundary.
// Correctness lives entirely server-side (the Proactive Frequency
// Gate, re-checked under the advisory lock at finalization): a reload
// or a genuinely new mount may call this endpoint again, and the
// backend alone decides whether another opening is allowed. Never
// backed by localStorage/sessionStorage/IndexedDB — those would make
// per-viewer browser storage the correctness boundary, which this
// checkpoint's own brief explicitly forbids.
let hasRequestedThisPageLoad = false;

// IMPORTANT — terminology: this is a V1 EVALUATION TRIGGER, not proof
// that Ahmed genuinely returned to BAZRA after an absence. It does not
// claim or implement true Attention Resume (presence/session
// tracking) — see backend/README.md's own Checkpoint 4.5c/4.5e
// sections. Route navigation between Home/My World/Chat (this hook
// lives in AppShell, which persists across that navigation via its
// own <Outlet>, never remounting) does NOT retrigger this — only a
// fresh AppShell mount does. Background/foreground switches and
// network reconnects are explicit, documented V1 gaps: this codebase
// adds no tab-visibility, window-focus, or connectivity listener of
// any kind, here or anywhere else.
export function useAppOpenedTrigger(): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (hasRequestedThisPageLoad) {
      return;
    }
    hasRequestedThisPageLoad = true;

    api
      .post<AppOpenedResponse>("/api/v1/attention/app-opened", {
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      })
      .then((response) => {
        if (response.status === "surfaced") {
          // Reuse the exact same query key useSendChatMessage's own
          // onSettled already invalidates — the proactive opening is a
          // normal assistant ChatMessage in the same conversation
          // stream, not a second parallel "proactive chat" UI.
          queryClient.invalidateQueries({ queryKey: ["chat", "messages"] });
        }
      })
      .catch(() => {
        // Best-effort background trigger. Silence is already a normal
        // successful response handled above (nothing to do); a
        // genuine network/backend failure here must never surface as
        // an error toast or interrupt the UI — this is not a user-
        // initiated action with feedback expectations.
      });
  }, [queryClient]);
}
