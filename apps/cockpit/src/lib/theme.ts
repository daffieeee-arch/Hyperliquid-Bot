export const COCKPIT_THEMES = ["terminal", "workstation", "hybrid"] as const;

export type CockpitTheme = (typeof COCKPIT_THEMES)[number];

/** Shipping default. CSS id stays `hybrid`; operator copy is Fail-Closed Amber. */
export const DEFAULT_COCKPIT_THEME: CockpitTheme = "hybrid";

export const COCKPIT_THEME_STORAGE_KEY = "hlq-cockpit-theme";

/** Operator chrome: Fail-Closed Amber ↔ Desk Dark. Critique stays in docs. */
export const OPERATOR_COCKPIT_THEMES = ["hybrid", "terminal"] as const;

export type CockpitThemeIntent = {
  label: string;
  density: string;
  ia: string;
  mobile: string;
};

export const COCKPIT_THEME_INTENTS: Record<CockpitTheme, CockpitThemeIntent> = {
  terminal: {
    label: "Desk Dark",
    density: "12px / 0 radius · densified terminal",
    ia: "same Health→Markets→Research→PAPER→Risk",
    mobile: "stack panels; PAPER+freshness stay sticky",
  },
  workstation: {
    label: "critique",
    density: "13px · Design docs only, not card-marketing",
    ia: "same zones; more pad only",
    mobile: "stack; chrome unchanged",
  },
  hybrid: {
    label: "Fail-Closed Amber",
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

export function showDesignThemeVariants(): boolean {
  return process.env.NEXT_PUBLIC_COCKPIT_DESIGN_VARIANTS === "1";
}

export function operatorCockpitThemes(): readonly CockpitTheme[] {
  return showDesignThemeVariants() ? COCKPIT_THEMES : OPERATOR_COCKPIT_THEMES;
}

export function isCockpitTheme(value: string | null | undefined): value is CockpitTheme {
  return value === "terminal" || value === "workstation" || value === "hybrid";
}

export function parseCockpitTheme(value: string | null | undefined): CockpitTheme {
  return isCockpitTheme(value) ? value : DEFAULT_COCKPIT_THEME;
}

export function parseOperatorCockpitTheme(value: string | null | undefined): CockpitTheme {
  const parsed = parseCockpitTheme(value);
  if (parsed === "workstation" && !showDesignThemeVariants()) {
    return DEFAULT_COCKPIT_THEME;
  }
  return parsed;
}
