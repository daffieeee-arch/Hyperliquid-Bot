"use client";

import { useEffect, type ReactNode } from "react";

import { COCKPIT_THEME_STORAGE_KEY, DEFAULT_COCKPIT_THEME, parseCockpitTheme } from "../lib/theme";

export function ThemeProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    const stored = parseCockpitTheme(window.localStorage.getItem(COCKPIT_THEME_STORAGE_KEY));
    document.documentElement.dataset.theme = stored;
  }, []);

  return children;
}

export function applyCockpitTheme(theme: string): void {
  const next = parseCockpitTheme(theme);
  document.documentElement.dataset.theme = next;
  window.localStorage.setItem(COCKPIT_THEME_STORAGE_KEY, next);
}

export { DEFAULT_COCKPIT_THEME };
