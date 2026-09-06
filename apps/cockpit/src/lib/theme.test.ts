import { describe, expect, it } from "vitest";

import {
  COCKPIT_THEME_INTENTS,
  COCKPIT_THEME_LABELS,
  DEFAULT_COCKPIT_THEME,
  DEFAULT_THEME_COPY,
  operatorCockpitThemes,
  parseCockpitTheme,
  parseOperatorCockpitTheme,
} from "./theme";

describe("cockpit theme variants", () => {
  it("defaults to C Fail-Closed Amber with optional Desk Dark; critique stays out of chrome", () => {
    expect(DEFAULT_COCKPIT_THEME).toBe("hybrid");
    expect(DEFAULT_THEME_COPY).toBe("C Fail-Closed Amber");
    expect(COCKPIT_THEME_LABELS.terminal).toBe("Desk Dark");
    expect(COCKPIT_THEME_LABELS.workstation).toBe("critique");
    expect(COCKPIT_THEME_LABELS.hybrid).toBe("C Fail-Closed Amber");
    expect(COCKPIT_THEME_LABELS.hybrid).not.toMatch(/HYBRID/i);
    expect(COCKPIT_THEME_LABELS.hybrid).not.toMatch(/DEFAULT C HYBRID/i);
    expect(operatorCockpitThemes()).toEqual(["hybrid", "terminal"]);
    expect(parseOperatorCockpitTheme("workstation")).toBe("hybrid");
    expect(COCKPIT_THEME_INTENTS.hybrid.ia).toMatch(/Health→Markets→Research→PAPER→Risk/);
    expect(parseCockpitTheme(undefined)).toBe("hybrid");
    expect(parseCockpitTheme("neon")).toBe("hybrid");
    expect(parseCockpitTheme("terminal")).toBe("terminal");
  });
});
