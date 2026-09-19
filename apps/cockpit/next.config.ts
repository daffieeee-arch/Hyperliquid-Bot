import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  images: {
    unoptimized: true,
  },
  // Tailscale Serve hostname (dev HMR/client assets). Not public internet.
  allowedDevOrigins: ["chupa.tail9f5972.ts.net"],
};

export default nextConfig;
