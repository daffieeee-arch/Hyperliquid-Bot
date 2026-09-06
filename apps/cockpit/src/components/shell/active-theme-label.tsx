"use client";

import { useEffect, useState } from "react";

import {
  COCKPIT_THEME_LABELS,
  DEFAULT_COCKPIT_THEME,
  parseOperatorCockpitTheme,
  type CockpitTheme,
} from "../../lib/theme";

/**
 * Footer label for the theme that is actually applied.
 *
 * A fixed string here would keep claiming "C Fail-Closed Amber" after the
 * operator switched to Desk Dark.
 */
export function ActiveThemeLabel() {
  const [theme, setTheme] = useState<CockpitTheme>(DEFAULT_COCKPIT_THEME);

  useEffect(() => {
    const read = (): void => {
      setTheme(parseOperatorCockpitTheme(document.documentElement.dataset.theme));
    };
    read();
    const observer = new MutationObserver(read);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => {
      observer.disconnect();
    };
  }, []);

  return <span>{COCKPIT_THEME_LABELS[theme]}</span>;
}
