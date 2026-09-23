import type { AgendaItem } from "../calendar/types";
import type { InboxItem } from "../inbox/types";
import type { Task } from "../tasks/types";

export interface HomeSummary {
  focus_today: Task[];
  coming_up: AgendaItem[];
  needs_attention: InboxItem[];
  anytime: Task[];
}
