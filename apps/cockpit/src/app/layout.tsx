import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "PAPER cockpit — Hyperliquid BTC-PERP",
  description:
    "First PAPER cockpit screen: public BTC-PERP mid plus reconstructable paper position, assumed overlay PnL, and capture health.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
