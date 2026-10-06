export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

// Checkpoint 5.7H — Manual Gemini Test Mode's own strict, closed enum,
// matching the backend's ModelProviderOverride Literal exactly (see
// app/modules/chat/schemas.py). "default" preserves production
// behavior exactly; "google_gemini_test" is the one currently-
// supported manual override. The backend alone maps this to an actual
// provider/model — this type never carries either directly.
export type ModelProviderOverride = "default" | "google_gemini_test";

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
