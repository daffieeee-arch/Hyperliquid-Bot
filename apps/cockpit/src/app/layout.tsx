import { IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";
import type { Metadata } from "next";
import type { ReactNode } from "react";

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
  title: "PAPER DESK — Hyperliquid BTC-PERP",
  description:
    "PAPER-only Operator Cockpit DESK, MARKETS, and RISK: public BTC-PERP mid, bound capture live-versus-stale, reconstructable paper position, assumed overlay PnL, fail-closed risk bounds, and a four-venue capture strip. No signing, no real capital, no invented prices or risk numbers.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${plexSans.variable} ${plexMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
