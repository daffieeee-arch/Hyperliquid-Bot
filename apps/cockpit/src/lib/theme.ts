export const COCKPIT_THEMES = ["terminal", "workstation", "hybrid"] as const;

export type CockpitTheme = (typeof COCKPIT_THEMES)[number];

/** Shipping default: C hybrid = Fail-Closed Amber, status-first. */
export const DEFAULT_COCKPIT_THEME: CockpitTheme = "hybrid";

export const COCKPIT_THEME_STORAGE_KEY = "hlq-cockpit-theme";

export type CockpitThemeIntent = {
  label: string;
  density: string;
  ia: string;
  mobile: string;
};

export const COCKPIT_THEME_INTENTS: Record<CockpitTheme, CockpitThemeIntent> = {
  terminal: {
    label: "A Desk Dark",
    density: "12px / 0 radius · densified terminal",
    ia: "same Health→Markets→Research→PAPER→Risk",
    mobile: "stack panels; PAPER+freshness stay sticky",
  },
  workstation: {
    label: "B critique",
    density: "13px · demoted, not card-marketing",
    ia: "same zones; more pad only",
    mobile: "stack; chrome unchanged",
  },
  hybrid: {
    label: "C Fail-Closed Amber",
    density: "13px body / 11px meta · dense workstation",
    ia: "status-first chrome + Health→Markets→Research→PAPER→Risk",
    mobile: "fewer columns; PAPER+freshness+strip stay visible",
  },
};

export const COCKPIT_THEME_LABELS: Record<CockpitTheme, string> = {
  terminal: COCKPIT_THEME_INTENTS.terminal.label,
  workstation: COCKPIT_THEME_INTENTS.workstation.label,
  hybrid: COCKPIT_THEME_INTENTS.hybrid.label,
};

export function isCockpitTheme(value: string | null | undefined): value is CockpitTheme {
  return value === "terminal" || value === "workstation" || value === "hybrid";
}

export function parseCockpitTheme(value: string | null | undefined): CockpitTheme {
  return isCockpitTheme(value) ? value : DEFAULT_COCKPIT_THEME;
}
