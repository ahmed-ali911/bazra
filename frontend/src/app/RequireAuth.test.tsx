import { screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { renderWithQueryClient } from "../test-utils";
import { RequireAuth } from "./RequireAuth";

function renderGuarded() {
  return renderWithQueryClient(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/login" element={<div>Login page</div>} />
        <Route element={<RequireAuth />}>
          <Route path="/" element={<div>Protected content</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("RequireAuth", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows protected content when the session check succeeds", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ authenticated: true }),
      }),
    );

    renderGuarded();
    expect(await screen.findByText("Protected content")).toBeInTheDocument();
  });

  it("redirects to /login on a 401", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        statusText: "Unauthorized",
        json: async () => ({ detail: "Not authenticated" }),
      }),
    );

    renderGuarded();
    expect(await screen.findByText("Login page")).toBeInTheDocument();
  });

  it("shows an ErrorState, NOT a redirect to login, on a non-auth failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    renderGuarded();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("Login page")).not.toBeInTheDocument();
    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });
});
