export const COCKPIT_ROUTES = [
  {
    id: "overview",
    href: "/",
    label: "Overview",
    icon: "gauge",
    title: "Operator overview",
    subtitle: "Capture health, what needs attention, and which research is available.",
  },
  {
    id: "markets",
    href: "/markets",
    label: "Markets",
    icon: "candlestick",
    title: "Markets",
    subtitle: "Public Hyperliquid mid and candles. Public mid is not research truth.",
  },
  {
    id: "research",
    href: "/research",
    label: "Research",
    icon: "flask",
    title: "Research",
    subtitle: "Artifact and summary viewer for retained capture runs. No edge, no strategy PnL.",
  },
  {
    id: "paper",
    href: "/paper",
    label: "PAPER",
    icon: "bot",
    title: "PAPER desk",
    subtitle: "COURSE-1 soak run: decisions, assumed overlay results and the intent tape.",
  },
  {
    id: "system",
    href: "/system",
    label: "System & risk",
    icon: "shield",
    title: "System & risk",
    subtitle: "Read-only capture health, run binding and the documented paper_risk gates.",
  },
] as const;

export type CockpitRoute = (typeof COCKPIT_ROUTES)[number];
export type CockpitRouteId = CockpitRoute["id"];

export function cockpitRouteOrder(): readonly CockpitRouteId[] {
  return COCKPIT_ROUTES.map((route) => route.id);
}

export function cockpitRoute(id: CockpitRouteId): CockpitRoute {
  const found = COCKPIT_ROUTES.find((route) => route.id === id);
  if (found === undefined) {
    throw new Error(`Unknown cockpit route: ${id}`);
  }
  return found;
}

/** Longest-prefix match so nested paths keep their section highlighted. */
export function activeRouteId(pathname: string): CockpitRouteId {
  const match = [...COCKPIT_ROUTES]
    .filter((route) => route.href !== "/" && pathname.startsWith(route.href))
    .sort((left, right) => right.href.length - left.href.length)[0];
  return match?.id ?? "overview";
}
