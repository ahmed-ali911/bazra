export interface LifeArea {
  id: number;
  name: string;
  slug: string;
}

export interface LifeAreaSummary {
  id: number;
  name: string;
  slug: string;
  open_task_count: number;
  most_urgent_due_at: string | null;
}

// Unassigned is not a Life Area — no id, no slug. Kept as its own type
// rather than making LifeAreaSummary's id optional, mirroring the
// backend's UnassignedSummary schema and the UnassignedCard/LifeAreaCard
// split on the frontend.
export interface UnassignedSummary {
  open_task_count: number;
  most_urgent_due_at: string | null;
}

export interface MyWorldSummary {
  life_areas: LifeAreaSummary[];
  unassigned: UnassignedSummary;
}
