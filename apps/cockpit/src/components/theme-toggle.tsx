"use client";

import { useEffect, useState } from "react";

import { applyCockpitTheme } from "./theme-provider";
import { Button } from "./ui/button";
import {
  COCKPIT_THEME_LABELS,
  COCKPIT_THEMES,
  DEFAULT_COCKPIT_THEME,
  parseCockpitTheme,
  type CockpitTheme,
} from "../lib/theme";

export function ThemeToggle() {
  const [theme, setTheme] = useState<CockpitTheme>(DEFAULT_COCKPIT_THEME);

  useEffect(() => {
    setTheme(parseCockpitTheme(document.documentElement.dataset.theme));
  }, []);

  return (
    <div className="theme-toggle" role="group" aria-label="Cockpit visual variant">
      {COCKPIT_THEMES.map((id) => (
        <Button
          key={id}
          type="button"
          size="sm"
          variant={theme === id ? "paper" : "outline"}
          aria-pressed={theme === id}
          onClick={() => {
            applyCockpitTheme(id);
            setTheme(id);
          }}
        >
          {COCKPIT_THEME_LABELS[id]}
        </Button>
      ))}
    </div>
  );
}
