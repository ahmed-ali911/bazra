export interface AgendaItem {
  source: "event" | "task";
  id: number;
  title: string;
  starts_at: string;
  ends_at: string | null;
  life_area_id: number | null;
}

export interface CalendarEvent {
  id: number;
  title: string;
  description: string | null;
  starts_at: string;
  ends_at: string | null;
  life_area_id: number | null;
  created_at: string;
  updated_at: string;
}
