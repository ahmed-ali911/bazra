import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LivingHero } from "./LivingHero";

describe("LivingHero", () => {
  it("renders the required state, and defaults scene/mood when omitted", () => {
    render(<LivingHero state="idle" />);
    expect(screen.getByText("idle")).toBeInTheDocument();
    expect(screen.getByText(/scene: home/)).toBeInTheDocument();
    expect(screen.getByText(/mood: neutral/)).toBeInTheDocument();
  });

  it("renders explicit scene/state/mood, matching Section 5's own example", () => {
    render(<LivingHero scene="work" state="working" mood="focused" />);
    expect(screen.getByText("working")).toBeInTheDocument();
    expect(screen.getByText(/scene: work/)).toBeInTheDocument();
    expect(screen.getByText(/mood: focused/)).toBeInTheDocument();
  });

  it("renders every state value without crashing", () => {
    const states = [
      "idle",
      "working",
      "thinking",
      "stretching",
      "celebrating",
      "deadline",
      "learning",
      "teaching",
      "serious",
    ] as const;

    for (const state of states) {
      const { unmount } = render(<LivingHero state={state} />);
      expect(screen.getByText(state)).toBeInTheDocument();
      unmount();
    }
  });
});
