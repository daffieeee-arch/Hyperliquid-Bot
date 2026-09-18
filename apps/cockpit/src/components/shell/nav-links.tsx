"use client";

import { Bot, CandlestickChart, FlaskConical, Gauge, ShieldCheck } from "lucide-react";
import Link from "next/link";
import type { ComponentType } from "react";

import { COCKPIT_ROUTES, type CockpitRouteId } from "../../lib/navigation";

const ICONS: Record<string, ComponentType<{ size?: number; "aria-hidden"?: boolean }>> = {
  gauge: Gauge,
  candlestick: CandlestickChart,
  flask: FlaskConical,
  bot: Bot,
  shield: ShieldCheck,
};

export function NavLinks({
  activeId,
  search,
  onNavigate,
}: {
  activeId: CockpitRouteId;
  search: string;
  onNavigate?: () => void;
}) {
  return (
    <nav className="nav-group" aria-label="Cockpit sections">
      <span className="nav-heading">Workspaces</span>
      {COCKPIT_ROUTES.map((route) => {
        const Icon = ICONS[route.icon] ?? Gauge;
        const active = route.id === activeId;
        return (
          <Link
            key={route.id}
            href={`${route.href}${search}`}
            className="nav-item"
            aria-current={active ? "page" : undefined}
            onClick={onNavigate}
          >
            <Icon size={15} aria-hidden={true} />
            {route.label}
          </Link>
        );
      })}
    </nav>
  );
}
