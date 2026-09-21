import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { Button } from "../design-system/components/Button";
import { Card } from "../design-system/components/Card";
import { useLogin } from "../features/auth/useLogin";

// Functional only, not a designed screen — same standard as every other
// probe UI in Phase 2. Real composition waits for the written screen spec.
export function LoginPage() {
  const [password, setPassword] = useState("");
  const navigate = useNavigate();
  const login = useLogin();

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    login.mutate(password, {
      onSuccess: () => navigate("/", { replace: true }),
    });
  }

  return (
    <div style={{ display: "flex", minHeight: "100vh", alignItems: "center", justifyContent: "center" }}>
      <Card>
        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "var(--space-3)" }}>
          <h1 className="text-[var(--color-text-heading)]">Sign in to BAZRA</h1>
          <input
            type="password"
            aria-label="Password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="rounded-md bg-[var(--color-bg-subtle)] text-[var(--color-text-body)]"
            style={{ padding: "8px 12px", border: "none" }}
          />
          {login.isError ? (
            <p className="text-[var(--color-status-danger)] text-sm">
              {login.error instanceof Error ? login.error.message : "Something went wrong."}
            </p>
          ) : null}
          <Button type="submit" disabled={login.isPending}>
            {login.isPending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </Card>
    </div>
  );
}
