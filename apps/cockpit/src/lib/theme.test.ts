import { describe, expect, it } from "vitest";

import { COCKPIT_THEME_LABELS, DEFAULT_COCKPIT_THEME, parseCockpitTheme } from "./theme";

describe("cockpit theme variants", () => {
  it("defaults to C hybrid as the densest operator-ready workstation", () => {
    expect(DEFAULT_COCKPIT_THEME).toBe("hybrid");
    expect(COCKPIT_THEME_LABELS.terminal).toBe("A terminal");
    expect(COCKPIT_THEME_LABELS.workstation).toBe("B shadcn workstation");
    expect(COCKPIT_THEME_LABELS.hybrid).toBe("C hybrid");
    expect(parseCockpitTheme(undefined)).toBe("hybrid");
    expect(parseCockpitTheme("neon")).toBe("hybrid");
    expect(parseCockpitTheme("terminal")).toBe("terminal");
  });
});
