export const RESEARCH_UNAVAILABLE = "UNAVAILABLE";
export const RESEARCH_UNAVAILABLE_REASON =
  "Quant hypothesis / experiment fields are not in reconstructable PAPER artifacts.";

export type ResearchStubView = {
  status: typeof RESEARCH_UNAVAILABLE;
  hypothesis: typeof RESEARCH_UNAVAILABLE;
  universe: typeof RESEARCH_UNAVAILABLE;
  trainValidationOos: typeof RESEARCH_UNAVAILABLE;
  reason: typeof RESEARCH_UNAVAILABLE_REASON;
};

export function researchStubView(): ResearchStubView {
  return {
    status: RESEARCH_UNAVAILABLE,
    hypothesis: RESEARCH_UNAVAILABLE,
    universe: RESEARCH_UNAVAILABLE,
    trainValidationOos: RESEARCH_UNAVAILABLE,
    reason: RESEARCH_UNAVAILABLE_REASON,
  };
}
