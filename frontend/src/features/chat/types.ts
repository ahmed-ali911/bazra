export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface SendMessageResponse {
  user_message: ChatMessage;
  assistant_message: ChatMessage;
}

// Checkpoint 5.1 — the structured HTTP 502 error detail Chat's own
// router returns when primary reply generation fails. `message` is
// the deterministic, already-localized (Arabic/English, matching the
// user's own triggering message) degradation wording chosen server-
// side — the frontend displays it verbatim, never invents its own
// phrasing (see ChatPage.tsx). `reason` is the normalized, provider-
// neutral failure category, exposed for any future caller that needs
// to branch on it programmatically rather than just display it.
export interface ModelUnavailableErrorDetail {
  error: "model_call_failed";
  reason: string;
  message: string;
  user_message_id: number;
}
