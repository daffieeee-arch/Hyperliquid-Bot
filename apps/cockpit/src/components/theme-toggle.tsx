"use client";

import { useEffect, useState } from "react";

import { applyCockpitTheme } from "./theme-provider";
import { Button } from "./ui/button";
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
    <div className="theme-toggle" role="group" aria-label="Cockpit theme">
      {operatorCockpitThemes().map((id) => (
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
