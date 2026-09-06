export const COCKPIT_THEMES = ["terminal", "workstation", "hybrid"] as const;

export type CockpitTheme = (typeof COCKPIT_THEMES)[number];

/** Densest operator-ready default: hybrid keeps terminal metrics with shadcn tokens. */
export const DEFAULT_COCKPIT_THEME: CockpitTheme = "hybrid";

export const COCKPIT_THEME_STORAGE_KEY = "hlq-cockpit-theme";

export const COCKPIT_THEME_LABELS: Record<CockpitTheme, string> = {
  terminal: "A terminal",
  workstation: "B shadcn workstation",
  hybrid: "C hybrid",
};

export function isCockpitTheme(value: string | null | undefined): value is CockpitTheme {
  return value === "terminal" || value === "workstation" || value === "hybrid";
}

export function parseCockpitTheme(value: string | null | undefined): CockpitTheme {
  return isCockpitTheme(value) ? value : DEFAULT_COCKPIT_THEME;
}
