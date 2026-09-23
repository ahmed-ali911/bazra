import { Button } from "../design-system/components/Button";
import { Card } from "../design-system/components/Card";
import { Divider } from "../design-system/components/Divider";
import { LivingHero } from "../design-system/components/LivingHero";
import { StatusBadge } from "../design-system/components/StatusBadge";
import { CalendarProbe } from "../features/calendar/CalendarProbe";
import { HomeSummaryView } from "../features/home/HomeSummaryView";
import { InboxProbe } from "../features/inbox/InboxProbe";
import { TasksProbe } from "../features/tasks/TasksProbe";

// The "/" route's page content, extracted from the old Layout.tsx —
// AppShell now provides the sidebar/top-nav chrome around it. HomeSummaryView
// is the real Home aggregation (Checkpoint 2.5a: Focus Today/Coming Up/
// Needs Attention/Anytime). The probes below it remain — they're still the
// only UI for creating/mutating tasks, events, and inbox items; nothing
// here is a designed Home screen yet, just real data.
export function HomePlaceholder() {
  return (
    <Card>
      <div style={{ display: "flex", gap: "var(--space-4)", alignItems: "flex-start" }}>
        <LivingHero scene="home" state="idle" mood="encouraging" />
        <div>
          <h1 className="text-[var(--color-text-heading)]">Welcome to BAZRA.</h1>
          <p className="text-[var(--color-text-body)]">Your personal AI companion.</p>
          <div style={{ marginTop: "var(--space-2)" }}>
            <Button>Get started</Button>
          </div>
        </div>
      </div>
      <div style={{ margin: "var(--space-4) 0" }}>
        <Divider />
      </div>
      <StatusBadge variant="success">On track</StatusBadge>
      <div style={{ marginTop: "var(--space-4)" }}>
        <HomeSummaryView />
      </div>
      <div style={{ margin: "var(--space-4) 0" }}>
        <Divider />
      </div>
      <div>
        <TasksProbe />
      </div>
      <div style={{ margin: "var(--space-4) 0" }}>
        <Divider />
      </div>
      <div>
        <CalendarProbe />
      </div>
      <div style={{ margin: "var(--space-4) 0" }}>
        <Divider />
      </div>
      <div>
        <InboxProbe />
      </div>
    </Card>
  );
}
