import { describe, expect, it } from "vitest";

import {
  COCKPIT_THEME_INTENTS,
  COCKPIT_THEME_LABELS,
  DEFAULT_COCKPIT_THEME,
  operatorCockpitThemes,
  parseCockpitTheme,
  parseOperatorCockpitTheme,
} from "./theme";

describe("cockpit theme variants", () => {
  it("defaults to Fail-Closed Amber with optional Desk Dark; critique stays out of chrome", () => {
    expect(DEFAULT_COCKPIT_THEME).toBe("hybrid");
    expect(COCKPIT_THEME_LABELS.terminal).toBe("Desk Dark");
    expect(COCKPIT_THEME_LABELS.workstation).toBe("critique");
    expect(COCKPIT_THEME_LABELS.hybrid).toBe("Fail-Closed Amber");
    expect(COCKPIT_THEME_LABELS.hybrid).not.toMatch(/HYBRID|DEFAULT C/i);
    expect(operatorCockpitThemes()).toEqual(["hybrid", "terminal"]);
    expect(parseOperatorCockpitTheme("workstation")).toBe("hybrid");
    expect(COCKPIT_THEME_INTENTS.hybrid.ia).toMatch(/Health→Markets→Research→PAPER→Risk/);
    expect(parseCockpitTheme(undefined)).toBe("hybrid");
    expect(parseCockpitTheme("neon")).toBe("hybrid");
    expect(parseCockpitTheme("terminal")).toBe("terminal");
  });
});
