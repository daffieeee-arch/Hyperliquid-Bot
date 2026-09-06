export const COCKPIT_NAV_ZONES = [
  { id: "health", label: "Health" },
  { id: "markets", label: "Markets" },
  { id: "research", label: "Research" },
  { id: "paper", label: "PAPER" },
  { id: "risk", label: "Risk" },
] as const;

export type CockpitNavZoneId = (typeof COCKPIT_NAV_ZONES)[number]["id"];

export function cockpitNavOrder(): readonly CockpitNavZoneId[] {
  return COCKPIT_NAV_ZONES.map((zone) => zone.id);
}
