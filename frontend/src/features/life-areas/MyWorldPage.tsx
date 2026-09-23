import { useState } from "react";
import type { FormEvent } from "react";

import { Button } from "../../design-system/components/Button";
import { ErrorState } from "../../design-system/components/ErrorState";
import { LoadingState } from "../../design-system/components/LoadingState";
import { LifeAreaCard } from "./LifeAreaCard";
import { UnassignedCard } from "./UnassignedCard";
import { useCreateLifeArea } from "./useCreateLifeArea";
import { useMyWorldSummary } from "./useMyWorldSummary";

// Real, navigable My World screen (Checkpoint 2.5b) — not a probe. Still
// functional-only, not a designed screen: no layout/visual work derived
// from the Home reference image, per the standing Phase 2 rule.
export function MyWorldPage() {
  const [name, setName] = useState("");
  const { data, isPending, error, refetch } = useMyWorldSummary();
  const createLifeArea = useCreateLifeArea();

  function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    createLifeArea.mutate(name, { onSuccess: () => setName("") });
  }

  if (isPending) {
    return <LoadingState message="Loading My World…" />;
  }

  if (error) {
    return <ErrorState description="Couldn't load My World." onRetry={() => refetch()} />;
  }

  return (
    <div>
      <h1 className="text-[var(--color-text-heading)]">My World</h1>

      <form
        onSubmit={handleCreate}
        style={{ display: "flex", gap: "var(--space-2)", margin: "var(--space-4) 0" }}
      >
        <input
          aria-label="New life area name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          className="rounded-md bg-[var(--color-bg-subtle)] text-[var(--color-text-body)]"
          style={{ padding: "8px 12px", border: "none", flex: 1 }}
        />
        <Button type="submit">Add life area</Button>
      </form>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-4)" }}>
        {data.life_areas.map((area) => (
          <LifeAreaCard key={area.id} area={area} />
        ))}
        <UnassignedCard summary={data.unassigned} />
      </div>
    </div>
  );
}
