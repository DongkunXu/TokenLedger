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

// Theme choice persisted per browser. The resolved theme is always written to
// <html data-theme>, so the stylesheet only needs a light and a dark branch
// (index.html sets it before first paint the same way).
export function useTheme(): [ThemeChoice, (c: ThemeChoice) => void, Resolved] {
  const [choice, setChoice] = useState<ThemeChoice>(readChoice);
  const [systemDark, setSystemDark] = useState(() => media().matches);

  useEffect(() => {
    const m = media();
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    m.addEventListener("change", onChange);
    return () => m.removeEventListener("change", onChange);
  }, []);

  const resolved: Resolved = choice === "system" ? (systemDark ? "dark" : "light") : choice;

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", resolved);
    try {
      localStorage.setItem(KEY, choice);
    } catch {
      /* ignore */
    }
  }, [choice, resolved]);

  return [choice, setChoice, resolved];
}
