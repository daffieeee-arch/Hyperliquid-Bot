import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import type { Metadata } from "next";
import type { ReactNode } from "react";

import { ThemeProvider } from "../components/theme-provider";
import { DEFAULT_COCKPIT_THEME } from "../lib/theme";

import "./globals.css";

const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-sans",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "PAPER Operator Cockpit — Hyperliquid BTC-PERP",
  description:
    "PAPER-only Operator Cockpit: fail-closed mode banner, separate COURSE-1 soak vs DATA retain identity, assumed overlay that is not venue-reconciled, paper_risk gates, public HL mid/candles, and read-only capture health. No signing, no real capital, no invented prices or risk numbers.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      data-theme={DEFAULT_COCKPIT_THEME}
      className={`${plexSans.variable} ${plexMono.variable}`}
    >
      <body>
        <ThemeProvider>{children}</ThemeProvider>
      </body>
    </html>
  );
}
