"use client";

import { useEffect, useState } from "react";

import { applyCockpitTheme } from "./theme-provider";
import {
  COCKPIT_THEME_LABELS,
  DEFAULT_COCKPIT_THEME,
  operatorCockpitThemes,
  parseOperatorCockpitTheme,
  type CockpitTheme,
} from "../lib/theme";

export function ThemeToggle() {
  const [theme, setTheme] = useState<CockpitTheme>(DEFAULT_COCKPIT_THEME);

  useEffect(() => {
    setTheme(parseOperatorCockpitTheme(document.documentElement.dataset.theme));
  }, []);

  return (
    <div className="seg" role="group" aria-label="Cockpit theme">
      {operatorCockpitThemes().map((id) => (
        <button
          key={id}
          type="button"
          aria-pressed={theme === id}
          onClick={() => {
            applyCockpitTheme(id);
            setTheme(id);
          }}
        >
          {COCKPIT_THEME_LABELS[id]}
        </button>
      ))}
    </div>
  );
}
