import { describe, expect, it } from "vitest";

import {
  COCKPIT_THEME_INTENTS,
  COCKPIT_THEME_LABELS,
  DEFAULT_COCKPIT_THEME,
  parseCockpitTheme,
} from "./theme";

describe("cockpit theme variants", () => {
  it("defaults to C Fail-Closed Amber with optional Desk Dark density", () => {
    expect(DEFAULT_COCKPIT_THEME).toBe("hybrid");
    expect(COCKPIT_THEME_LABELS.terminal).toBe("A Desk Dark");
    expect(COCKPIT_THEME_LABELS.workstation).toBe("B critique");
    expect(COCKPIT_THEME_LABELS.hybrid).toBe("C Fail-Closed Amber");
    expect(COCKPIT_THEME_INTENTS.hybrid.ia).toMatch(/Health→Markets→Research→PAPER→Risk/);
    expect(parseCockpitTheme(undefined)).toBe("hybrid");
    expect(parseCockpitTheme("neon")).toBe("hybrid");
    expect(parseCockpitTheme("terminal")).toBe("terminal");
  });
});
