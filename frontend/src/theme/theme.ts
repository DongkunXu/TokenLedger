import { useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
export type Resolved = "light" | "dark";

const KEY = "tokenledger.theme";
const media = () => window.matchMedia("(prefers-color-scheme: dark)");

function readChoice(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    /* storage unavailable: fall back to system */
  }
  return "system";
}

// Theme choice persisted per browser; returns the resolved theme so charts can re-render.
export function useTheme(): [ThemeChoice, (c: ThemeChoice) => void, Resolved] {
  const [choice, setChoice] = useState<ThemeChoice>(readChoice);
  const [systemDark, setSystemDark] = useState(() => media().matches);

  useEffect(() => {
    const m = media();
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    m.addEventListener("change", onChange);
    return () => m.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    if (choice === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", choice);
    try {
      localStorage.setItem(KEY, choice);
    } catch {
      /* ignore */
    }
  }, [choice]);

  const resolved: Resolved = choice === "system" ? (systemDark ? "dark" : "light") : choice;
  return [choice, setChoice, resolved];
}

// Current values of the CSS design tokens, for chart options.
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function providerColor(provider: string): string {
  const known = ["claude", "codex", "antigravity"];
  return cssVar(known.includes(provider) ? `--${provider}` : "--other");
}
