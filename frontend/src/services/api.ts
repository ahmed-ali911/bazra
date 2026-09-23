const API_BASE_URL = import.meta.env.VITE_API_BASE_URL;

// The shared HTTP boundary every future feature hook goes through — thin,
// but its response/error contract is deliberate and fixed:
//
// - successful JSON response -> parsed typed value
// - successful response with no body (e.g. 204) -> undefined, no JSON parse
//   attempted
// - non-2xx JSON backend error -> ApiError(status, message, detail) using
//   the backend's own `detail` as the message when it's a string; the
//   raw `detail` value (string OR object — e.g. Life Area's blocked-
//   deletion body) is always preserved on .detail, for callers that need
//   structured error data, not just a display string
// - non-2xx response without usable JSON -> ApiError with a safe fallback
//   message (statusText, or a generic one), .detail left undefined
// - network/fetch failure (offline, DNS, connection refused, CORS
//   rejection, ...) never reaches an HTTP status at all — rethrown as-is,
//   NEVER wrapped as ApiError, so callers (e.g. RequireAuth) can tell "the
//   network failed" apart from "the server said 401" without string-
//   matching error messages.
export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });

  if (!response.ok) {
    let message = response.statusText || `Request failed with status ${response.status}`;
    let detail: unknown;
    try {
      const body = await response.json();
      detail = body?.detail;
      if (typeof body?.detail === "string") {
        message = body.detail;
      }
    } catch {
      // non-JSON or empty error body — keep the statusText fallback above
    }
    throw new ApiError(response.status, message, detail);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string): Promise<T> => request<T>(path),
  post: <T>(path: string, data?: unknown): Promise<T> =>
    request<T>(path, { method: "POST", body: data !== undefined ? JSON.stringify(data) : undefined }),
  patch: <T>(path: string, data?: unknown): Promise<T> =>
    request<T>(path, { method: "PATCH", body: data !== undefined ? JSON.stringify(data) : undefined }),
  delete: <T>(path: string): Promise<T> => request<T>(path, { method: "DELETE" }),
};
