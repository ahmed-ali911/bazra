import { describe, expect, it } from "vitest";

import { localDayBoundaries } from "./localDayBoundaries";

describe("localDayBoundaries", () => {
  it("computes tomorrowStart as one local day after the reference date, at local midnight", () => {
    const { tomorrowStart } = localDayBoundaries(new Date(2026, 5, 15, 14, 30)); // June 15, 2:30pm local
    const tomorrow = new Date(tomorrowStart);
    expect(tomorrow.getHours()).toBe(0);
    expect(tomorrow.getMinutes()).toBe(0);
    expect(tomorrow.getDate()).toBe(16);
    expect(tomorrow.getMonth()).toBe(5);
  });

  it("computes windowEnd as exactly 7 local days after tomorrowStart", () => {
    const { tomorrowStart, windowEnd } = localDayBoundaries(new Date(2026, 5, 15, 9, 0));
    const tomorrow = new Date(tomorrowStart);
    const end = new Date(windowEnd);
    expect(end.getDate()).toBe(tomorrow.getDate() + 7);
    expect(end.getHours()).toBe(0);
  });

  it("rolls over correctly at a month boundary", () => {
    const { tomorrowStart } = localDayBoundaries(new Date(2026, 0, 31, 12, 0)); // Jan 31
    const tomorrow = new Date(tomorrowStart);
    expect(tomorrow.getMonth()).toBe(1); // February
    expect(tomorrow.getDate()).toBe(1);
  });
});
