import { describe, expect, it } from "vitest";

import { RESEARCH_UNAVAILABLE, RESEARCH_UNAVAILABLE_REASON, researchStubView } from "./research";

describe("research stub", () => {
  it("stays UNAVAILABLE until Quant fields exist", () => {
    const view = researchStubView();
    expect(view.status).toBe(RESEARCH_UNAVAILABLE);
    expect(view.hypothesis).toBe(RESEARCH_UNAVAILABLE);
    expect(view.reason).toBe(RESEARCH_UNAVAILABLE_REASON);
  });
});
