import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

import "@testing-library/jest-dom/vitest";

// Testing Library's own auto-cleanup only registers itself when it detects a
// global `afterEach` — we don't set vitest's `test.globals: true`, so it
// never ran, and DOM from one test leaked into the next test in the same
// file. Only invisible until a file had more than one it() per describe.
afterEach(cleanup);
