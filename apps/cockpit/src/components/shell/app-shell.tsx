"use client";

import { Menu, X } from "lucide-react";
import { usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { ActiveThemeLabel } from "./active-theme-label";
import { CapturePulse } from "./capture-pulse";
import { NavLinks } from "./nav-links";
import { Button } from "../ui/button";
import { ThemeToggle } from "../theme-toggle";
import { activeRouteId, cockpitRoute } from "../../lib/navigation";
import type { VenueCaptureQuery } from "../../lib/paths";
import { VENUE_CAPTURE_QUERY_KEYS } from "../../lib/venue-capture-poll";

const CONSTRAINTS = [
  "NO WALLET SIGNING",
  "NO LIVE CAPITAL",
  "NO CAPTURE START/STOP",
  "SOAK != RETAIN",
  "ASSUMED != VENUE RECONCILED",
  "RISK != INVENTED",
  "PUBLIC MID != RESEARCH",
  "PUBLIC MID != PAPER PNL",
];

function Brand() {
  return (
    <div className="brand">
      <span className="brand-mark" aria-hidden="true">
        HLQ
      </span>
      <span className="brand-copy">
        <strong>Operator Cockpit</strong>
        <span>BTC-PERP · COURSE-1 · PAPER</span>
      </span>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const [drawerOpen, setDrawerOpen] = useState(false);

  const activeId = activeRouteId(pathname);
  const route = cockpitRoute(activeId);

  const search = useMemo(() => {
    const params = new URLSearchParams();
    for (const key of VENUE_CAPTURE_QUERY_KEYS) {
      const value = searchParams.get(key);
      if (value !== null && value.trim() !== "") {
        params.set(key, value.trim());
      }
    }
    const encoded = params.toString();
    return encoded === "" ? "" : `?${encoded}`;
  }, [searchParams]);

  const query = useMemo<VenueCaptureQuery>(
    () => ({
      data1a_run_id: searchParams.get("data1a_run_id") ?? undefined,
      data1b_run_id: searchParams.get("data1b_run_id") ?? undefined,
      data1e_run_id: searchParams.get("data1e_run_id") ?? undefined,
      data1f_run_id: searchParams.get("data1f_run_id") ?? undefined,
    }),
    [searchParams],
  );

  useEffect(() => {
    setDrawerOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!drawerOpen) {
      return;
    }
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        setDrawerOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, [drawerOpen]);

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <Link href={`/${search}`} style={{ textDecoration: "none", color: "inherit" }}>
          <Brand />
        </Link>
        <NavLinks activeId={activeId} search={search} />
        <div className="nav-foot">
          <span className="nav-heading">Appearance</span>
          <ThemeToggle />
        </div>
      </aside>

      {drawerOpen ? (
        <>
          <div
            className="drawer-backdrop"
            role="presentation"
            onClick={() => {
              setDrawerOpen(false);
            }}
          />
          <div className="drawer" role="dialog" aria-modal="true" aria-label="Cockpit navigation">
            <div className="row">
              <Brand />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="spacer"
                aria-label="Close navigation"
                onClick={() => {
                  setDrawerOpen(false);
                }}
              >
                <X aria-hidden="true" />
              </Button>
            </div>
            <NavLinks
              activeId={activeId}
              search={search}
              onNavigate={() => {
                setDrawerOpen(false);
              }}
            />
            <div className="nav-foot">
              <span className="nav-heading">Appearance</span>
              <ThemeToggle />
            </div>
          </div>
        </>
      ) : null}

      <div className="app-main">
        <header className="topbar">
          <Button
            type="button"
            variant="outline"
            size="icon"
            className="lg:hidden"
            aria-label="Open navigation"
            aria-expanded={drawerOpen}
            onClick={() => {
              setDrawerOpen(true);
            }}
          >
            <Menu aria-hidden="true" />
          </Button>
          <nav className="topbar-crumb" aria-label="Breadcrumb">
            <span className="crumb-root">Cockpit</span>
            <span className="crumb-root sep" aria-hidden="true">
              /
            </span>
            <strong className="truncate-1">{route.label}</strong>
          </nav>
          <div className="topbar-actions">
            <span className="paper-pill" title="PAPER only. No signing, no live capital.">
              <span className="dot" aria-hidden="true" />
              Paper
            </span>
            <CapturePulse query={query} />
          </div>
        </header>

        <main className="app-content">{children}</main>

        <footer className="app-footer">
          <span>PAPER ONLY</span>
          <ActiveThemeLabel />
          <details className="spacer">
            <summary>Fail-closed constraints</summary>
            <div className="app-footer-list">
              {CONSTRAINTS.map((item) => (
                <span key={item}>{item}</span>
              ))}
            </div>
          </details>
        </footer>
      </div>
    </div>
  );
}
