import { Button } from "../design-system/components/Button";
import { Card } from "../design-system/components/Card";

// The "/" route's page content, extracted from the old Layout.tsx —
// AppShell now provides the sidebar/top-nav chrome around it.
export function HomePlaceholder() {
  return (
    <Card>
      <h1 className="text-[var(--color-text-heading)]">Welcome to BAZRA.</h1>
      <p className="text-[var(--color-text-body)]">Your personal AI companion.</p>
      <Button>Get started</Button>
    </Card>
  );
}
