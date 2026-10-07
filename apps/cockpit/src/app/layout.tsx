import localFont from "next/font/local";
import type { Metadata, Viewport } from "next";
import { Suspense, type ReactNode } from "react";

import { CockpitRefreshProvider } from "../components/providers/cockpit-refresh";
import { AppShell } from "../components/shell/app-shell";
import { ThemeProvider } from "../components/theme-provider";
import { DEFAULT_COCKPIT_THEME } from "../lib/theme";

import "./globals.css";

// Vendored IBM Plex (SIL OFL 1.1, from Fontsource 5.3.0): the build never
// fetches fonts, so a Google Fonts outage cannot fail it.
const plexSans = localFont({
  src: [
    { path: "./fonts/ibm-plex-sans-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "./fonts/ibm-plex-sans-latin-500-normal.woff2", weight: "500", style: "normal" },
    { path: "./fonts/ibm-plex-sans-latin-600-normal.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-plex-sans",
  display: "swap",
});

const plexMono = localFont({
  src: [
    { path: "./fonts/ibm-plex-mono-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "./fonts/ibm-plex-mono-latin-500-normal.woff2", weight: "500", style: "normal" },
    { path: "./fonts/ibm-plex-mono-latin-600-normal.woff2", weight: "600", style: "normal" },
  ],
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
