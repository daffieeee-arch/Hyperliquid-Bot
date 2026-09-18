import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import type { Metadata, Viewport } from "next";
import { Suspense, type ReactNode } from "react";

import { CockpitRefreshProvider } from "../components/providers/cockpit-refresh";
import { AppShell } from "../components/shell/app-shell";
import { ThemeProvider } from "../components/theme-provider";
import { DEFAULT_COCKPIT_THEME } from "../lib/theme";

import "./globals.css";

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-sans",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "PAPER Operator Cockpit — Hyperliquid BTC-PERP",
  description:
    "PAPER-only operator cockpit: capture health, public market context, retained-run research artifacts and the COURSE-1 soak desk. No signing, no real capital, no invented prices or risk numbers.",
};

export const viewport: Viewport = {
  themeColor: "#0a0c10",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      data-theme={DEFAULT_COCKPIT_THEME}
      className={`${plexSans.variable} ${plexMono.variable}`}
    >
      <body>
        <ThemeProvider>
          <CockpitRefreshProvider>
            <Suspense fallback={null}>
              <AppShell>{children}</AppShell>
            </Suspense>
          </CockpitRefreshProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
